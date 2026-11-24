"""Generación de reportes exportables en XLSX y PDF (RF-7, RNF-3).

Diseño:

- ``build_document`` consulta la base con el alcance del usuario y construye un
  ``ReportDocument`` independiente del formato. Los datos se leen en modo
  estrictamente de solo lectura.
- ``render_document`` convierte ese documento a XLSX (openpyxl) o PDF
  (reportlab). Así la generación se prueba sin base de datos.
- ``export_report`` orquesta lectura + render + auditoría append-only.

Permisos:

- Gastos y equipos: los tres roles; el coordinador solo ve sus proyectos.
- Prorrateo: solo Administrador y Aprobador (el router responde 403 al
  coordinador).

Límites: máximo 5000 registros por reporte y 500 períodos de overhead. Si se
supera, se responde 422 pidiendo refinar los filtros; nunca se genera un archivo
gigante sin control.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Literal
from uuid import UUID
from xml.sax.saxutils import escape

from fastapi import HTTPException, Request, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.model.equipment import (
    EquipamentSupplierModel,
    EquipmentModel,
    ReservationEquipamentModel,
    StatusEquipamentModel,
)
from src.model.expense import (
    ExpenseApprovalModel,
    StatusExpenseApprovalModel,
)
from src.model.inventory import RawMaterialModel, RawMaterialSupplierModel
from src.model.overhead import OverheadAllocationModel, OverheadPeriodModel
from src.model.project import ProjectModel
from src.model.user import UserModel
from src.model.vendor import VendorModel
from src.schema.report import ReportFilters, ReportFormat, ReportType
from src.schema.user import CurrentUser
from src.service.expense import STATUS_CODES as EXPENSE_STATUS_CODES
from src.utils.audit import record_audit
from src.utils.roles import COORDINATOR_ROLE

logger = logging.getLogger(__name__)

MAX_REPORT_RECORDS = 5000
MAX_OVERHEAD_PERIODS = 500

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
PDF_MEDIA_TYPE = "application/pdf"

_EXPENSE_STATUS_LABELS: dict[str, str] = {
    "draft": "Borradores",
    "pending": "Pendientes",
    "approved": "Aprobadas",
    "rejected": "Rechazadas",
    "executed": "Ejecutadas",
}
_EXPENSE_STATUS_ORDER = ("pending", "approved", "executed", "rejected", "draft")
_CRITERION_LABELS = {
    "proporcional": "Proporcional",
    "equitativo": "Equitativo",
}
_REPORT_BASENAMES = {
    "expenses": "gastos",
    "overhead": "prorrateo",
    "equipment": "equipos",
}
_REPORT_LABELS = {
    "expenses": "Análisis de gastos",
    "overhead": "Resumen de prorrateo",
    "equipment": "Hoja de vida y uso de equipos",
}


# ---------------------------------------------------------------------------
# Modelo de documento independiente del render
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportColumn:
    key: str
    header: str
    width: int = 18
    kind: Literal[
        "text", "money", "quantity", "integer", "date", "datetime"
    ] = "text"


@dataclass
class ReportTable:
    sheet_name: str
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]
    totals: list[tuple[str, str]] = field(default_factory=list)
    note: str | None = None


@dataclass
class ReportDocument:
    report_type: ReportType
    title: str
    generated_at: datetime
    generated_by: str
    filters_text: str
    table: ReportTable


@dataclass(frozen=True)
class RenderedReport:
    content: bytes
    media_type: str
    filename: str


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _full_name(
    name: str | None, lastname: str | None, email: str | None = None
) -> str:
    parts = [part for part in (name, lastname) if part]
    return " ".join(parts) or email or "Sin responsable"


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _day_after(value: date) -> datetime:
    return datetime.combine(
        value + timedelta(days=1), time.min, tzinfo=timezone.utc
    )


def _limit_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "El reporte supera el máximo de "
            f"{MAX_REPORT_RECORDS} registros. Refina los filtros"
        ),
    )


def _timestamp(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m-%d %H:%M UTC")


def _money_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _hours_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


# ---------------------------------------------------------------------------
# Totales puros (probados sin base de datos)
# ---------------------------------------------------------------------------


def expense_status_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Conteo por estado + total + cantidad total solicitada."""
    counts: dict[str, Any] = {code: 0 for code in _EXPENSE_STATUS_LABELS}
    quantity = Decimal("0.00")
    for row in rows:
        code = str(row.get("status_code") or "")
        if code in counts:
            counts[code] += 1
        quantity += _dec(row.get("quantity"))
    counts["total"] = len(rows)
    counts["quantity_total"] = quantity
    return counts


def overhead_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrupa por período y verifica que la suma de asignaciones cuadre.

    Devuelve ``difference`` como ``total de períodos - suma asignada``. El
    valor esperado es ``0.00``; un valor distinto señala un descuadre de datos
    y se muestra en el reporte, sin ocultarlo.
    """
    period_totals: dict[str, Decimal] = {}
    allocated = Decimal("0.00")
    for row in rows:
        period_id = str(row.get("period_id") or "")
        period_totals[period_id] = _dec(row.get("period_total"))
        allocated += _dec(row.get("amount"))
    total = sum(period_totals.values(), Decimal("0.00"))
    return {
        "periods": len(period_totals),
        "total": total,
        "allocated": allocated,
        "difference": total - allocated,
    }


def equipment_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    units = {str(row["unit_id"]) for row in rows if row.get("unit_id")}
    projects = {
        str(row["project_id"]) for row in rows if row.get("project_id")
    }
    hours = sum((_dec(row.get("hours")) for row in rows), Decimal("0.00"))
    return {
        "reservations": len(rows),
        "units": len(units),
        "projects": len(projects),
        "hours": hours,
    }


# ---------------------------------------------------------------------------
# Consultas con alcance por rol
# ---------------------------------------------------------------------------


async def _validate_project_scope(
    db: AsyncSession, actor: CurrentUser, project_id: UUID | None
) -> None:
    """El coordinador solo puede filtrar por un proyecto propio (anti-IDOR)."""
    if project_id is None or actor.rol != COORDINATOR_ROLE:
        return
    owner_id = await db.scalar(
        select(ProjectModel.id_user).where(ProjectModel.id == project_id)
    )
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proyecto no encontrado",
        )
    if owner_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )


def _expense_conditions(
    actor: CurrentUser, filters: ReportFilters
) -> list:
    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)
    if filters.project_id is not None:
        conditions.append(
            ExpenseApprovalModel.id_project == filters.project_id
        )
    if filters.status is not None:
        code = filters.status.strip().lower()
        if code not in EXPENSE_STATUS_CODES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Estado de solicitud inválido",
            )
        conditions.append(StatusExpenseApprovalModel.code == code)
    if filters.from_date is not None:
        conditions.append(
            ExpenseApprovalModel.created_at >= _day_start(filters.from_date)
        )
    if filters.to_date is not None:
        conditions.append(
            ExpenseApprovalModel.created_at < _day_after(filters.to_date)
        )
    return conditions


async def fetch_expense_rows(
    db: AsyncSession, actor: CurrentUser, filters: ReportFilters
) -> list[dict[str, Any]]:
    """Solicitudes de gasto con proyecto, material, estado y solicitante."""
    await _validate_project_scope(db, actor, filters.project_id)

    stmt = (
        select(
            ExpenseApprovalModel.id,
            ExpenseApprovalModel.description,
            ExpenseApprovalModel.count_raw_materials,
            ExpenseApprovalModel.created_at,
            ExpenseApprovalModel.modifiet_at,
            ProjectModel.unique_code.label("project_code"),
            ProjectModel.name.label("project_name"),
            StatusExpenseApprovalModel.name.label("status_name"),
            StatusExpenseApprovalModel.code.label("status_code"),
            UserModel.name,
            UserModel.lastname,
            UserModel.email,
            RawMaterialModel.name.label("material_name"),
            RawMaterialModel.unit.label("unit"),
        )
        .join(
            ProjectModel,
            ProjectModel.id == ExpenseApprovalModel.id_project,
        )
        .join(
            StatusExpenseApprovalModel,
            StatusExpenseApprovalModel.id == ExpenseApprovalModel.id_status,
        )
        .join(UserModel, UserModel.id == ExpenseApprovalModel.id_user)
        .outerjoin(
            RawMaterialSupplierModel,
            RawMaterialSupplierModel.id
            == ExpenseApprovalModel.id_raw_materials_suppliers,
        )
        .outerjoin(
            RawMaterialModel,
            RawMaterialModel.id_inventroy
            == RawMaterialSupplierModel.raw_materials_id,
        )
    )

    conditions = _expense_conditions(actor, filters)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(
        ExpenseApprovalModel.created_at.desc(),
        ExpenseApprovalModel.id.desc(),
    ).limit(MAX_REPORT_RECORDS + 1)

    result = (await db.execute(stmt)).all()
    if len(result) > MAX_REPORT_RECORDS:
        raise _limit_error()

    return [
        {
            "id": str(row.id),
            "project_code": row.project_code,
            "project_name": row.project_name,
            "description": row.description,
            "material": row.material_name or "Sin material",
            "quantity": _dec(row.count_raw_materials),
            "unit": row.unit or "",
            "status": row.status_name,
            "status_code": row.status_code,
            "requester": _full_name(row.name, row.lastname, row.email),
            "created_at": row.created_at,
            "updated_at": row.modifiet_at,
        }
        for row in result
    ]


async def fetch_overhead_rows(
    db: AsyncSession, actor: CurrentUser, filters: ReportFilters
) -> list[dict[str, Any]]:
    """Períodos cerrados y sus asignaciones, con el reparto completo."""
    if filters.status is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El reporte de prorrateo no admite el filtro de estado",
        )
    await _validate_project_scope(db, actor, filters.project_id)

    stmt = select(OverheadPeriodModel).options(
        selectinload(OverheadPeriodModel.allocations).selectinload(
            OverheadAllocationModel.project
        )
    )
    conditions = []
    if filters.from_date is not None:
        conditions.append(
            OverheadPeriodModel.end_date >= filters.from_date
        )
    if filters.to_date is not None:
        conditions.append(
            OverheadPeriodModel.start_date <= filters.to_date
        )
    if filters.project_id is not None:
        conditions.append(
            OverheadPeriodModel.allocations.any(
                OverheadAllocationModel.id_project == filters.project_id
            )
        )
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(
        OverheadPeriodModel.start_date.desc(),
        OverheadPeriodModel.closed_at.desc(),
    ).limit(MAX_OVERHEAD_PERIODS + 1)

    periods = (await db.execute(stmt)).scalars().all()
    if len(periods) > MAX_OVERHEAD_PERIODS:
        raise _limit_error()

    rows: list[dict[str, Any]] = []
    for period in periods:
        period_label = (
            f"{period.start_date.isoformat()} a {period.end_date.isoformat()}"
        )
        allocations = sorted(
            (item for item in period.allocations if item.project is not None),
            key=lambda item: item.project.unique_code,
        )
        for allocation in allocations:
            rows.append(
                {
                    "period_id": str(period.id),
                    "period": period_label,
                    "criterion": _CRITERION_LABELS.get(
                        period.criterion, period.criterion
                    ),
                    "period_total": _dec(period.total_cost),
                    "project_code": allocation.project.unique_code,
                    "project_name": allocation.project.name,
                    "consumption": _dec(allocation.consumption),
                    "amount": _dec(allocation.amount),
                }
            )
            if len(rows) > MAX_REPORT_RECORDS:
                raise _limit_error()
    return rows


async def _equipment_status_id(
    db: AsyncSession, status_name: str
) -> int:
    status_id = await db.scalar(
        select(StatusEquipamentModel.id)
        .where(
            func.lower(StatusEquipamentModel.name)
            == status_name.strip().lower()
        )
        .limit(1)
    )
    if status_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Estado de equipo inválido",
        )
    return int(status_id)


async def fetch_equipment_rows(
    db: AsyncSession, actor: CurrentUser, filters: ReportFilters
) -> list[dict[str, Any]]:
    """Reservas/uso de equipos con su unidad, proveedor, proyecto y usuario."""
    await _validate_project_scope(db, actor, filters.project_id)

    stmt = (
        select(
            ReservationEquipamentModel.id,
            ReservationEquipamentModel.reservation_date,
            ReservationEquipamentModel.end_date,
            ReservationEquipamentModel.purpose,
            EquipamentSupplierModel.id.label("unit_id"),
            EquipamentSupplierModel.model,
            EquipamentSupplierModel.serial_number,
            EquipmentModel.name.label("equipment_name"),
            VendorModel.name.label("vendor_name"),
            StatusEquipamentModel.name.label("status_name"),
            ProjectModel.id.label("project_id"),
            ProjectModel.unique_code.label("project_code"),
            ProjectModel.name.label("project_name"),
            UserModel.name,
            UserModel.lastname,
            UserModel.email,
        )
        .join(
            EquipamentSupplierModel,
            EquipamentSupplierModel.id
            == ReservationEquipamentModel.id_equipament_supplier,
        )
        .join(
            EquipmentModel,
            EquipmentModel.id == EquipamentSupplierModel.id_equipament,
        )
        .join(
            VendorModel, VendorModel.id == EquipamentSupplierModel.id_vendors
        )
        .join(
            StatusEquipamentModel,
            StatusEquipamentModel.id == EquipamentSupplierModel.id_status,
        )
        .join(
            ProjectModel,
            ProjectModel.id == ReservationEquipamentModel.id_project,
        )
        .join(
            UserModel, UserModel.id == ReservationEquipamentModel.id_user
        )
    )

    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)
    if filters.project_id is not None:
        conditions.append(
            ReservationEquipamentModel.id_project == filters.project_id
        )
    if filters.status is not None:
        status_id = await _equipment_status_id(db, filters.status)
        conditions.append(EquipamentSupplierModel.id_status == status_id)
    if filters.from_date is not None:
        conditions.append(
            ReservationEquipamentModel.end_date
            >= _day_start(filters.from_date)
        )
    if filters.to_date is not None:
        conditions.append(
            ReservationEquipamentModel.reservation_date
            < _day_after(filters.to_date)
        )
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(
        ReservationEquipamentModel.reservation_date.desc(),
        ReservationEquipamentModel.id.desc(),
    ).limit(MAX_REPORT_RECORDS + 1)

    result = (await db.execute(stmt)).all()
    if len(result) > MAX_REPORT_RECORDS:
        raise _limit_error()

    rows: list[dict[str, Any]] = []
    for row in result:
        start = _utc(row.reservation_date)
        end = _utc(row.end_date)
        hours = Decimal(
            str(max(0.0, (end - start).total_seconds() / 3600))
        ).quantize(Decimal("0.01"))
        rows.append(
            {
                "id": str(row.id),
                "unit_id": str(row.unit_id),
                "equipment": row.equipment_name,
                "model": row.model or "Sin modelo",
                "serial": row.serial_number or "Sin serial",
                "vendor": row.vendor_name,
                "status": row.status_name,
                "project_id": str(row.project_id),
                "project_code": row.project_code,
                "project_name": row.project_name,
                "user": _full_name(row.name, row.lastname, row.email),
                "start": start,
                "end": end,
                "hours": hours,
                "purpose": row.purpose or "Sin propósito",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Construcción del documento
# ---------------------------------------------------------------------------


def _expense_table(rows: list[dict[str, Any]]) -> ReportTable:
    totals = expense_status_totals(rows)
    total_items = [
        ("Solicitudes", str(totals["total"])),
        ("Cantidad total", _money_text(totals["quantity_total"])),
    ]
    total_items.extend(
        (
            _EXPENSE_STATUS_LABELS[code],
            str(totals[code]),
        )
        for code in _EXPENSE_STATUS_ORDER
    )
    return ReportTable(
        sheet_name="Gastos",
        columns=[
            ReportColumn("project_code", "Código de proyecto", 18),
            ReportColumn("project_name", "Proyecto", 26),
            ReportColumn("description", "Descripción", 32),
            ReportColumn("material", "Material", 22),
            ReportColumn("quantity", "Cantidad", 11, "quantity"),
            ReportColumn("unit", "Unidad", 9),
            ReportColumn("status", "Estado", 15),
            ReportColumn("requester", "Solicitante", 22),
            ReportColumn("created_at", "Creada", 18, "datetime"),
            ReportColumn("updated_at", "Actualizada", 18, "datetime"),
        ],
        rows=rows,
        totals=total_items,
    )


def _overhead_table(rows: list[dict[str, Any]]) -> ReportTable:
    totals = overhead_totals(rows)
    difference = totals["difference"]
    note = None
    if difference != Decimal("0.00"):
        note = (
            "Atención: la suma de asignaciones no coincide con el total de "
            f"los períodos (diferencia {_money_text(difference)})."
        )
    return ReportTable(
        sheet_name="Prorrateo",
        columns=[
            ReportColumn("period", "Período", 24),
            ReportColumn("criterion", "Criterio", 14),
            ReportColumn("period_total", "Total período", 15, "money"),
            ReportColumn("project_code", "Código de proyecto", 18),
            ReportColumn("project_name", "Proyecto", 26),
            ReportColumn("consumption", "Consumo", 14, "quantity"),
            ReportColumn("amount", "Monto asignado", 16, "money"),
        ],
        rows=rows,
        totals=[
            ("Períodos cerrados", str(totals["periods"])),
            ("Total de períodos", _money_text(totals["total"])),
            ("Suma asignada", _money_text(totals["allocated"])),
            ("Diferencia", _money_text(difference)),
        ],
        note=note,
    )


def _equipment_table(rows: list[dict[str, Any]]) -> ReportTable:
    totals = equipment_totals(rows)
    return ReportTable(
        sheet_name="Equipos",
        columns=[
            ReportColumn("equipment", "Equipo", 24),
            ReportColumn("model", "Modelo", 16),
            ReportColumn("serial", "Número de serie", 18),
            ReportColumn("vendor", "Proveedor", 20),
            ReportColumn("status", "Estado", 15),
            ReportColumn("project_code", "Código de proyecto", 18),
            ReportColumn("user", "Usuario", 22),
            ReportColumn("start", "Inicio", 18, "datetime"),
            ReportColumn("end", "Fin", 18, "datetime"),
            ReportColumn("hours", "Horas", 10, "quantity"),
            ReportColumn("purpose", "Propósito", 30),
        ],
        rows=rows,
        totals=[
            ("Reservas/uso", str(totals["reservations"])),
            ("Unidades distintas", str(totals["units"])),
            ("Proyectos distintos", str(totals["projects"])),
            ("Horas reservadas", _hours_text(totals["hours"])),
        ],
    )


async def build_document(
    db: AsyncSession,
    report_type: ReportType,
    actor: CurrentUser,
    filters: ReportFilters,
    *,
    generated_at: datetime | None = None,
) -> ReportDocument:
    """Consulta los datos y arma el documento (sin renderizar)."""
    if report_type == "expenses":
        rows = await fetch_expense_rows(db, actor, filters)
        table = _expense_table(rows)
    elif report_type == "overhead":
        rows = await fetch_overhead_rows(db, actor, filters)
        table = _overhead_table(rows)
    elif report_type == "equipment":
        rows = await fetch_equipment_rows(db, actor, filters)
        table = _equipment_table(rows)
    else:  # pragma: no cover - el tipo lo fija el router
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reporte no encontrado",
        )

    now = generated_at or datetime.now(timezone.utc)
    return ReportDocument(
        report_type=report_type,
        title=_REPORT_LABELS[report_type],
        generated_at=now,
        generated_by=_full_name(
            actor.name, actor.lastname, actor.email
        ),
        filters_text=filters.describe(),
        table=table,
    )


# ---------------------------------------------------------------------------
# Render XLSX
# ---------------------------------------------------------------------------

_HEADER_FILL = PatternFill("solid", fgColor="1D4ED8")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_TITLE_FONT = Font(bold=True, size=14, color="111827")
_META_FONT = Font(size=9, color="374151")
_TOTAL_FILL = PatternFill("solid", fgColor="EFF6FF")
_THIN = Side(style="thin", color="D1D5DB")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_XLSX_NUMBER_FORMATS = {
    "money": "#,##0.00",
    "quantity": "#,##0.00",
    "integer": "0",
    "date": "yyyy-mm-dd",
    "datetime": "yyyy-mm-dd hh:mm",
}


def _xlsx_value(value: Any, kind: str) -> Any:
    if value is None:
        return ""
    if kind == "datetime" and isinstance(value, datetime):
        return _utc(value).replace(tzinfo=None)
    if kind == "date" and isinstance(value, datetime):
        return _utc(value).date()
    if kind in ("money", "quantity"):
        return float(_dec(value))
    if kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def _force_text(cell) -> None:
    """Evita inyección de fórmulas en XLSX (CWE-1236).

    openpyxl interpreta como fórmula un texto que empieza por ``=``. Al forzar
    ``data_type="s"`` el valor se guarda como cadena y Excel lo muestra tal cual,
    sin evaluarlo.
    """
    if isinstance(cell.value, str):
        cell.data_type = "s"


def render_xlsx(document: ReportDocument) -> bytes:
    """Genera un libro con encabezados, formatos, anchos y totales."""
    table = document.table
    wb = Workbook()
    ws: Worksheet = wb.active
    ws.title = table.sheet_name[:31]
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"

    last_col = max(1, len(table.columns))
    ws.merge_cells(
        start_row=1, start_column=1, end_row=1, end_column=last_col
    )
    title_cell = ws.cell(row=1, column=1, value=document.title)
    title_cell.font = _TITLE_FONT
    title_cell.alignment = Alignment(vertical="center")
    _force_text(title_cell)
    ws.row_dimensions[1].height = 22

    metadata = [
        f"Generado (UTC): {_timestamp(document.generated_at)}",
        f"Usuario: {document.generated_by}",
        f"Filtros: {document.filters_text}",
    ]
    if table.note:
        metadata.append(f"Nota: {table.note}")
    for index, text in enumerate(metadata, start=2):
        cell = ws.cell(row=index, column=1, value=text)
        cell.font = _META_FONT
        _force_text(cell)

    header_row = 2 + len(metadata) + 1
    for index, column in enumerate(table.columns, start=1):
        cell = ws.cell(
            row=header_row, column=index, value=column.header
        )
        _force_text(cell)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        cell.border = _BORDER
    ws.row_dimensions[header_row].height = 30

    first_data_row = header_row + 1
    for offset, row in enumerate(table.rows):
        excel_row = first_data_row + offset
        for index, column in enumerate(table.columns, start=1):
            value = _xlsx_value(row.get(column.key), column.kind)
            cell = ws.cell(row=excel_row, column=index, value=value)
            if column.kind == "text":
                _force_text(cell)
            cell.border = _BORDER
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=column.kind == "text" and column.width >= 24,
                horizontal=(
                    "right"
                    if column.kind in ("money", "quantity", "integer")
                    else "left"
                ),
            )
            if column.kind in _XLSX_NUMBER_FORMATS and value != "":
                cell.number_format = _XLSX_NUMBER_FORMATS[column.kind]

    last_data_row = first_data_row + len(table.rows) - 1
    if table.rows:
        ws.auto_filter.ref = (
            f"A{header_row}:{get_column_letter(last_col)}{last_data_row}"
        )
    ws.freeze_panes = f"A{first_data_row}"

    for index, column in enumerate(table.columns, start=1):
        ws.column_dimensions[get_column_letter(index)].width = column.width

    if table.totals:
        total_row = last_data_row + 2
        for offset, (label, value) in enumerate(table.totals):
            excel_row = total_row + offset
            label_cell = ws.cell(row=excel_row, column=1, value=label)
            label_cell.font = Font(bold=True, size=10)
            label_cell.fill = _TOTAL_FILL
            _force_text(label_cell)
            value_cell = ws.cell(row=excel_row, column=2, value=value)
            value_cell.font = Font(bold=True, size=10)
            value_cell.fill = _TOTAL_FILL
            value_cell.alignment = Alignment(horizontal="right")
            _force_text(value_cell)

    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Render PDF
# ---------------------------------------------------------------------------

_PDF_PAGE = landscape(A4)
_PDF_MARGIN = 18 * mm


def _pdf_paragraph(
    value: Any, style: ParagraphStyle, kind: str = "text"
) -> Paragraph:
    if value is None or value == "":
        text = "-"
    elif kind == "datetime" and isinstance(value, datetime):
        text = _utc(value).strftime("%Y-%m-%d %H:%M")
    elif kind == "date" and isinstance(value, (datetime, date)):
        text = (
            _utc(value).date().isoformat()
            if isinstance(value, datetime)
            else value.isoformat()
        )
    elif kind in ("money", "quantity") and isinstance(
        value, (Decimal, int, float)
    ):
        text = f"{_dec(value):,.2f}"
    elif isinstance(value, Decimal):
        text = f"{value:,.2f}"
    else:
        text = str(value)
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    dark = colors.HexColor("#111827")
    muted = colors.HexColor("#374151")
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            alignment=0,
            spaceAfter=2,
            textColor=dark,
        ),
        "meta": ParagraphStyle(
            "ReportMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=muted,
        ),
        "header": ParagraphStyle(
            "ReportHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=colors.white,
        ),
        "cell": ParagraphStyle(
            "ReportCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
        ),
        "cell_right": ParagraphStyle(
            "ReportCellRight",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            alignment=2,
        ),
        "note": ParagraphStyle(
            "ReportNote",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#B45309"),
        ),
        "total": ParagraphStyle(
            "ReportTotal",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
            textColor=dark,
        ),
    }


def _pdf_footer(canvas, doc) -> None:  # pragma: no cover - callback visual
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawRightString(
        doc.pagesize[0] - _PDF_MARGIN + 4 * mm,
        9 * mm,
        f"Página {doc.page}",
    )
    canvas.restoreState()


def render_pdf(document: ReportDocument) -> bytes:
    """Genera un PDF legible con metadatos, tabla y totales."""
    table = document.table
    styles = _pdf_styles()
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=_PDF_PAGE,
        leftMargin=_PDF_MARGIN,
        rightMargin=_PDF_MARGIN,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=document.title,
        author="Sistema de Control de Proyectos y Recursos",
        subject=f"Reporte de {document.title}",
    )

    elements: list[Any] = [
        Paragraph(escape(document.title), styles["title"]),
        Paragraph(
            f"Generado (UTC): {_timestamp(document.generated_at)}"
            f" &nbsp;|&nbsp; Usuario: {escape(document.generated_by)}",
            styles["meta"],
        ),
        Paragraph(
            f"Filtros: {escape(document.filters_text)}", styles["meta"]
        ),
        Spacer(1, 4 * mm),
    ]
    if table.note:
        elements.append(
            Paragraph(escape(table.note), styles["note"])
        )
        elements.append(Spacer(1, 2 * mm))

    data: list[list[Any]] = [
        [
            Paragraph(escape(column.header), styles["header"])
            for column in table.columns
        ]
    ]
    for row in table.rows:
        data.append(
            [
                _pdf_paragraph(
                    row.get(column.key),
                    (
                        styles["cell_right"]
                        if column.kind in ("money", "quantity", "integer")
                        else styles["cell"]
                    ),
                    column.kind,
                )
                for column in table.columns
            ]
        )

    available = _PDF_PAGE[0] - 2 * _PDF_MARGIN
    weights = [max(6, column.width) for column in table.columns]
    total_weight = sum(weights) or 1
    col_widths = [
        available * weight / total_weight for weight in weights
    ]

    pdf_table = Table(data, colWidths=col_widths, repeatRows=1)
    pdf_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D4ED8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F9FAFB")],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(pdf_table)
    elements.append(Spacer(1, 4 * mm))

    if table.totals:
        totals_data = [
            [
                Paragraph(escape(label), styles["total"]),
                Paragraph(escape(value), styles["total"]),
            ]
            for label, value in table.totals
        ]
        totals_table = Table(
            totals_data, colWidths=[60 * mm, 35 * mm], hAlign="RIGHT"
        )
        totals_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        colors.HexColor("#EFF6FF"),
                    ),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFDBFE")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(totals_table)

    pdf.build(
        elements,
        onFirstPage=_pdf_footer,
        onLaterPages=_pdf_footer,
    )
    return buffer.getvalue()


def render_document(
    document: ReportDocument, report_format: ReportFormat
) -> bytes:
    if report_format == "xlsx":
        return render_xlsx(document)
    if report_format == "pdf":
        return render_pdf(document)
    # pragma: no cover - el formato lo fija el router
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Formato no soportado",
    )


# ---------------------------------------------------------------------------
# Export + auditoría
# ---------------------------------------------------------------------------


def _filename(
    report_type: ReportType, report_format: ReportFormat, moment: datetime
) -> str:
    base = _REPORT_BASENAMES[report_type]
    stamp = _utc(moment).strftime("%Y%m%d-%H%M%S")
    return f"{base}-{stamp}.{report_format}"


async def export_report(
    db: AsyncSession,
    report_type: ReportType,
    report_format: ReportFormat,
    actor: CurrentUser,
    filters: ReportFilters,
    request: Request | None = None,
) -> RenderedReport:
    """Construye, renderiza y audita el archivo antes de responder."""
    document = await build_document(db, report_type, actor, filters)
    content = render_document(document, report_format)
    filename = _filename(
        report_type, report_format, document.generated_at
    )

    # Auditoría de negocio append-only. Si la escritura falla, se registra el
    # incidente pero no se rompe la descarga ya generada.
    try:
        await record_audit(
            db,
            entity="Report",
            action="export",
            entity_id=None,
            user_id=actor.id,
            request=request,
            status_code=status.HTTP_200_OK,
            outcome="success",
            after={
                "report": report_type,
                "format": report_format,
                "filters": filters.for_audit(),
                "records": len(document.table.rows),
                "filename": filename,
            },
        )
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "No se pudo auditar la exportación %s.%s (%s)",
            report_type,
            report_format,
            type(exc).__name__,
        )

    return RenderedReport(
        content=content,
        media_type=(
            XLSX_MEDIA_TYPE if report_format == "xlsx" else PDF_MEDIA_TYPE
        ),
        filename=filename,
    )
