"""Contrato HTTP de solicitudes de gasto y overhead (sin base de datos).

- Sin token: todos los endpoints responden 401.
- Con un usuario simulado se comprueban los 403 de rol y los 422 de validacion
  estricta antes de llegar a la base.
"""

from __future__ import annotations

import uuid

import pytest

from src.schema.user import CurrentUser
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

EXPENSE_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
SUPPLIER_ITEM_ID = str(uuid.uuid4())

EXPENSE_BODY = {
    "id_project": PROJECT_ID,
    "id_raw_materials_suppliers": SUPPLIER_ITEM_ID,
    "description": "Compra de reactivo para el laboratorio.",
    "count_raw_materials": "10.00",
}
PERIOD_BODY = {
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "criterion": "equitativo",
    "total_cost": "1000.00",
}
PREVIEW = (
    "/api/overhead/preview?start_date=2026-12-01&end_date=2026-12-31"
    "&total_cost=1000.00&criterion=equitativo"
)


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
        app.dependency_overrides[get_current_user] = (
            lambda: make_actor(role)
        )

    yield apply
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/expenses", None),
        ("GET", f"/api/expenses/{EXPENSE_ID}", None),
        ("POST", "/api/expenses", EXPENSE_BODY),
        ("PATCH", f"/api/expenses/{EXPENSE_ID}/status", {"status": "pending"}),
        ("GET", "/api/overhead/periods", None),
        ("GET", f"/api/overhead/periods/{EXPENSE_ID}", None),
        ("GET", PREVIEW, None),
        ("POST", "/api/overhead/periods", PERIOD_BODY),
    ],
)
async def test_finance_endpoints_require_token(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "No autenticado"


@pytest.mark.asyncio
async def test_approver_cannot_create_expenses(client, override_role):
    override_role(APPROVER_ROLE)
    response = await client.post("/api/expenses", json=EXPENSE_BODY)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/overhead/periods", None),
        ("GET", f"/api/overhead/periods/{EXPENSE_ID}", None),
        ("GET", PREVIEW, None),
        ("POST", "/api/overhead/periods", PERIOD_BODY),
    ],
)
async def test_coordinator_cannot_access_overhead(
    client, override_role, method, path, body
):
    override_role(COORDINATOR_ROLE)
    response = await client.request(method, path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden",
    ["id", "id_user", "id_status", "status", "created_at"],
)
async def test_expense_create_rejects_mass_assignment(
    client, override_role, forbidden
):
    override_role(ADMIN_ROLE)
    response = await client.post(
        "/api/expenses",
        json={**EXPENSE_BODY, forbidden: str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"status": "draft"},
        {"status": "inventado"},
        {"status": "pending", "id_user": str(uuid.uuid4())},
        {"status": "pending", "id_status": 3},
    ],
)
async def test_expense_status_rejects_mass_assignment(
    client, override_role, body
):
    override_role(ADMIN_ROLE)
    response = await client.patch(
        f"/api/expenses/{EXPENSE_ID}/status", json=body
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"total_cost": "0"},
        {"total_cost": "-10"},
        {"total_cost": "1.234"},
        {"criterion": "otro"},
        {"closed_by": str(uuid.uuid4())},
        {"closed_at": "2026-12-31T00:00:00Z"},
    ],
)
async def test_overhead_create_rejects_invalid_bodies(
    client, override_role, overrides
):
    override_role(ADMIN_ROLE)
    response = await client.post(
        "/api/overhead/periods", json={**PERIOD_BODY, **overrides}
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_overhead_preview_rejects_invalid_criterion(
    client, override_role
):
    override_role(ADMIN_ROLE)
    response = await client.get(
        "/api/overhead/preview?start_date=2026-12-01&end_date=2026-12-31"
        "&total_cost=10&criterion=otro"
    )
    assert response.status_code == 422, response.text
