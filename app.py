import json
import os

from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
    request_validation_exception_handler as default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.middleware.audit import AuditMiddleware
from src.router.health import router_health
from src.router.user import router_users

load_dotenv()

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
limits_string = os.getenv("RATE_LIMIT", "100/minute")
limits_list = [limit.strip() for limit in limits_string.split(",") if limit.strip()]
if not limits_list:
    limits_list = ["100/minute"]

limiter = Limiter(key_func=get_remote_address, default_limits=limits_list)

environment = os.getenv("ENVIRONMENT", "development")
is_production = environment.lower() == "production"

# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Sistema de Control de Proyectos y Recursos",
    version="0.1.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)

app.state.limiter = limiter

# El orden importa: el ultimo middleware agregado queda mas afuera.
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AuditMiddleware)

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
    # No guardar `input`: puede contener passwords o tokens.
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
    return await default_validation_exception_handler(request, exc)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
app.include_router(router_users, prefix="/api", tags=["users"])
app.include_router(router_health, prefix="", tags=["system"])


@app.get("/")
def read_root():
    return {"status": "success", "message": "API is up and running"}
