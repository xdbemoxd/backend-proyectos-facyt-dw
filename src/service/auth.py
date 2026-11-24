import logging

from fastapi import HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.model.user import UserModel
from src.schema.user import CurrentUser

logger = logging.getLogger(__name__)


async def logout( db: AsyncSession, current_user: CurrentUser, request: Request | None = None ) -> dict:
    """
    Cierra sesion de verdad: sube token_version y todos los JWT emitidos
    hasta ahora para este usuario dejan de ser validos (en todos sus dispositivos).
    """
    try:
        await db.execute(
            update( UserModel )
            .where( UserModel.id == current_user.id )
            .values( token_version=UserModel.token_version + 1 )
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error( "Error en logout de %s: %s", current_user.id, e )
        raise HTTPException( status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error" )

    ip = request.client.host if request and request.client else None
    logger.info( "Logout de %s (ip=%s)", current_user.id, ip )
    return { "status": "ok" }
