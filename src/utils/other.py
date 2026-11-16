import bcrypt
import uuid
import logging
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from jose import jwt,  ExpiredSignatureError, JWTError
from fastapi import HTTPException, status

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

def generate_jwt_to_frontend( id: uuid.UUID, email : str, rol : str | None ):

    load_dotenv()

    #JWT data
    SECRET_KEY = os.getenv( "SECRET_KEY" ) 
    ALGORITHM = "HS256"

    expire = datetime.now(timezone.utc) + timedelta( hours = 8 );
    
    to_encode = { "sub": str(id), "email": email, "rol": rol, "exp": expire };
    
    encoded_jwt = jwt.encode( to_encode, SECRET_KEY, algorithm = ALGORITHM );
    
    return encoded_jwt

def verify_token_acces( token : str ):

    load_dotenv()

    try:
        payload = jwt.decode(
            token, 
            os.getenv("SECRET_KEY"), 
            algorithms=["HS256"]
        )
        return payload

    except ExpiredSignatureError as e:

        logger.error(f"ExpiredSignatureError: {e}")
       
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )
    
    except JWTError as e:

        logger.error(f"JWTError: {e}")
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error"
        )
