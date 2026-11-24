"""Schemas de cierre de periodo y prorrateo de overhead (RF-6, RN-6).

- El reparto nunca se recibe del cliente: se calcula con ``Decimal`` en el
  servicio a partir de los proyectos activos y los despachos reales.
- ``extra="forbid"`` evita mass assignment de ``closed_by``, ``closed_at``,
  montos por proyecto o ids de asignaciones.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TotalCost = Annotated[
    Decimal,
    Field(gt=Decimal("0.00"), le=Decimal("999999999999.99"), decimal_places=2),
]
NonNegativeMoney = Annotated[
    Decimal, Field(ge=Decimal("0.00"), le=Decimal("999999999999.99"))
]

Criterion = Literal["proporcional", "equitativo"]


class OverheadProjectOut(BaseModel):
    id: UUID
    unique_code: str
    name: str


class OverheadUserOut(BaseModel):
    id: UUID
    full_name: str


class OverheadAllocationOut(BaseModel):
    id: UUID
    project: OverheadProjectOut
    consumption: NonNegativeMoney
    amount: NonNegativeMoney


class OverheadPeriodOut(BaseModel):
    id: UUID
    start_date: date
    end_date: date
    criterion: Criterion
    total_cost: TotalCost
    closed_by: OverheadUserOut
    closed_at: datetime
    allocations: list[OverheadAllocationOut]


class PaginatedOverheadPeriods(BaseModel):
    items: list[OverheadPeriodOut]
    total: int
    limit: int
    offset: int


class OverheadAllocationPreviewOut(BaseModel):
    project: OverheadProjectOut
    consumption: NonNegativeMoney
    share: Decimal
    amount: NonNegativeMoney


class OverheadPreviewOut(BaseModel):
    """Reparto calculado sin guardar (GET /api/overhead/preview)."""

    start_date: date
    end_date: date
    criterion: Criterion
    total_cost: TotalCost
    total_consumption: NonNegativeMoney
    allocations: list[OverheadAllocationPreviewOut]


class OverheadPeriodCreate(BaseModel):
    """POST /api/overhead/periods. El reparto lo calcula el servidor."""

    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    criterion: Criterion
    total_cost: TotalCost
