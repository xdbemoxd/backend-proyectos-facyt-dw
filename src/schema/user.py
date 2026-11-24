import uuid
from datetime import datetime
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


# ---------------------------------------------------------------------------
# Esquemas de entrada / salida del router de usuarios
# ---------------------------------------------------------------------------

class UserCreate( BaseModel ):
    # Solo correo y rol. Cualquier otro campo se rechaza (422).
    model_config = ConfigDict( extra="forbid" )

    email: EmailStr
    id_rol: uuid.UUID


class UserUpdate( BaseModel ):
    # Solo rol y estado.
    model_config = ConfigDict( extra="forbid" )

    id_rol: uuid.UUID
    is_active: bool


class RoleOut( BaseModel ):
    model_config = ConfigDict( from_attributes=True )

    id: uuid.UUID
    name: str
    description: str | None = None


class UserOut( BaseModel ):
    model_config = ConfigDict( from_attributes=True )

    id: uuid.UUID
    email: str
    name: str | None = None
    lastname: str | None = None
    is_active: bool
    create_at: datetime | None = None
    id_rol: uuid.UUID
    rol: RoleOut | None = None


class CurrentUser( BaseModel ):
    """Usuario autenticado (lo devuelve get_current_user). `rol` es el nombre."""

    id: uuid.UUID
    email: str
    name: str | None = None
    lastname: str | None = None
    is_active: bool
    id_rol: uuid.UUID
    rol: str
    create_at: datetime | None = None


class PaginatedUsers( BaseModel ):
    items: list[UserOut]
    total: int
    limit: int
    offset: int

