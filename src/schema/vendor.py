"""Schemas de proveedores (RF-4, RNF-7).

- ``extra="forbid"`` en todos los bodies: no se aceptan ``id``, ``create_at``
  ni ``updated_at`` enviados por el cliente.
- La edicion usa bloqueo optimista con ``expected_updated_at`` (mismo patron
  que ``Project``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class VendorOut(BaseModel):
    """Proveedor tal como vive en ``private.vendors``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    create_at: datetime
    updated_at: datetime


class VendorCreate(BaseModel):
    """POST /api/vendors."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=255)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=255)


class VendorUpdate(BaseModel):
    """PATCH /api/vendors/{id}.

    Todos los campos son opcionales, pero al menos uno debe venir y
    ``name`` no puede ser ``None`` si se envia.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=255)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=255)
    expected_updated_at: datetime


class PaginatedVendors(BaseModel):
    items: list[VendorOut]
    total: int
    limit: int
    offset: int
