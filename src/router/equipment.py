"""Endpoints de equipos, unidades fisicas y reservas (RF-5, RN-5).

El router valida HTTP, aplica las dependencias de rol y delega al servicio.
Las rutas estaticas (``/catalogs`` y ``/reservations``) se declaran antes de
``/{equipment_id}`` para que no se interpreten como un UUID.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.equipment import (
    EquipmentCatalogs,
    EquipmentCreate,
    EquipmentUnitOut,
    EquipmentUpdate,
    PaginatedEquipmentUnits,
    PaginatedReservations,
    ReservationCreate,
    ReservationOut,
)
from src.schema.user import CurrentUser
from src.service import equipment as equipment_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

router_equipment = APIRouter(prefix="/equipment", tags=["equipment"])

_ANY_ROLE = (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE)
_MANAGERS = (ADMIN_ROLE, APPROVER_ROLE)


@router_equipment.get("/catalogs", response_model=EquipmentCatalogs)
async def get_equipment_catalogs(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Estados de equipo y proveedores reales para los formularios."""
    return await equipment_service.list_catalogs(db)


@router_equipment.get(
    "/reservations", response_model=PaginatedReservations
)
async def list_reservations(
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    project_id: uuid.UUID | None = None,
    equipment_supplier_id: uuid.UUID | None = None,
):
    """Reservas; el coordinador solo ve las de sus proyectos."""
    return await equipment_service.list_reservations(
        db,
        current_user,
        limit=limit,
        offset=offset,
        search=search,
        project_id=project_id,
        equipment_supplier_id=equipment_supplier_id,
    )


@router_equipment.post(
    "/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_reservation(
    payload: ReservationCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Reserva una unidad; el trigger de BD rechaza el solape (409)."""
    return await equipment_service.create_reservation(
        db, payload, current_user, request=request
    )


@router_equipment.get("", response_model=PaginatedEquipmentUnits)
async def list_equipment(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    id_status: int | None = Query(default=None, gt=0),
    equipment_id: uuid.UUID | None = None,
):
    """Unidades fisicas con equipo, proveedor, estado y serial."""
    return await equipment_service.list_equipment_units(
        db,
        limit=limit,
        offset=offset,
        search=search,
        id_status=id_status,
        equipment_id=equipment_id,
    )


@router_equipment.post(
    "",
    response_model=EquipmentUnitOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_equipment(
    payload: EquipmentCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Administrador o aprobador registran una unidad (crea el equipo si falta)."""
    return await equipment_service.create_equipment_unit(
        db, payload, current_user, request=request
    )


@router_equipment.get(
    "/{equipment_id}", response_model=EquipmentUnitOut
)
async def get_equipment(
    equipment_id: uuid.UUID,
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Detalle de una unidad fisica; 404 si no existe."""
    return await equipment_service.get_equipment_unit(db, equipment_id)


@router_equipment.patch(
    "/{equipment_id}", response_model=EquipmentUnitOut
)
async def update_equipment(
    equipment_id: uuid.UUID,
    payload: EquipmentUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    """Edita la unidad con bloqueo optimista y auditoria."""
    return await equipment_service.update_equipment_unit(
        db, equipment_id, payload, current_user, request=request
    )
