"""Contrato HTTP de los endpoints de proyectos: sin token siempre 401.

No requiere base de datos: ``get_current_user`` rechaza antes de consultarla.
"""

from __future__ import annotations

import uuid

import pytest

from src.schema.user import CurrentUser
from src.utils.roles import COORDINATOR_ROLE

PROJECT_ID = str(uuid.uuid4())
VALID_UPDATE = {
    "name": "Proyecto actualizado",
    "description": None,
    "id_status": 2,
    "expected_updated_at": "2026-11-24T12:00:00Z",
}
VALID_PRIORITY = {"id_priority": 1, "explaining": "Motivo suficiente"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/projects", None),
        ("GET", f"/api/projects/{PROJECT_ID}", None),
        ("GET", "/api/projects/catalogs", None),
        ("GET", "/api/projects/priority-log", None),
        ("PATCH", f"/api/projects/{PROJECT_ID}", VALID_UPDATE),
        ("PATCH", f"/api/projects/{PROJECT_ID}/priority", VALID_PRIORITY),
    ],
)
async def test_project_endpoints_require_token(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "No autenticado"


@pytest.fixture
def coordinator_override():
    from app import app
    from src.utils.auth import get_current_user

    actor = CurrentUser(
        id=uuid.uuid4(),
        email="coordinador@example.com",
        name="Coordinador",
        lastname="Prueba",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=COORDINATOR_ROLE,
    )
    app.dependency_overrides[get_current_user] = lambda: actor
    yield actor
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id_status", 2),
        ("progress", 50),
        ("budget_execution", 50),
    ],
)
async def test_coordinator_cannot_patch_restricted_fields(
    client, coordinator_override, field, value
):
    response = await client.patch(
        f"/api/projects/{PROJECT_ID}",
        json={
            "expected_updated_at": "2026-11-24T12:00:00Z",
            field: value,
        },
    )
    assert response.status_code == 403, response.text
    assert "nombre y la descripción" in response.json()["detail"]
