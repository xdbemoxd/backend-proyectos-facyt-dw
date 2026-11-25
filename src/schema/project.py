"""Schemas de proyectos y priorizacion (RF-2, RF-3, RN-2, RN-3).

Reglas de entrada:

- ``extra="forbid"`` en todos los bodies: nada de mass assignment de campos
  internos (``id``, ``unique_code``, ``id_user``, ``id_priority`` en la
  edicion general, etc.).
- La prioridad **solo** cambia por ``PATCH /api/projects/{id}/priority`` con
  justificacion obligatoria (RN-3); no se acepta en la edicion general.
- La edicion general exige ``expected_updated_at`` para el bloqueo optimista.
- Progreso y ejecucion presupuestaria son porcentajes 0-100.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StatusProjectOut(BaseModel):
    """Catalogo ``private.status_project``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class PriorityProjectOut(BaseModel):
    """Catalogo ``private.priority_project``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class ProjectUserOut(BaseModel):
    """Coordinador responsable, sin exponer datos internos de la cuenta."""

    id: UUID
    full_name: str
    email: EmailStr


class ProjectOut(BaseModel):
    """Proyecto con las FK resueltas (misma forma que consume el frontend)."""

    id: UUID
    unique_code: str
    name: str
    description: str | None = None
    status: StatusProjectOut
    priority: PriorityProjectOut
    progress: float
    budget_execution: float
    user: ProjectUserOut
    create_at: datetime
    updated_at: datetime


class PaginatedProjects(BaseModel):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int


class ProjectCatalogs(BaseModel):
    """Catalogos necesarios para editar y priorizar con IDs reales."""

    statuses: list[StatusProjectOut]
    priorities: list[PriorityProjectOut]


class ProjectUpdate(BaseModel):
    """PATCH /api/projects/{id}.

    ``expected_updated_at`` es obligatorio: si no coincide con
    ``Project.updated_at`` el servicio responde ``409`` (optimistic locking).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=255)
    id_status: int | None = Field(default=None, gt=0)
    progress: float | None = Field(default=None, ge=0, le=100)
    budget_execution: float | None = Field(default=None, ge=0, le=100)
    expected_updated_at: datetime


class PriorityChangeIn(BaseModel):
    """PATCH /api/projects/{id}/priority (RN-3).

    El espacio se recorta antes de validar longitudes: una justificacion de
    solo espacios no puede pasar el minimo de 5 caracteres.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_priority: int = Field(gt=0)
    explaining: str = Field(min_length=5, max_length=2000)


class PriorityChangeProjectOut(BaseModel):
    id: UUID
    unique_code: str
    name: str


class PriorityChangeUserOut(BaseModel):
    id: UUID
    full_name: str


class PriorityChangeOut(BaseModel):
    """Fila de ``private.audith_project`` con la prioridad anterior resuelta."""

    id: UUID
    project: PriorityChangeProjectOut
    from_priority: str | None = None
    to_priority: str
    user: PriorityChangeUserOut
    explaining: str
    created_at: datetime


class PaginatedPriorityLog(BaseModel):
    items: list[PriorityChangeOut]
    total: int
    limit: int
    offset: int


class ProjectCreate(BaseModel):
    """POST /api/projects.

    - ``area_code``: siglas del area (2 a 6 letras, p. ej. ``QUI``). El backend
      arma el codigo unico ``{AREA}-{AÑO}-{NNN}``; el cliente nunca lo envia.
    - ``id_user``: coordinador responsable. Obligatorio para el administrador;
      un coordinador siempre queda como responsable de lo que crea.
    - Estatus inicial ``Propuesta`` y prioridad inicial ``Media``: la prioridad
      solo cambia luego por el endpoint auditado (RN-3).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    area_code: str = Field(min_length=2, max_length=6, pattern=r"^[A-Za-z]{2,6}$")
    name: str = Field(min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=255)
    id_user: UUID | None = None


class CoordinatorOut(BaseModel):
    """Coordinador activo que puede ser responsable de un proyecto."""

    id: UUID
    full_name: str
    email: EmailStr

