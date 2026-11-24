import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.model.audit import AuditLogModel
from src.schema.user import CurrentUser
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE

router_audit = APIRouter()

PAGE_SIZE = 20
Outcome = Literal[ "success", "denied", "failed" ]


def _as_text( value ) -> str | None:
    if value is None:
        return None
    return value if isinstance( value, str ) else json.dumps( value, ensure_ascii=False )


def _entry( row: AuditLogModel ) -> dict:
    u = row.user
    return {
        "id": str( row.id ),
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "user": (
            {
                "id": str( u.id ),
                "full_name": f"{u.name or ''} {u.lastname or ''}".strip() or u.email,
                "email": u.email,
            }
            if u else None
        ),
        "ip_address": row.ip_address,
        "entity": row.entity,
        "action": row.action,
        "entity_id": row.entity_id,
        "entity_label": None,
        "outcome": row.outcome,
        # El frontend espera JSON en texto (diffAudit).
        "before_str": _as_text( row.before_data ),
        "after_str": _as_text( row.after_data ),
        "request_id": str( row.request_id ) if row.request_id else None,
        "method": row.method,
        "path": row.path,
        "status_code": row.status_code,
        "duration_ms": row.duration_ms,
        "error_code": row.error_code,
        "error_detail": row.error_detail,
    }


@router_audit.get( "/audit" )
async def get_audit(
    _: CurrentUser = Depends( require_roles( ADMIN_ROLE ) ),
    db: AsyncSession = Depends( get_db ),
    from_: date = Query( alias="from" ),
    to: date = Query(),
    user_id: uuid.UUID | None = None,
    entity: str | None = Query( default=None, max_length=255 ),
    outcome: Outcome | None = None,
    page: int = Query( default=1, ge=1 ),
):
    if to < from_:
        raise HTTPException( status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="El rango de fechas no es válido." )

    start = datetime.combine( from_, time.min, tzinfo=timezone.utc )
    end = datetime.combine( to + timedelta( days=1 ), time.min, tzinfo=timezone.utc )

    base = [ AuditLogModel.timestamp >= start, AuditLogModel.timestamp < end ]
    if user_id:
        base.append( AuditLogModel.user_id == user_id )
    if entity:
        base.append( AuditLogModel.entity == entity )

    # Totales por resultado en el rango (sin el filtro de outcome ni paginar).
    counts_rows = await db.execute(
        select( AuditLogModel.outcome, func.count() ).where( *base ).group_by( AuditLogModel.outcome )
    )
    counts = { "success": 0, "denied": 0, "failed": 0 }
    for name, n in counts_rows.all():
        counts[ name ] = n

    filters = base + ( [ AuditLogModel.outcome == outcome ] if outcome else [] )
    total = await db.scalar( select( func.count() ).select_from( AuditLogModel ).where( *filters ) ) or 0

    result = await db.execute(
        select( AuditLogModel )
        .options( selectinload( AuditLogModel.user ) )
        .where( *filters )
        .order_by( AuditLogModel.timestamp.desc() )
        .limit( PAGE_SIZE )
        .offset( ( page - 1 ) * PAGE_SIZE )
    )

    return {
        "items": [ _entry( r ) for r in result.scalars() ],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "counts": counts,
    }
