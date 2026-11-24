"""Contrato HTTP de los endpoints de proyectos: sin token siempre 401.

No requiere base de datos: ``get_current_user`` rechaza antes de consultarla.
"""

from __future__ import annotations

import uuid

import pytest

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
