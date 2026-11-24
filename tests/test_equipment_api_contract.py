"""Contrato HTTP de equipos y reservas (sin base de datos).

- Sin token: todos los endpoints responden 401.
- Con un usuario simulado (dependency override) se comprueban los 403 de rol
  y los 422 de validacion estricta antes de tocar la BD.
"""

from __future__ import annotations

import uuid

import pytest

from src.schema.user import CurrentUser
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

UNIT_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
VENDOR_ID = str(uuid.uuid4())

EQUIPMENT_BODY = {
    "name": "Espectrofotómetro de prueba",
    "description": "Equipo de laboratorio",
    "id_vendors": VENDOR_ID,
    "id_status": 1,
    "model": "UV-1800",
    "serial_number": "SN-TEST-001",
}
EQUIPMENT_PATCH = {
    "name": "Espectrofómetro de prueba",
    "description": "Equipo de laboratorio",
    "id_status": 1,
    "model": "UV-1800",
    "serial_number": "SN-TEST-001",
    "expected_updated_at": "2026-11-24T12:00:00Z",
}
RESERVATION_BODY = {
    "id_equipament_supplier": UNIT_ID,
    "id_project": PROJECT_ID,
    "reservation_date": "2026-12-10T08:00:00Z",
    "end_date": "2026-12-10T10:00:00Z",
    "purpose": "Medición de muestras",
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
        ("GET", "/api/equipment", None),
        ("GET", f"/api/equipment/{UNIT_ID}", None),
        ("GET", "/api/equipment/catalogs", None),
        ("GET", "/api/equipment/reservations", None),
        ("POST", "/api/equipment", EQUIPMENT_BODY),
        ("PATCH", f"/api/equipment/{UNIT_ID}", EQUIPMENT_PATCH),
        ("POST", "/api/equipment/reservations", RESERVATION_BODY),
    ],
)
async def test_equipment_endpoints_require_token(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "No autenticado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/equipment", EQUIPMENT_BODY),
        ("PATCH", f"/api/equipment/{UNIT_ID}", EQUIPMENT_PATCH),
    ],
)
async def test_coordinator_cannot_mutate_equipment(
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
            "/api/equipment",
            {**EQUIPMENT_BODY, "id": str(uuid.uuid4())},
        ),
        (
            "POST",
            "/api/equipment",
            {**EQUIPMENT_BODY, "pending_reservations": 2},
        ),
        (
            "PATCH",
            f"/api/equipment/{UNIT_ID}",
            {**EQUIPMENT_PATCH, "updated_at": "2026-01-01T00:00:00Z"},
        ),
        (
            "PATCH",
            f"/api/equipment/{UNIT_ID}",
            {k: v for k, v in EQUIPMENT_PATCH.items()
             if k != "expected_updated_at"},
        ),
        (
            "POST",
            "/api/equipment/reservations",
            {**RESERVATION_BODY, "id_user": str(uuid.uuid4())},
        ),
        (
            "POST",
            "/api/equipment/reservations",
            {**RESERVATION_BODY, "id": str(uuid.uuid4())},
        ),
        (
            "POST",
            "/api/equipment/reservations",
            {
                **RESERVATION_BODY,
                "end_date": RESERVATION_BODY["reservation_date"],
            },
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
async def test_approver_is_allowed_by_equipment_role_guard(
    client, override_role
):
    """El 403 no aplica al aprobador; el body invalido responde 422."""
    override_role(APPROVER_ROLE)
    response = await client.post(
        "/api/equipment", json={**EQUIPMENT_BODY, "name": "x"}
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_coordinator_is_allowed_to_attempt_a_valid_reservation(
    client, override_role
):
    """Reservar esta permitido a los tres roles; el body invalido da 422."""
    override_role(COORDINATOR_ROLE)
    response = await client.post(
        "/api/equipment/reservations",
        json={
            **RESERVATION_BODY,
            "end_date": RESERVATION_BODY["reservation_date"],
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_equipment_detail_rejects_non_uuid(client, override_role):
    override_role(ADMIN_ROLE)
    response = await client.get("/api/equipment/no-es-uuid")
    assert response.status_code == 422, response.text
