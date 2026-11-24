"""Servicio de proveedores (RF-4).

Decision de permisos: solo Administrador y Aprobador/Gestor de Inventario
crean, editan y consultan proveedores (el menu y las rutas del frontend
aplican la misma regla). El coordinador no gestiona compras.

La edicion usa bloqueo optimista con ``expected_updated_at`` y un bloqueo
``FOR UPDATE`` para serializar dos ediciones concurrentes del mismo registro.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.model.vendor import VendorModel
from src.schema.user import CurrentUser
from src.schema.vendor import (
    PaginatedVendors,
    VendorCreate,
    VendorOut,
    VendorUpdate,
)
from src.service.project import is_stale_update
from src.utils.audit import record_audit
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE

logger = logging.getLogger(__name__)

MANAGER_ROLES: frozenset[str] = frozenset({ADMIN_ROLE, APPROVER_ROLE})


def can_manage_vendors(role: str | None) -> bool:
    return role in MANAGER_ROLES


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


def vendor_snapshot(vendor: VendorModel) -> dict:
    """Estado auditable del proveedor (sin columnas internas)."""
    return {
        "id": str(vendor.id),
        "name": vendor.name,
        "description": vendor.description,
        "contact_email": vendor.contact_email,
        "contact_phone": vendor.contact_phone,
    }


def _vendor_out(vendor: VendorModel) -> VendorOut:
    return VendorOut.model_validate(vendor)


async def _get_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, *, lock: bool = False
) -> VendorModel:
    statement = select(VendorModel).where(VendorModel.id == vendor_id)
    if lock:
        statement = statement.with_for_update()
    vendor = (await db.execute(statement)).scalar_one_or_none()
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proveedor no encontrado",
        )
    return vendor


async def _name_taken(
    db: AsyncSession, name: str, *, exclude_id: uuid.UUID | None = None
) -> bool:
    conditions = [func.lower(VendorModel.name) == name.lower()]
    if exclude_id is not None:
        conditions.append(VendorModel.id != exclude_id)
    found = await db.scalar(select(VendorModel.id).where(*conditions).limit(1))
    return found is not None


async def list_vendors(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
) -> PaginatedVendors:
    _pagination(limit, offset)

    conditions = []
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                VendorModel.name.ilike(pattern, escape="\\"),
                VendorModel.contact_email.ilike(pattern, escape="\\"),
            )
        )

    count_stmt = select(func.count()).select_from(VendorModel)
    statement = select(VendorModel)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                VendorModel.name.asc(), VendorModel.id.asc()
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedVendors(
        items=[_vendor_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, actor: CurrentUser
) -> VendorOut:
    if not can_manage_vendors(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )
    return _vendor_out(await _get_vendor(db, vendor_id))


async def create_vendor(
    db: AsyncSession,
    payload: VendorCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> VendorOut:
    if not can_manage_vendors(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )

    if await _name_taken(db, payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un proveedor con ese nombre",
        )

    vendor = VendorModel(
        name=payload.name,
        description=payload.description or None,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        contact_phone=payload.contact_phone or None,
    )
    db.add(vendor)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Proveedor duplicado al crear (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un proveedor con ese nombre",
        ) from exc

    await record_audit(
        db,
        entity="Vendor",
        action="create_vendor",
        entity_id=vendor.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_201_CREATED,
        after=vendor_snapshot(vendor),
    )
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error guardando proveedor (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    await db.refresh(vendor)
    return _vendor_out(vendor)


async def update_vendor(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    payload: VendorUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> VendorOut:
    if not can_manage_vendors(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )

    vendor = await _get_vendor(db, vendor_id, lock=True)

    if is_stale_update(payload.expected_updated_at, vendor.updated_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El proveedor fue modificado por otra persona. "
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
    if "name" in updates and updates["name"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El nombre no puede ser nulo",
        )

    before = vendor_snapshot(vendor)
    changed = False

    new_name = updates.get("name")
    if new_name and new_name != vendor.name:
        if await _name_taken(db, new_name, exclude_id=vendor.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un proveedor con ese nombre",
            )
        vendor.name = new_name
        changed = True

    if "description" in updates:
        description = updates["description"] or None
        if description != vendor.description:
            vendor.description = description
            changed = True
    if "contact_email" in updates:
        email = updates["contact_email"]
        email_value = str(email) if email else None
        if email_value != vendor.contact_email:
            vendor.contact_email = email_value
            changed = True
    if "contact_phone" in updates:
        phone = updates["contact_phone"] or None
        if phone != vendor.contact_phone:
            vendor.contact_phone = phone
            changed = True

    if not changed:
        # PATCH idempotente: nada cambio, no se escribe auditoria.
        await db.rollback()
        return _vendor_out(await _get_vendor(db, vendor_id))

    await record_audit(
        db,
        entity="Vendor",
        action="update_vendor",
        entity_id=vendor.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=vendor_snapshot(vendor),
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Error de integridad al actualizar proveedor (%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se pudo guardar el proveedor",
        ) from exc

    await db.refresh(vendor)
    return _vendor_out(vendor)
