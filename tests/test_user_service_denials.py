"""Pruebas unitarias (sin BD) de las denegaciones auditadas en el servicio.

SEC-205: cada rechazo de negocio debe producir un evento ``outcome="denied"``
con ``entity="User"``, actor y motivo acotado, sin PII.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.schema.user import CurrentUser, UserCreate, UserUpdate
from src.service import user as user_service
from src.utils.roles import ADMIN_ROLE, COORDINATOR_ROLE


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Sesion minima para ejercitar los caminos que no mutan la BD."""

    def __init__(self, *, scalar=None, execute_result=None, get_result=None):
        self._scalar = scalar
        self._execute = execute_result
        self._get = get_result
        self.added = []
        self.rollbacks = 0

    async def scalar(self, statement):
        return self._scalar

    async def execute(self, statement):
        return FakeResult(self._execute)

    async def get(self, model, key):
        return self._get

    async def rollback(self):
        self.rollbacks += 1

    def add(self, value):
        self.added.append(value)


def make_actor(user_id=None) -> CurrentUser:
    return CurrentUser(
        id=user_id or uuid.uuid4(),
        email="actor@example.com",
        name="Actor",
        lastname="Prueba",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=ADMIN_ROLE,
    )


def make_user(*, user_id=None, role=ADMIN_ROLE, is_active=True):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        email="user@example.com",
        name=None,
        lastname=None,
        is_active=is_active,
        id_rol=uuid.uuid4(),
        rol=SimpleNamespace(name=role),
    )


@pytest.fixture
def denials(monkeypatch):
    recorded: list[dict] = []

    async def fake_standalone(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        user_service, "record_audit_standalone", fake_standalone
    )
    return recorded


@pytest.mark.asyncio
async def test_create_user_duplicate_email_is_audited_without_pii(denials):
    existing = make_user(role=COORDINATOR_ROLE)
    payload = UserCreate(email="user@example.com", id_rol=uuid.uuid4())
    session = FakeSession(scalar=existing)

    with pytest.raises(HTTPException) as exc:
        await user_service.create_user(
            session, payload, make_actor(), request=None
        )

    assert exc.value.status_code == 409
    assert len(denials) == 1
    event = denials[0]
    assert event["entity"] == "User"
    assert event["action"] == "create_user_denied"
    assert event["outcome"] == "denied"
    assert event["entity_id"] == existing.id
    assert event["after"] == {"reason": "duplicate_email"}
    assert "@" not in str(event["after"])


@pytest.mark.asyncio
async def test_create_user_invalid_role_is_audited(denials):
    payload = UserCreate(email="new@example.com", id_rol=uuid.uuid4())
    session = FakeSession(scalar=None, get_result=None)

    with pytest.raises(HTTPException) as exc:
        await user_service.create_user(
            session, payload, make_actor(), request=None
        )

    assert exc.value.status_code == 422
    event = denials[0]
    assert event["action"] == "create_user_denied"
    assert event["after"]["reason"] == "invalid_role"
    assert event["after"]["id_rol"] == str(payload.id_rol)


@pytest.mark.asyncio
async def test_update_empty_payload_is_audited(denials):
    user = make_user(role=COORDINATOR_ROLE)
    actor = make_actor()
    session = FakeSession(execute_result=user)

    with pytest.raises(HTTPException) as exc:
        await user_service.update_user(
            session, user.id, UserUpdate(), actor, request=None
        )

    assert exc.value.status_code == 422
    event = denials[0]
    assert event["action"] == "update_user_denied"
    assert event["entity_id"] == user.id
    assert event["after"] == {"reason": "empty_update"}


@pytest.mark.asyncio
async def test_update_null_field_is_audited(denials):
    user = make_user(role=COORDINATOR_ROLE)
    session = FakeSession(execute_result=user)

    with pytest.raises(HTTPException) as exc:
        await user_service.update_user(
            session,
            user.id,
            UserUpdate(is_active=None),
            make_actor(),
            request=None,
        )

    assert exc.value.status_code == 422
    event = denials[0]
    assert event["after"] == {"reason": "null_field", "field": "is_active"}


@pytest.mark.asyncio
async def test_self_deactivation_is_audited(denials):
    actor = make_actor()
    user = make_user(user_id=actor.id)
    session = FakeSession(execute_result=user)

    with pytest.raises(HTTPException) as exc:
        await user_service.update_user(
            session,
            user.id,
            UserUpdate(is_active=False),
            actor,
            request=None,
        )

    assert exc.value.status_code == 409
    event = denials[0]
    assert event["after"] == {"reason": "self_deactivation"}


@pytest.mark.asyncio
async def test_self_role_change_is_audited(denials):
    actor = make_actor()
    user = make_user(user_id=actor.id)
    new_role = SimpleNamespace(id=uuid.uuid4(), name=COORDINATOR_ROLE)
    session = FakeSession(execute_result=user, get_result=new_role)

    with pytest.raises(HTTPException) as exc:
        await user_service.update_user(
            session,
            user.id,
            UserUpdate(id_rol=new_role.id),
            actor,
            request=None,
        )

    assert exc.value.status_code == 409
    event = denials[0]
    assert event["after"]["reason"] == "self_role_change"
    assert event["after"]["id_rol"] == str(new_role.id)


@pytest.mark.asyncio
async def test_last_admin_removal_is_audited(denials, monkeypatch):
    user = make_user(role=ADMIN_ROLE, is_active=True)
    session = FakeSession(execute_result=user)

    async def only_one_admin(db, *, lock=False):
        return 1

    monkeypatch.setattr(
        user_service, "_count_active_admins", only_one_admin
    )

    with pytest.raises(HTTPException) as exc:
        await user_service.update_user(
            session,
            user.id,
            UserUpdate(is_active=False),
            make_actor(),
            request=None,
        )

    assert exc.value.status_code == 409
    # El lock FOR UPDATE se libera antes de escribir la auditoria standalone.
    assert session.rollbacks == 1
    event = denials[0]
    assert event["after"]["reason"] == "last_admin"
    assert event["after"]["active_admins"] == 1
    assert event["after"]["new_is_active"] is False
    assert event["after"]["new_role"] == ADMIN_ROLE
