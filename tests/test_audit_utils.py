import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schema.audit import PageViewIn
from src.utils.audit import (
    jsonable,
    outcome_from_status,
    sanitize_headers,
    sanitize_query_params,
    should_audit_request,
)


def test_sanitize_headers_redacts_sensitive_and_keeps_allowlist():
    result = sanitize_headers(
        {
            "Authorization": "Bearer super-secret",
            "Cookie": "session=abc",
            "X-Api-Key": "secret-key",
            "Content-Type": "application/json",
            "User-Agent": "pytest-agent",
            "X-Custom-Header": "no debe guardarse",
        }
    )

    assert result["authorization"] == "[REDACTED]"
    assert result["cookie"] == "[REDACTED]"
    assert result["x-api-key"] == "[REDACTED]"
    assert result["content-type"] == "application/json"
    assert result["user-agent"] == "pytest-agent"
    assert "x-custom-header" not in result


def test_sanitize_query_params_redacts_sensitive_keys():
    result = sanitize_query_params(
        {
            "token": "abc123",
            "password": "secreto",
            "api_key": "xyz",
            "session": "sid",
            "page": "2",
            "email": "a@b.com",
        }
    )

    assert result["token"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["api_key"] == "[REDACTED]"
    assert result["session"] == "[REDACTED]"
    assert result["page"] == "2"
    assert result["email"] == "a@b.com"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "success"),
        (201, "success"),
        (302, "success"),
        (400, "failed"),
        (401, "denied"),
        (403, "denied"),
        (404, "failed"),
        (409, "failed"),
        (422, "failed"),
        (500, "failed"),
    ],
)
def test_outcome_from_status(status, expected):
    assert outcome_from_status(status) == expected


def test_jsonable_handles_domain_types():
    value = {
        "id": uuid.UUID("01a200ae-ed62-7162-5c68-768cccc126d6"),
        "when": datetime(2026, 11, 22, tzinfo=timezone.utc),
        "amount": Decimal("12.50"),
        "tags": {"a", "b"},
    }

    result = jsonable(value)

    assert result["id"] == "01a200ae-ed62-7162-5c68-768cccc126d6"
    assert result["when"].startswith("2026-11-22T00:00:00")
    assert result["amount"] == "12.50"
    assert sorted(result["tags"]) == ["a", "b"]


def test_should_audit_request(monkeypatch):
    monkeypatch.delenv("AUDIT_HTTP_ALL", raising=False)

    assert should_audit_request("GET", "/health", 200) is False
    assert should_audit_request("POST", "/api/projects", 201) is True
    assert should_audit_request("GET", "/api/user/auth/google/login", 200) is True
    assert should_audit_request("GET", "/api/projects", 500) is True
    assert should_audit_request("GET", "/", 200) is False
    assert should_audit_request("POST", "/api/audit/pageview", 204) is False
    assert should_audit_request("POST", "/api/audit/pageview", 401) is True
    assert should_audit_request("GET", "/api/audit", 200) is False
    assert should_audit_request("GET", "/api/audit", 401) is True

    monkeypatch.setenv("AUDIT_HTTP_ALL", "true")
    assert should_audit_request("GET", "/", 200) is True


def test_pageview_path_is_validated():
    assert PageViewIn(path="/dashboard/users/0193a1b2-0001-7000-8000-000000000001/edit").path.startswith("/dashboard")

    with pytest.raises(ValidationError):
        PageViewIn(path="https://ejemplo.com/dashboard")

    with pytest.raises(ValidationError):
        PageViewIn(path="/dashboard?token=abc")
