from fastapi import Depends, HTTPException, APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

import os
import asyncio
from src.config.db import get_db
from src.utils.mailer import _send_sync, mail_configured
from src.utils.auth import get_current_user

router_health = APIRouter()


@router_health.get("/health")
async def health_check():
    """Publico a proposito: lo usan Docker / el balanceador y no expone datos."""
    return {"status": "ok", "message": "API running"}


@router_health.get("/health/2", dependencies=[Depends(get_current_user)])
async def health_check_db(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "message": "Database connection successful"}
    except Exception:
        # No devolvemos el error real: puede incluir la URL de la BD.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection failed",
        )


# ---------------------------------------------------------------------------
# PRUEBA DE CORREO (solo desarrollo). Abre en el navegador:
#   http://localhost:8000/health/email
# ---------------------------------------------------------------------------

# Pon aqui tu correo personal: