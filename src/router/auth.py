import logging
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.controller.user import verify_auth_google
from src.schema.user import GoogleAuthRequest
from src.config.db import get_db

logger = logging.getLogger(__name__)

# Router PUBLICO: lo unico que se puede llamar sin JWT.
router_auth = APIRouter()


@router_auth.post("/user/auth/google/login")
async def login_con_google(request: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    # Las HTTPException (401/403/500) del controller suben tal cual.
    return await verify_auth_google(request.id_token, db)
