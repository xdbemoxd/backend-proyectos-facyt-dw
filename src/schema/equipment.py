"""Schemas de equipos, unidades y reservas (RF-5 / RN-5).

Reglas de entrada:

- ``extra="forbid"`` en todos los bodies: no se aceptan ``id``, fechas de
  creacion, ``updated_at`` ni el usuario de la reserva (sale del token).
- La unidad fisica es ``private.equipament_supplier``; ``equipment`` es el
  catalogo/tipo. El ``id`` de las rutas de edicion es el de la **unidad**.
- El serial y los campos opcionales vacios se normalizan a ``NULL``.
- Una reserva exige ``end_date > reservation_date``. Las fechas sin zona se
  interpretan como UTC. El solape no se valida aqui ni en el servicio: lo
  decide el trigger ``trg_reservation_no_overlap`` (RN-5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StatusEquipmentOut(BaseModel):
    """Catalogo ``private.status_equipament``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class EquipmentVendorOut(BaseModel):
    """Proveedor minimo para selectores (sin datos de contacto)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class EquipmentRefOut(BaseModel):
    """Catalogo ``private.equipment`` (tipo de equipo)."""

    id: UUID
    name: str
    description: str | None = None


class EquipmentUnitOut(BaseModel):
    """Unidad fisica con equipo, proveedor y estado resueltos."""

    id: UUID
    equipment: EquipmentRefOut
    vendor: EquipmentVendorOut
    status: StatusEquipmentOut
    model: str | None = None
    serial_number: str | None = None
    create_at: datetime
    updated_at: datetime
    # Reservas cuya hora de fin aun no pasa. Un 409 al pasar a mantenimiento
    # se explica con este numero; la autoridad sigue siendo el servicio.
    pending_reservations: int = 0


class PaginatedEquipmentUnits(BaseModel):
    items: list[EquipmentUnitOut]
    total: int
    limit: int
    offset: int


class EquipmentCatalogs(BaseModel):
    """Catalogos necesarios para crear/editar equipos y reservar."""

    statuses: list[StatusEquipmentOut]
    vendors: list[EquipmentVendorOut]


class EquipmentCreate(BaseModel):
    """POST /api/equipment: crea el tipo de equipo si no existe y su unidad."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    id_vendors: UUID
    id_status: int = Field(gt=0)
    model: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)

    @field_validator("description", "model", "serial_number", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class EquipmentUpdate(BaseModel):
    """PATCH /api/equipment/{unit_id}.

    ``name`` y ``description`` pertenecen al equipo (afectan a todas sus
    unidades); el resto es de esta unidad. El proveedor no se cambia.
    ``expected_updated_at`` es obligatorio (bloqueo optimista).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    id_status: int | None = Field(default=None, gt=0)
    model: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)
    expected_updated_at: datetime

    @field_validator("description", "model", "serial_number", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ReservationUnitOut(BaseModel):
    id: UUID
    equipment_name: str
    serial_number: str | None = None


class ReservationProjectOut(BaseModel):
    id: UUID
    unique_code: str
    name: str


class ReservationUserOut(BaseModel):
    id: UUID
    full_name: str


class ReservationOut(BaseModel):
    """Reserva con las FK resueltas (misma forma que consume el frontend)."""

    id: UUID
    unit: ReservationUnitOut
    project: ReservationProjectOut
    user: ReservationUserOut
    reservation_date: datetime
    end_date: datetime
    purpose: str | None = None
    created_date: datetime


class PaginatedReservations(BaseModel):
    items: list[ReservationOut]
    total: int
    limit: int
    offset: int


class ReservationCreate(BaseModel):
    """POST /api/equipment/reservations.

    El usuario de la reserva sale del token. El trigger de BD rechaza el
    solape de la misma unidad (RN-5); aqui solo se valida el rango.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_equipament_supplier: UUID
    id_project: UUID
    reservation_date: datetime
    end_date: datetime
    purpose: str | None = Field(default=None, max_length=2000)

    @field_validator("reservation_date", "end_date")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("purpose", mode="before")
    @classmethod
    def _blank_purpose(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def _end_after_start(self) -> "ReservationCreate":
        if self.end_date <= self.reservation_date:
            raise ValueError("La fecha de fin debe ser posterior al inicio")
        return self
