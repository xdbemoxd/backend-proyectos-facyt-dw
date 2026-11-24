"""Pruebas LIVE contra la base real: equipos y reservas (solo lectura segura).

Se habilitan con ``EQUIPMENT_LIVE_TESTS=1`` (ver ``tests/conftest.py``).

Que se comprueba:

- listado, detalle y catalogos legibles por los tres roles;
- alcance del coordinador en reservas (solo sus proyectos) y 403 con un
  proyecto ajeno;
- 403 del coordinador en POST/PATCH de equipos sin escribir (si el guard de rol
  fallara, el servicio responde 422/404 antes de insertar);
- una reserva que choca con otra existente responde 409 y **no inserta**:
  antes se verifica que el trigger ``trg_reservation_no_overlap`` exista y
  despues se compara el conteo de filas.

Los 403/409 dejan eventos en ``audit_log`` (append-only); no se borra nada.
Los tokens se firman en memoria y nunca se imprimen.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

from src.config.db import AsyncSessionLocal
from src.model.equipment import (
    EquipamentSupplierModel,
    ReservationEquipamentModel,
    StatusEquipamentModel,
)
from src.model.project import ProjectModel
from src.model.user import RolModel, UserModel
from src.utils.other import generate_jwt_to_frontend
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("EQUIPMENT_LIVE_TESTS", "").strip().lower()
        not in ("1", "true", "yes"),
        reason="Definir EQUIPMENT_LIVE_TESTS=1 para pruebas live",
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

    return {
        role: generate_jwt_to_frontend(
            by_email[email].id,
            by_email[email].email,
            role,
            int(by_email[email].token_version or 0),
        )
        for role, email in SEEDED_USERS.items()
    }


@pytest.fixture
async def role_tokens():
    return await _tokens_by_role()


@pytest.fixture
async def coordinator_id(role_tokens):  # noqa: ARG001
    async with AsyncSessionLocal() as db:
        user_id = await db.scalar(
            select(UserModel.id).where(
                UserModel.email == SEEDED_USERS[COORDINATOR_ROLE]
            )
        )
    if user_id is None:
        pytest.skip("No existe el coordinador semilla")
    return user_id


async def _reservation_count() -> int:
    async with AsyncSessionLocal() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(
                    ReservationEquipamentModel
                )
            )
            or 0
        )


async def _overlap_source() -> dict | None:
    """Reserva existente cuya unidad no esta en mantenimiento."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(
                    ReservationEquipamentModel.id,
                    ReservationEquipamentModel.id_equipament_supplier,
                    ReservationEquipamentModel.id_project,
                    ReservationEquipamentModel.reservation_date,
                    ReservationEquipamentModel.end_date,
                    StatusEquipamentModel.name,
                )
                .join(
                    EquipamentSupplierModel,
                    EquipamentSupplierModel.id
                    == ReservationEquipamentModel.id_equipament_supplier,
                )
                .join(
                    StatusEquipamentModel,
                    StatusEquipamentModel.id
                    == EquipamentSupplierModel.id_status,
                )
                .where(
                    func.lower(StatusEquipamentModel.name)
                    != "en mantenimiento"
                )
                .order_by(ReservationEquipamentModel.reservation_date.asc())
                .limit(1)
            )
        ).one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "unit_id": row.id_equipament_supplier,
        "project_id": row.id_project,
        "reservation_date": row.reservation_date.isoformat(),
        "end_date": row.end_date.isoformat(),
    }


async def _trigger_exists() -> bool:
    async with AsyncSessionLocal() as db:
        return bool(
            await db.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'trg_reservation_no_overlap' "
                    "AND NOT tgisinternal"
                )
            )
        )


@pytest.mark.asyncio
async def test_equipment_reads_are_available_for_every_role(
    client, role_tokens
):
    for role, token in role_tokens.items():
        listed = await client.get(
            "/api/equipment?limit=200", headers=auth(token)
        )
        assert listed.status_code == 200, (role, listed.text)
        body = listed.json()
        assert body["total"] >= 1, role
        assert isinstance(body["items"], list)
        first = body["items"][0]
        for key in (
            "id",
            "equipment",
            "vendor",
            "status",
            "model",
            "serial_number",
            "create_at",
            "updated_at",
            "pending_reservations",
        ):
            assert key in first, (role, key)

        detail = await client.get(
            f"/api/equipment/{first['id']}", headers=auth(token)
        )
        assert detail.status_code == 200, (role, detail.text)
        assert detail.json()["id"] == first["id"]

        catalogs = await client.get(
            "/api/equipment/catalogs", headers=auth(token)
        )
        assert catalogs.status_code == 200, (role, catalogs.text)
        assert len(catalogs.json()["statuses"]) >= 1
        assert len(catalogs.json()["vendors"]) >= 1


@pytest.mark.asyncio
async def test_equipment_filters_work(client, role_tokens):
    token = role_tokens[ADMIN_ROLE]
    listed = await client.get(
        "/api/equipment?limit=1", headers=auth(token)
    )
    assert listed.status_code == 200, listed.text
    unit = listed.json()["items"][0]

    by_status = await client.get(
        f"/api/equipment?id_status={unit['status']['id']}&limit=200",
        headers=auth(token),
    )
    assert by_status.status_code == 200, by_status.text
    assert all(
        item["status"]["id"] == unit["status"]["id"]
        for item in by_status.json()["items"]
    )

    if unit["serial_number"]:
        by_serial = await client.get(
            f"/api/equipment?search={unit['serial_number']}&limit=200",
            headers=auth(token),
        )
        assert by_serial.status_code == 200, by_serial.text
        assert any(
            item["serial_number"] == unit["serial_number"]
            for item in by_serial.json()["items"]
        )


@pytest.mark.asyncio
async def test_reservations_scope_for_coordinator(
    client, role_tokens, coordinator_id
):
    admin = await client.get(
        "/api/equipment/reservations?limit=200",
        headers=auth(role_tokens[ADMIN_ROLE]),
    )
    assert admin.status_code == 200, admin.text
    admin_body = admin.json()

    coordinator = await client.get(
        "/api/equipment/reservations?limit=200",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
    )
    assert coordinator.status_code == 200, coordinator.text
    coordinator_body = coordinator.json()
    assert coordinator_body["total"] <= admin_body["total"]

    async with AsyncSessionLocal() as db:
        own_ids = set(
            (
                await db.execute(
                    select(ProjectModel.id).where(
                        ProjectModel.id_user == coordinator_id
                    )
                )
            ).scalars().all()
        )
        foreign_project = await db.scalar(
            select(ProjectModel.id).where(
                ProjectModel.id_user != coordinator_id
            ).limit(1)
        )

    for item in coordinator_body["items"]:
        assert uuid.UUID(item["project"]["id"]) in own_ids, item

    if own_ids:
        own = await client.get(
            f"/api/equipment/reservations?project_id="
            f"{next(iter(own_ids))}&limit=200",
            headers=auth(role_tokens[COORDINATOR_ROLE]),
        )
        assert own.status_code == 200, own.text

    if foreign_project is not None:
        forbidden = await client.get(
            f"/api/equipment/reservations?project_id={foreign_project}",
            headers=auth(role_tokens[COORDINATOR_ROLE]),
        )
        assert forbidden.status_code == 403, forbidden.text


@pytest.mark.asyncio
async def test_coordinator_cannot_create_or_edit_equipment(
    client, role_tokens
):
    token = role_tokens[COORDINATOR_ROLE]
    catalogs = await client.get(
        "/api/equipment/catalogs", headers=auth(token)
    )
    assert catalogs.status_code == 200, catalogs.text
    status_id = catalogs.json()["statuses"][0]["id"]

    # Si el guard fallara, el proveedor inexistente corta en 422 sin insertar.
    create = await client.post(
        "/api/equipment",
        headers=auth(token),
        json={
            "name": "Intento live de coordinador",
            "description": None,
            "id_vendors": str(uuid.uuid4()),
            "id_status": status_id,
            "model": None,
            "serial_number": None,
        },
    )
    assert create.status_code == 403, create.text

    # Si el guard fallara, la unidad inexistente corta en 404 sin escribir.
    update = await client.patch(
        f"/api/equipment/{uuid.uuid4()}",
        headers=auth(token),
        json={
            "name": "Intento live",
            "description": None,
            "id_status": status_id,
            "model": None,
            "serial_number": None,
            "expected_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert update.status_code == 403, update.text


@pytest.mark.asyncio
async def test_overlapping_reservation_is_rejected_without_insert(
    client, role_tokens
):
    if not await _trigger_exists():
        pytest.skip("La base real no tiene trg_reservation_no_overlap")
    source = await _overlap_source()
    if source is None:
        pytest.skip("No hay reservas existentes con unidad reservable")

    before = await _reservation_count()
    response = await client.post(
        "/api/equipment/reservations",
        headers=auth(role_tokens[ADMIN_ROLE]),
        json={
            "id_equipament_supplier": str(source["unit_id"]),
            "id_project": str(source["project_id"]),
            "reservation_date": source["reservation_date"],
            "end_date": source["end_date"],
            "purpose": "Prueba live de solape",
        },
    )
    assert response.status_code == 409, response.text
    assert "solapa" in response.json()["detail"].lower()
    assert await _reservation_count() == before


@pytest.mark.asyncio
async def test_coordinator_cannot_reserve_foreign_project(
    client, role_tokens, coordinator_id
):
    if not await _trigger_exists():
        pytest.skip("La base real no tiene trg_reservation_no_overlap")
    source = await _overlap_source()
    if source is None:
        pytest.skip("No hay reservas existentes con unidad reservable")

    async with AsyncSessionLocal() as db:
        foreign_project = await db.scalar(
            select(ProjectModel.id).where(
                ProjectModel.id_user != coordinator_id
            ).limit(1)
        )
    if foreign_project is None:
        pytest.skip("No hay un proyecto ajeno para el coordinador")

    before = await _reservation_count()
    response = await client.post(
        "/api/equipment/reservations",
        headers=auth(role_tokens[COORDINATOR_ROLE]),
        json={
            "id_equipament_supplier": str(source["unit_id"]),
            "id_project": str(foreign_project),
            # Mismo horario: si el alcance fallara, el trigger corta en 409.
            "reservation_date": source["reservation_date"],
            "end_date": source["end_date"],
            "purpose": "Prueba live de alcance",
        },
    )
    assert response.status_code == 403, response.text
    assert await _reservation_count() == before


@pytest.mark.asyncio
async def test_invalid_bodies_are_rejected_without_writing(
    client, role_tokens
):
    token = role_tokens[ADMIN_ROLE]
    before = await _reservation_count()

    bad_range = await client.post(
        "/api/equipment/reservations",
        headers=auth(token),
        json={
            "id_equipament_supplier": str(uuid.uuid4()),
            "id_project": str(uuid.uuid4()),
            "reservation_date": "2026-12-10T10:00:00Z",
            "end_date": "2026-12-10T08:00:00Z",
        },
    )
    assert bad_range.status_code == 422, bad_range.text

    mass_assignment = await client.post(
        "/api/equipment",
        headers=auth(token),
        json={
            "name": "Equipo live",
            "description": None,
            "id_vendors": str(uuid.uuid4()),
            "id_status": 1,
            "model": None,
            "serial_number": None,
            "pending_reservations": 99,
        },
    )
    assert mass_assignment.status_code == 422, mass_assignment.text
    assert await _reservation_count() == before
