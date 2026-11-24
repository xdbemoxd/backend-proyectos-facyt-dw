"""Endpoints de solicitudes de gasto (RF-5, RN-7).

El router valida HTTP y roles; la maquina de estados, el alcance por proyecto y
la autoaprobacion se resuelven en el servicio.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.expense import (
    ExpenseCreate,
    ExpenseOut,
    ExpenseStatusUpdate,
    PaginatedExpenses,
)
from src.schema.user import CurrentUser
from src.service import expense as expense_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

router_expenses = APIRouter(prefix="/expenses", tags=["expenses"])

_ANY_ROLE = (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE)


@router_expenses.get("", response_model=PaginatedExpenses)
async def list_expenses(
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=30),
    project_id: uuid.UUID | None = None,
):
    """Admin/aprobador ven todo; coordinador solo las de sus proyectos."""
    return await expense_service.list_expenses(
        db,
        current_user,
        limit=limit,
        offset=offset,
        search=search,
        status_code=status,
        project_id=project_id,
    )


@router_expenses.get("/{expense_id}", response_model=ExpenseOut)
async def get_expense(
    expense_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await expense_service.get_expense(
        db, expense_id, current_user
    )


@router_expenses.post(
    "",
    response_model=ExpenseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_expense(
    payload: ExpenseCreate,
    request: Request,
    current_user: CurrentUser = Depends(
        require_roles(ADMIN_ROLE, COORDINATOR_ROLE)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Administrador o coordinador; nace en Borrador y se audita."""
    return await expense_service.create_expense(
        db, payload, current_user, request=request
    )


@router_expenses.patch("/{expense_id}/status", response_model=ExpenseOut)
async def update_expense_status(
    expense_id: uuid.UUID,
    payload: ExpenseStatusUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Transiciones RN-7; el servicio valida rol y autoaprobacion."""
    return await expense_service.update_expense_status(
        db, expense_id, payload, current_user, request=request
    )
