import bcrypt
import uuid
import logging
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from jose import jwt,  ExpiredSignatureError, JWTError
from fastapi import HTTPException, status

load_dotenv()

#JWT data
SECRET_KEY: str = os.environ["SECRET_KEY"]

if not SECRET_KEY:
    raise RuntimeError("La variable de entorno SECRET_KEY no está configurada")

ALGORITHM = "HS256"
JWT_ISSUER = os.getenv("JWT_ISSUER", "proyectos-facyt-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "proyectos-facyt-frontend")
JWT_ACCESS_TTL_MINUTES = int(os.getenv("JWT_ACCESS_TTL_MINUTES", "60"))


logger = logging.getLogger(__name__)

def encode( string : str ):

    string_bytes = string.encode( 'utf-8' );

    salt = bcrypt.gensalt(); 
    hashed_string = bcrypt.hashpw( string_bytes, salt );

    string_decode = hashed_string.decode( 'utf-8' )

    return string_decode;

def decode( string : str, string_encode : str ):

    string_copy =  string.encode( 'utf-8' );
    string_copy_2 = string_encode.encode( 'utf-8' )

    return bcrypt.checkpw( string_copy, string_copy_2 );

def get_n_caracter( text: str, n: int ) -> str:
   
    return text[:n]

def generate_jwt_to_frontend(
    id: uuid.UUID,
    email: str,
    rol: str | None,
    token_version: int = 0,
) -> str:
    now = datetime.now(timezone.utc)
    to_encode = {
        "sub": str(id),
        "email": email,
        "rol": rol,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=JWT_ACCESS_TTL_MINUTES),
        "jti": str(uuid.uuid4()),
        "ver": int(token_version),
    }

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token_acces(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except ExpiredSignatureError as e:
        logger.error("Token expirado (%s)", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error",
        )
    except JWTError as e:
        logger.error("JWT invalido (%s)", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error",
        )
