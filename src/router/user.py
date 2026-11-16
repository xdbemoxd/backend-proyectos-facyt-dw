import logging
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.controller.user import verify_auth_google
from src.schema.user import GoogleAuthRequest
from src.config.db import get_db

logger = logging.getLogger(__name__)
router_users = APIRouter()

@router_users.post("/user/auth/google/login")
async def login_con_google(request: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    # Las HTTPException (401/403/500) del controller suben tal cual;
    # cualquier otro error lo maneja FastAPI como 500.
    return await verify_auth_google(request.id_token, db)
