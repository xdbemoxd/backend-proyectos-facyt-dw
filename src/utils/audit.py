"""Servicio de auditoria.

Dos modos de uso:

1. ``build_http_audit_data`` + ``write_http_audit``: lo usa el middleware para
   registrar cada request relevante (mutaciones, auth y errores).
2. ``record_audit``: lo usan los servicios de negocio para registrar el
   antes/despues de una entidad dentro de la misma transaccion. Si la accion
   debe sobrevivir a un rollback, usar ``record_audit_standalone``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import AsyncSessionLocal
from src.model.audit import AuditLogModel
from src.utils.other import verify_token_acces

logger = logging.getLogger(__name__)

# Headers que se guardan (en minusculas). Cualquier otro se omite.
_ALLOWED_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "content-length",
    "content-type",
    "host",
    "origin",
    "referer",
    "user-agent",
    "x-forwarded-for",
    "x-real-ip",
    "x-request-id",
}

# Headers que se registran como presentes pero con valor redactado.
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
}

_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(token|password|passwd|secret|api[_-]?key|auth|session|code|otp)",
    re.IGNORECASE,
)

_EXCLUDED_PATHS = {
    "/health",
    "/health/2",
    "/docs",
    "/redoc",
    "/openapi.json",
}

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def jsonable(value: Any) -> Any:
    """Convierte valores de dominio a algo serializable como JSONB."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return jsonable(model_dump(mode="json"))
        except Exception:  # pragma: no cover - defensivo
            pass
    return str(value)


def sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Guarda solo headers permitidos y redacta los sensibles."""
    result: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _SENSITIVE_HEADERS:
            result[lower] = "[REDACTED]"
        elif lower in _ALLOWED_HEADERS:
            result[lower] = _truncate(value, 1000) or ""
    return result


def sanitize_query_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Redacta query params con nombres sensibles."""
    result: dict[str, str] = {}
    for key, value in params.items():
        if _SENSITIVE_QUERY_PATTERN.search(key):
            result[str(key)] = "[REDACTED]"
        else:
            result[str(key)] = _truncate(value, 500) or ""
    return result


def outcome_from_status(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "success"
    if status_code in (401, 403):
        return "denied"
    return "failed"


def get_client_ip(request: Request) -> str:
    """Obtiene la IP del cliente.

    Solo confia en X-Forwarded-For / X-Real-IP si TRUST_PROXY_HEADERS=true.
    De lo contrario, un cliente podria falsificar su IP.
    """
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
                return _truncate(first, 255) or "unknown"
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return _truncate(real_ip.strip(), 255) or "unknown"

    if request.client and request.client.host:
        return _truncate(request.client.host, 255) or "unknown"
    return "unknown"


def _to_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def extract_actor(request: Request) -> tuple[uuid.UUID | None, str | None]:
    """Devuelve (user_id, email) desde request.state o desde el JWT.

    El JWT se verifica con la misma firma que usa la API. No se consulta la BD
    aqui: la auditoria registra el actor del token; la autorizacion real debe
    hacerla la dependencia de autenticacion.
    """
    user_id = getattr(request.state, "user_id", None)
    user_email = getattr(request.state, "user_email", None)
    if user_id:
        return _to_uuid(user_id), _truncate(user_email, 255)

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None, None

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None, None

    try:
        payload = verify_token_acces(token)
    except Exception:
        return None, None

    return _to_uuid(payload.get("sub")), _truncate(payload.get("email"), 255)


def should_audit_request(method: str, path: str, status_code: int) -> bool:
    """Evita ruido de health/docs y audita lo relevante.

    - Siempre: mutaciones (POST/PUT/PATCH/DELETE), auth y errores.
    - GET/HEAD/OPTIONS: solo auth, errores o si AUDIT_HTTP_ALL=true.
    """
    if path in _EXCLUDED_PATHS:
        return False
    # El endpoint de navegacion escribe su propio evento de negocio; solo se
    # audita por HTTP cuando falla (p. ej. 401 de un intento anonimo).
    if path == "/api/audit/pageview" and status_code < 400:
        return False
    if path == "/api/audit/ui-event" and status_code < 400:
        return False
    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return True
    if path.startswith("/api/user/auth/"):
        return True
    if status_code >= 400:
        return True
    return os.getenv("AUDIT_HTTP_ALL", "false").lower() in ("1", "true", "yes")


def build_http_audit_data(
    request: Request,
    *,
    status_code: int,
    duration_ms: int,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    """Construye el diccionario para una fila de auditoria HTTP."""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4()
    user_id, _ = extract_actor(request)
    outcome = outcome_from_status(status_code)

    if error_detail is None:
        error_detail = getattr(request.state, "error_detail", None)
    if error_code is None and status_code >= 400:
        error_code = f"HTTP_{status_code}"

    return {
        "request_id": request_id,
        "user_id": user_id,
        # PII fuera de auditoria: el email se resuelve desde private."user".
        "user_email": None,
        "ip_address": get_client_ip(request),
        "method": request.method.upper(),
        "path": _truncate(request.url.path, 500),
        "status_code": status_code,
        "user_agent": _truncate(request.headers.get("user-agent"), 512),
        "request_headers": sanitize_headers(request.headers),
        "query_params": sanitize_query_params(dict(request.query_params)),
        "duration_ms": max(0, int(duration_ms)),
        "entity": "HTTP",
        "action": "http_request",
        "entity_id": str(request_id)[:100],
        "outcome": outcome,
        "error_code": _truncate(error_code, 100),
        "error_detail": _truncate(error_detail, 1000),
        "before_data": None,
        "after_data": None,
    }


async def write_http_audit(data: dict[str, Any]) -> None:
    """Persiste una fila HTTP en una sesion independiente.

    Nunca propaga errores: la auditoria no debe tumbar la request.
    """
    try:
        async with AsyncSessionLocal() as db:
            db.add(AuditLogModel(**data))
            await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        logger.error("No se pudo escribir audit_log HTTP: %s", type(exc).__name__)


async def record_audit(
    db: AsyncSession,
    *,
    entity: str,
    action: str,
    entity_id: str | uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ip_address: str = "unknown",
    outcome: str = "success",
    status_code: int | None = None,
    before: Any = None,
    after: Any = None,
    request: Request | None = None,
    request_id: uuid.UUID | None = None,
    http_method: str | None = None,
    http_path: str | None = None,
) -> AuditLogModel:
    """Agrega un evento de negocio a la transaccion actual (sin commit).

    Si se pasa ``request``, el evento incluye la misma metadata HTTP que la
    fila del middleware (metodo, endpoint, user-agent, headers y query params).
    ``status_code`` debe indicar el resultado real de la operacion de negocio.
    """
    http_fields: dict[str, Any] = {}
    if request is not None:
        if user_id is None:
            user_id, _ = _actor_from_state_or_token(request)
        if ip_address == "unknown":
            ip_address = get_client_ip(request)
        if request_id is None:
            request_id = getattr(request.state, "request_id", None)

        http_fields = {
            "method": (
                (http_method or request.method).upper()
                if (http_method or request.method).upper() in _HTTP_METHODS
                else request.method.upper()
            ),
            "path": _truncate(http_path or request.url.path, 500),
            "user_agent": _truncate(request.headers.get("user-agent"), 512),
            "request_headers": sanitize_headers(request.headers),
            "query_params": sanitize_query_params(dict(request.query_params)),
        }

    entry = AuditLogModel(
        request_id=request_id,
        user_id=_to_uuid(user_id),
        # PII fuera de auditoria: el email se resuelve desde private."user".
        user_email=None,
        ip_address=_truncate(ip_address, 255) or "unknown",
        status_code=status_code,
        entity=_truncate(entity, 255) or "Unknown",
        action=_truncate(action, 255) or "unknown",
        entity_id=_truncate(entity_id, 100),
        outcome=outcome,
        error_code=None,
        error_detail=None,
        before_data=jsonable(before),
        after_data=jsonable(after),
        **http_fields,
    )
    db.add(entry)
    await db.flush()
    return entry


async def record_audit_standalone(**kwargs: Any) -> None:
    """Registra un evento en su propia transaccion.

    Util para acciones denegadas o fallidas cuyo rollback no debe borrar la
    evidencia de auditoria.
    """
    try:
        async with AsyncSessionLocal() as db:
            await record_audit(db, **kwargs)
            await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        logger.error("No se pudo escribir audit_log: %s", type(exc).__name__)


def _actor_from_state_or_token(request: Request) -> tuple[uuid.UUID | None, str | None]:
    user_id = getattr(request.state, "user_id", None)
    user_email = getattr(request.state, "user_email", None)
    if user_id:
        return _to_uuid(user_id), _truncate(user_email, 255)
    return extract_actor(request)
