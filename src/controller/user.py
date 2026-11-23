import os
import asyncio
import logging
import time
from typing import cast
from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from src.utils.other import generate_jwt_to_frontend
from src.model.user import UserModel

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
