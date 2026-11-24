from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PageViewIn(BaseModel):
    path: str = Field(min_length=1, max_length=300, pattern=r"^/[A-Za-z0-9/_-]*$")


UiEntity = Literal[
    "Project",
    "Vendor",
    "RawMaterial",
    "Lot",
    "Dispatch",
    "Equipment",
    "Reservation",
    "Expense",
    "OverheadPeriod",
]

UiAction = Literal[
    "create",
    "update",
    "delete",
    "update_priority",
    "update_status",
    "register_lot",
    "dispatch",
    "close_period",
    "reserve",
]


class UiEventIn(BaseModel):
    entity: UiEntity
    action: UiAction
    summary: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=300, pattern=r"^/dashboard[A-Za-z0-9/_-]*$")


class AuditUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str | None = None
    lastname: str | None = None


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    timestamp: datetime
    request_id: UUID | None = None
    user_id: UUID | None = None
    user_email: str | None = None
    user: AuditUserOut | None = None
    ip_address: str
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    user_agent: str | None = None
    request_headers: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None
    duration_ms: int | None = None
    entity: str
    action: str
    entity_id: str | None = None
    outcome: str
    error_code: str | None = None
    error_detail: str | None = None
    before_data: Any | None = None
    after_data: Any | None = None


class PaginatedAudit(BaseModel):
    items: list[AuditLogOut]
    total: int
    limit: int
    offset: int
    counts: dict[str, int] = {}
