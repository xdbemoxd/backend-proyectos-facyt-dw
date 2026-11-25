"""Validacion de SECRET_KEY al importar (sin trabajo por request)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils.other import SECRET_KEY_MIN_BYTES, validate_secret_key

ROOT = Path(__file__).resolve().parents[1]
VALID = "clave-de-prueba-suficientemente-larga-0123456789"


def test_valid_secret_is_accepted():
    validate_secret_key(VALID)
    validate_secret_key("x" * SECRET_KEY_MIN_BYTES)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        "corta",
        "x" * (SECRET_KEY_MIN_BYTES - 1),
        "reemplazar-por-un-secreto-largo-y-aleatorio",
        "local-dev-only-change-me-32-chars-minimum",
        "change-me-please-this-is-not-secret",
        "your-secret-key-that-is-long-enough-123456",
        "placeholder-value-with-enough-length-1234",
    ],
)
def test_invalid_or_placeholder_secret_is_rejected(value):
    with pytest.raises(RuntimeError) as exc:
        validate_secret_key(value)
    assert "SECRET_KEY" in str(exc.value)
    # Nunca se refleja el valor recibido.
    if value:
        assert value not in str(exc.value)


def test_multibyte_secret_is_measured_in_bytes():
    # 16 caracteres "ñ" = 32 bytes UTF-8: cumple el minimo.
    validate_secret_key("ñ" * 16)
    with pytest.raises(RuntimeError):
        validate_secret_key("ñ" * 15)


def test_app_import_fails_in_production_with_placeholder():
    env = os.environ.copy()
    env["SECRET_KEY"] = "corta"
    env["ENVIRONMENT"] = "production"

    result = subprocess.run(
        [sys.executable, "-c", "import src.utils.other"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr
