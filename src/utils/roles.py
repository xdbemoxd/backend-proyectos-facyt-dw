"""Roles del sistema (RN-1).

Los nombres deben coincidir exactamente con `private.rol.name`.
"""

from __future__ import annotations

ADMIN_ROLE = "Administrador del Sistema"
APPROVER_ROLE = "Aprobador / Gestor de Inventario"
COORDINATOR_ROLE = "Coordinador de Proyecto"

ALLOWED_ROLES: frozenset[str] = frozenset(
    {ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE}
)
