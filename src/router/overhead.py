"""Endpoints de cierre de periodo y prorrateo de overhead (RF-6, RN-6).

Solo Administrador y Aprobador/Gestor de Inventario acceden a estos endpoints.
El reparto se calcula en el servidor y los periodos cerrados no se modifican.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.overhead import (
    OverheadPeriodCreate,
    OverheadPeriodOut,
    OverheadPreviewOut,
    PaginatedOverheadPeriods,
)
from src.schema.user import CurrentUser
from src.service import overhead as overhead_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE

router_overhead = APIRouter(prefix="/overhead", tags=["overhead"])

_MANAGER_ROLES = (ADMIN_ROLE, APPROVER_ROLE)
_CRITERION = Literal["proporcional", "equitativo"]


@router_overhead.get(
    "/periods", response_model=PaginatedOverheadPeriods
)
async def list_periods(
    _: CurrentUser = Depends(require_roles(*_MANAGER_ROLES)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await overhead_service.list_periods(
        db, limit=limit, offset=offset
    )


@router_overhead.get(
    "/periods/{period_id}", response_model=OverheadPeriodOut
)
async def get_period(
    period_id: uuid.UUID,
    _: CurrentUser = Depends(require_roles(*_MANAGER_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    return await overhead_service.get_period(db, period_id)


@router_overhead.get("/preview", response_model=OverheadPreviewOut)
async def preview_period(
    _: CurrentUser = Depends(require_roles(*_MANAGER_ROLES)),
    db: AsyncSession = Depends(get_db),
    start_date: date = Query(...),
    end_date: date = Query(...),
    total_cost: Decimal = Query(gt=0, le=Decimal("999999999999.99")),
    criterion: _CRITERION = Query(...),
):
    """Vista previa del reparto; no escribe en la base."""
    return await overhead_service.preview_period(
        db,
        start_date=start_date,
        end_date=end_date,
        total_cost=total_cost,
        criterion=criterion,
    )


@router_overhead.post(
    "/periods",
    response_model=OverheadPeriodOut,
    status_code=status.HTTP_201_CREATED,
)
async def close_period(
    payload: OverheadPeriodCreate,
    request: Request,
    current_user: CurrentUser = Depends(
        require_roles(*_MANAGER_ROLES)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Cierra el periodo y guarda el reparto en una sola transaccion."""
    return await overhead_service.close_period(
        db, payload, current_user, request=request
    )
