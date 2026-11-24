"""Pruebas LIVE de SOLO LECTURA de gastos y overhead contra la base real.

Se habilitan con ``FINANCE_LIVE_TESTS=1``. No crean solicitudes, no cierran
periodos y no modifican filas: las peticiones mutables de estas pruebas son
rechazadas antes de escribir (409/422) y solo dejan el evento HTTP de auditoria,
que es append-only.

Los tokens se firman en memoria para los usuarios semilla y nunca se imprimen.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.config.db import AsyncSessionLocal
from src.model.project import ProjectModel
from src.model.user import RolModel, UserModel
from src.utils.other import generate_jwt_to_frontend
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FINANCE_LIVE_TESTS", "").strip().lower()
        not in ("1", "true", "yes"),
        reason="Definir FINANCE_LIVE_TESTS=1 para pruebas live de solo lectura",
    ),
]

SEEDED_USERS = {
    ADMIN_ROLE: "admin@example.com",
    APPROVER_ROLE: "aprobador@example.com",
    COORDINATOR_ROLE: "coordinador1@example.com",
}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tokens_by_role() -> dict[str, str]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    UserModel.id,
                    UserModel.email,
                    UserModel.token_version,
                    UserModel.rol,
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
async def own_project_ids(role_tokens):
    async with AsyncSessionLocal() as db:
        coordinator = await db.scalar(
            select(UserModel).where(
                UserModel.email == SEEDED_USERS[COORDINATOR_ROLE]
            )
        )
        if coordinator is None:
            pytest.skip("No existe el coordinador semilla")
        ids = set(
            (
                await db.scalars(
                    select(ProjectModel.id).where(
                        ProjectModel.id_user == coordinator.id
                    )
                )
            ).all()
        )
    if not ids:
        pytest.skip("El coordinador semilla no tiene proyectos")
    return ids


# ---------------------------------------------------------------------------
# Gastos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expenses_are_listed_with_role_scope(
    client, role_tokens, own_project_ids
):
    admin = await client.get(
        "/api/expenses?limit=200", headers=auth(role_tokens[ADMIN_ROLE])
    )
    assert admin.status_code == 200, admin.text
    admin_body = admin.json()
    assert admin_body["total"] >= 5
    assert len(admin_body["items"]) >= 5

    approver = await client.get(
        "/api/expenses?limit=200", headers=auth(role_tokens[APPROVER_ROLE])
    )
    assert approver.status_code == 200, approver.text
    assert approver.json()["total"] == admin_body["total"]

    coordinator = await client.get(
        "/api/expenses?limit=200",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert coordinator.status_code == 200, coordinator.text
    items = coordinator.json()["items"]
    assert items, "El coordinador semilla deberia tener solicitudes"
    for item in items:
        assert uuid.UUID(item["project"]["id"]) in own_project_ids
        assert item["requester"]["id"]


@pytest.mark.asyncio
async def test_expense_detail_and_foreign_scope(
    client, role_tokens, own_project_ids
):
    admin = await client.get(
        "/api/expenses?limit=200", headers=auth(role_tokens[ADMIN_ROLE])
    )
    assert admin.status_code == 200, admin.text
    items = admin.json()["items"]
    assert items

    first = items[0]
    detail = await client.get(
        f"/api/expenses/{first['id']}", headers=auth(role_tokens[ADMIN_ROLE])
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    for key in (
        "id",
        "project",
        "supplier_item",
        "status",
        "requester",
        "description",
        "count_raw_materials",
        "created_at",
        "modifiet_at",
    ):
        assert key in body

    foreign = next(
        (
            item
            for item in items
            if uuid.UUID(item["project"]["id"]) not in own_project_ids
        ),
        None,
    )
    if foreign is None:
        pytest.skip("No hay una solicitud ajena para probar IDOR")

    denied = await client.get(
        f"/api/expenses/{foreign['id']}",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert denied.status_code == 403, denied.text

    denied_filter = await client.get(
        f"/api/expenses?project_id={foreign['project']['id']}",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert denied_filter.status_code == 403, denied_filter.text


@pytest.mark.asyncio
async def test_invalid_transition_does_not_modify_expense(
    client, role_tokens
):
    token = role_tokens[ADMIN_ROLE]
    draft = await client.get(
        "/api/expenses?status=draft&limit=200", headers=auth(token)
    )
    assert draft.status_code == 200, draft.text
    items = draft.json()["items"]
    if not items:
        pytest.skip("La base real no tiene solicitudes en Borrador")

    target = items[0]
    before = target["status"]["code"]

    response = await client.patch(
        f"/api/expenses/{target['id']}/status",
        json={"status": "executed"},
        headers=auth(token),
    )
    assert response.status_code == 409, response.text

    after = await client.get(
        f"/api/expenses/{target['id']}", headers=auth(token)
    )
    assert after.status_code == 200, after.text
    assert after.json()["status"]["code"] == before


@pytest.mark.asyncio
async def test_invalid_status_filter_is_rejected_without_writing(
    client, role_tokens
):
    response = await client.get(
        "/api/expenses?status=no-existe",
        headers=auth(role_tokens[ADMIN_ROLE]),
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Overhead
# ---------------------------------------------------------------------------


async def _periods(client, token) -> list[dict]:
    response = await client.get(
        "/api/overhead/periods?limit=200", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.asyncio
async def test_existing_period_is_readable_and_balanced(
    client, role_tokens
):
    for role in (ADMIN_ROLE, APPROVER_ROLE):
        periods = await _periods(client, role_tokens[role])
        if not periods:
            pytest.skip("La base real no tiene periodos de overhead")
        period = periods[0]
        detail = await client.get(
            f"/api/overhead/periods/{period['id']}",
            headers=auth(role_tokens[role]),
        )
        assert detail.status_code == 200, (role, detail.text)
        body = detail.json()
        total = Decimal(str(body["total_cost"]))
        allocated = sum(
            (Decimal(str(row["amount"])) for row in body["allocations"]),
            Decimal("0.00"),
        )
        assert allocated == total
        assert body["closed_by"]["id"]
        for row in body["allocations"]:
            assert row["project"]["unique_code"]


@pytest.mark.asyncio
async def test_coordinator_cannot_read_overhead(client, role_tokens):
    response = await client.get(
        "/api/overhead/periods",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_preview_equitativo_is_exact_and_writes_nothing(
    client, role_tokens
):
    response = await client.get(
        "/api/overhead/preview?start_date=2026-10-01&end_date=2026-10-31"
        "&total_cost=123.45&criterion=equitativo",
        headers=auth(role_tokens[ADMIN_ROLE]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allocations"]
    total = Decimal(str(body["total_cost"]))
    allocated = sum(
        (Decimal(str(row["amount"])) for row in body["allocations"]),
        Decimal("0.00"),
    )
    assert allocated == total


@pytest.mark.asyncio
async def test_overlapping_period_is_rejected_without_writing(
    client, role_tokens
):
    token = role_tokens[ADMIN_ROLE]
    before = await _periods(client, token)
    if not before:
        pytest.skip("La base real no tiene periodos para probar el solape")

    existing = before[0]
    response = await client.post(
        "/api/overhead/periods",
        headers=auth(token),
        json={
            "start_date": existing["start_date"],
            "end_date": existing["end_date"],
            "criterion": "equitativo",
            "total_cost": "10.00",
        },
    )
    assert response.status_code == 409, response.text

    after = await _periods(client, token)
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_invalid_date_range_is_rejected_without_writing(
    client, role_tokens
):
    response = await client.post(
        "/api/overhead/periods",
        headers=auth(role_tokens[ADMIN_ROLE]),
        json={
            "start_date": "2026-12-31",
            "end_date": "2026-12-01",
            "criterion": "equitativo",
            "total_cost": "10.00",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_unknown_period_returns_404(client, role_tokens):
    response = await client.get(
        f"/api/overhead/periods/{uuid.uuid4()}",
        headers=auth(role_tokens[ADMIN_ROLE]),
    )
    assert response.status_code == 404, response.text
