"""Configuracion compartida de rate limiting (SlowAPI).

Vive fuera de `app.py` para que los routers puedan aplicar limites
especificos (p. ej. login) sin importar la aplicacion y crear un ciclo.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()

_limits_string = os.getenv("RATE_LIMIT", "100/minute")
_limits = [limit.strip() for limit in _limits_string.split(",") if limit.strip()]
if not _limits:
    _limits = ["100/minute"]

limiter = Limiter(key_func=get_remote_address, default_limits=_limits)

# Limite mas estricto para el login (fuerza bruta / credential stuffing).
LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "10/minute")
