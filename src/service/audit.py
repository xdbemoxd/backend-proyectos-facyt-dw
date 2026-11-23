"""Consultas de solo lectura sobre `audit_log`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.model.audit import AuditLogModel
from src.schema.audit import AuditLogOut, PaginatedAudit


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
    conditions = []
    if entity:
        conditions.append(AuditLogModel.entity == entity)
    if action:
        conditions.append(AuditLogModel.action == action)
    if outcome:
        conditions.append(AuditLogModel.outcome == outcome)
    if status_code is not None:
        conditions.append(AuditLogModel.status_code == status_code)
    if user_id is not None:
        conditions.append(AuditLogModel.user_id == user_id)
    if request_id is not None:
        conditions.append(AuditLogModel.request_id == request_id)
    if path:
        conditions.append(AuditLogModel.path == path)
    if from_date is not None:
        conditions.append(AuditLogModel.timestamp >= from_date)
    if to_date is not None:
        conditions.append(AuditLogModel.timestamp <= to_date)

    # Las funciones de ventana se evaluan antes de LIMIT/OFFSET: una sola
    # consulta devuelve la pagina, el total y los contadores por resultado.
    summary_columns = (
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
    )

    # joinedload resuelve el actor en la misma consulta.
    base = (
        select(AuditLogModel, *summary_columns)
        .options(joinedload(AuditLogModel.user))
    )
    if conditions:
        base = base.where(*conditions)

    result = await db.execute(
        base.order_by(AuditLogModel.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.unique().all()

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
        # Pagina fuera de rango: solo se necesita el resumen.
        summary_stmt = select(
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
            summary_stmt = summary_stmt.where(*conditions)

        summary = (await db.execute(summary_stmt)).one()
        total = int(summary.total or 0)
        counts = {
            "success": int(summary.success or 0),
            "denied": int(summary.denied or 0),
            "failed": int(summary.failed or 0),
        }
        items = []

    return PaginatedAudit(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        counts=counts,
    )
