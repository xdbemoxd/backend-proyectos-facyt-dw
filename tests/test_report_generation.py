"""Pruebas unitarias de generación de reportes (sin base de datos).

Comprueban que:

- el XLSX es un ZIP/Office válido con la hoja y las columnas esperadas;
- el PDF empieza con ``%PDF``, termina en ``%%EOF`` y contiene el título;
- los totales de gastos por estado y el cuadre exacto del overhead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook

from src.service.report import (
    ReportColumn,
    ReportDocument,
    ReportTable,
    expense_status_totals,
    overhead_totals,
    render_pdf,
    render_xlsx,
)

NOW = datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc)


def _expense_document(note: str | None = None) -> ReportDocument:
    rows = [
        {
            "project_code": "FIS-2026-001",
            "project_name": "Materiales cuánticos",
            "description": "Compra de reactivo para el laboratorio",
            "material": "Ácido clorhídrico",
            "quantity": Decimal("10.50"),
            "unit": "L",
            "status": "Pendiente de Aprobación",
            "status_code": "pending",
            "requester": "Ana Pérez",
            "created_at": NOW,
            "updated_at": NOW,
        },
        {
            "project_code": "BIO-2026-003",
            "project_name": "Biología celular",
            "description": "Guantes de nitrilo",
            "material": "Guantes",
            "quantity": Decimal("2.00"),
            "unit": "caja",
            "status": "Ejecutado",
            "status_code": "executed",
            "requester": "Luis Gómez",
            "created_at": NOW,
            "updated_at": NOW,
        },
    ]
    return ReportDocument(
        report_type="expenses",
        title="Análisis de gastos",
        generated_at=NOW,
        generated_by="Admin Prueba",
        filters_text="sin filtros",
        table=ReportTable(
            sheet_name="Gastos",
            columns=[
                ReportColumn("project_code", "Código de proyecto", 18),
                ReportColumn("description", "Descripción", 32),
                ReportColumn("quantity", "Cantidad", 11, "quantity"),
                ReportColumn("status", "Estado", 15),
                ReportColumn("created_at", "Creada", 18, "datetime"),
            ],
            rows=rows,
            totals=[("Solicitudes", "2"), ("Pendientes", "1")],
            note=note,
        ),
    )


def test_render_xlsx_is_valid_office_file_with_headers():
    document = _expense_document()
    content = render_xlsx(document)

    assert content[:2] == b"PK"
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert "xl/workbook.xml" in archive.namelist()

    workbook = load_workbook(BytesIO(content))
    assert workbook.sheetnames == ["Gastos"]
    sheet = workbook["Gastos"]

    # Título, metadatos y encabezados (sin nota: fila 6).
    assert sheet["A1"].value == "Análisis de gastos"
    assert "Generado (UTC)" in sheet["A2"].value
    assert "Admin Prueba" in sheet["A3"].value
    assert "sin filtros" in sheet["A4"].value
    headers = [sheet.cell(row=6, column=index).value for index in range(1, 6)]
    assert headers == [
        "Código de proyecto",
        "Descripción",
        "Cantidad",
        "Estado",
        "Creada",
    ]

    assert sheet["A7"].value == "FIS-2026-001"
    assert sheet["C7"].value == 10.5
    assert sheet["C7"].number_format == "#,##0.00"
    assert sheet["E7"].number_format == "yyyy-mm-dd hh:mm"
    assert sheet.freeze_panes == "A7"

    # Totales debajo de las dos filas de datos.
    assert sheet["A10"].value == "Solicitudes"
    assert sheet["B10"].value == "2"
    assert sheet["A11"].value == "Pendientes"


def test_render_xlsx_includes_note_and_currency_format():
    document = _expense_document(note="Atención: revisar diferencia.")
    document.table.columns.append(
        ReportColumn("amount", "Monto", 14, "money")
    )
    document.table.rows[0]["amount"] = Decimal("1234.5")
    document.table.rows[1]["amount"] = Decimal("0.5")

    sheet = load_workbook(BytesIO(render_xlsx(document)))["Gastos"]
    # Con nota, los encabezados pasan a la fila 7.
    assert "Atención" in sheet["A5"].value
    assert sheet["F7"].value == "Monto"
    assert sheet["F8"].value == 1234.5
    assert sheet["F8"].number_format == "#,##0.00"


def test_render_xlsx_neutralizes_formula_injection():
    document = _expense_document()
    payload = '=HYPERLINK("http://example.invalid","clic")'
    document.table.rows[0]["description"] = payload

    content = render_xlsx(document)
    sheet = load_workbook(BytesIO(content))["Gastos"]

    # El texto se conserva, pero como cadena y no como fórmula evaluable.
    assert sheet["B7"].value == payload
    assert sheet["B7"].data_type == "s"
    with ZipFile(BytesIO(content)) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")
    assert b"<f>" not in sheet_xml


def test_render_pdf_starts_with_signature_and_contains_title():
    content = render_pdf(_expense_document())

    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")
    assert len(content) > 1500
    assert b"An" in content  # el título se escribe en el Info del PDF


def test_render_pdf_handles_empty_rows():
    document = _expense_document()
    document.table.rows = []
    content = render_pdf(document)
    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")


def test_expense_status_totals_counts_by_state():
    rows = _expense_document().table.rows
    totals = expense_status_totals(rows)

    assert totals["total"] == 2
    assert totals["pending"] == 1
    assert totals["executed"] == 1
    assert totals["approved"] == 0
    assert totals["rejected"] == 0
    assert totals["draft"] == 0
    assert totals["quantity_total"] == Decimal("12.50")


def test_overhead_totals_match_exactly():
    rows = [
        {
            "period_id": "period-1",
            "period_total": Decimal("1200.00"),
            "amount": Decimal("450.00"),
        },
        {
            "period_id": "period-1",
            "period_total": Decimal("1200.00"),
            "amount": Decimal("750.00"),
        },
    ]
    totals = overhead_totals(rows)

    assert totals["periods"] == 1
    assert totals["total"] == Decimal("1200.00")
    assert totals["allocated"] == Decimal("1200.00")
    assert totals["difference"] == Decimal("0.00")


def test_overhead_totals_reveal_mismatch_instead_of_hiding_it():
    rows = [
        {
            "period_id": "period-1",
            "period_total": Decimal("1200.00"),
            "amount": Decimal("450.00"),
        },
        {
            "period_id": "period-1",
            "period_total": Decimal("1200.00"),
            "amount": Decimal("700.00"),
        },
    ]
    totals = overhead_totals(rows)

    assert totals["allocated"] == Decimal("1150.00")
    assert totals["difference"] == Decimal("50.00")
