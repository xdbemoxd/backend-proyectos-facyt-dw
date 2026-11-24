"""Filtros compartidos por las exportaciones de reportes (RF-7, RNF-3).

Los reportes son de solo lectura: no crean ni modifican datos de negocio. Los
filtros se reciben por query string y se validan aquí antes de tocar la base:

- ``from_date`` / ``to_date``: rango inclusivo de fechas (ISO ``YYYY-MM-DD``).
- ``project_id``: UUID de un proyecto; el coordinador solo puede usar los suyos.
- ``status``: código de estado de gasto o nombre de estado de equipo, según el
  reporte. El servicio valida el valor concreto de cada dominio.

``extra="forbid"`` no aplica a query params, pero el modelo deja el contrato
explícito y limita longitudes para evitar entradas arbitrarias.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReportType = Literal["expenses", "overhead", "equipment"]
ReportFormat = Literal["xlsx", "pdf"]

REPORT_TITLES: dict[str, str] = {
    "expenses": "Análisis de gastos",
    "overhead": "Resumen de prorrateo",
    "equipment": "Hoja de vida y uso de equipos",
}


class ReportFilters(BaseModel):
    """Filtros opcionales aceptados por los seis endpoints de reportes."""

    model_config = ConfigDict(extra="forbid")

    from_date: date | None = None
    to_date: date | None = None
    project_id: UUID | None = None
    status: str | None = Field(default=None, max_length=50)

    @field_validator("status", mode="before")
    @classmethod
    def _blank_status(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def _validate_range(self) -> "ReportFilters":
        if (
            self.from_date is not None
            and self.to_date is not None
            and self.from_date > self.to_date
        ):
            raise ValueError(
                "La fecha de inicio no puede ser posterior a la de fin"
            )
        return self

    def describe(self) -> str:
        """Texto legible para la cabecera del PDF/XLSX (sin datos sensibles)."""
        parts: list[str] = []
        if self.from_date is not None:
            parts.append(f"desde {self.from_date.isoformat()}")
        if self.to_date is not None:
            parts.append(f"hasta {self.to_date.isoformat()}")
        if self.project_id is not None:
            parts.append(f"proyecto {self.project_id}")
        if self.status is not None:
            parts.append(f"estado {self.status}")
        return ", ".join(parts) if parts else "sin filtros"

    def for_audit(self) -> dict[str, str | None]:
        """Representación serializable y sin PII para ``audit_log``."""
        return {
            "from_date": (
                self.from_date.isoformat() if self.from_date else None
            ),
            "to_date": self.to_date.isoformat() if self.to_date else None,
            "project_id": str(self.project_id) if self.project_id else None,
            "status": self.status,
        }
