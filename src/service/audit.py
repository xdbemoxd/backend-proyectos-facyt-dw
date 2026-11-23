"""Consultas de solo lectura sobre `audit_log`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

    base = select(AuditLogModel).options(selectinload(AuditLogModel.user))
    count_stmt = select(func.count()).select_from(AuditLogModel)
    counts_stmt = select(
        AuditLogModel.outcome, func.count()
    ).select_from(AuditLogModel)
    if conditions:
        base = base.where(*conditions)
        count_stmt = count_stmt.where(*conditions)
        counts_stmt = counts_stmt.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    counts_result = await db.execute(
        counts_stmt.group_by(AuditLogModel.outcome)
    )
    counts = {"success": 0, "denied": 0, "failed": 0}
    for outcome, count in counts_result.all():
        counts[outcome] = int(count)

    result = await db.execute(
        base.order_by(AuditLogModel.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )

    return PaginatedAudit(
        items=[
            AuditLogOut.model_validate(row)
            for row in result.scalars().all()
        ],
        total=total,
        limit=limit,
        offset=offset,
        counts=counts,
    )
