from fastapi import Depends, HTTPException, APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


from src.config.db import get_db
router_health = APIRouter();

@router_health.get("/health")
async def health_check():
    return {"status": "ok", "message": "API running"}


@router_health.get("/health/2")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        # Execute a simple query to verify the connection using the session
        await db.execute(text("SELECT 1"))
        
        return {
            "status": "ok", 
            "message": "Database connection successful 🚀"
        }
    except Exception as e:
        # Return 500 if the connection fails
        raise HTTPException(
            status_code=500, 
            detail=f"Database connection failed: {str(e)}"
        )