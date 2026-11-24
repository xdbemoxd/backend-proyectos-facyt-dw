import uuid
import asyncio
import logging

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.controller import user as user_controller
from src.model.user import RolModel, UserModel
from src.schema.user import CurrentUser, PaginatedUsers, RoleOut, UserCreate, UserOut, UserUpdate
from src.utils.mailer import mail_configured

logger = logging.getLogger(__name__)

# Referencias a las tareas de correo para que no las recoja el GC antes de terminar.
_email_tasks: set[asyncio.Task] = set()


async def list_roles( db: AsyncSession ) -> list[RoleOut]:
    result = await db.execute( select( RolModel ).order_by( RolModel.name ) )
    return [ RoleOut.model_validate( r ) for r in result.scalars() ]


async def list_users(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    search: str | None = None,
    role_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> PaginatedUsers:

    filters = []
    if search and search.strip():
        like = f"%{search.strip()}%"
        filters.append( or_(
            UserModel.email.ilike( like ),
            UserModel.name.ilike( like ),
            UserModel.lastname.ilike( like ),
        ) )
    if role_id is not None:
        filters.append( UserModel.id_rol == role_id )
    if is_active is not None:
        filters.append( UserModel.is_active.is_( is_active ) )

    total = await db.scalar( select( func.count() ).select_from( UserModel ).where( *filters ) ) or 0

    stmt = (
        select( UserModel )
        .options( selectinload( UserModel.rol ) )
        .where( *filters )
        .order_by( UserModel.create_at.desc(), UserModel.id )
        .limit( limit )
        .offset( offset )
    )
    result = await db.execute( stmt )

    return PaginatedUsers(
        items=[ UserOut.model_validate( u ) for u in result.scalars() ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_user( db: AsyncSession, user_id: uuid.UUID ) -> UserOut:
    return UserOut.model_validate( await user_controller.get_user( user_id, db ) )


async def create_user(
    db: AsyncSession,
    payload: UserCreate,
    current_user: CurrentUser,
    request: Request | None = None,
) -> UserOut:

    user, rol, admin_emails = await user_controller.create_user( payload, db )

    if mail_configured():
        # Se envia en segundo plano: la respuesta no espera a Gmail.
        task = asyncio.create_task( user_controller.send_user_created_emails(
            user.email,
            user_controller.role_label_of( rol ),
            current_user.email or "un administrador",
            admin_emails,
        ) )
        _email_tasks.add( task )
        task.add_done_callback( _email_tasks.discard )

    return UserOut.model_validate( user_controller._user_dict( user, rol ) )


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: CurrentUser,
    request: Request | None = None,
) -> UserOut:
    return UserOut.model_validate(
        await user_controller.update_user( user_id, payload, current_user, db )
    )
