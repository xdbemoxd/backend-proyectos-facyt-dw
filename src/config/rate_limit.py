"""Configuracion compartida de rate limiting (SlowAPI).

Vive fuera de `app.py` para que los routers puedan aplicar limites
especificos (p. ej. login) sin importar la aplicacion y crear un ciclo.
"""

from __future__ import annotations

import os

from fastapi import Request
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()


def rate_limit_key(request: Request) -> str:
    """IP real del cliente cuando hay un proxy confiable delante."""
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return get_remote_address(request)

_limits_string = os.getenv("RATE_LIMIT", "100/minute")
_limits = [limit.strip() for limit in _limits_string.split(",") if limit.strip()]
if not _limits:
    _limits = ["100/minute"]

limiter = Limiter(key_func=rate_limit_key, default_limits=_limits)

# Limite mas estricto para el login (fuerza bruta / credential stuffing).
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "10/minute")

# Navegacion del frontend: evita que un cliente con sesion sature audit_log.
PAGEVIEW_RATE_LIMIT = os.getenv("PAGEVIEW_RATE_LIMIT", "120/minute")
