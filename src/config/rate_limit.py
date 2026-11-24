import os
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()

_limits = os.getenv( "RATE_LIMIT" ) or "100/minute"
DEFAULT_LIMITS = [ l.strip() for l in _limits.split( "," ) if l.strip() ]

# Limite propio del login (evita fuerza bruta). Ej: LOGIN_RATE_LIMIT=10/minute
LOGIN_RATE_LIMIT = os.getenv( "LOGIN_RATE_LIMIT" ) or "10/minute"

# Un solo limiter para toda la app (app.py lo registra en app.state).
limiter = Limiter( key_func=get_remote_address, default_limits=DEFAULT_LIMITS )
