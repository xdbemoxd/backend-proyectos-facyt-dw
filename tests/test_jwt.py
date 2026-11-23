import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from src.utils.other import (
    ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    SECRET_KEY,
    generate_jwt_to_frontend,
    verify_token_acces,
)


def test_generated_jwt_has_issuer_audience_and_version():
    user_id = uuid.uuid4()
    token = generate_jwt_to_frontend(
        user_id, "user@example.com", "Coordinador de Proyecto", 7
    )
    payload = verify_token_acces(token)

    assert payload["sub"] == str(user_id)
    assert payload["iss"] == JWT_ISSUER
    assert payload["aud"] == JWT_AUDIENCE
    assert payload["ver"] == 7
    assert payload["jti"]
    assert payload["iat"]
    assert datetime.fromtimestamp(
        payload["exp"], tz=timezone.utc
    ) > datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [
        ("otro-issuer", JWT_AUDIENCE),
        (JWT_ISSUER, "otra-audiencia"),
    ],
)
def test_jwt_with_wrong_issuer_or_audience_is_rejected(issuer, audience):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": issuer,
            "aud": audience,
            "exp": now + timedelta(minutes=5),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    with pytest.raises(HTTPException):
        verify_token_acces(token)
