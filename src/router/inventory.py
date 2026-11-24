"""Endpoints de inventario (RF-4, RN-4).

El router solo valida HTTP, aplica dependencias de rol y delega la logica al
servicio. El stock se modifica **solo** por los triggers de la base de datos.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.inventory import (
    DispatchCreate,
    DispatchOut,
    InventoryCatalogs,
    LotCreate,
    LotOut,
    PaginatedDispatches,
    PaginatedLots,
    PaginatedStock,
    RawMaterialCreate,
    RawMaterialUpdate,
    StockItemOut,
)
from src.schema.user import CurrentUser
from src.service import inventory as inventory_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

router_inventory = APIRouter(prefix="/inventory", tags=["inventory"])

_ANY_ROLE = (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE)
_MANAGERS = (ADMIN_ROLE, APPROVER_ROLE)


@router_inventory.get("/catalogs", response_model=InventoryCatalogs)
async def get_inventory_catalogs(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Tipos de recurso y proveedores reales para los formularios."""
    return await inventory_service.list_catalogs(db)


@router_inventory.get("/stock", response_model=PaginatedStock)
async def list_stock(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    resource_type: str | None = Query(default=None, max_length=255),
    only_critical: bool = Query(default=False),
):
    """Stock por proveedor con busqueda, tipo y filtro de criticos."""
    return await inventory_service.list_stock(
        db,
        limit=limit,
        offset=offset,
        search=search,
        resource_type=resource_type,
        only_critical=only_critical,
    )


@router_inventory.get(
    "/stock/{supplier_item_id}", response_model=StockItemOut
)
async def get_stock_item(
    supplier_item_id: uuid.UUID,
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Detalle de un registro de stock (para editar materia prima)."""
    return await inventory_service.get_stock_item(db, supplier_item_id)


@router_inventory.post(
    "/raw-materials",
    response_model=StockItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_raw_material(
    payload: RawMaterialCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Administrador o aprobador registran materia prima y proveedor."""
    return await inventory_service.create_raw_material(
        db, payload, current_user, request=request
    )


@router_inventory.patch(
    "/raw-materials/{supplier_item_id}", response_model=StockItemOut
)
async def update_raw_material(
    supplier_item_id: uuid.UUID,
    payload: RawMaterialUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Edita el material y el stock minimo; la unidad queda bloqueada con movimientos."""
    return await inventory_service.update_raw_material(
        db, supplier_item_id, payload, current_user, request=request
    )


@router_inventory.get("/lots", response_model=PaginatedLots)
async def list_lots(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    supplier_item_id: uuid.UUID | None = None,
):
    """Lotes de compra con busqueda por material, proveedor o factura."""
    return await inventory_service.list_lots(
        db,
        limit=limit,
        offset=offset,
        search=search,
        supplier_item_id=supplier_item_id,
    )


@router_inventory.post(
    "/lots", response_model=LotOut, status_code=status.HTTP_201_CREATED
)
async def create_lot(
    payload: LotCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Registra un lote de entrada; el trigger de BD suma el stock."""
    return await inventory_service.create_lot(
        db, payload, current_user, request=request
    )


@router_inventory.get("/dispatch", response_model=PaginatedDispatches)
async def list_dispatches(
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    project_id: uuid.UUID | None = None,
    supplier_item_id: uuid.UUID | None = None,
):
    """Bitacora de despachos; el coordinador solo ve sus proyectos."""
    return await inventory_service.list_dispatches(
        db,
        current_user,
        limit=limit,
        offset=offset,
        search=search,
        project_id=project_id,
        supplier_item_id=supplier_item_id,
    )


@router_inventory.post(
    "/dispatch",
    response_model=DispatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dispatch(
    payload: DispatchCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Solo admin/aprobador: el coordinador solicita el retiro via gastos."""
    return await inventory_service.create_dispatch(
        db, payload, current_user, request=request
    )
