from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware( BaseHTTPMiddleware ):
    """Cabeceras de seguridad en todas las respuestas (es una API JSON)."""

    async def dispatch( self, request: Request, call_next ):
        response = await call_next( request )

        headers = response.headers
        headers.setdefault( "X-Content-Type-Options", "nosniff" )
        headers.setdefault( "X-Frame-Options", "DENY" )
        headers.setdefault( "Referrer-Policy", "no-referrer" )
        headers.setdefault( "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'" )
        headers.setdefault( "Permissions-Policy", "camera=(), microphone=(), geolocation=()" )
        headers.setdefault( "Cross-Origin-Opener-Policy", "same-origin" )
        # Las respuestas llevan datos personales: que no se guarden en cache.
        headers.setdefault( "Cache-Control", "no-store" )

        # HSTS solo si llega por HTTPS (con --proxy-headers detras de un proxy).
        if request.url.scheme == "https":
            headers.setdefault( "Strict-Transport-Security", "max-age=63072000; includeSubDomains" )

        return response
