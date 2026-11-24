"""Endpoints de proyectos y priorizacion.

El router solo valida HTTP (path/query), aplica dependencias de rol y delega
todas las reglas de negocio y el alcance por proyecto al servicio.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.project import (
    PaginatedPriorityLog,
    PaginatedProjects,
    PriorityChangeIn,
    ProjectCatalogs,
    ProjectOut,
    ProjectUpdate,
)
from src.schema.user import CurrentUser
from src.service import project as project_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

router_projects = APIRouter(prefix="/projects", tags=["projects"])

_ANY_ROLE = (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE)


@router_projects.get("/catalogs", response_model=ProjectCatalogs)
async def get_project_catalogs(
    _: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    """Estados y prioridades reales para editar/priorizar sin IDs fijos."""
    return await project_service.list_catalogs(db)


@router_projects.get("/priority-log", response_model=PaginatedPriorityLog)
async def get_priority_log(
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    project_id: uuid.UUID | None = None,
):
    """Historial de prioridad; el coordinador solo ve sus proyectos."""
    return await project_service.list_priority_log(
        db,
        current_user,
        limit=limit,
        offset=offset,
        project_id=project_id,
    )


@router_projects.get("", response_model=PaginatedProjects)
async def list_projects(
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    id_status: int | None = Query(default=None, gt=0),
    id_priority: int | None = Query(default=None, gt=0),
    user_id: uuid.UUID | None = None,
):
    return await project_service.list_projects(
        db,
        current_user,
        limit=limit,
        offset=offset,
        search=search,
        id_status=id_status,
        id_priority=id_priority,
        user_id=user_id,
    )


@router_projects.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.get_project(db, project_id, current_user)


@router_projects.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    current_user: CurrentUser = Depends(
        require_roles(ADMIN_ROLE, COORDINATOR_ROLE)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Administrador edita cualquiera; coordinador solo los suyos."""
    return await project_service.update_project(
        db, project_id, payload, current_user, request=request
    )


@router_projects.patch("/{project_id}/priority", response_model=ProjectOut)
async def change_project_priority(
    project_id: uuid.UUID,
    payload: PriorityChangeIn,
    request: Request,
    current_user: CurrentUser = Depends(
        require_roles(ADMIN_ROLE, APPROVER_ROLE)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Administrador o aprobador; justificacion obligatoria (RN-3)."""
    return await project_service.change_priority(
        db, project_id, payload, current_user, request=request
    )
