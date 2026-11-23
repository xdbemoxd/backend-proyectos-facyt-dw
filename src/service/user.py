"""Servicio de usuarios y roles (RF-1.1)."""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.model.user import RolModel, UserModel
from src.schema.user import (
    CurrentUser,
    PaginatedUsers,
    RoleOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from src.utils.audit import record_audit, record_audit_standalone
from src.utils.permissions import would_remove_last_admin
from src.utils.roles import ADMIN_ROLE, ALLOWED_ROLES

logger = logging.getLogger(__name__)


def _user_out(user: UserModel, role_name: str | None) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        lastname=user.lastname,
        is_active=bool(user.is_active),
        id_rol=user.id_rol,
        rol=role_name,
        create_at=getattr(user, "create_at", None),
    )


def _snapshot(user: UserModel, role_name: str | None) -> dict:
    # Sin PII: el email se resuelve desde private."user" usando id/entity_id.
    return {
        "id": str(user.id),
        "is_active": bool(user.is_active),
        "id_rol": str(user.id_rol),
        "rol": role_name,
    }


def _like_pattern(term: str) -> str:
    """Escapa comodines de LIKE para que la busqueda sea literal.

    No es una defensa contra inyeccion SQL (la consulta esta parametrizada),
    sino para que `%` o `_` enviados por el cliente no actuen como comodines.
    """
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


async def _audit_denial(
    *,
    action: str,
    actor: CurrentUser,
    request: Request | None,
    reason: str,
    entity_id: str | uuid.UUID | None = None,
    extra: dict | None = None,
) -> None:
    """Registra un intento denegado como evento de negocio (SEC-205).

    Usa una transaccion independiente para que sobreviva al rollback de la
    request. No incluye PII: el actor se resuelve por ``user_id`` y el motivo
    es un codigo acotado. El evento HTTP del middleware se conserva; este es
    adicional y comparte ``request_id``.
    """
    after = {"reason": reason}
    if extra:
        after.update(extra)

    await record_audit_standalone(
        entity="User",
        action=action,
        entity_id=entity_id,
        user_id=actor.id,
        request=request,
        outcome="denied",
        after=after,
    )


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> UserModel:
    statement = (
        select(UserModel)
        .options(selectinload(UserModel.rol))
        .where(UserModel.id == user_id)
    )
    result = await db.execute(statement)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado",
        )
    return user


async def _get_role_or_422(
    db: AsyncSession,
    role_id: uuid.UUID,
    *,
    actor: CurrentUser | None = None,
    request: Request | None = None,
    entity_id: str | uuid.UUID | None = None,
    action: str = "update_user_denied",
) -> RolModel:
    role = await db.get(RolModel, role_id)
    if role is None or role.name not in ALLOWED_ROLES:
        if actor is not None:
            await _audit_denial(
                action=action,
                actor=actor,
                request=request,
                entity_id=entity_id,
                reason="invalid_role",
                extra={"id_rol": str(role_id)},
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Rol inválido",
        )
    return role


async def _count_active_admins(
    db: AsyncSession, *, lock: bool = False
) -> int:
    """Cuenta administradores activos.

    Con ``lock=True`` toma un lock de fila (``SELECT ... FOR UPDATE``) sobre
    los administradores activos. Asi dos cambios concurrentes que intenten
    degradar/desactivar administradores se serializan y no pueden dejar al
    sistema sin ninguno.
    """
    role_id = await db.scalar(
        select(RolModel.id).where(RolModel.name == ADMIN_ROLE)
    )
    if role_id is None:
        return 0

    statement = select(UserModel.id).where(
        UserModel.is_active.is_(True),
        UserModel.id_rol == role_id,
    )
    if lock:
        statement = statement.with_for_update()

    result = await db.execute(statement)
    return len(result.scalars().all())


async def list_users(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    role_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> PaginatedUsers:
    conditions = []
    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                UserModel.email.ilike(pattern, escape="\\"),
                UserModel.name.ilike(pattern, escape="\\"),
                UserModel.lastname.ilike(pattern, escape="\\"),
            )
        )
    if role_id is not None:
        conditions.append(UserModel.id_rol == role_id)
    if is_active is not None:
        conditions.append(UserModel.is_active.is_(is_active))

    base = select(UserModel).options(selectinload(UserModel.rol))
    count_stmt = select(func.count()).select_from(UserModel)
    if conditions:
        base = base.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    result = await db.execute(
        base.order_by(UserModel.create_at.desc()).limit(limit).offset(offset)
    )
    users = result.scalars().all()

    return PaginatedUsers(
        items=[
            _user_out(user, user.rol.name if user.rol else None)
            for user in users
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> UserOut:
    user = await _get_user_or_404(db, user_id)
    return _user_out(user, user.rol.name if user.rol else None)


async def list_roles(db: AsyncSession) -> list[RoleOut]:
    result = await db.execute(
        select(RolModel)
        .where(RolModel.name.in_(ALLOWED_ROLES))
        .order_by(RolModel.name)
    )
    return [RoleOut.model_validate(role) for role in result.scalars().all()]


async def create_user(
    db: AsyncSession,
    payload: UserCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> UserOut:
    email = str(payload.email).strip().lower()

    existing = await db.scalar(
        select(UserModel).where(func.lower(UserModel.email) == email)
    )
    if existing is not None:
        await _audit_denial(
            action="create_user_denied",
            actor=actor,
            request=request,
            entity_id=existing.id,
            reason="duplicate_email",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El email ya está registrado",
        )

    role = await _get_role_or_422(
        db,
        payload.id_rol,
        actor=actor,
        request=request,
        action="create_user_denied",
    )

    user = UserModel(
        email=email,
        name=payload.name,
        lastname=payload.lastname,
        is_active=payload.is_active,
        id_rol=role.id,
    )
    db.add(user)

    try:
        await db.flush()
        await record_audit(
            db,
            entity="User",
            action="create_user",
            entity_id=user.id,
            user_id=actor.id,
            request=request,
            after=_snapshot(user, role.name),
        )
        await db.commit()
    except IntegrityError as exc:
        # Carrera entre la verificacion previa y el INSERT: el indice unico
        # de email es la ultima palabra.
        await db.rollback()
        if "email" in str(getattr(exc, "orig", exc)).lower():
            await _audit_denial(
                action="create_user_denied",
                actor=actor,
                request=request,
                reason="duplicate_email",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El email ya está registrado",
            ) from exc
        logger.error(
            "Error de integridad creando usuario (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc
    except Exception as exc:
        await db.rollback()
        # Sin traceback ni parametros: el log no debe filtrar SQL/PII.
        logger.error(
            "Error creando usuario (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return _user_out(user, role.name)


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: UserUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> UserOut:
    user = await _get_user_or_404(db, user_id)
    target_id = user.id
    current_role_name = user.rol.name if user.rol else None
    before = _snapshot(user, current_role_name)

    updates = payload.model_dump(exclude_unset=True)

    # `null` explicito en campos no anulables no es un "no cambiar": es un
    # payload invalido. Evita que un PATCH vacio escriba auditoria sin efecto.
    for field in ("id_rol", "is_active"):
        if field in updates and updates[field] is None:
            await _audit_denial(
                action="update_user_denied",
                actor=actor,
                request=request,
                entity_id=target_id,
                reason="null_field",
                extra={"field": field},
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El campo {field} no puede ser nulo",
            )

    if not updates:
        await _audit_denial(
            action="update_user_denied",
            actor=actor,
            request=request,
            entity_id=target_id,
            reason="empty_update",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nada que actualizar",
        )

    new_role = None
    if "id_rol" in updates:
        new_role = await _get_role_or_422(
            db,
            updates["id_rol"],
            actor=actor,
            request=request,
            entity_id=target_id,
            action="update_user_denied",
        )

    new_role_name = new_role.name if new_role else current_role_name
    new_is_active = updates.get("is_active", bool(user.is_active))
    target_is_active_admin = bool(user.is_active) and current_role_name == ADMIN_ROLE

    # Un admin no puede desactivarse ni cambiarse el rol a si mismo.
    if target_id == actor.id:
        if "is_active" in updates and not updates["is_active"]:
            await _audit_denial(
                action="update_user_denied",
                actor=actor,
                request=request,
                entity_id=target_id,
                reason="self_deactivation",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puedes desactivar tu propia cuenta",
            )
        if new_role is not None and new_role.id != user.id_rol:
            await _audit_denial(
                action="update_user_denied",
                actor=actor,
                request=request,
                entity_id=target_id,
                reason="self_role_change",
                extra={"id_rol": str(new_role.id)},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puedes cambiar tu propio rol",
            )

    if target_is_active_admin:
        # Bloquea a los administradores activos para que dos degradaciones
        # concurrentes no dejen el sistema sin administradores.
        active_admins = await _count_active_admins(db, lock=True)
        if would_remove_last_admin(
            active_admins,
            target_is_active_admin,
            new_is_active,
            new_role_name or "",
        ):
            # El SELECT ... FOR UPDATE anterior bloqueo (entre otros) la fila
            # del actor. La auditoria standalone inserta con FK a esa fila y
            # esperaria el lock -> deadlock. Se libera antes de auditar.
            await db.rollback()
            await _audit_denial(
                action="update_user_denied",
                actor=actor,
                request=request,
                entity_id=target_id,
                reason="last_admin",
                extra={
                    "active_admins": active_admins,
                    "new_is_active": bool(new_is_active),
                    "new_role": new_role_name,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se puede dejar el sistema sin administradores activos",
            )

    if "name" in updates:
        user.name = updates["name"]
    if "lastname" in updates:
        user.lastname = updates["lastname"]
    if new_role is not None:
        user.id_rol = new_role.id
    if "is_active" in updates:
        user.is_active = updates["is_active"]

    await record_audit(
        db,
        entity="User",
        action="update_user",
        entity_id=user.id,
        user_id=actor.id,
        request=request,
        before=before,
        after=_snapshot(user, new_role_name),
    )

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # Sin traceback ni parametros: el log no debe filtrar SQL/PII.
        logger.error(
            "Error actualizando usuario (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return _user_out(user, new_role_name)
