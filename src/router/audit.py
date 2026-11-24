import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.config.rate_limit import PAGEVIEW_RATE_LIMIT, UI_EVENT_RATE_LIMIT, limiter
from src.schema.audit import PageViewIn, PaginatedAudit, UiEventIn
from src.schema.user import CurrentUser
from src.service.audit import list_audit
from src.utils.audit import record_audit
from src.utils.auth import get_current_user, require_roles
from src.utils.roles import ADMIN_ROLE

router_audit = APIRouter()


@router_audit.post("/audit/pageview", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(PAGEVIEW_RATE_LIMIT)
async def register_page_view(
    request: Request,
    payload: PageViewIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await record_audit(
        db,
        entity="Navigation",
        action="page_view",
        user_id=current_user.id,
        request=request,
        http_method="GET",
        http_path=payload.path,
        status_code=status.HTTP_200_OK,
        after={"path": payload.path},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_audit.post("/audit/ui-event", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(UI_EVENT_RATE_LIMIT)
async def register_ui_event(
    request: Request,
    payload: UiEventIn,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await record_audit(
        db,
        entity=payload.entity,
        action=payload.action,
        user_id=current_user.id,
        request=request,
        http_method="UI",
        http_path=payload.path,
        status_code=status.HTTP_200_OK,
        after={
            "summary": payload.summary,
            "source": "ui_mock",
            "role": current_user.rol,
        },
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_audit.get("/audit", response_model=PaginatedAudit)
async def get_audit_log(
    _: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    entity: str | None = Query(default=None, max_length=255),
    action: str | None = Query(default=None, max_length=255),
    outcome: str | None = Query(default=None, max_length=20),
    status_code: int | None = Query(default=None, ge=100, le=599),
    user_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    path: str | None = Query(default=None, max_length=500),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
):
    return await list_audit(
        db,
        limit=limit,
        offset=offset,
        entity=entity,
        action=action,
        outcome=outcome,
        status_code=status_code,
        user_id=user_id,
        request_id=request_id,
        path=path,
        from_date=from_date,
        to_date=to_date,
    )
