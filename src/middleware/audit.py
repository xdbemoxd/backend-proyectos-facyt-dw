import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.utils.audit import build_http_audit_data, should_audit_request, write_http_audit


def _request_id( request: Request ) -> uuid.UUID:
    # Respeta el X-Request-ID del cliente solo si es un UUID valido.
    raw = request.headers.get( "x-request-id" )
    try:
        return uuid.UUID( raw ) if raw else uuid.uuid4()
    except ValueError:
        return uuid.uuid4()


class AuditMiddleware( BaseHTTPMiddleware ):
    """
    Una fila HTTP en audit_log por cada request relevante (ver
    should_audit_request) y X-Request-ID en la respuesta. Los servicios
    agregan sus eventos de negocio con record_audit usando el mismo request_id.
    Un fallo al escribir la auditoria nunca rompe la respuesta.
    """

    async def dispatch( self, request: Request, call_next ):
        request.state.request_id = _request_id( request )
        started = time.perf_counter()

        try:
            response = await call_next( request )
        except Exception as exc:
            elapsed = int( ( time.perf_counter() - started ) * 1000 )
            if should_audit_request( request.method, request.url.path, 500 ):
                await write_http_audit( build_http_audit_data(
                    request,
                    status_code=500,
                    duration_ms=elapsed,
                    error_code="UNHANDLED_EXCEPTION",
                    error_detail=type( exc ).__name__,
                ) )
            raise

        elapsed = int( ( time.perf_counter() - started ) * 1000 )
        response.headers[ "X-Request-ID" ] = str( request.state.request_id )

        if should_audit_request( request.method, request.url.path, response.status_code ):
            await write_http_audit( build_http_audit_data(
                request,
                status_code=response.status_code,
                duration_ms=elapsed,
            ) )

        return response
