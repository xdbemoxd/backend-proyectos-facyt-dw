"""Schemas de solicitudes de gasto (RF-5, RN-7).

Reglas de entrada:

- ``extra="forbid"`` en todos los bodies: no se aceptan ``id``, ``id_user``,
  ``id_status``, fechas ni estados internos por mass assignment.
- El estado inicial siempre es Borrador y las transiciones solo se piden con el
  ``code`` estable en ``PATCH /expenses/{id}/status``.
- La cantidad usa ``Decimal`` (``Numeric(14, 2)`` en PostgreSQL), nunca float.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Numeric(14, 2): hasta 12 digitos enteros y 2 decimales.
Quantity = Annotated[
    Decimal,
    Field(gt=Decimal("0.00"), le=Decimal("999999999999.99"), decimal_places=2),
]

ExpenseStatusCode = Literal[
    "draft", "pending", "approved", "rejected", "executed"
]
ExpenseTargetStatus = Literal["pending", "approved", "rejected", "executed"]


class ExpenseStatusOut(BaseModel):
    """Catalogo ``private.status_expense_approval``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str


class ExpenseProjectOut(BaseModel):
    id: UUID
    unique_code: str
    name: str


class ExpenseUserOut(BaseModel):
    id: UUID
    full_name: str


class ExpenseSupplierItemOut(BaseModel):
    """Material + proveedor resueltos para mostrar la solicitud."""

    id: UUID
    resource_name: str
    vendor_name: str
    unit: str


class ExpenseOut(BaseModel):
    """Solicitud con las FK resueltas (sin campos internos de la cuenta)."""

    id: UUID
    project: ExpenseProjectOut
    supplier_item: ExpenseSupplierItemOut | None = None
    status: ExpenseStatusOut
    requester: ExpenseUserOut
    description: str
    count_raw_materials: Quantity
    created_at: datetime
    modifiet_at: datetime


class PaginatedExpenses(BaseModel):
    items: list[ExpenseOut]
    total: int
    limit: int
    offset: int


class ExpenseCreate(BaseModel):
    """POST /api/expenses. Nace en Borrador y el solicitante sale del token."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_project: UUID
    id_raw_materials_suppliers: UUID | None = None
    description: str = Field(min_length=5, max_length=255)
    count_raw_materials: Quantity


class ExpenseStatusUpdate(BaseModel):
    """PATCH /api/expenses/{id}/status.

    Solo se acepta el codigo destino: el estado actual y el solicitante no se
    pueden cambiar desde el cliente.
    """

    model_config = ConfigDict(extra="forbid")

    status: ExpenseTargetStatus
