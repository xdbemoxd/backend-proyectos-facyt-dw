"""Contrato HTTP de inventario, lotes, despachos y proveedores.

- Sin token: todos los endpoints responden 401 sin tocar la base.
- Con un usuario simulado (dependency override) se comprueban los 403 de rol
  y los 422 de validacion estricta, sin base de datos.
"""

from __future__ import annotations

import uuid

import pytest

from src.schema.user import CurrentUser
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

SUPPLIER_ITEM_ID = str(uuid.uuid4())
VENDOR_ID = str(uuid.uuid4())

RAW_MATERIAL_BODY = {
    "name": "Ácido sulfúrico 98%",
    "description": "Reactivo",
    "resource_type_id": 3,
    "unit": "L",
    "vendors_id": VENDOR_ID,
    "minimun_stock": 5,
}
RAW_MATERIAL_PATCH = {
    "name": "Ácido sulfúrico 98%",
    "resource_type_id": 3,
    "unit": "L",
    "minimun_stock": 6,
}
LOT_BODY = {
    "id_raw_materials_supplier": SUPPLIER_ITEM_ID,
    "in_date": "2026-11-01T12:00:00Z",
    "count_raw_materials_in": 2.5,
    "unit_cost": 1.25,
    "invoice_reference": "FAC-1",
}
DISPATCH_BODY = {
    "id_raw_materials_suppliers": SUPPLIER_ITEM_ID,
    "id_project": str(uuid.uuid4()),
    "use_raw_materials": 1.5,
    "notes": "Retiro",
}
VENDOR_BODY = {
    "name": "Proveedor de prueba",
    "description": "Reactivos",
    "contact_email": "ventas@example.com",
    "contact_phone": "+58 414-1234567",
}
VENDOR_PATCH = {
    **VENDOR_BODY,
    "expected_updated_at": "2026-11-24T12:00:00Z",
}


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
    """Sustituye get_current_user por un actor con el rol indicado."""
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
        ("GET", "/api/inventory/stock", None),
        ("GET", f"/api/inventory/stock/{SUPPLIER_ITEM_ID}", None),
        ("GET", "/api/inventory/catalogs", None),
        ("GET", "/api/inventory/lots", None),
        ("POST", "/api/inventory/lots", LOT_BODY),
        ("GET", "/api/inventory/dispatch", None),
        ("POST", "/api/inventory/dispatch", DISPATCH_BODY),
        ("POST", "/api/inventory/raw-materials", RAW_MATERIAL_BODY),
        (
            "PATCH",
            f"/api/inventory/raw-materials/{SUPPLIER_ITEM_ID}",
            RAW_MATERIAL_PATCH,
        ),
        ("GET", "/api/vendors", None),
        ("GET", f"/api/vendors/{VENDOR_ID}", None),
        ("POST", "/api/vendors", VENDOR_BODY),
        ("PATCH", f"/api/vendors/{VENDOR_ID}", VENDOR_PATCH),
    ],
)
async def test_inventory_endpoints_require_token(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "No autenticado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/inventory/raw-materials", RAW_MATERIAL_BODY),
        (
            "PATCH",
            f"/api/inventory/raw-materials/{SUPPLIER_ITEM_ID}",
            RAW_MATERIAL_PATCH,
        ),
        ("POST", "/api/inventory/lots", LOT_BODY),
        ("POST", "/api/inventory/dispatch", DISPATCH_BODY),
        ("GET", "/api/vendors", None),
        ("GET", f"/api/vendors/{VENDOR_ID}", None),
        ("POST", "/api/vendors", VENDOR_BODY),
        ("PATCH", f"/api/vendors/{VENDOR_ID}", VENDOR_PATCH),
    ],
)
async def test_coordinator_cannot_mutate_inventory_or_vendors(
    client, override_role, method, path, body
):
    override_role(COORDINATOR_ROLE)
    response = await client.request(method, path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/inventory/raw-materials",
            {**RAW_MATERIAL_BODY, "current_stock": 100},
        ),
        (
            "PATCH",
            f"/api/inventory/raw-materials/{SUPPLIER_ITEM_ID}",
            {**RAW_MATERIAL_PATCH, "current_stock": 100},
        ),
        (
            "POST",
            "/api/inventory/lots",
            {**LOT_BODY, "count_raw_materials_in": 0},
        ),
        (
            "POST",
            "/api/inventory/dispatch",
            {**DISPATCH_BODY, "use_raw_materials": -1},
        ),
        (
            "POST",
            "/api/vendors",
            {**VENDOR_BODY, "id": VENDOR_ID},
        ),
        (
            "PATCH",
            f"/api/vendors/{VENDOR_ID}",
            {**VENDOR_PATCH, "updated_at": "2026-01-01T00:00:00Z"},
        ),
    ],
)
async def test_strict_validation_rejects_mass_assignment(
    client, override_role, method, path, body
):
    override_role(ADMIN_ROLE)
    response = await client.request(method, path, json=body)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_approver_is_allowed_by_role_guard(client, override_role):
    """El 403 no aplica al aprobador; el body invalido responde 422."""
    override_role(APPROVER_ROLE)
    response = await client.post(
        "/api/inventory/lots",
        json={**LOT_BODY, "count_raw_materials_in": 0},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_admin_is_allowed_by_role_guard(client, override_role):
    override_role(ADMIN_ROLE)
    response = await client.post(
        "/api/inventory/dispatch",
        json={**DISPATCH_BODY, "use_raw_materials": 0},
    )
    assert response.status_code == 422, response.text
