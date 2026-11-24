"""Servicio de equipos, unidades fisicas y reservas (RF-5, RN-5).

Decisiones de permisos:

- Lectura de equipos, unidades y catalogos: cualquier usuario autenticado.
- Crear y editar equipos/unidades: solo Administrador y Aprobador/Gestor de
  Inventario. El coordinador tiene la vista en solo lectura.
- Reservar: los tres roles. El coordinador **solo** puede reservar para los
  proyectos donde ``Project.id_user`` es su propio id; administrador y
  aprobador pueden reservar para cualquier proyecto.
- Listar reservas: administrador y aprobador ven todas; el coordinador solo
  las de sus proyectos (prevencion de IDOR).

El solape de reservas **no se valida aqui**: la autoridad es el trigger
``trg_reservation_no_overlap`` (rango semiabierto, RN-5). El servicio inserta
la fila y traduce ``exclusion_violation`` (23P01) a ``409``. Tampoco se toca el
stock ni el estado del equipo a mano: una reserva no cambia
``equipament_supplier``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from src.model.equipment import (
    EquipamentSupplierModel,
    EquipmentModel,
    ReservationEquipamentModel,
    StatusEquipamentModel,
)
from src.model.project import ProjectModel
from src.model.user import UserModel
from src.model.vendor import VendorModel
from src.schema.equipment import (
    EquipmentCatalogs,
    EquipmentCreate,
    EquipmentRefOut,
    EquipmentUnitOut,
    EquipmentUpdate,
    EquipmentVendorOut,
    PaginatedEquipmentUnits,
    PaginatedReservations,
    ReservationCreate,
    ReservationOut,
    ReservationProjectOut,
    ReservationUnitOut,
    ReservationUserOut,
    StatusEquipmentOut,
)
from src.schema.user import CurrentUser
from src.utils.audit import record_audit
from src.utils.permissions import can_access_project
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

logger = logging.getLogger(__name__)

MANAGER_ROLES: frozenset[str] = frozenset({ADMIN_ROLE, APPROVER_ROLE})
STATUS_MAINTENANCE = "En mantenimiento"


def can_manage_equipment(role: str | None) -> bool:
    """Solo admin y aprobador crean/editan equipos."""
    return role in MANAGER_ROLES


def can_reserve_for_project(
    role: str | None, user_id: uuid.UUID, project_owner_id: uuid.UUID
) -> bool:
    """Admin/aprobador reservan cualquiera; coordinador solo sus proyectos."""
    if role not in (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE):
        return False
    return can_access_project(role, user_id, project_owner_id)


def _require_manager(actor: CurrentUser) -> None:
    if not can_manage_equipment(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )


def _pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Paginación inválida",
        )


def _like_pattern(term: str) -> str:
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def normalize_serial(value: str | None) -> str | None:
    """Serial sin espacios; vacio se guarda como ``NULL``."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def is_maintenance(status_name: str | None) -> bool:
    return bool(status_name) and status_name.strip().casefold() == (
        STATUS_MAINTENANCE.casefold()
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_stale_update(expected: datetime, current: datetime) -> bool:
    """True si el cliente edito sobre una version distinta a la actual."""
    return _as_utc(expected) != _as_utc(current)


def _full_name(user) -> str:
    if user is None:
        return "Sin responsable"
    parts = [part for part in (user.name, user.lastname) if part]
    return " ".join(parts) or user.email or "Sin responsable"


def unit_snapshot(unit: EquipamentSupplierModel) -> dict:
    """Estado auditable de la unidad, sin PII."""
    return {
        "id": str(unit.id),
        "equipment_id": str(unit.id_equipament),
        "equipment_name": unit.equipment.name if unit.equipment else None,
        "vendor_id": str(unit.id_vendors),
        "status_id": unit.id_status,
        "status": unit.status.name if unit.status else None,
        "model": unit.model,
        "serial_number": unit.serial_number,
    }


def reservation_snapshot(reservation: ReservationEquipamentModel) -> dict:
    """Estado auditable de la reserva, sin PII."""
    return {
        "id": str(reservation.id),
        "unit_id": str(reservation.id_equipament_supplier),
        "project_id": str(reservation.id_project),
        "user_id": str(reservation.id_user),
        "reservation_date": reservation.reservation_date,
        "end_date": reservation.end_date,
        "purpose": reservation.purpose,
    }


def _unit_query():
    return (
        select(EquipamentSupplierModel)
        .join(EquipamentSupplierModel.equipment)
        .join(EquipamentSupplierModel.vendor)
        .join(EquipamentSupplierModel.status)
        .options(
            contains_eager(EquipamentSupplierModel.equipment),
            contains_eager(EquipamentSupplierModel.vendor),
            contains_eager(EquipamentSupplierModel.status),
        )
    )


def _reservation_query():
    return (
        select(ReservationEquipamentModel)
        .join(ReservationEquipamentModel.equipament_supplier)
        .join(EquipamentSupplierModel.equipment)
        .join(ReservationEquipamentModel.project)
        .join(ReservationEquipamentModel.user)
        .options(
            contains_eager(
                ReservationEquipamentModel.equipament_supplier
            ).contains_eager(EquipamentSupplierModel.equipment),
            contains_eager(ReservationEquipamentModel.project),
            contains_eager(ReservationEquipamentModel.user),
        )
    )


async def _get_unit(
    db: AsyncSession, unit_id: uuid.UUID, *, lock: bool = False
) -> EquipamentSupplierModel:
    statement = _unit_query().where(EquipamentSupplierModel.id == unit_id)
    if lock:
        # Solo se bloquea la fila de la unidad; los catalogos del join no.
        statement = statement.with_for_update(of=EquipamentSupplierModel)
    unit = (await db.execute(statement)).scalar_one_or_none()
    if unit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Equipo no encontrado",
        )
    return unit


async def _pending_reservation_count(
    db: AsyncSession, unit_id: uuid.UUID
) -> int:
    now = datetime.now(timezone.utc)
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ReservationEquipamentModel)
            .where(
                ReservationEquipamentModel.id_equipament_supplier == unit_id,
                ReservationEquipamentModel.end_date > now,
            )
        )
        or 0
    )


async def _pending_counts(
    db: AsyncSession, unit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not unit_ids:
        return {}
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(
                ReservationEquipamentModel.id_equipament_supplier,
                func.count(),
            )
            .where(
                ReservationEquipamentModel.id_equipament_supplier.in_(
                    unit_ids
                ),
                ReservationEquipamentModel.end_date > now,
            )
            .group_by(ReservationEquipamentModel.id_equipament_supplier)
        )
    ).all()
    return {unit_id: int(count) for unit_id, count in rows}


def _unit_out(
    unit: EquipamentSupplierModel, *, pending_reservations: int = 0
) -> EquipmentUnitOut:
    if unit.equipment is None or unit.vendor is None or unit.status is None:
        # Las FK son NOT NULL; si falta una relacion es un error interno.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    return EquipmentUnitOut(
        id=unit.id,
        equipment=EquipmentRefOut(
            id=unit.equipment.id,
            name=unit.equipment.name,
            description=unit.equipment.description,
        ),
        vendor=EquipmentVendorOut.model_validate(unit.vendor),
        status=StatusEquipmentOut.model_validate(unit.status),
        model=unit.model,
        serial_number=unit.serial_number,
        create_at=unit.create_at,
        updated_at=unit.updated_at,
        pending_reservations=pending_reservations,
    )


def _reservation_out(
    reservation: ReservationEquipamentModel,
) -> ReservationOut:
    unit = reservation.equipament_supplier
    project = reservation.project
    user = reservation.user
    if unit is None or unit.equipment is None or project is None or user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    return ReservationOut(
        id=reservation.id,
        unit=ReservationUnitOut(
            id=unit.id,
            equipment_name=unit.equipment.name,
            serial_number=unit.serial_number,
        ),
        project=ReservationProjectOut(
            id=project.id,
            unique_code=project.unique_code,
            name=project.name,
        ),
        user=ReservationUserOut(id=user.id, full_name=_full_name(user)),
        reservation_date=reservation.reservation_date,
        end_date=reservation.end_date,
        purpose=reservation.purpose,
        created_date=reservation.created_date,
    )


async def list_catalogs(db: AsyncSession) -> EquipmentCatalogs:
    statuses = (
        await db.execute(
            select(StatusEquipamentModel).order_by(
                StatusEquipamentModel.id.asc()
            )
        )
    ).scalars().all()
    vendors = (
        await db.execute(select(VendorModel).order_by(VendorModel.name.asc()))
    ).scalars().all()
    return EquipmentCatalogs(
        statuses=[
            StatusEquipmentOut.model_validate(item) for item in statuses
        ],
        vendors=[
            EquipmentVendorOut.model_validate(vendor) for vendor in vendors
        ],
    )


async def _get_equipment_by_name(
    db: AsyncSession, name: str
) -> EquipmentModel | None:
    return await db.scalar(
        select(EquipmentModel)
        .where(func.lower(EquipmentModel.name) == name.lower())
        .limit(1)
    )


async def _serial_taken(
    db: AsyncSession, serial: str, *, exclude_unit_id: uuid.UUID | None = None
) -> bool:
    statement = select(EquipamentSupplierModel.id).where(
        func.lower(func.btrim(EquipamentSupplierModel.serial_number))
        == serial.lower()
    )
    if exclude_unit_id is not None:
        statement = statement.where(
            EquipamentSupplierModel.id != exclude_unit_id
        )
    return await db.scalar(statement.limit(1)) is not None


async def _status_or_422(
    db: AsyncSession, status_id: int
) -> StatusEquipamentModel:
    status_row = await db.get(StatusEquipamentModel, status_id)
    if status_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Estado de equipo inválido",
        )
    return status_row


async def _vendor_or_422(
    db: AsyncSession, vendor_id: uuid.UUID
) -> VendorModel:
    vendor = await db.get(VendorModel, vendor_id)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Proveedor inválido",
        )
    return vendor


def _conditions(
    *,
    search: str | None,
    id_status: int | None,
    equipment_id: uuid.UUID | None,
) -> list:
    conditions = []
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                EquipmentModel.name.ilike(pattern, escape="\\"),
                VendorModel.name.ilike(pattern, escape="\\"),
                EquipamentSupplierModel.model.ilike(pattern, escape="\\"),
                EquipamentSupplierModel.serial_number.ilike(
                    pattern, escape="\\"
                ),
            )
        )
    if id_status is not None:
        conditions.append(EquipamentSupplierModel.id_status == id_status)
    if equipment_id is not None:
        conditions.append(
            EquipamentSupplierModel.id_equipament == equipment_id
        )
    return conditions


async def list_equipment_units(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    id_status: int | None = None,
    equipment_id: uuid.UUID | None = None,
) -> PaginatedEquipmentUnits:
    """Lista unidades fisicas con equipo, proveedor y estado resueltos."""
    _pagination(limit, offset)
    conditions = _conditions(
        search=search, id_status=id_status, equipment_id=equipment_id
    )

    count_stmt = (
        select(func.count())
        .select_from(EquipamentSupplierModel)
        .join(EquipamentSupplierModel.equipment)
        .join(EquipamentSupplierModel.vendor)
        .join(EquipamentSupplierModel.status)
    )
    statement = _unit_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                EquipmentModel.name.asc(),
                EquipamentSupplierModel.serial_number.asc(),
                EquipamentSupplierModel.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    pending = await _pending_counts(db, [row.id for row in rows])
    return PaginatedEquipmentUnits(
        items=[
            _unit_out(row, pending_reservations=pending.get(row.id, 0))
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_equipment_unit(
    db: AsyncSession, unit_id: uuid.UUID
) -> EquipmentUnitOut:
    unit = await _get_unit(db, unit_id)
    pending = await _pending_reservation_count(db, unit.id)
    return _unit_out(unit, pending_reservations=pending)


async def create_equipment_unit(
    db: AsyncSession,
    payload: EquipmentCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> EquipmentUnitOut:
    """Crea el tipo de equipo si no existe y registra la unidad fisica."""
    _require_manager(actor)

    vendor = await _vendor_or_422(db, payload.id_vendors)
    status_row = await _status_or_422(db, payload.id_status)
    serial = normalize_serial(payload.serial_number)
    if serial and await _serial_taken(db, serial):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una unidad con ese número de serie",
        )

    equipment = await _get_equipment_by_name(db, payload.name)
    if equipment is None:
        equipment = EquipmentModel(
            name=payload.name, description=payload.description or None
        )
        db.add(equipment)
        try:
            await db.flush()
        except IntegrityError as exc:  # pragma: no cover - carrera de nombre
            await db.rollback()
            logger.error(
                "Conflicto creando equipo (%s)", type(exc).__name__
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo registrar el equipo",
            ) from exc

    unit = EquipamentSupplierModel(
        id_equipament=equipment.id,
        id_vendors=vendor.id,
        id_status=status_row.id,
        model=payload.model,
        serial_number=serial,
        equipment=equipment,
        vendor=vendor,
        status=status_row,
    )
    db.add(unit)

    try:
        await db.flush()
    except IntegrityError as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Unidad de equipo rechazada por la BD (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se pudo registrar la unidad del equipo",
        ) from exc

    await record_audit(
        db,
        entity="Equipment",
        action="create_equipment",
        entity_id=unit.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_201_CREATED,
        after=unit_snapshot(unit),
    )
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error guardando unidad de equipo (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return await get_equipment_unit(db, unit.id)


async def update_equipment_unit(
    db: AsyncSession,
    unit_id: uuid.UUID,
    payload: EquipmentUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> EquipmentUnitOut:
    """Edita la unidad y los datos del catalogo con bloqueo optimista."""
    _require_manager(actor)

    unit = await _get_unit(db, unit_id, lock=True)

    if is_stale_update(payload.expected_updated_at, unit.updated_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El equipo fue modificado por otra persona. "
                "Recarga la página para ver los cambios"
            ),
        )

    updates = payload.model_dump(exclude_unset=True)
    updates.pop("expected_updated_at", None)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nada que actualizar",
        )
    for field in ("name", "id_status"):
        if field in updates and updates[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El campo {field} no puede ser nulo",
            )

    new_status: StatusEquipamentModel | None = None
    if "id_status" in updates:
        new_status = await _status_or_422(db, updates["id_status"])

    if "serial_number" in updates:
        serial = normalize_serial(updates["serial_number"])
        if serial and await _serial_taken(
            db, serial, exclude_unit_id=unit.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe una unidad con ese número de serie",
            )

    if (
        new_status is not None
        and new_status.id != unit.id_status
        and is_maintenance(new_status.name)
        and not is_maintenance(unit.status.name if unit.status else None)
    ):
        pending = await _pending_reservation_count(db, unit.id)
        if pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No se puede pasar a mantenimiento: tiene "
                    f"{pending} reserva(s) pendiente(s). "
                    "Reprográmalas primero"
                ),
            )

    before = unit_snapshot(unit)
    equipment = unit.equipment
    changed = False

    if "name" in updates and updates["name"] != equipment.name:
        equipment.name = updates["name"]
        changed = True

    if "description" in updates:
        description = updates["description"] or None
        if description != equipment.description:
            equipment.description = description
            changed = True

    if new_status is not None and new_status.id != unit.id_status:
        unit.id_status = new_status.id
        unit.status = new_status
        changed = True

    if "model" in updates:
        model = updates["model"] or None
        if model != unit.model:
            unit.model = model
            changed = True

    if "serial_number" in updates:
        serial = normalize_serial(updates["serial_number"])
        if serial != unit.serial_number:
            unit.serial_number = serial
            changed = True

    if not changed:
        # PATCH idempotente: no se escribe auditoria de un cambio inexistente.
        pending = await _pending_reservation_count(db, unit.id)
        return _unit_out(unit, pending_reservations=pending)

    unit.updated_at = datetime.now(timezone.utc)
    await record_audit(
        db,
        entity="Equipment",
        action="update_equipment",
        entity_id=unit.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=unit_snapshot(unit),
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Conflicto actualizando equipo (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se pudo guardar el equipo",
        ) from exc
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error actualizando equipo (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return await get_equipment_unit(db, unit.id)


def reservation_db_error(exc: IntegrityError) -> HTTPException:
    """Traduce errores del trigger/FK de la reserva sin filtrar SQL."""
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    message = str(
        getattr(original, "message", None) or original or ""
    ).lower()

    if (
        sqlstate == "23P01"
        or "solapa" in message
        or "overlap" in message
        or "exclusion" in message
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La unidad ya tiene una reserva que se solapa con ese horario"
            ),
        )
    if sqlstate == "23505":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se pudo registrar la reserva",
        )
    if sqlstate == "23514":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Rango de fechas de reserva inválido",
        )
    if sqlstate == "23503":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Equipo, proyecto o usuario no válido",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
    )


async def _project_for_reservation(
    db: AsyncSession, project_id: uuid.UUID, actor: CurrentUser
) -> ProjectModel:
    project = await db.get(ProjectModel, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proyecto no encontrado",
        )
    if not can_reserve_for_project(actor.rol, actor.id, project.id_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )
    return project


async def create_reservation(
    db: AsyncSession,
    payload: ReservationCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> ReservationOut:
    """Inserta la reserva; el trigger de BD rechaza el solape (409)."""
    await _project_for_reservation(db, payload.id_project, actor)

    unit = await _get_unit(db, payload.id_equipament_supplier)
    if is_maintenance(unit.status.name if unit.status else None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El equipo está en mantenimiento y no se puede reservar"
            ),
        )

    reservation = ReservationEquipamentModel(
        id_equipament_supplier=unit.id,
        id_project=payload.id_project,
        id_user=actor.id,
        reservation_date=payload.reservation_date,
        end_date=payload.end_date,
        purpose=payload.purpose,
        equipament_supplier=unit,
    )
    db.add(reservation)

    try:
        await db.flush()
        await record_audit(
            db,
            entity="Reservation",
            action="reserve",
            entity_id=reservation.id,
            user_id=actor.id,
            request=request,
            status_code=status.HTTP_201_CREATED,
            after=reservation_snapshot(reservation),
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Reserva rechazada por la BD (%s)",
            getattr(getattr(exc, "orig", None), "sqlstate", "unknown"),
        )
        raise reservation_db_error(exc) from exc
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error guardando reserva (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    full = await db.scalar(
        _reservation_query().where(
            ReservationEquipamentModel.id == reservation.id
        )
    )
    if full is None:  # pragma: no cover - recien insertado
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    return _reservation_out(full)


async def list_reservations(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    project_id: uuid.UUID | None = None,
    equipment_supplier_id: uuid.UUID | None = None,
) -> PaginatedReservations:
    """Reservas con alcance: coordinador solo las de sus proyectos."""
    _pagination(limit, offset)

    if actor.rol == COORDINATOR_ROLE and project_id is not None:
        owner_id = await db.scalar(
            select(ProjectModel.id_user).where(
                ProjectModel.id == project_id
            )
        )
        if owner_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proyecto no encontrado",
            )
        if owner_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado",
            )

    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)
    if project_id is not None:
        conditions.append(ReservationEquipamentModel.id_project == project_id)
    if equipment_supplier_id is not None:
        conditions.append(
            ReservationEquipamentModel.id_equipament_supplier
            == equipment_supplier_id
        )
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                EquipmentModel.name.ilike(pattern, escape="\\"),
                EquipamentSupplierModel.serial_number.ilike(
                    pattern, escape="\\"
                ),
                ProjectModel.unique_code.ilike(pattern, escape="\\"),
                ProjectModel.name.ilike(pattern, escape="\\"),
                UserModel.name.ilike(pattern, escape="\\"),
                UserModel.lastname.ilike(pattern, escape="\\"),
            )
        )

    count_stmt = (
        select(func.count())
        .select_from(ReservationEquipamentModel)
        .join(ReservationEquipamentModel.equipament_supplier)
        .join(EquipamentSupplierModel.equipment)
        .join(ReservationEquipamentModel.project)
        .join(ReservationEquipamentModel.user)
    )
    statement = _reservation_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                ReservationEquipamentModel.reservation_date.desc(),
                ReservationEquipamentModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedReservations(
        items=[_reservation_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
