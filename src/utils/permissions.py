"""Reglas de alcance y protecciones de administracion."""

from __future__ import annotations

import uuid

from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE


def can_access_project(
    user_role: str, user_id: uuid.UUID, project_owner_id: uuid.UUID
) -> bool:
    """Administrador y Aprobador ven todo; Coordinador solo sus proyectos."""
    if user_role in (ADMIN_ROLE, APPROVER_ROLE):
        return True
    return user_id == project_owner_id


def would_remove_last_admin(
    active_admins: int,
    target_is_active_admin: bool,
    new_is_active: bool,
    new_role: str,
) -> bool:
    """True si el cambio dejaria al sistema sin administradores activos."""
    if not target_is_active_admin:
        return False
    if active_admins > 1:
        return False
    return (not new_is_active) or new_role != ADMIN_ROLE
