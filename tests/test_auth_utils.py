"""Pruebas unitarias de utilidades de autenticacion (sin BD)."""

import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.schema.user import CurrentUser
from src.utils.auth import (
    generate_random_password,
    require_roles,
    verify_token_cron,
)
from src.utils.roles import ADMIN_ROLE, COORDINATOR_ROLE


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/user",
            "raw_path": b"/api/user",
            "query_string": b"",
            "headers": [],
            "client": ("1.2.3.4", 1234),
            "server": ("testserver", 80),
        }
    )


def make_current_user(role: str) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="user@example.com",
        name="Nombre",
        lastname="Apellido",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=role,
    )


@pytest.mark.asyncio
async def test_require_roles_allows_matching_role():
    checker = require_roles(ADMIN_ROLE)
    admin = make_current_user(ADMIN_ROLE)

    assert await checker(make_request(), admin) is admin


@pytest.mark.asyncio
async def test_require_roles_rejects_other_roles(monkeypatch):
    recorded: list[dict] = []

    async def fake_standalone(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "src.utils.auth.record_audit_standalone", fake_standalone
    )

    checker = require_roles(ADMIN_ROLE)
    coordinator = make_current_user(COORDINATOR_ROLE)

    with pytest.raises(HTTPException) as exc:
        await checker(make_request(), coordinator)

    assert exc.value.status_code == 403
    assert len(recorded) == 1
    event = recorded[0]
    assert event["entity"] == "User"
    assert event["action"] == "rbac_denied"
    assert event["outcome"] == "denied"
    assert event["entity_id"] == coordinator.id
    assert event["user_id"] == coordinator.id
    assert event["after"]["reason"] == "role_not_allowed"
    assert event["after"]["required_roles"] == [ADMIN_ROLE]
    assert "@" not in str(event["after"])


@pytest.mark.asyncio
async def test_verify_token_cron_requires_matching_secret(monkeypatch):
    monkeypatch.setenv("TOKEN_CRON", "cron-secreto")

    assert await verify_token_cron("cron-secreto") is True

    for candidate in (None, "", "otro-token"):
        with pytest.raises(HTTPException) as exc:
            await verify_token_cron(candidate)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_cron_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("TOKEN_CRON", raising=False)

    with pytest.raises(HTTPException) as exc:
        await verify_token_cron("cualquier-cosa")
    assert exc.value.status_code == 401


def test_generate_random_password_meets_policy():
    password = generate_random_password(30)

    assert len(password) == 30
    assert any(c.islower() for c in password)
    assert any(c.isupper() for c in password)
    assert any(c.isdigit() for c in password)
    assert any(c in "!@#$%^&*-_" for c in password)
