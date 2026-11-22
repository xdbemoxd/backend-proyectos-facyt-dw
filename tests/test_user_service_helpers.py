"""Pruebas unitarias de helpers puros del servicio de usuarios."""

import uuid
from types import SimpleNamespace

from src.service.user import _like_pattern, _snapshot, _user_out
from src.utils.roles import COORDINATOR_ROLE


def test_like_pattern_escapes_wildcards_and_backslash():
    assert _like_pattern("100%_x\\y") == "%100\\%\\_x\\\\y%"
    assert _like_pattern("normal") == "%normal%"


def test_snapshot_has_no_pii_and_only_expected_fields():
    user = SimpleNamespace(
        id=uuid.uuid4(), is_active=True, id_rol=uuid.uuid4()
    )

    snapshot = _snapshot(user, COORDINATOR_ROLE)

    assert set(snapshot) == {"id", "is_active", "id_rol", "rol"}
    assert "@" not in str(snapshot)
    assert snapshot["rol"] == COORDINATOR_ROLE
    assert isinstance(snapshot["id"], str)
    assert isinstance(snapshot["id_rol"], str)


def test_user_out_maps_orm_like_object():
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        name=None,
        lastname=None,
        is_active=True,
        id_rol=uuid.uuid4(),
    )

    out = _user_out(user, COORDINATOR_ROLE)

    assert out.id == user.id
    assert out.email == "user@example.com"
    assert out.name is None
    assert out.is_active is True
    assert out.rol == COORDINATOR_ROLE
