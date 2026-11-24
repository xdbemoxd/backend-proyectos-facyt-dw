from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserValidation(BaseModel):
    email: str
    password: str


class UserCreated(BaseModel):
    name: str
    lastname: str
    email: str
    password: str | None


class EmailRequestUser(BaseModel):
    email: EmailStr


class GoogleAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_token: str = Field(min_length=1, max_length=8192)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    name: str | None = None
    lastname: str | None = None
    is_active: bool
    id_rol: UUID
    rol: str | None = None
    create_at: datetime | None = None


class CurrentUser(UserOut):
    """Usuario autenticado que devuelve `get_current_user`."""


class UserCreate(BaseModel):
    # `extra="forbid"`: evita mass assignment de campos internos
    # (id, rol, email_verified, etc.) y devuelve 422 en vez de ignorarlos.
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    name: str | None = Field(default=None, max_length=255)
    lastname: str | None = Field(default=None, max_length=255)
    id_rol: UUID
    # Por defecto se pre-registra activo: solo el dueño de la cuenta de Google
    # con este email puede completar el login.
    is_active: bool = True


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    lastname: str | None = Field(default=None, max_length=255)
    id_rol: UUID | None = None
    is_active: bool | None = None


class PaginatedUsers(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int
