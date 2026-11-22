import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db

logger = logging.getLogger(__name__)
router_health = APIRouter()


@router_health.get("/health")
async def health_check():
    return {"status": "ok", "message": "API running"}


@router_health.get("/health/2")
async def health_check_db(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "message": "Database connection successful",
        }
    except Exception:
        # El detalle queda en logs internos; nunca se devuelve al cliente.
        logger.exception("Database health check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection failed",
        )
