"""Pruebas LIVE de SOLO LECTURA contra la base real configurada en .env.

Se habilitan explicitamente con ``INVENTORY_LIVE_TESTS=1`` (ver
``tests/conftest.py``). No insertan lotes, despachos, materias primas ni
proveedores: solo hacen GET y comprueban 403/422 que se resuelven antes de
escribir en las tablas de negocio.

Los 403 denegados si dejan un evento en ``audit_log`` (comportamiento normal
del sistema, append-only); no se borra ni se modifica auditoria.

Los tokens se firman en memoria para los usuarios semilla de cada rol y nunca
se imprimen.
"""

from __future__ import annotations

import os
import uuid

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
        os.getenv("INVENTORY_LIVE_TESTS", "").strip().lower()
        not in ("1", "true", "yes"),
        reason="Definir INVENTORY_LIVE_TESTS=1 para pruebas live de solo lectura",
    ),
]

# Usuarios semilla de db/seeds/02_users_projects.sql. Si no existen en la base
# real, las pruebas se omiten con un mensaje claro (no se inventan actores).
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
async def stock_item(role_tokens, client):
    response = await client.get(
        "/api/inventory/stock?limit=1", headers=auth(role_tokens[ADMIN_ROLE])
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    if not items:
        pytest.skip("La base real no tiene filas de stock")
    return items[0]


@pytest.mark.asyncio
async def test_stock_and_single_item_are_readable_for_every_role(
    client, role_tokens
):
    for role, token in role_tokens.items():
        listed = await client.get(
            "/api/inventory/stock?limit=10", headers=auth(token)
        )
        assert listed.status_code == 200, (role, listed.text)
        body = listed.json()
        assert body["limit"] == 10
        assert body["total"] >= 1
        first = body["items"][0]
        for key in (
            "supplier_item_id",
            "resource_id",
            "resource_name",
            "resource_type",
            "unit",
            "vendor_name",
            "current_stock",
            "minimun_stock",
            "stock_status",
            "is_critical",
            "has_movements",
        ):
            assert key in first, (role, key)

        detail = await client.get(
            f"/api/inventory/stock/{first['supplier_item_id']}",
            headers=auth(token),
        )
        assert detail.status_code == 200, (role, detail.text)
        assert detail.json()["supplier_item_id"] == first["supplier_item_id"]


@pytest.mark.asyncio
async def test_catalogs_and_lots_are_readable_for_every_role(
    client, role_tokens
):
    for role, token in role_tokens.items():
        catalogs = await client.get(
            "/api/inventory/catalogs", headers=auth(token)
        )
        assert catalogs.status_code == 200, (role, catalogs.text)
        assert len(catalogs.json()["resource_types"]) >= 1
        assert len(catalogs.json()["vendors"]) >= 1

        lots = await client.get(
            "/api/inventory/lots?limit=5", headers=auth(token)
        )
        assert lots.status_code == 200, (role, lots.text)
        assert lots.json()["total"] >= 1


@pytest.mark.asyncio
async def test_dispatch_log_is_readable_for_every_role(client, role_tokens):
    for role, token in role_tokens.items():
        response = await client.get(
            "/api/inventory/dispatch?limit=5", headers=auth(token)
        )
        assert response.status_code == 200, (role, response.text)
        body = response.json()
        assert body["limit"] == 5
        assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_stock_filters_work(client, role_tokens):
    token = role_tokens[ADMIN_ROLE]

    critical = await client.get(
        "/api/inventory/stock?only_critical=true&limit=200",
        headers=auth(token),
    )
    assert critical.status_code == 200, critical.text
    assert all(item["is_critical"] for item in critical.json()["items"])

    reactivos = await client.get(
        "/api/inventory/stock?resource_type=Reactivo&limit=200",
        headers=auth(token),
    )
    assert reactivos.status_code == 200, reactivos.text
    assert all(
        item["resource_type"] == "Reactivo"
        for item in reactivos.json()["items"]
    )

    searched = await client.get(
        "/api/inventory/stock?search=ácido&limit=200", headers=auth(token)
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["total"] >= 1


@pytest.mark.asyncio
async def test_vendors_are_admin_or_approver_only(client, role_tokens):
    for role in (ADMIN_ROLE, APPROVER_ROLE):
        listed = await client.get(
            "/api/vendors?limit=5", headers=auth(role_tokens[role])
        )
        assert listed.status_code == 200, (role, listed.text)
        body = listed.json()
        assert body["total"] >= 1

    coordinator = await client.get(
        "/api/vendors?limit=5", headers=auth(role_tokens[COORDINATOR_ROLE])
    )
    assert coordinator.status_code == 403
    assert coordinator.json()["detail"] == "No autorizado"


@pytest.mark.asyncio
async def test_coordinator_cannot_mutate_inventory(client, role_tokens, stock_item):
    token = role_tokens[COORDINATOR_ROLE]
    supplier_item_id = stock_item["supplier_item_id"]

    attempts = [
        (
            "POST",
            "/api/inventory/raw-materials",
            {
                "name": "Material de prueba live",
                "description": None,
                "resource_type_id": 1,
                "unit": "unidad",
                "vendors_id": str(uuid.uuid4()),
                "minimun_stock": 1,
            },
        ),
        (
            "PATCH",
            f"/api/inventory/raw-materials/{supplier_item_id}",
            {"minimun_stock": 1},
        ),
        (
            "POST",
            "/api/inventory/lots",
            {
                "id_raw_materials_supplier": supplier_item_id,
                "count_raw_materials_in": 0.01,
                "unit_cost": 0.01,
            },
        ),
        (
            "POST",
            "/api/inventory/dispatch",
            {
                "id_raw_materials_suppliers": supplier_item_id,
                "id_project": str(uuid.uuid4()),
                "use_raw_materials": 0.01,
            },
        ),
        (
            "POST",
            "/api/vendors",
            {"name": "Proveedor de prueba live"},
        ),
    ]

    for method, path, body in attempts:
        response = await client.request(
            method, path, json=body, headers=auth(token)
        )
        assert response.status_code == 403, (path, response.text)


@pytest.mark.asyncio
async def test_coordinator_cannot_read_foreign_project_dispatch(
    client, role_tokens
):
    """IDOR: filtrar despachos por un proyecto ajeno responde 403."""
    async with AsyncSessionLocal() as db:
        coordinator = (
            await db.execute(
                select(UserModel.id, UserModel.email).where(
                    UserModel.email == SEEDED_USERS[COORDINATOR_ROLE]
                )
            )
        ).one_or_none()
        if coordinator is None:
            pytest.skip("No existe el coordinador semilla en la base real")
        foreign_project = await db.scalar(
            select(ProjectModel.id).where(
                ProjectModel.id_user != coordinator.id
            ).limit(1)
        )

    if foreign_project is None:
        pytest.skip("No hay un proyecto de otro coordinador en la base real")

    response = await client.get(
        f"/api/inventory/dispatch?project_id={foreign_project}",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_invalid_bodies_are_rejected_without_writing(
    client, role_tokens, stock_item
):
    """422 antes del servicio: no se inserta ningun lote/despacho real."""
    token = role_tokens[ADMIN_ROLE]
    supplier_item_id = stock_item["supplier_item_id"]

    lot = await client.post(
        "/api/inventory/lots",
        headers=auth(token),
        json={
            "id_raw_materials_supplier": supplier_item_id,
            "count_raw_materials_in": 0,
            "unit_cost": 0,
        },
    )
    assert lot.status_code == 422, lot.text

    dispatch = await client.post(
        "/api/inventory/dispatch",
        headers=auth(token),
        json={
            "id_raw_materials_suppliers": supplier_item_id,
            "id_project": str(uuid.uuid4()),
            "use_raw_materials": -1,
        },
    )
    assert dispatch.status_code == 422, dispatch.text

    mass_assignment = await client.post(
        "/api/inventory/raw-materials",
        headers=auth(token),
        json={
            "name": "Material live",
            "resource_type_id": 1,
            "unit": "unidad",
            "vendors_id": str(uuid.uuid4()),
            "minimun_stock": 1,
            "current_stock": 999,
        },
    )
    assert mass_assignment.status_code == 422, mass_assignment.text
