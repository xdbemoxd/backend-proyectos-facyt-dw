"""Middleware de auditoria HTTP.

Genera un `request_id`, mide la duracion, captura metadata de la request y
escribe una fila en `audit_log`. Si la escritura falla, la request continua:
la auditoria no debe tumbar la API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.utils.audit import (
    build_http_audit_data,
    should_audit_request,
    write_http_audit,
)

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


async def _schedule_audit(data: dict) -> None:
    """Escribe la auditoria sin bloquear la respuesta.

    En tests se puede forzar escritura sincrona con AUDIT_SYNC_WRITES=true.
    """
    if os.getenv("AUDIT_SYNC_WRITES", "false").lower() in ("1", "true", "yes"):
        await write_http_audit(data)
        return

    task = asyncio.create_task(write_http_audit(data))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4()
        request.state.request_id = request_id

        start = time.perf_counter()
        status_code = 500
        error_code: str | None = None
        error_detail: str | None = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers.setdefault("X-Request-ID", str(request_id))
            return response
        except Exception as exc:
            error_code = type(exc).__name__
            error_detail = str(exc)
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)

            if should_audit_request(request.method, request.url.path, status_code):
                try:
                    data = build_http_audit_data(
                        request,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        error_code=error_code,
                        error_detail=error_detail,
                    )
                    await _schedule_audit(data)
                except Exception as exc:  # pragma: no cover - defensivo
                    logger.error(
                        "Fallo construyendo auditoria HTTP: %s",
                        type(exc).__name__,
                    )
