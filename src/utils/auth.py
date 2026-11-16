import os
import secrets
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.utils.other import verify_token_acces
import string

# Inicializamos el "guardia de seguridad" de FastAPI
security = HTTPBearer()

async def verify_token_access_top( credentials: HTTPAuthorizationCredentials = Depends( security ) ):

    token_limpio = credentials.credentials
    
    payload = verify_token_acces( token_limpio )
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )
    
    return payload

async def verify_token_cron( token_cron: str = Header(...) ):

    load_dotenv()
    real_token_cron = os.getenv( "TOKEN_CRON" )

    if token_cron is None or not secrets.compare_digest(token_cron, real_token_cron):
        print( "invalid token" )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )

    return True

def generate_random_password(length: int = 30) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%^&*-_" for c in pwd)):
            return pwd
