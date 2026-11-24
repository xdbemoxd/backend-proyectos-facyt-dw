"""Contrato HTTP de los reportes XLSX/PDF (sin base de datos).

- Sin token: los seis endpoints responden 401.
- Coordinador: 403 en prorrateo; 200 en gastos/equipos con el servicio simulado.
- Administrador: 200, content-type correcto y ``Content-Disposition`` adjunto.
- Rango de fechas inválido: 422 antes de tocar la base.
- Filtros de estado no aplicables: 422 en el servicio.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from src.schema.report import ReportFilters
from src.schema.user import CurrentUser
from src.service import report as report_service
from src.service.report import RenderedReport
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

ENDPOINTS = [
    ("expenses", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("expenses", "pdf", "application/pdf"),
    ("overhead", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("overhead", "pdf", "application/pdf"),
    ("equipment", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("equipment", "pdf", "application/pdf"),
]


def path(kind: str, fmt: str) -> str:
    return f"/api/reports/{kind}.{fmt}"


def make_actor(role: str) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="actor@example.com",
        name="Actor",
        lastname="Prueba",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=role,
    )


@pytest.fixture
def override_role():
    from app import app
    from src.utils.auth import get_current_user

    def apply(role: str) -> None:
        app.dependency_overrides[get_current_user] = lambda: make_actor(role)

    yield apply
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def fake_export(monkeypatch):
    """Evita la base: el render real se prueba en los unitarios."""

    async def fake(
        db,
        report_type,
        report_format,
        actor,
        filters,
        request=None,
    ):
        return RenderedReport(
            content=b"%PDF-1.4\n% fake\n",
            media_type=(
                "application/pdf"
                if report_format == "pdf"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            filename=f"{report_type}.{report_format}",
        )

    monkeypatch.setattr(report_service, "export_report", fake)
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "fmt", "media_type"), ENDPOINTS)
async def test_reports_require_token(client, kind, fmt, media_type):
    response = await client.get(path(kind, fmt))
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "No autenticado"


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["xlsx", "pdf"])
async def test_coordinator_cannot_export_overhead(
    client, override_role, fake_export, fmt
):
    override_role(COORDINATOR_ROLE)
    response = await client.get(path("overhead", fmt))
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "fmt", "media_type"), ENDPOINTS)
async def test_admin_can_export_all_reports(
    client, override_role, fake_export, kind, fmt, media_type
):
    override_role(ADMIN_ROLE)
    response = await client.get(path(kind, fmt))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == media_type
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f"filename=\"{kind}.{fmt}\"" == disposition.split("; ", 1)[1]
    assert response.content.startswith(b"%PDF")


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["xlsx", "pdf"])
async def test_coordinator_can_export_expenses_and_equipment(
    client, override_role, fake_export, fmt
):
    override_role(COORDINATOR_ROLE)
    for kind in ("expenses", "equipment"):
        response = await client.get(path(kind, fmt))
        assert response.status_code == 200, (kind, response.text)


@pytest.mark.asyncio
async def test_approver_can_export_overhead(
    client, override_role, fake_export
):
    override_role(APPROVER_ROLE)
    response = await client.get(path("overhead", "xlsx"))
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_invalid_date_range_is_rejected_with_422(
    client, override_role, fake_export
):
    override_role(ADMIN_ROLE)
    response = await client.get(
        "/api/reports/expenses.xlsx?from_date=2026-12-31&to_date=2026-12-01"
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_invalid_project_id_is_rejected_with_422(
    client, override_role, fake_export
):
    override_role(ADMIN_ROLE)
    response = await client.get(
        "/api/reports/expenses.pdf?project_id=no-es-uuid"
    )
    assert response.status_code == 422, response.text


def test_expense_status_filter_rejects_unknown_code():
    with pytest.raises(HTTPException) as exc:
        report_service._expense_conditions(
            make_actor(ADMIN_ROLE), ReportFilters(status="inventado")
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "Estado de solicitud inválido"


@pytest.mark.asyncio
async def test_overhead_status_filter_is_rejected_before_db():
    with pytest.raises(HTTPException) as exc:
        await report_service.fetch_overhead_rows(
            None,  # la validación ocurre antes de usar la sesión
            make_actor(ADMIN_ROLE),
            ReportFilters(status="pending"),
        )
    assert exc.value.status_code == 422
    assert "no admite" in exc.value.detail
