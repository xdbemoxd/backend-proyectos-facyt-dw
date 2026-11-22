import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel
from starlette.requests import Request as StarletteRequest

from src.utils.audit import (
    _to_uuid,
    build_http_audit_data,
    extract_actor,
    get_client_ip,
    jsonable,
    record_audit,
)
from src.utils.other import generate_jwt_to_frontend


def make_request(
    *,
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    query: str = "",
    client: tuple[str, int] | None = ("1.2.3.4", 1234),
    state: dict | None = None,
) -> StarletteRequest:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": query.encode("latin-1"),
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
    }
    request = StarletteRequest(scope)
    for key, value in (state or {}).items():
        setattr(request.state, key, value)
    return request


class Color(Enum):
    RED = "red"


@dataclass
class Point:
    x: int
    y: int


class Payload(BaseModel):
    value: int


class Fallback:
    def __str__(self) -> str:
        return "fallback"


def test_jsonable_enum_path_dataclass_pydantic_and_fallback():
    assert jsonable(Color.RED) == "red"
    assert jsonable(Path("a/b")) == str(Path("a/b"))
    assert jsonable(Point(1, 2)) == {"x": 1, "y": 2}
    assert jsonable(Payload(value=5)) == {"value": 5}
    assert jsonable(Fallback()) == "fallback"


def test_get_client_ip_uses_peer_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    request = make_request(headers={"x-forwarded-for": "9.9.9.9"})
    assert get_client_ip(request) == "1.2.3.4"


def test_get_client_ip_trusts_forwarded_header(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    request = make_request(
        headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
    )
    assert get_client_ip(request) == "9.9.9.9"


def test_get_client_ip_trusts_real_ip_fallback(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    request = make_request(headers={"x-real-ip": "8.8.8.8"})
    assert get_client_ip(request) == "8.8.8.8"


def test_get_client_ip_without_client(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    request = make_request(client=None)
    assert get_client_ip(request) == "unknown"


def test_to_uuid_handles_uuid_none_and_invalid_values():
    user_id = uuid.uuid4()
    assert _to_uuid(user_id) == user_id
    assert _to_uuid(None) is None
    assert _to_uuid("no-es-uuid") is None


def test_extract_actor_from_state():
    user_id = uuid.uuid4()
    request = make_request(
        state={"user_id": str(user_id), "user_email": "a@b.com"}
    )
    assert extract_actor(request) == (user_id, "a@b.com")


def test_extract_actor_accepts_uuid_state_and_ignores_invalid_uuid():
    user_id = uuid.uuid4()
    assert extract_actor(make_request(state={"user_id": user_id})) == (
        user_id,
        None,
    )
    assert extract_actor(
        make_request(state={"user_id": "no-es-uuid"})
    ) == (None, None)


def test_extract_actor_from_valid_token():
    user_id = uuid.uuid4()
    token = generate_jwt_to_frontend(user_id, "a@b.com", "Administrador")
    request = make_request(headers={"authorization": f"Bearer {token}"})
    assert extract_actor(request) == (user_id, "a@b.com")


def test_extract_actor_invalid_or_empty_token():
    assert extract_actor(make_request()) == (None, None)
    assert extract_actor(
        make_request(headers={"authorization": "Basic abc"})
    ) == (None, None)
    assert extract_actor(
        make_request(headers={"authorization": "Bearer "})
    ) == (None, None)
    assert extract_actor(
        make_request(headers={"authorization": "Bearer no-es-jwt"})
    ) == (None, None)


def test_build_http_audit_data_includes_http_metadata():
    request_id = uuid.uuid4()
    request = make_request(
        method="post",
        path="/api/projects",
        query="token=abc&page=2",
        headers={
            "authorization": "Bearer secreto",
            "user-agent": "pytest-agent",
            "content-type": "application/json",
        },
        state={"request_id": request_id, "error_detail": "detalle del error"},
    )

    data = build_http_audit_data(
        request, status_code=422, duration_ms=-5
    )

    assert data["request_id"] == request_id
    assert data["method"] == "POST"
    assert data["path"] == "/api/projects"
    assert data["status_code"] == 422
    assert data["outcome"] == "failed"
    assert data["error_code"] == "HTTP_422"
    assert data["error_detail"] == "detalle del error"
    assert data["duration_ms"] == 0
    assert data["query_params"]["token"] == "[REDACTED]"
    assert data["query_params"]["page"] == "2"
    assert data["request_headers"]["authorization"] == "[REDACTED]"
    assert data["user_agent"] == "pytest-agent"
    assert data["ip_address"] == "1.2.3.4"


class FakeSession:
    def __init__(self):
        self.entry = None

    def add(self, entry):
        self.entry = entry

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_record_audit_uses_request_context():
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    request = make_request(
        method="PATCH",
        path="/api/projects/1",
        state={
            "user_id": str(user_id),
            "user_email": "a@b.com",
            "request_id": request_id,
        },
    )
    session = FakeSession()

    entry = await record_audit(
        session,
        entity="Project",
        action="update_priority",
        entity_id="1",
        before={"priority": "Media"},
        after={"priority": "Alta"},
        request=request,
    )

    assert entry is session.entry
    assert entry.user_id == user_id
    assert entry.user_email == "a@b.com"
    assert entry.request_id == request_id
    assert entry.ip_address == "1.2.3.4"
    assert entry.before_data == {"priority": "Media"}
    assert entry.after_data == {"priority": "Alta"}


@pytest.mark.asyncio
async def test_record_audit_uses_token_when_state_is_empty():
    user_id = uuid.uuid4()
    token = generate_jwt_to_frontend(user_id, "token@b.com", "Administrador")
    request = make_request(headers={"authorization": f"Bearer {token}"})
    session = FakeSession()

    entry = await record_audit(
        session,
        entity="Project",
        action="token_actor",
        entity_id="1",
        request=request,
    )

    assert entry.user_id == user_id
    assert entry.user_email == "token@b.com"
    assert entry.ip_address == "1.2.3.4"
