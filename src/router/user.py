import uuid
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.controller.user import (
    create_user,
    get_user,
    list_roles,
    list_users,
    update_user,
    role_label_of,
    send_user_created_emails,
)
from src.schema.user import UserCreateRequest, UserUpdateRequest
from src.config.db import get_db
from src.model.user import UserModel
from src.utils.auth import get_current_user, require_admin
from src.utils.mailer import mail_configured

logger = logging.getLogger(__name__)

# Todo este router exige JWT valido (y usuario activo en la BD).
# La gestion de usuarios y roles ademas exige rol admin_sistema.
router_users = APIRouter(dependencies=[Depends(get_current_user)])


@router_users.get("/roles")
async def get_roles(_admin: UserModel = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_roles(db)


@router_users.get("/users")
async def get_users(_admin: UserModel = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await list_users(db)


@router_users.post("/users", status_code=status.HTTP_200_OK)
async def post_user(
    request: UserCreateRequest,
    background_tasks: BackgroundTasks,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user, rol, admin_emails = await create_user( request, db )

    if mail_configured():
        # Se envía después de responder: invitación al usuario + aviso a los admins.
        background_tasks.add_task(
            send_user_created_emails,
            user.email,
            role_label_of(rol),
            str(admin.email) or "un administrador",
            admin_emails,
        )

    return { "status": "ok" }


@router_users.get("/users/{user_id}")
async def get_user_by_id(
    user_id: uuid.UUID,
    _admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await get_user( user_id, db )


@router_users.patch("/users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    request: UserUpdateRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await update_user( user_id, request, admin, db )

