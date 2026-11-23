import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.config.rate_limit import LOGIN_RATE_LIMIT, limiter
from src.controller.user import verify_auth_google
from src.schema.user import (
    CurrentUser,
    GoogleAuthRequest,
    PaginatedUsers,
    RoleOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from src.service import auth as auth_service
from src.service import user as user_service
from src.utils.auth import get_current_user, require_roles
from src.utils.roles import ADMIN_ROLE

router_users = APIRouter()


@router_users.post("/user/auth/google/login")
@limiter.limit(LOGIN_RATE_LIMIT)
async def login_con_google(
    payload: GoogleAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Las HTTPException (401/403/500) del controller suben tal cual;
    # cualquier otro error lo maneja FastAPI como 500.
    return await verify_auth_google(payload.id_token, db, request)


@router_users.post("/user/auth/logout")
async def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.logout(db, current_user, request)


@router_users.get("/user/me", response_model=UserOut)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
):
    return current_user


@router_users.get("/user/roles", response_model=list[RoleOut])
async def get_roles(
    _: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.list_roles(db)


@router_users.get("/user", response_model=PaginatedUsers)
async def get_users(
    _: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    role_id: uuid.UUID | None = None,
    is_active: bool | None = None,
):
    return await user_service.list_users(
        db,
        limit=limit,
        offset=offset,
        search=search,
        role_id=role_id,
        is_active=is_active,
    )


@router_users.post(
    "/user",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: UserCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.create_user(
        db, payload, current_user, request=request
    )


@router_users.get("/user/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    _: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.get_user(db, user_id)


@router_users.patch("/user/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.update_user(
        db, user_id, payload, current_user, request=request
    )
