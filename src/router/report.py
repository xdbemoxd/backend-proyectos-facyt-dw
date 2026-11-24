"""Endpoints de exportación de reportes en XLSX y PDF (RF-7).

El router solo valida HTTP, roles y filtros; la consulta con alcance por
proyecto, la generación del archivo y la auditoría viven en
``src.service.report``.

Permisos:

- ``/expenses.*`` y ``/equipment.*``: Administrador, Aprobador y Coordinador.
  El coordinador recibe únicamente datos de sus proyectos.
- ``/overhead.*``: solo Administrador y Aprobador; el coordinador recibe 403.
- Sin token: 401.

Los errores inesperados se traducen a un 500 genérico: nunca se devuelve una
traza, SQL ni una ruta interna.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status as http_status,
)
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.report import ReportFilters
from src.schema.user import CurrentUser
from src.service import report as report_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

logger = logging.getLogger(__name__)

router_reports = APIRouter(prefix="/reports", tags=["reports"])

_ANY_ROLE = (ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE)
_MANAGER_ROLES = (ADMIN_ROLE, APPROVER_ROLE)


def get_report_filters(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=50),
) -> ReportFilters:
    """Filtros comunes; se valida el rango antes de llegar al servicio."""
    if (
        from_date is not None
        and to_date is not None
        and from_date > to_date
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La fecha de inicio no puede ser posterior a la de fin",
        )
    return ReportFilters(
        from_date=from_date,
        to_date=to_date,
        project_id=project_id,
        status=status,
    )


def _attachment(rendered: report_service.RenderedReport) -> Response:
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{rendered.filename}"'
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _export(
    *,
    report_type: str,
    report_format: str,
    request: Request,
    current_user: CurrentUser,
    db: AsyncSession,
    filters: ReportFilters,
) -> Response:
    try:
        rendered = await report_service.export_report(
            db,
            report_type,  # type: ignore[arg-type]
            report_format,  # type: ignore[arg-type]
            current_user,
            filters,
            request=request,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - depende de la BD/render
        logger.error(
            "Error generando reporte %s.%s (%s)",
            report_type,
            report_format,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo generar el reporte",
        ) from exc
    return _attachment(rendered)


# ---------------------------------------------------------------------------
# Gastos
# ---------------------------------------------------------------------------


@router_reports.get("/expenses.xlsx", response_class=Response)
async def export_expenses_xlsx(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="expenses",
        report_format="xlsx",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )


@router_reports.get("/expenses.pdf", response_class=Response)
async def export_expenses_pdf(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="expenses",
        report_format="pdf",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )


# ---------------------------------------------------------------------------
# Prorrateo (solo Administrador y Aprobador)
# ---------------------------------------------------------------------------


@router_reports.get("/overhead.xlsx", response_class=Response)
async def export_overhead_xlsx(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGER_ROLES)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="overhead",
        report_format="xlsx",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )


@router_reports.get("/overhead.pdf", response_class=Response)
async def export_overhead_pdf(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGER_ROLES)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="overhead",
        report_format="pdf",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )


# ---------------------------------------------------------------------------
# Equipos
# ---------------------------------------------------------------------------


@router_reports.get("/equipment.xlsx", response_class=Response)
async def export_equipment_xlsx(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="equipment",
        report_format="xlsx",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )


@router_reports.get("/equipment.pdf", response_class=Response)
async def export_equipment_pdf(
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_ANY_ROLE)),
    db: AsyncSession = Depends(get_db),
    filters: ReportFilters = Depends(get_report_filters),
):
    return await _export(
        report_type="equipment",
        report_format="pdf",
        request=request,
        current_user=current_user,
        db=db,
        filters=filters,
    )
