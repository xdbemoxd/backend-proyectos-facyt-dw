import os
import uuid
import asyncio
import logging
import time
from typing import cast
from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from src.utils.other import generate_jwt_to_frontend
from src.model.user import UserModel, RolModel
from src.schema.user import UserCreateRequest, UserUpdateRequest
from src.utils.mailer import send_email
from src.utils.email_templates import admin_notice_email, invitation_email, role_label

logger = logging.getLogger(__name__)
load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    # Sin CLIENT_ID no se valida el `aud` y se aceptarian tokens de otras apps.
    raise RuntimeError("GOOGLE_CLIENT_ID no esta definido")

# Dominio institucional opcional (ej. "uc.edu.ve"). Si esta vacio, no se filtra.
GOOGLE_ALLOWED_DOMAIN = os.getenv("GOOGLE_ALLOWED_DOMAIN") or None

_google_request = google_requests.Request()


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Error")


async def _get_user_by_email(email: str, db: AsyncSession) -> UserModel | None:
    stmt = (
        select(UserModel)
        .options(joinedload(UserModel.rol))  # evita lazy-load en async
        .where(UserModel.email == email)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def complete_user_profile(user: UserModel, name: str, lastname: str, db: AsyncSession) -> UserModel:
    """Completa nombre/apellido en el primer login.

    No activa la cuenta: la activacion/desactivacion es una decision del
    administrador y no debe revertirse con un login.
    """
    try:
        user.name = name or None # type: ignore
        user.lastname = lastname or None # type: ignore
        await db.commit()
        await db.refresh(user)
        return user
    except Exception as e:
        await db.rollback()
        # Error interno: se registra el traceback solo en logs, nunca en la
        # respuesta. Sin volcar el mensaje del driver al cliente.
        logger.error(
            "Error completando perfil de usuario (%s)",
            type(e).__name__,
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error")


async def verify_auth_google(
    token: str, db: AsyncSession, request: Request | None = None
) -> dict:
    started = time.perf_counter()

    # 1. Verificar firma, exp, iss y aud del id_token (llamada bloqueante -> hilo aparte)
    try:
        idinfo = await asyncio.to_thread(
            id_token.verify_oauth2_token, token, _google_request, GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        # No se registra el token ni el mensaje del verificador.
        logger.warning("id_token invalido (%s)", type(e).__name__)
        raise _unauthorized()

    verified_at = time.perf_counter()

    # 2. Claims obligatorios
    email = idinfo.get("email")
    if not email or not idinfo.get("email_verified"):
        logger.warning("Email ausente o no verificado por Google")
        raise _unauthorized()

    if GOOGLE_ALLOWED_DOMAIN and idinfo.get("hd") != GOOGLE_ALLOWED_DOMAIN:
        logger.warning("Dominio de Google no permitido")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Error")

    # 3. Solo usuarios pre-registrados por un administrador
    user = await _get_user_by_email(email, db)
    db_at = time.perf_counter()
    if user is None:
        logger.info("Login Google con email no registrado")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Error")

    # 4. Una cuenta desactivada no puede entrar, ni siquiera en el primer login.
    if not cast(bool, user.is_active):
        logger.info("Cuenta inactiva: %s", user.id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Error")

    # 5. Primer login: completar perfil con los datos de Google (sin activar).
    if user.name is None or user.lastname is None:
        user = await complete_user_profile(
            user,
            (idinfo.get("given_name") or "").strip(),
            (idinfo.get("family_name") or "").strip(),
            db,
        )

    rol_name = user.rol.name if user.rol else None
    access_token = generate_jwt_to_frontend(
        user.id,
        user.email,
        rol_name,
        int(getattr(user, "token_version", 0) or 0),
    ) # type: ignore

    # Deja el actor autenticado disponible para el middleware de auditoria.
    if request is not None:
        request.state.user_id = str(user.id)
        request.state.user_email = user.email
        request.state.user_role = rol_name

    logger.info(
        "Google login timing: verify=%.2fs db=%.2fs total=%.2fs",
        verified_at - started,
        db_at - verified_at,
        time.perf_counter() - started,
    )

    return {
        "id": str(user.id),
        "email": user.email,
        "fullname": f"{user.name or ''} {user.lastname or ''}".strip(),
        "rol": rol_name,
        "token": access_token,
    }


# ---------------------------------------------------------------------------
# Roles y alta de usuarios
# ---------------------------------------------------------------------------

async def list_roles(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(RolModel).order_by(RolModel.name))
    return [{"id": str(r.id), "name": r.name, "description": r.description} for r in result.scalars()]


async def _active_admin_emails(db: AsyncSession) -> list[str]:
    from src.utils.roles import is_admin_role

    stmt = (
        select(UserModel.email, RolModel.name)
        .join(RolModel, UserModel.id_rol == RolModel.id)
        .where(UserModel.is_active.is_(True))
    )
    result = await db.execute(stmt)
    return [email for email, rol_name in result.all() if email and is_admin_role(rol_name)]


async def create_user(data: UserCreateRequest, db: AsyncSession) -> tuple[UserModel, RolModel, list[str]]:
    """Registra un usuario pendiente de primer ingreso. Devuelve (usuario, rol, correos de admins)."""
    email = str(data.email).strip().lower()

    if GOOGLE_ALLOWED_DOMAIN and not email.endswith(f"@{GOOGLE_ALLOWED_DOMAIN}"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Debe ser un correo @{GOOGLE_ALLOWED_DOMAIN}.")

    rol = await db.get(RolModel, data.id_rol)
    if rol is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rol no válido.")

    exists = await db.execute(select(UserModel.id).where(func.lower(UserModel.email) == email))
    if exists.first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese correo ya está registrado.")

    # Activo desde el alta: "pendiente" = sin nombre hasta su primer login.
    # (Un inactivo ya no puede entrar, ni siquiera la primera vez.)
    user = UserModel(email=email, id_rol=rol.id, is_active=True)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese correo ya está registrado.")
    except Exception as e:
        await db.rollback()
        logger.error(f"Exception creando usuario: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo registrar el usuario.")
    await db.refresh(user)

    admins = await _active_admin_emails(db)
    return user, rol, admins


async def send_user_created_emails(new_email: str, role: str, created_by: str, admin_emails: list[str]) -> None:
    """Tarea en segundo plano: invitación al nuevo usuario + aviso a cada administrador activo."""
    subject, text, html = invitation_email(new_email, role)
    await send_email(new_email, subject, text, html)

    subject, text, html = admin_notice_email(new_email, role, created_by)
    for admin in admin_emails:
        if admin.lower() != new_email:
            # Uno por uno para no exponer los correos de los demás administradores.
            await send_email(admin, subject, text, html)


def role_label_of(rol: RolModel) -> str:
    return role_label(rol.name, rol.description)  # type: ignore[arg-type]


def _user_dict(u: UserModel, rol: RolModel | None = None) -> dict:
    rol = rol or u.rol
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "lastname": u.lastname,
        "is_active": bool(u.is_active),
        "create_at": u.create_at.isoformat() if u.create_at else None,
        "id_rol": str(u.id_rol),
        "rol": (
            {"id": str(rol.id), "name": rol.name, "description": rol.description}
            if rol else None
        ),
    }


async def list_users(db: AsyncSession) -> list[dict]:
    """Todos los usuarios con su rol (UserModel + RolModel)."""
    stmt = (
        select(UserModel)
        .options(selectinload(UserModel.rol))  # evita lazy-load en async
        .order_by(UserModel.create_at.desc())
    )
    result = await db.execute(stmt)
    return [_user_dict(u) for u in result.scalars()]


async def _get_user_with_rol(user_id: uuid.UUID, db: AsyncSession) -> UserModel:
    result = await db.execute(
        select(UserModel).options(selectinload(UserModel.rol)).where(UserModel.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
    return user


async def get_user(user_id: uuid.UUID, db: AsyncSession) -> dict:
    return _user_dict(await _get_user_with_rol(user_id, db))


async def update_user(user_id: uuid.UUID, data: UserUpdateRequest, current: UserModel, db: AsyncSession) -> dict:
    """Cambia rol y/o estado. Reglas:
    - Nadie puede cambiar su propio rol ni desactivarse.
    - Debe quedar al menos un administrador activo.
    - Un usuario pendiente de primer ingreso se activa solo al entrar con Google.
    """
    from src.utils.roles import is_admin_role

    user = await _get_user_with_rol(user_id, db)

    rol = await db.get(RolModel, data.id_rol)
    if rol is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rol no válido.")

    is_self = user.id == current.id
    if is_self and (rol.id != user.id_rol or not data.is_active):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No puedes cambiar tu propio rol ni desactivar tu cuenta.")

    # El estado lo decide el admin (tambien para usuarios pendientes).
    new_active = data.is_active

    was_active_admin = bool(user.is_active) and is_admin_role(user.rol.name if user.rol else None)
    stays_active_admin = new_active and is_admin_role(rol.name)
    if was_active_admin and not stays_active_admin:
        result = await db.execute(
            select(UserModel.id, RolModel.name)
            .join(RolModel, UserModel.id_rol == RolModel.id)
            .where(UserModel.is_active.is_(True), UserModel.id != user.id)
        )
        if not any(is_admin_role(name) for _id, name in result.all()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Debe quedar al menos un administrador activo.")

    changed = rol.id != user.id_rol or new_active != bool(user.is_active)

    try:
        user.rol = rol
        user.is_active = new_active  # type: ignore
        if changed:
            # Sus JWT dejan de valer: debe volver a iniciar sesion con el rol/estado nuevo.
            user.token_version = (user.token_version or 0) + 1  # type: ignore
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Exception actualizando usuario: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo guardar el usuario.")

    await db.refresh(user)
    return _user_dict(user, rol)
