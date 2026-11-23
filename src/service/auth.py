"""Cierre de sesion y revocacion de tokens."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.model.user import UserModel
from src.schema.user import CurrentUser
from src.utils.audit import record_audit


async def logout(
    db: AsyncSession,
    current_user: CurrentUser,
    request: Request | None = None,
) -> dict:
    user = await db.get(UserModel, current_user.id)
    if user is not None:
        user.token_version = int(getattr(user, "token_version", 0) or 0) + 1
        await record_audit(
            db,
            entity="User",
            action="logout",
            entity_id=user.id,
            user_id=user.id,
            request=request,
            outcome="success",
        )
        await db.commit()

    return {"status": "ok"}
