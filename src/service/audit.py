"""Consultas de solo lectura sobre `audit_log`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.model.audit import AuditLogModel
from src.schema.audit import AuditLogOut, PaginatedAudit


async def _outcome_counts(
    db: AsyncSession, conditions: list
) -> tuple[int, dict[str, int]]:
    """Total y distribucion por resultado, sin aplicar el filtro `outcome`."""
    statement = select(
        func.count().label("total"),
        func.count(case((AuditLogModel.outcome == "success", 1))).label(
            "success"
        ),
        func.count(case((AuditLogModel.outcome == "denied", 1))).label(
            "denied"
        ),
        func.count(case((AuditLogModel.outcome == "failed", 1))).label(
            "failed"
        ),
    ).select_from(AuditLogModel)
    if conditions:
        statement = statement.where(*conditions)

    row = (await db.execute(statement)).one()
    return int(row.total or 0), {
        "success": int(row.success or 0),
        "denied": int(row.denied or 0),
        "failed": int(row.failed or 0),
    }


async def list_audit(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    entity: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    status_code: int | None = None,
    user_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    path: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> PaginatedAudit:
    # Filtros que definen el contexto de los contadores.
    base_conditions = []
    if entity:
        base_conditions.append(AuditLogModel.entity == entity)
    if action:
        base_conditions.append(AuditLogModel.action == action)
    if status_code is not None:
        base_conditions.append(AuditLogModel.status_code == status_code)
    if user_id is not None:
        base_conditions.append(AuditLogModel.user_id == user_id)
    if request_id is not None:
        base_conditions.append(AuditLogModel.request_id == request_id)
    if path:
        base_conditions.append(AuditLogModel.path == path)
    if from_date is not None:
        base_conditions.append(AuditLogModel.timestamp >= from_date)
    if to_date is not None:
        base_conditions.append(AuditLogModel.timestamp <= to_date)

    # La tabla sí respeta el filtro de resultado; los contadores no.
    page_conditions = list(base_conditions)
    if outcome:
        page_conditions.append(AuditLogModel.outcome == outcome)

    if outcome is None:
        # Camino rapido: una sola consulta para pagina, total y contadores.
        page_stmt = select(
            AuditLogModel,
            func.count().over().label("total_count"),
            func.count(case((AuditLogModel.outcome == "success", 1)))
            .over()
            .label("success_count"),
            func.count(case((AuditLogModel.outcome == "denied", 1)))
            .over()
            .label("denied_count"),
            func.count(case((AuditLogModel.outcome == "failed", 1)))
            .over()
            .label("failed_count"),
        ).options(joinedload(AuditLogModel.user))
        if base_conditions:
            page_stmt = page_stmt.where(*base_conditions)

        rows = (
            await db.execute(
                page_stmt.order_by(AuditLogModel.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
        ).unique().all()

        if rows:
            first = rows[0]
            total = int(first.total_count or 0)
            counts = {
                "success": int(first.success_count or 0),
                "denied": int(first.denied_count or 0),
                "failed": int(first.failed_count or 0),
            }
            items = [AuditLogOut.model_validate(row[0]) for row in rows]
        else:
            total, counts = await _outcome_counts(db, base_conditions)
            items = []
    else:
        # Con filtro de resultado, los contadores se calculan sin ese filtro
        # para que al seleccionar "Denegado" no se pongan en cero los demás.
        _, counts = await _outcome_counts(db, base_conditions)

        page_stmt = select(
            AuditLogModel,
            func.count().over().label("total_count"),
        ).options(joinedload(AuditLogModel.user))
        if page_conditions:
            page_stmt = page_stmt.where(*page_conditions)

        rows = (
            await db.execute(
                page_stmt.order_by(AuditLogModel.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
        ).unique().all()

        if rows:
            total = int(rows[0].total_count or 0)
            items = [AuditLogOut.model_validate(row[0]) for row in rows]
        else:
            count_stmt = select(func.count()).select_from(AuditLogModel)
            if page_conditions:
                count_stmt = count_stmt.where(*page_conditions)
            total = int(await db.scalar(count_stmt) or 0)
            items = []

    return PaginatedAudit(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        counts=counts,
    )
