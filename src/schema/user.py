import uuid
from pydantic import BaseModel, ConfigDict, EmailStr

class UserValidation( BaseModel ):
    email: str
    password: str

class UserCreated( BaseModel ):
    name:str
    lastname:str
    email:str
    password:str | None

class EmailRequestUser( BaseModel ):
    email: EmailStr

class GoogleAuthRequest( BaseModel ):
    id_token: str


class UserCreateRequest( BaseModel ):
    # Solo correo y rol. Cualquier otro campo se rechaza (422).
    model_config = ConfigDict( extra="forbid" )

    email: EmailStr
    id_rol: uuid.UUID


class UserUpdateRequest( BaseModel ):
    # Solo rol y estado. El correo y el nombre no se editan aqui.
    model_config = ConfigDict( extra="forbid" )

    id_rol: uuid.UUID
    is_active: bool

