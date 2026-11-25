import bcrypt
import uuid
import logging
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from jose import jwt,  ExpiredSignatureError, JWTError
from fastapi import HTTPException, status

load_dotenv()

logger = logging.getLogger(__name__)

# Longitud minima recomendada para HMAC-SHA256. Se valida en bytes UTF-8.
SECRET_KEY_MIN_BYTES = 32

# Valores exactos que aparecen en ejemplos, compose y documentacion. Nunca
# deben llegar a un entorno real: son publicos.
_PLACEHOLDER_SECRETS = frozenset(
    {
        "reemplazar-por-un-secreto-largo-y-aleatorio",
        "local-dev-only-change-me-32-chars-minimum",
        "change-me",
        "change_me",
        "changeme",
        "default",
        "default-secret",
        "dev",
        "dev-secret",
        "development",
        "development-secret",
        "dummy",
        "example",
        "example-secret",
        "insecure",
        "password",
        "placeholder",
        "secret",
        "secret-key",
        "supersecret",
        "test",
        "test-secret",
        "your-secret",
        "your-secret-key",
        "your_secret_key",
    }
)

# Fragmentos que delatan un secreto de ejemplo aunque el valor exacto cambie.
_PLACEHOLDER_SECRET_PATTERNS = (
    "change-me",
    "change_me",
    "changeme",
    "example-secret",
    "local-dev-only",
    "placeholder",
    "reemplazar",
    "your-secret",
    "your_secret",
)


def validate_secret_key(secret: str | None) -> None:
    """Valida ``SECRET_KEY`` una sola vez, al importar el modulo.

    No se llama por request: cualquier error aqui aborta el arranque, que es
    justo lo que se quiere en produccion. El mensaje nunca incluye el valor.
    """
    if not secret or not secret.strip():
        raise RuntimeError(
            "La variable de entorno SECRET_KEY no está configurada"
        )
    if len(secret.encode("utf-8")) < SECRET_KEY_MIN_BYTES:
        raise RuntimeError(
            "SECRET_KEY debe tener al menos "
            f"{SECRET_KEY_MIN_BYTES} bytes (UTF-8)"
        )
    normalized = secret.strip().lower()
    if normalized in _PLACEHOLDER_SECRETS or any(
        pattern in normalized for pattern in _PLACEHOLDER_SECRET_PATTERNS
    ):
        raise RuntimeError(
            "SECRET_KEY usa un valor placeholder conocido o de ejemplo. "
            "Genera una clave aleatoria de al menos 32 bytes"
        )


# JWT data
SECRET_KEY: str = os.getenv("SECRET_KEY", "")
validate_secret_key(SECRET_KEY)

ALGORITHM = "HS256"
JWT_ISSUER = os.getenv("JWT_ISSUER", "proyectos-facyt-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "proyectos-facyt-frontend")
JWT_ACCESS_TTL_MINUTES = int(os.getenv("JWT_ACCESS_TTL_MINUTES", "60"))


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
