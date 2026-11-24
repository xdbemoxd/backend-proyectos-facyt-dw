import uuid

import pytest

from src.utils.permissions import can_access_project, would_remove_last_admin
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_access_any_project(role):
    assert can_access_project(role, uuid.uuid4(), uuid.uuid4()) is True


def test_coordinator_only_accesses_own_project():
    user_id = uuid.uuid4()
    other_id = uuid.uuid4()
    assert can_access_project(COORDINATOR_ROLE, user_id, user_id) is True
    assert can_access_project(COORDINATOR_ROLE, user_id, other_id) is False


@pytest.mark.parametrize(
    ("active_admins", "target_is_active_admin", "new_is_active", "new_role", "expected"),
    [
        # No es admin activo: nunca bloquea.
        (1, False, False, COORDINATOR_ROLE, False),
        # Hay mas de un admin: se puede desactivar o degradar a uno.
        (2, True, False, ADMIN_ROLE, False),
        (2, True, True, COORDINATOR_ROLE, False),
        # Ultimo admin: desactivarlo o degradarlo bloquea.
        (1, True, False, ADMIN_ROLE, True),
        (1, True, True, COORDINATOR_ROLE, True),
        # Ultimo admin que sigue activo y con el mismo rol: no bloquea.
        (1, True, True, ADMIN_ROLE, False),
    ],
)
def test_would_remove_last_admin(
    active_admins, target_is_active_admin, new_is_active, new_role, expected
):
    assert (
        would_remove_last_admin(
            active_admins, target_is_active_admin, new_is_active, new_role
        )
        is expected
    )
