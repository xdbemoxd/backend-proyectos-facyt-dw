"""SEC-211: la respuesta 422 no debe reflejar `input` ni `ctx`."""

import json

import pytest
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from app import validation_exception_handler


def make_request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/user/auth/google/login",
        "raw_path": b"/api/user/auth/google/login",
        "query_string": b"",
        "headers": [],
        "client": ("1.2.3.4", 1234),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_validation_error_response_hides_input_and_ctx():
    exc = RequestValidationError(
        [
            {
                "type": "string_type",
                "loc": ("body", "id_token"),
                "msg": "Input should be a valid string",
                "input": {"token": "super-secret"},
                "ctx": {"error": "detalle interno"},
            }
        ]
    )

    response = await validation_exception_handler(make_request(), exc)

    assert response.status_code == 422
    assert json.loads(response.body) == {
        "detail": [
            {
                "loc": ["body", "id_token"],
                "msg": "Input should be a valid string",
                "type": "string_type",
            }
        ]
    }
    assert b"super-secret" not in response.body
    assert b"detalle interno" not in response.body
    assert b'"input"' not in response.body
    assert b'"ctx"' not in response.body
