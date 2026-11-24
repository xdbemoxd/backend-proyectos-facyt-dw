"""Schemas de inventario: stock, materia prima, lotes y despachos (RF-4, RN-4).

Reglas de entrada:

- ``extra="forbid"`` en todos los bodies: nada de mass assignment de
  ``current_stock``, ``id``, fechas de creacion ni del usuario del despacho.
- El stock **nunca** se recibe por API: lo suman los lotes y lo restan los
  despachos mediante triggers de la base de datos (``trg_lot_add_stock`` y
  ``trg_dispatch_log_subtract_stock``).
- Cantidades y costos usan ``Decimal`` (Numeric(14, 2) en PostgreSQL), no
  ``float``.
- La unidad de una materia prima es inmutable cuando ya tiene movimientos; esa
  regla se valida en el servicio con los lotes/despachos reales.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

# Numeric(14, 2): hasta 12 digitos enteros y 2 decimales.
Money = Annotated[
    Decimal, Field(ge=Decimal("0.00"), le=Decimal("999999999999.99"))
]
Quantity = Annotated[
    Decimal, Field(gt=Decimal("0.00"), le=Decimal("999999999999.99"))
]
MinimumStock = Annotated[
    Decimal, Field(ge=Decimal("0.00"), le=Decimal("999999999999.99"))
]


class ResourceTypeOut(BaseModel):
    """Catalogo ``private.resource_type``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class CatalogVendorOut(BaseModel):
    """Proveedor minimo para selectores (sin datos de contacto)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class InventoryCatalogs(BaseModel):
    """Catalogos necesarios para los formularios de inventario."""

    resource_types: list[ResourceTypeOut]
    vendors: list[CatalogVendorOut]


class StockItemOut(BaseModel):
    """Fila de ``private.v_inventory_stock_status`` con su estado."""

    model_config = ConfigDict(from_attributes=True)

    supplier_item_id: UUID
    resource_id: UUID
    resource_name: str
    resource_type: str
    unit: str
    vendor_name: str
    current_stock: Money
    minimun_stock: Money
    stock_status: str
    is_critical: bool
    # True si el material ya tiene lotes o despachos: bloquea el cambio de
    # unidad en la edicion. Se calcula en el servicio.
    has_movements: bool = False


class PaginatedStock(BaseModel):
    items: list[StockItemOut]
    total: int
    limit: int
    offset: int


class RawMaterialCreate(BaseModel):
    """POST /api/inventory/raw-materials.

    Crea la materia prima y su primer proveedor con stock 0. Si la materia
    prima ya existe (mismo nombre), se reutiliza y solo se agrega el proveedor.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    resource_type_id: int = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)
    vendors_id: UUID
    minimun_stock: MinimumStock


class RawMaterialUpdate(BaseModel):
    """PATCH /api/inventory/raw-materials/{supplier_item_id}.

    ``name``, ``resource_type_id`` y ``unit`` pertenecen al material (afectan a
    todos sus proveedores); ``minimun_stock`` es de este proveedor. El stock no
    se edita.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=255)
    resource_type_id: int | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    minimun_stock: MinimumStock | None = None


class SupplierItemRef(BaseModel):
    """Referencia resuelta del stock (material + proveedor)."""

    id: UUID
    resource_name: str
    vendor_name: str
    unit: str


class LotOut(BaseModel):
    """Lote de entrada (``private.audith_raw_material_supplier``)."""

    id: UUID
    supplier_item: SupplierItemRef
    in_date: datetime
    count_raw_materials_in: Money
    unit_cost: Money
    invoice_reference: str | None = None
    created_at: datetime


class PaginatedLots(BaseModel):
    items: list[LotOut]
    total: int
    limit: int
    offset: int


class LotCreate(BaseModel):
    """POST /api/inventory/lots. El trigger de BD suma el stock."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_raw_materials_supplier: UUID
    in_date: datetime | None = None
    count_raw_materials_in: Quantity
    unit_cost: Money
    invoice_reference: str | None = Field(default=None, max_length=100)

    @field_validator("in_date")
    @classmethod
    def _not_in_the_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        # Tolerancia de 5 minutos por desfase de reloj entre cliente y API.
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("La fecha de ingreso no puede ser futura")
        return value


class DispatchProjectOut(BaseModel):
    id: UUID
    unique_code: str
    name: str


class DispatchUserOut(BaseModel):
    id: UUID
    full_name: str


class DispatchOut(BaseModel):
    """Despacho (``private.dispatch_log``) con las FK resueltas."""

    id: UUID
    supplier_item: SupplierItemRef
    project: DispatchProjectOut
    user: DispatchUserOut
    use_raw_materials: Money
    material_requested_at: datetime
    notes: str | None = None


class PaginatedDispatches(BaseModel):
    items: list[DispatchOut]
    total: int
    limit: int
    offset: int


class DispatchCreate(BaseModel):
    """POST /api/inventory/dispatch.

    El responsable y la fecha/hora salen del token y del servidor: no se
    aceptan en el body. El trigger de BD resta el stock y rechaza negativos.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_raw_materials_suppliers: UUID
    id_project: UUID
    use_raw_materials: Quantity
    notes: str | None = Field(default=None, max_length=1000)
