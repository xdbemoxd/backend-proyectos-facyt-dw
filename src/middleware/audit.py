"""Middleware de auditoria HTTP.

Genera un `request_id`, mide la duracion, captura metadata de la request y
escribe una fila en `audit_log`.

Rendimiento y durabilidad:

- Por defecto la escritura es **no bloqueante**: la fila se encola en una cola
  acotada en memoria y un pool de workers la persiste fuera del camino de
  respuesta. La request nunca espera a la base de datos.
- Los workers agrupan eventos en lotes y hacen un commit por lote, lo que
  reduce viajes a la BD frente a un commit por request.
- Reintentan con backoff si la escritura falla y registran el fallo (solo el
  tipo de excepcion) sin frenar la API.
- Al apagar la aplicacion se drena la cola durante un tiempo acotado.
- ``AUDIT_SYNC_WRITES=true`` fuerza escritura sincrona: solo para pruebas que
  consultan `audit_log` inmediatamente despues del response.
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
    _persist_http_audit,
    _persist_http_audit_batch,
    build_http_audit_data,
    should_audit_request,
    write_http_audit,
)

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_MAXSIZE = 10000
_DEFAULT_WRITE_RETRIES = 2
_DEFAULT_RETRY_BACKOFF_MS = 200
_DEFAULT_DRAIN_TIMEOUT_SECONDS = 5.0
_DEFAULT_WORKERS = 2
_DEFAULT_BATCH_SIZE = 50

# Estado del pool. Se recrea por event loop (los tests crean loops nuevos).
_queue: asyncio.Queue[dict] | None = None
_worker_tasks: set[asyncio.Task] = set()
_worker_loop: asyncio.AbstractEventLoop | None = None


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _sync_writes() -> bool:
    return os.getenv("AUDIT_SYNC_WRITES", "false").lower() in (
        "1",
        "true",
        "yes",
    )


async def _write_with_retry(data: dict) -> bool:
    """Persiste con reintentos acotados. Nunca lanza errores de BD."""
    retries = _int_env("AUDIT_WRITE_RETRIES", _DEFAULT_WRITE_RETRIES)
    backoff_ms = _int_env(
        "AUDIT_RETRY_BACKOFF_MS", _DEFAULT_RETRY_BACKOFF_MS
    )
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            await _persist_http_audit(data)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende de la BD
            last_error = exc
            if attempt < retries and backoff_ms:
                await asyncio.sleep((backoff_ms * (2**attempt)) / 1000)

    logger.error(
        "No se pudo persistir auditoria HTTP tras %s intentos: %s",
        retries + 1,
        type(last_error).__name__ if last_error else "Unknown",
    )
    return False


async def _write_batch_with_retry(batch: list[dict]) -> None:
    """Persiste un lote. Si el lote falla, reintenta fila por fila."""
    if len(batch) == 1:
        await _write_with_retry(batch[0])
        return

    retries = _int_env("AUDIT_WRITE_RETRIES", _DEFAULT_WRITE_RETRIES)
    backoff_ms = _int_env(
        "AUDIT_RETRY_BACKOFF_MS", _DEFAULT_RETRY_BACKOFF_MS
    )
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            await _persist_http_audit_batch(batch)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - depende de la BD
            last_error = exc
            if attempt < retries and backoff_ms:
                await asyncio.sleep((backoff_ms * (2**attempt)) / 1000)

    logger.error(
        "No se pudo persistir un lote de %s eventos de auditoria tras %s "
        "intentos (%s); se reintenta fila por fila",
        len(batch),
        retries + 1,
        type(last_error).__name__ if last_error else "Unknown",
    )
    # Un evento invalido no debe tumbar el resto del lote.
    for data in batch:
        await _write_with_retry(data)


async def _audit_worker(queue: asyncio.Queue[dict]) -> None:
    """Consume la cola por lotes hasta que se cancele tras el drenaje."""
    batch_size = _int_env(
        "AUDIT_BATCH_SIZE", _DEFAULT_BATCH_SIZE, minimum=1
    )
    while True:
        data = await queue.get()
        batch = [data]
        # Aprovecha los eventos ya encolados: un commit por lote en vez de uno
        # por request. No espera a que lleguen mas.
        for _ in range(batch_size - 1):
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            await _write_batch_with_retry(batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensivo
            logger.error(
                "Error inesperado en worker de auditoria: %s",
                type(exc).__name__,
            )
        finally:
            for _ in batch:
                queue.task_done()


def _ensure_worker() -> asyncio.Queue[dict]:
    """Crea (si hace falta) la cola y el pool del event loop actual."""
    global _queue, _worker_tasks, _worker_loop

    loop = asyncio.get_running_loop()
    if _queue is None or _worker_loop is not loop:
        _queue = asyncio.Queue(
            maxsize=_int_env(
                "AUDIT_QUEUE_MAXSIZE", _DEFAULT_QUEUE_MAXSIZE, minimum=1
            )
        )
        _worker_loop = loop
        _worker_tasks = set()

    concurrency = _int_env(
        "AUDIT_WORKER_CONCURRENCY", _DEFAULT_WORKERS, minimum=1
    )
    running = sum(1 for task in _worker_tasks if not task.done())
    for _ in range(concurrency - running):
        task = loop.create_task(_audit_worker(_queue))
        _worker_tasks.add(task)
        task.add_done_callback(_worker_tasks.discard)
    return _queue


async def _schedule_audit(data: dict) -> None:
    """Encola la auditoria sin esperar a la base de datos."""
    if _sync_writes():
        await write_http_audit(data)
        return

    queue = _ensure_worker()
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        # Bajo presion extrema se prefiere degradar la auditoria antes que
        # crecer sin limite o bloquear la respuesta.
        logger.error(
            "Cola de auditoria llena (%s eventos); se descarta un evento",
            queue.maxsize,
        )


async def drain_audit_queue(timeout: float | None = None) -> None:
    """Espera a que el pool vacie la cola y lo detiene (shutdown)."""
    global _queue, _worker_tasks, _worker_loop

    queue = _queue
    tasks = list(_worker_tasks)
    if queue is None or not tasks:
        return

    limit = (
        _DEFAULT_DRAIN_TIMEOUT_SECONDS if timeout is None else max(0.0, timeout)
    )
    try:
        await asyncio.wait_for(queue.join(), timeout=limit)
    except asyncio.TimeoutError:  # pragma: no cover - depende de la BD
        logger.error(
            "Drenaje de auditoria incompleto: %s eventos pendientes",
            queue.qsize(),
        )
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - defensivo
                logger.error(
                    "Error deteniendo worker de auditoria: %s",
                    type(exc).__name__,
                )
        _worker_tasks = set()
        _queue = None
        _worker_loop = None


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
            # Nunca se persiste str(exc): puede contener rutas, SQL o datos
            # internos. Solo el tipo y el request_id para correlacionar.
            error_code = type(exc).__name__
            error_detail = f"{error_code}; request_id={request_id}"
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
