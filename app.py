import json
import os

from dotenv import load_dotenv

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config.rate_limit import limiter
from src.middleware.audit import AuditMiddleware
from src.middleware.security_headers import SecurityHeadersMiddleware
from src.router.audit import router_audit
from src.router.health import router_health
from src.router.project import router_projects
from src.router.user import router_users

load_dotenv()

environment = os.getenv("ENVIRONMENT", "development")
is_production = environment.lower() == "production"
docs_enabled = (
    not is_production
    and os.getenv("ENABLE_DOCS", "false").lower() in ("1", "true", "yes")
)

# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Sistema de Control de Proyectos y Recursos",
    version="0.1.0",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

app.state.limiter = limiter

# El orden importa: el ultimo middleware agregado queda mas afuera.
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
origins_str = os.getenv("CORS_ORIGINS", "")
origins = [origin.strip() for origin in origins_str.split(",") if origin.strip()]

if not origins and not is_production:
    # Origenes locales por defecto para desarrollo.
    origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Handlers: dejan el detalle del error disponible para audit_log
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request.state.error_detail = str(exc.detail)[:1000]
    return await default_http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Solo `loc`, `msg` y `type`: ni `input` ni `ctx` (pueden contener tokens,
    # passwords o valores reflejados). SEC-211.
    safe_errors = [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg"),
            "type": error.get("type"),
        }
        for error in exc.errors()
    ]
    request.state.error_detail = json.dumps(
        safe_errors, ensure_ascii=False, default=str
    )[:1000]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": safe_errors},
    )


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
app.include_router(router_users, prefix="/api", tags=["users"])
app.include_router(router_projects, prefix="/api")
app.include_router(router_audit, prefix="/api", tags=["audit"])
app.include_router(router_health, prefix="", tags=["system"])


# HEAD: Render y otros balanceadores comprueban el servicio con HEAD /.
@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "success", "message": "API is up and running"}
