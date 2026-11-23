import os
import secrets
import string
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.config.db import get_db
from src.model.user import UserModel
from src.schema.user import CurrentUser
from src.utils.audit import record_audit_standalone
from src.utils.other import verify_token_acces
from src.utils.roles import ALLOWED_ROLES

# auto_error=False para devolver 401 (y no 403) cuando falta el token.
security = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No autorizado",
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Valida el JWT y devuelve el usuario actual desde la base de datos.

    El rol se toma de la BD, no del token: si a un usuario le cambian el rol o
    lo desactivan, el cambio aplica de inmediato.
    """
    if credentials is None:
        raise _unauthorized()

    try:
        payload = verify_token_acces(credentials.credentials)
    except HTTPException:
        raise _unauthorized()
    except Exception:
        raise _unauthorized()

    try:
        user_id = UUID(str(payload.get("sub")))
    except (ValueError, TypeError, AttributeError):
        raise _unauthorized()

    statement = (
        select(UserModel)
        .options(joinedload(UserModel.rol))
        .where(UserModel.id == user_id)
    )
    result = await db.execute(statement)
    user = result.scalar_one_or_none()

    if user is None:
        raise _unauthorized()
    try:
        token_version = int(payload.get("ver", -1))
    except (TypeError, ValueError):
        raise _unauthorized()
    if token_version != int(getattr(user, "token_version", 0) or 0):
        raise _unauthorized()
    if not bool(user.is_active):
        # No revelar si el token corresponde a una cuenta desactivada.
        raise _unauthorized()
    if user.rol is None or user.rol.name not in ALLOWED_ROLES:
        raise _forbidden()

    current_user = CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        lastname=user.lastname,
        is_active=bool(user.is_active),
        id_rol=user.id_rol,
        rol=user.rol.name,
        create_at=getattr(user, "create_at", None),
    )

    # Disponible para el middleware de auditoria.
    request.state.user_id = str(current_user.id)
    request.state.user_email = current_user.email
    request.state.user_role = current_user.rol

    return current_user


def require_roles(*roles: str):
    """Dependencia que exige uno de los roles indicados."""

    async def checker(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.rol not in roles:
            # SEC-205: deja un evento de negocio, no solo el HTTP del
            # middleware. Sin PII; el actor se resuelve por user_id.
            await record_audit_standalone(
                entity="User",
                action="rbac_denied",
                entity_id=current_user.id,
                user_id=current_user.id,
                request=request,
                outcome="denied",
                after={
                    "reason": "role_not_allowed",
                    "role": current_user.rol,
                    "required_roles": list(roles),
                },
            )
            raise _forbidden()
        return current_user

    return checker


async def verify_token_cron(token_cron: str | None = Header(default=None)):
    load_dotenv()
    real_token_cron = os.getenv("TOKEN_CRON")

    if (
        not token_cron
        or not real_token_cron
        or not secrets.compare_digest(token_cron, real_token_cron)
    ):
        raise _unauthorized()

    return True


def generate_random_password(length: int = 30) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*-_" for c in pwd)
        ):
            return pwd
