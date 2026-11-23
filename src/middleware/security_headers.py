"""Headers de seguridad para la API.

HSTS solo se emite cuando la request llega por HTTPS (directo o por un proxy
confiable). El resto de headers aplica también en desarrollo.
"""

from __future__ import annotations

import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}
_TRUST_PROXY = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in (
    "1",
    "true",
    "yes",
)


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if _TRUST_PROXY:
        proto = request.headers.get("x-forwarded-proto", "")
        return proto.split(",", 1)[0].strip().lower() == "https"
    return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Cross-Origin-Resource-Policy", "same-origin"
        )

        # Swagger necesita scripts/estilos inline; no se le aplica CSP estricta.
        if request.url.path not in _DOCS_PATHS:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )

        if _is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        # Mitiga fingerprinting de Uvicorn cuando el servidor no lo oculta.
        if "server" in response.headers:
            del response.headers["server"]
        return response
