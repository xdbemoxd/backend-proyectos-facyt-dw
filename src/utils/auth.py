import os
import unicodedata
import uuid
import secrets
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from src.config.db import get_db
from src.model.user import UserModel
from src.utils.other import verify_token_acces
import string

# Inicializamos el "guardia de seguridad" de FastAPI
security = HTTPBearer()

ADMIN_ROLE = 'Administrador del Sistema'


def is_admin_role( rol_name: str | None ) -> bool:
    # Acepta "admin_sistema" o nombres como "Administrador del Sistema".
    if not rol_name:
        return False
    r = unicodedata.normalize( "NFD", rol_name )
    r = "".join( c for c in r if unicodedata.category( c ) != "Mn" ).lower().strip()
    return r == ADMIN_ROLE or r.startswith( "admin" )


async def verify_token_access_top( credentials: HTTPAuthorizationCredentials = Depends( security ) ):

    token_limpio = credentials.credentials

    payload = verify_token_acces( token_limpio )

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )

    return payload


async def get_current_user( payload: dict = Depends( verify_token_access_top ), db: AsyncSession = Depends( get_db ) ):

    try:
        user_id = uuid.UUID( str( payload.get( "sub" ) ) )
    except ( ValueError, TypeError ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )

    stmt = select( UserModel ).options( selectinload( UserModel.rol ) ).where( UserModel.id == user_id )

    result = await db.execute( stmt )

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Error"
        )

    return user


async def require_admin( user: UserModel = Depends( get_current_user ) ):

    if not user.rol or not is_admin_role( user.rol.name ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Error"
        )

    return user


async def verify_token_cron( token_cron: str = Header(...) ):

    load_dotenv()
    real_token_cron = os.getenv( "TOKEN_CRON" )

    if not token_cron or not real_token_cron or not secrets.compare_digest( token_cron, real_token_cron ):
        print( "invalid token" )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )

    return True


def generate_random_password(length: int = 30) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%^&*-_" for c in pwd)):
            return pwd
