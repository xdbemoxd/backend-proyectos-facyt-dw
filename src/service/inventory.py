"""Servicio de inventario: stock, materias primas, lotes y despachos (RF-4).

Decisiones de permisos y responsabilidades:

- Lectura de stock, catalogos, lotes y despachos: cualquier usuario
  autenticado. El coordinador ve **solo los despachos de sus proyectos**;
  administrador y aprobador ven todos (prevencion de IDOR).
- Crear/editar materias primas, registrar lotes y registrar despachos: solo
  Administrador y Aprobador/Gestor de Inventario.
- El coordinador **solicita el retiro via solicitudes de gasto** (RF-5) y el
  aprobador/gestor registra el despacho fisico. Por eso el POST de despacho no
  esta disponible para el coordinador; esta decision se refleja en la UI.

El stock **no se escribe a mano**: ``trg_lot_add_stock`` lo suma al insertar un
lote y ``trg_dispatch_log_subtract_stock`` lo resta al insertar un despacho.
El servicio solo inserta la fila; el trigger valida y actualiza el stock en la
misma transaccion. No se duplica esa logica ni se usa ``FOR UPDATE`` sobre el
stock (el trigger ya bloquea la fila).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from src.model.inventory import (
    AudithRawMaterialSupplierModel,
    DispatchLogModel,
    RawMaterialModel,
    RawMaterialSupplierModel,
    ResourceTypeModel,
)
from src.model.project import ProjectModel
from src.model.user import UserModel
from src.model.vendor import VendorModel
from src.model.views import InventoryStockStatusView
from src.schema.inventory import (
    CatalogVendorOut,
    DispatchCreate,
    DispatchOut,
    DispatchProjectOut,
    DispatchUserOut,
    InventoryCatalogs,
    LotCreate,
    LotOut,
    PaginatedDispatches,
    PaginatedLots,
    PaginatedStock,
    RawMaterialCreate,
    RawMaterialUpdate,
    ResourceTypeOut,
    StockItemOut,
    SupplierItemRef,
)
from src.schema.user import CurrentUser
from src.utils.audit import record_audit
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

logger = logging.getLogger(__name__)

MANAGER_ROLES: frozenset[str] = frozenset({ADMIN_ROLE, APPROVER_ROLE})


def can_manage_inventory(role: str | None) -> bool:
    """Solo admin y aprobador mutan inventario."""
    return role in MANAGER_ROLES


def _require_manager(actor: CurrentUser) -> None:
    if not can_manage_inventory(actor.rol):
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


def _full_name(user) -> str:
    if user is None:
        return "Sin responsable"
    parts = [part for part in (user.name, user.lastname) if part]
    return " ".join(parts) or user.email or "Sin responsable"


def _stock_out(
    row: InventoryStockStatusView, *, has_movements: bool
) -> StockItemOut:
    item = StockItemOut.model_validate(row)
    return item.model_copy(update={"has_movements": has_movements})


async def _materials_with_movements(
    db: AsyncSession, resource_ids: set[uuid.UUID] | None = None
) -> set[uuid.UUID]:
    """Ids de materias primas con al menos un lote o un despacho."""
    lot_suppliers = select(
        AudithRawMaterialSupplierModel.id_raw_materials_supplier
    )
    dispatch_suppliers = select(DispatchLogModel.id_raw_materials_suppliers)
    moved_suppliers = lot_suppliers.union(dispatch_suppliers)

    statement = select(RawMaterialSupplierModel.raw_materials_id).where(
        RawMaterialSupplierModel.id.in_(moved_suppliers)
    )
    if resource_ids:
        statement = statement.where(
            RawMaterialSupplierModel.raw_materials_id.in_(resource_ids)
        )
    return set((await db.scalars(statement)).all())


async def _stock_row(
    db: AsyncSession, supplier_item_id: uuid.UUID
) -> InventoryStockStatusView:
    row = await db.scalar(
        select(InventoryStockStatusView).where(
            InventoryStockStatusView.supplier_item_id == supplier_item_id
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro de stock no encontrado",
        )
    return row


async def list_catalogs(db: AsyncSession) -> InventoryCatalogs:
    types = (
        await db.execute(
            select(ResourceTypeModel).order_by(ResourceTypeModel.id.asc())
        )
    ).scalars().all()
    vendors = (
        await db.execute(select(VendorModel).order_by(VendorModel.name.asc()))
    ).scalars().all()
    return InventoryCatalogs(
        resource_types=[ResourceTypeOut.model_validate(item) for item in types],
        vendors=[CatalogVendorOut.model_validate(vendor) for vendor in vendors],
    )


async def list_stock(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    resource_type: str | None = None,
    only_critical: bool = False,
) -> PaginatedStock:
    _pagination(limit, offset)

    conditions = []
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                InventoryStockStatusView.resource_name.ilike(
                    pattern, escape="\\"
                ),
                InventoryStockStatusView.vendor_name.ilike(
                    pattern, escape="\\"
                ),
            )
        )
    if resource_type:
        conditions.append(
            InventoryStockStatusView.resource_type == resource_type.strip()
        )
    if only_critical:
        conditions.append(InventoryStockStatusView.is_critical.is_(True))

    count_stmt = select(func.count()).select_from(InventoryStockStatusView)
    statement = select(InventoryStockStatusView)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                InventoryStockStatusView.resource_name.asc(),
                InventoryStockStatusView.vendor_name.asc(),
                InventoryStockStatusView.supplier_item_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    moved = (
        await _materials_with_movements(
            db, {row.resource_id for row in rows}
        )
        if rows
        else set()
    )
    return PaginatedStock(
        items=[
            _stock_out(row, has_movements=row.resource_id in moved)
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_stock_item(
    db: AsyncSession, supplier_item_id: uuid.UUID
) -> StockItemOut:
    row = await _stock_row(db, supplier_item_id)
    moved = await _materials_with_movements(db, {row.resource_id})
    return _stock_out(row, has_movements=row.resource_id in moved)


def raw_material_snapshot(
    material: RawMaterialModel,
    supplier: RawMaterialSupplierModel,
) -> dict:
    """Estado auditable sin PII ni costos historicos."""
    return {
        "resource_id": str(material.id_inventroy),
        "supplier_item_id": str(supplier.id),
        "name": material.name,
        "description": material.description,
        "resource_type_id": material.resource_type_id,
        "unit": material.unit,
        "vendors_id": str(supplier.vendors_id),
        "current_stock": supplier.current_stock,
        "minimun_stock": supplier.minimun_stock,
    }


async def _resource_type_or_422(
    db: AsyncSession, resource_type_id: int
) -> ResourceTypeModel:
    resource_type = await db.get(ResourceTypeModel, resource_type_id)
    if resource_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Tipo de recurso inválido",
        )
    return resource_type


async def _vendor_or_422(db: AsyncSession, vendor_id: uuid.UUID) -> VendorModel:
    vendor = await db.get(VendorModel, vendor_id)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Proveedor inválido",
        )
    return vendor


async def _supplier_for_write(
    db: AsyncSession,
    supplier_item_id: uuid.UUID,
    *,
    not_found_status: int = status.HTTP_404_NOT_FOUND,
    lock: bool = False,
) -> RawMaterialSupplierModel:
    statement = (
        select(RawMaterialSupplierModel)
        .options(
            contains_eager(RawMaterialSupplierModel.raw_material),
            contains_eager(RawMaterialSupplierModel.vendor),
        )
        .join(RawMaterialSupplierModel.raw_material)
        .join(RawMaterialSupplierModel.vendor)
        .where(RawMaterialSupplierModel.id == supplier_item_id)
    )
    if lock:
        statement = statement.with_for_update(
            of=RawMaterialSupplierModel
        )
    supplier = (await db.execute(statement)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(
            status_code=not_found_status,
            detail="Material o proveedor no válido",
        )
    return supplier


def _integrity_conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=message
    )


async def create_raw_material(
    db: AsyncSession,
    payload: RawMaterialCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> StockItemOut:
    _require_manager(actor)

    resource_type = await _resource_type_or_422(db, payload.resource_type_id)
    vendor = await _vendor_or_422(db, payload.vendors_id)

    material = await db.scalar(
        select(RawMaterialModel)
        .where(func.lower(RawMaterialModel.name) == payload.name.lower())
        .limit(1)
    )
    if material is not None:
        if (
            material.resource_type_id != resource_type.id
            or material.unit != payload.unit
        ):
            raise _integrity_conflict(
                "Ya existe una materia prima con ese nombre "
                "y otro tipo o unidad"
            )
        duplicate = await db.scalar(
            select(RawMaterialSupplierModel.id)
            .where(
                RawMaterialSupplierModel.raw_materials_id
                == material.id_inventroy,
                RawMaterialSupplierModel.vendors_id == vendor.id,
            )
            .limit(1)
        )
        if duplicate is not None:
            raise _integrity_conflict(
                "Esa materia prima ya está registrada con ese proveedor"
            )
    else:
        material = RawMaterialModel(
            name=payload.name,
            description=payload.description or None,
            resource_type_id=resource_type.id,
            unit=payload.unit,
        )
        db.add(material)
        await db.flush()

    supplier = RawMaterialSupplierModel(
        raw_materials_id=material.id_inventroy,
        vendors_id=vendor.id,
        current_stock=Decimal("0.00"),
        minimun_stock=payload.minimun_stock,
    )
    db.add(supplier)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Conflicto creando materia prima (%s)", type(exc).__name__
        )
        raise _integrity_conflict(
            "No se pudo registrar la materia prima"
        ) from exc

    await record_audit(
        db,
        entity="RawMaterial",
        action="create_raw_material",
        entity_id=material.id_inventroy,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_201_CREATED,
        after=raw_material_snapshot(material, supplier),
    )
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error guardando materia prima (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    row = await _stock_row(db, supplier.id)
    moved = await _materials_with_movements(db, {row.resource_id})
    return _stock_out(row, has_movements=row.resource_id in moved)


async def _has_stock_movements(
    db: AsyncSession, resource_id: uuid.UUID
) -> bool:
    return bool(await _materials_with_movements(db, {resource_id}))


async def update_raw_material(
    db: AsyncSession,
    supplier_item_id: uuid.UUID,
    payload: RawMaterialUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> StockItemOut:
    _require_manager(actor)

    supplier = await _supplier_for_write(
        db, supplier_item_id, lock=True
    )
    material = supplier.raw_material

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nada que actualizar",
        )
    for field in ("name", "resource_type_id", "unit", "minimun_stock"):
        if field in updates and updates[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El campo {field} no puede ser nulo",
            )

    before = raw_material_snapshot(material, supplier)
    changed = False

    new_name = updates.get("name")
    if new_name and new_name != material.name:
        other = await db.scalar(
            select(RawMaterialModel.id_inventroy)
            .where(
                func.lower(RawMaterialModel.name) == new_name.lower(),
                RawMaterialModel.id_inventroy != material.id_inventroy,
            )
            .limit(1)
        )
        if other is not None:
            raise _integrity_conflict(
                "Ya existe otra materia prima con ese nombre"
            )
        material.name = new_name
        changed = True

    new_type = updates.get("resource_type_id")
    if new_type is not None and new_type != material.resource_type_id:
        resource_type = await _resource_type_or_422(db, new_type)
        material.resource_type_id = resource_type.id
        changed = True

    new_unit = updates.get("unit")
    if new_unit is not None and new_unit != material.unit:
        if await _has_stock_movements(db, material.id_inventroy):
            raise _integrity_conflict(
                "No se puede cambiar la unidad: el material ya tiene "
                "movimientos de stock"
            )
        material.unit = new_unit
        changed = True

    new_minimum = updates.get("minimun_stock")
    if (
        new_minimum is not None
        and Decimal(new_minimum) != Decimal(supplier.minimun_stock)
    ):
        supplier.minimun_stock = new_minimum
        changed = True

    if not changed:
        await db.rollback()
        return await get_stock_item(db, supplier_item_id)

    await record_audit(
        db,
        entity="RawMaterial",
        action="update_raw_material",
        entity_id=material.id_inventroy,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=raw_material_snapshot(material, supplier),
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Conflicto actualizando materia prima (%s)",
            type(exc).__name__,
        )
        raise _integrity_conflict(
            "No se pudo guardar la materia prima"
        ) from exc

    return await get_stock_item(db, supplier_item_id)


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------


def _lot_out(lot: AudithRawMaterialSupplierModel) -> LotOut:
    supplier = lot.raw_material_supplier
    material = supplier.raw_material
    vendor = supplier.vendor
    return LotOut(
        id=lot.id,
        supplier_item=SupplierItemRef(
            id=supplier.id,
            resource_name=material.name,
            vendor_name=vendor.name,
            unit=material.unit,
        ),
        in_date=lot.in_date,
        count_raw_materials_in=lot.count_raw_materials_in,
        unit_cost=lot.unit_cost,
        invoice_reference=lot.invoice_reference,
        created_at=lot.created_at,
    )


def _lot_query():
    return (
        select(AudithRawMaterialSupplierModel)
        .join(AudithRawMaterialSupplierModel.raw_material_supplier)
        .join(RawMaterialSupplierModel.raw_material)
        .join(RawMaterialSupplierModel.vendor)
        .options(
            contains_eager(
                AudithRawMaterialSupplierModel.raw_material_supplier
            ).contains_eager(RawMaterialSupplierModel.raw_material),
            contains_eager(
                AudithRawMaterialSupplierModel.raw_material_supplier
            ).contains_eager(RawMaterialSupplierModel.vendor),
        )
    )


def _lot_conditions(
    *, search: str | None, supplier_item_id: uuid.UUID | None
) -> list:
    conditions = []
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                RawMaterialModel.name.ilike(pattern, escape="\\"),
                VendorModel.name.ilike(pattern, escape="\\"),
                AudithRawMaterialSupplierModel.invoice_reference.ilike(
                    pattern, escape="\\"
                ),
            )
        )
    if supplier_item_id is not None:
        conditions.append(
            AudithRawMaterialSupplierModel.id_raw_materials_supplier
            == supplier_item_id
        )
    return conditions


async def list_lots(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    supplier_item_id: uuid.UUID | None = None,
) -> PaginatedLots:
    _pagination(limit, offset)
    conditions = _lot_conditions(
        search=search, supplier_item_id=supplier_item_id
    )

    count_stmt = (
        select(func.count())
        .select_from(AudithRawMaterialSupplierModel)
        .join(AudithRawMaterialSupplierModel.raw_material_supplier)
        .join(RawMaterialSupplierModel.raw_material)
        .join(RawMaterialSupplierModel.vendor)
    )
    statement = _lot_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                AudithRawMaterialSupplierModel.in_date.desc(),
                AudithRawMaterialSupplierModel.created_at.desc(),
                AudithRawMaterialSupplierModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedLots(
        items=[_lot_out(lot) for lot in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _get_lot(
    db: AsyncSession, lot_id: uuid.UUID
) -> AudithRawMaterialSupplierModel:
    lot = await db.scalar(
        _lot_query().where(AudithRawMaterialSupplierModel.id == lot_id)
    )
    if lot is None:  # pragma: no cover - recien insertado
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lote no encontrado",
        )
    return lot


def lot_snapshot(lot: AudithRawMaterialSupplierModel) -> dict:
    return {
        "id": str(lot.id),
        "supplier_item_id": str(lot.id_raw_materials_supplier),
        "in_date": lot.in_date,
        "count_raw_materials_in": lot.count_raw_materials_in,
        "unit_cost": lot.unit_cost,
        "invoice_reference": lot.invoice_reference,
    }


async def create_lot(
    db: AsyncSession,
    payload: LotCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> LotOut:
    """Inserta el lote; el trigger de BD suma el stock (no se toca a mano)."""
    _require_manager(actor)

    await _supplier_for_write(
        db,
        payload.id_raw_materials_supplier,
        not_found_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )

    lot = AudithRawMaterialSupplierModel(
        id_raw_materials_supplier=payload.id_raw_materials_supplier,
        in_date=payload.in_date or datetime.now(timezone.utc),
        count_raw_materials_in=payload.count_raw_materials_in,
        unit_cost=payload.unit_cost,
        invoice_reference=payload.invoice_reference or None,
    )
    db.add(lot)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Lote rechazado por la BD (%s)", type(exc).__name__)
        raise _integrity_conflict(
            "No se pudo registrar el lote"
        ) from exc

    await record_audit(
        db,
        entity="Lot",
        action="register_lot",
        entity_id=lot.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_201_CREATED,
        after=lot_snapshot(lot),
    )
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error("Error guardando lote (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return _lot_out(await _get_lot(db, lot.id))


# ---------------------------------------------------------------------------
# Despachos
# ---------------------------------------------------------------------------


def _dispatch_out(dispatch: DispatchLogModel) -> DispatchOut:
    supplier = dispatch.raw_material_supplier
    material = supplier.raw_material
    vendor = supplier.vendor
    return DispatchOut(
        id=dispatch.id,
        supplier_item=SupplierItemRef(
            id=supplier.id,
            resource_name=material.name,
            vendor_name=vendor.name,
            unit=material.unit,
        ),
        project=DispatchProjectOut(
            id=dispatch.project.id,
            unique_code=dispatch.project.unique_code,
            name=dispatch.project.name,
        ),
        user=DispatchUserOut(
            id=dispatch.user.id,
            full_name=_full_name(dispatch.user),
        ),
        use_raw_materials=dispatch.use_raw_materials,
        material_requested_at=dispatch.material_requested_at,
        notes=dispatch.notes,
    )


def _dispatch_query():
    return (
        select(DispatchLogModel)
        .join(DispatchLogModel.raw_material_supplier)
        .join(RawMaterialSupplierModel.raw_material)
        .join(RawMaterialSupplierModel.vendor)
        .join(DispatchLogModel.project)
        .join(DispatchLogModel.user)
        .options(
            contains_eager(
                DispatchLogModel.raw_material_supplier
            ).contains_eager(RawMaterialSupplierModel.raw_material),
            contains_eager(
                DispatchLogModel.raw_material_supplier
            ).contains_eager(RawMaterialSupplierModel.vendor),
            contains_eager(DispatchLogModel.project),
            contains_eager(DispatchLogModel.user),
        )
    )


def _dispatch_conditions(
    actor: CurrentUser,
    *,
    search: str | None,
    project_id: uuid.UUID | None,
    supplier_item_id: uuid.UUID | None,
) -> list:
    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)
    if project_id is not None:
        conditions.append(DispatchLogModel.id_project == project_id)
    if supplier_item_id is not None:
        conditions.append(
            DispatchLogModel.id_raw_materials_suppliers == supplier_item_id
        )
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                RawMaterialModel.name.ilike(pattern, escape="\\"),
                VendorModel.name.ilike(pattern, escape="\\"),
                ProjectModel.unique_code.ilike(pattern, escape="\\"),
                ProjectModel.name.ilike(pattern, escape="\\"),
                UserModel.name.ilike(pattern, escape="\\"),
                UserModel.lastname.ilike(pattern, escape="\\"),
            )
        )
    return conditions


async def list_dispatches(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    project_id: uuid.UUID | None = None,
    supplier_item_id: uuid.UUID | None = None,
) -> PaginatedDispatches:
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

    conditions = _dispatch_conditions(
        actor,
        search=search,
        project_id=project_id,
        supplier_item_id=supplier_item_id,
    )

    count_stmt = (
        select(func.count())
        .select_from(DispatchLogModel)
        .join(DispatchLogModel.raw_material_supplier)
        .join(RawMaterialSupplierModel.raw_material)
        .join(RawMaterialSupplierModel.vendor)
        .join(DispatchLogModel.project)
        .join(DispatchLogModel.user)
    )
    statement = _dispatch_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                DispatchLogModel.material_requested_at.desc(),
                DispatchLogModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedDispatches(
        items=[_dispatch_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def dispatch_snapshot(dispatch: DispatchLogModel) -> dict:
    """Estado auditable del despacho (sin email ni nombre del usuario)."""
    return {
        "id": str(dispatch.id),
        "supplier_item_id": str(dispatch.id_raw_materials_suppliers),
        "project_id": str(dispatch.id_project),
        "user_id": str(dispatch.id_user),
        "use_raw_materials": dispatch.use_raw_materials,
        "material_requested_at": dispatch.material_requested_at,
        "notes": dispatch.notes,
    }


def dispatch_db_error(exc: IntegrityError) -> HTTPException:
    """Traduce errores del trigger/FK del despacho a HTTP sin filtrar SQL."""
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    message = str(
        getattr(original, "message", None) or original or ""
    ).lower()

    if sqlstate == "23514" or "stock insuficiente" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock insuficiente para la cantidad solicitada",
        )
    if sqlstate == "23503":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Material, usuario o proyecto no válido",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
    )


async def create_dispatch(
    db: AsyncSession,
    payload: DispatchCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> DispatchOut:
    """Registra el despacho; el trigger resta el stock o lo rechaza (409)."""
    _require_manager(actor)

    await _supplier_for_write(
        db,
        payload.id_raw_materials_suppliers,
        not_found_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
    project = await db.get(ProjectModel, payload.id_project)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proyecto no encontrado",
        )

    dispatch = DispatchLogModel(
        id_raw_materials_suppliers=payload.id_raw_materials_suppliers,
        id_project=payload.id_project,
        id_user=actor.id,
        use_raw_materials=payload.use_raw_materials,
        material_requested_at=datetime.now(timezone.utc),
        notes=payload.notes or None,
    )
    db.add(dispatch)

    try:
        await db.flush()
        await record_audit(
            db,
            entity="Dispatch",
            action="dispatch",
            entity_id=dispatch.id,
            user_id=actor.id,
            request=request,
            status_code=status.HTTP_201_CREATED,
            after=dispatch_snapshot(dispatch),
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Despacho rechazado por la BD (%s)",
            getattr(getattr(exc, "orig", None), "sqlstate", "unknown"),
        )
        raise dispatch_db_error(exc) from exc

    full = await db.scalar(
        _dispatch_query().where(DispatchLogModel.id == dispatch.id)
    )
    if full is None:  # pragma: no cover - recien insertado
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        )
    return _dispatch_out(full)
