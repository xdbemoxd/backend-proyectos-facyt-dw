"""Pruebas LIVE de solo lectura de los reportes contra la base real.

Se habilitan con ``REPORTS_LIVE_TESTS=1``. Solo hacen peticiones GET: no crean
ni modifican proyectos, gastos, reservas ni períodos. La única escritura es el
evento append-only ``Report/export`` que el propio endpoint registra en
``audit_log`` (requisito de auditoría); por eso también se verifica que aumente.

Se descargan los seis archivos con tokens reales de Administrador y Aprobador,
se validan firma/tamaño/content-type y el alcance del Coordinador (solo sus
proyectos en gastos y equipos; 403 en prorrateo). Los tokens se firman en
memoria y nunca se imprimen.
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from src.config.db import AsyncSessionLocal
from src.model.audit import AuditLogModel
from src.model.project import ProjectModel
from src.model.user import RolModel, UserModel
from src.utils.other import generate_jwt_to_frontend
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("REPORTS_LIVE_TESTS", "").strip().lower()
        not in ("1", "true", "yes"),
        reason="Definir REPORTS_LIVE_TESTS=1 para pruebas live de solo lectura",
    ),
]

SEEDED_USERS = {
    ADMIN_ROLE: "admin@example.com",
    APPROVER_ROLE: "aprobador@example.com",
    COORDINATOR_ROLE: "coordinador1@example.com",
}

XLSX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
PDF_MEDIA = "application/pdf"
PROJECT_CODE_RE = re.compile(r"^[A-Z]{3}-\d{4}-\d{3}$")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tokens_by_role() -> dict[str, str]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    UserModel.id,
                    UserModel.email,
                    UserModel.name,
                    UserModel.token_version,
                    RolModel.name,
                )
                .join(RolModel, RolModel.id == UserModel.id_rol)
                .where(
                    UserModel.email.in_(SEEDED_USERS.values()),
                    UserModel.is_active.is_(True),
                )
            )
        ).all()

    by_email = {row.email: row for row in rows}
    missing = [
        email for email in SEEDED_USERS.values() if email not in by_email
    ]
    if missing:
        pytest.skip(
            "Faltan usuarios semilla activos en la base real: "
            + ", ".join(missing)
        )

    tokens = {}
    for role, email in SEEDED_USERS.items():
        row = by_email[email]
        tokens[role] = generate_jwt_to_frontend(
            row.id, row.email, row.name, int(row.token_version or 0)
        )
    return tokens


@pytest.fixture
async def role_tokens():
    return await _tokens_by_role()


@pytest.fixture
async def own_project_codes(role_tokens):
    async with AsyncSessionLocal() as db:
        coordinator = await db.scalar(
            select(UserModel).where(
                UserModel.email == SEEDED_USERS[COORDINATOR_ROLE]
            )
        )
        if coordinator is None:
            pytest.skip("No existe el coordinador semilla")
        rows = (
            await db.execute(
                select(ProjectModel.id, ProjectModel.unique_code).where(
                    ProjectModel.id_user == coordinator.id
                )
            )
        ).all()
    if not rows:
        pytest.skip("El coordinador semilla no tiene proyectos")
    return {row.unique_code: row.id for row in rows}


def _project_codes(content: bytes) -> set[str]:
    workbook = load_workbook(
        BytesIO(content), read_only=True, data_only=True
    )
    sheet = workbook[workbook.sheetnames[0]]
    codes: set[str] = set()
    for row in sheet.iter_rows(values_only=True):
        for value in row:
            if isinstance(value, str) and PROJECT_CODE_RE.match(value):
                codes.add(value)
    return codes


def _overhead_difference(content: bytes) -> str | None:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    for row in sheet.iter_rows(values_only=True):
        if row and row[0] == "Diferencia":
            return str(row[1])
    return None


async def _assert_download(
    client, token: str, kind: str, fmt: str
) -> bytes:
    response = await client.get(
        f"/api/reports/{kind}.{fmt}", headers=auth(token)
    )
    assert response.status_code == 200, (kind, fmt, response.text)
    expected_media = XLSX_MEDIA if fmt == "xlsx" else PDF_MEDIA
    assert response.headers["content-type"] == expected_media
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert f".{fmt}\"" in disposition

    content = response.content
    if fmt == "xlsx":
        assert content.startswith(b"PK")
        with ZipFile(BytesIO(content)) as archive:
            assert archive.testzip() is None
    else:
        assert content.startswith(b"%PDF")
        assert content.rstrip().endswith(b"%%EOF")
    assert len(content) > 1000
    return content


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
async def test_admin_and_approver_download_all_six_files(
    client, role_tokens, role
):
    token = role_tokens[role]
    for kind in ("expenses", "overhead", "equipment"):
        for fmt in ("xlsx", "pdf"):
            content = await _assert_download(client, token, kind, fmt)
            if kind == "overhead" and fmt == "xlsx":
                # El reparto guardado debe cuadrar exactamente.
                assert _overhead_difference(content) == "0.00"


@pytest.mark.asyncio
async def test_coordinator_scope_and_overhead_denied(
    client, role_tokens, own_project_codes
):
    token = role_tokens[COORDINATOR_ROLE]

    for kind in ("expenses", "equipment"):
        content = await _assert_download(client, token, kind, "xlsx")
        codes = _project_codes(content)
        assert codes, f"El reporte {kind} del coordinador no tiene filas"
        assert codes <= set(own_project_codes), (
            kind,
            sorted(codes - set(own_project_codes)),
        )

    for fmt in ("xlsx", "pdf"):
        denied = await client.get(
            f"/api/reports/overhead.{fmt}",
            headers=auth(token),
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
async def test_export_writes_append_only_audit_event(client, role_tokens):
    async with AsyncSessionLocal() as db:
        before = int(
            await db.scalar(
                select(func.count())
                .select_from(AuditLogModel)
                .where(
                    AuditLogModel.entity == "Report",
                    AuditLogModel.action == "export",
                )
            )
            or 0
        )

    await _assert_download(
        client, role_tokens[ADMIN_ROLE], "expenses", "xlsx"
    )

    async with AsyncSessionLocal() as db:
        after = int(
            await db.scalar(
                select(func.count())
                .select_from(AuditLogModel)
                .where(
                    AuditLogModel.entity == "Report",
                    AuditLogModel.action == "export",
                )
            )
            or 0
        )
    assert after == before + 1


@pytest.mark.asyncio
async def test_filters_do_not_break_export(client, role_tokens):
    token = role_tokens[ADMIN_ROLE]
    response = await client.get(
        "/api/reports/expenses.xlsx?from_date=2020-01-01&to_date=2030-12-31",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"PK")
