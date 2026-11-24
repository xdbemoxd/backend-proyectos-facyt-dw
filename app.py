import os
from dotenv import load_dotenv

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.router.auth import router_auth
from src.router.user import router_users
from src.router.health import router_health

load_dotenv()

limits_string = os.getenv( "RATE_LIMIT" ) or "100/minute"

limits_list = [l.strip() for l in limits_string.split(",") if l.strip()]

limiter = Limiter( key_func = get_remote_address, default_limits = limits_list  )

environment = os.getenv( "ENVIRONMENT" )

is_production = environment == "production"

app = FastAPI(
    title="API Control de Proyectos FaCyT",
    docs_url = None if is_production else "/docs",
    redoc_url = None if is_production else "/redoc",
    openapi_url = None if is_production else "/openapi.json",
)

app.state.limiter = limiter
app.add_middleware( SlowAPIMiddleware )

app.add_exception_handler( RateLimitExceeded, _rate_limit_exceeded_handler )

if is_production:
    origins_str = os.getenv( "CORS_ORIGINS", "" )
    origins = [origin.strip() for origin in origins_str.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Publico: solo el login.
app.include_router( router_auth, prefix="/api", tags=[ "auth" ] )

# Protegido con JWT (ver dependencies del router).
app.include_router( router_users, prefix="/api", tags=[ "users" ] )

app.include_router( router_health, prefix="", tags=[ "system" ] )

@app.get("/")
def read_root():
    return {"status": "success", "message": "API is up and running"}