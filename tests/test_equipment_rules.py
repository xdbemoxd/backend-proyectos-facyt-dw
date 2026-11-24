"""Reglas unitarias de equipos, unidades y reservas (sin BD).

Cubre permisos, validacion estricta de schemas, paginacion, bloqueo optimista,
helpers de estado y el mapeo del trigger de solape (``23P01`` -> ``409``) sin
filtrar SQL. La regla de solape en si vive en la BD y se prueba en
``test_equipment_live.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.schema.equipment import (
    EquipmentCreate,
    EquipmentUpdate,
    ReservationCreate,
)
from src.schema.user import CurrentUser
from src.service import equipment as equipment_service
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def make_actor(role: str = ADMIN_ROLE) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="actor@example.com",
        name="Actor",
        lastname="Prueba",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=role,
    )


def equipment_payload(**overrides) -> dict:
    payload = {
        "name": "Espectrofotómetro UV-Vis",
        "description": "Equipo de laboratorio",
        "id_vendors": str(uuid.uuid4()),
        "id_status": 1,
        "model": "UV-1800",
        "serial_number": "SN-TEST-001",
    }
    payload.update(overrides)
    return payload


def update_payload(**overrides) -> dict:
    payload = {
        "name": "Espectrofotómetro UV-Vis",
        "description": "Equipo de laboratorio",
        "id_status": 1,
        "model": "UV-1800",
        "serial_number": "SN-TEST-001",
        "expected_updated_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def reservation_payload(**overrides) -> dict:
    payload = {
        "id_equipament_supplier": str(uuid.uuid4()),
        "id_project": str(uuid.uuid4()),
        "reservation_date": "2026-12-10T08:00:00Z",
        "end_date": "2026-12-10T10:00:00Z",
        "purpose": "Medición de muestras",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_manage_equipment(role):
    assert equipment_service.can_manage_equipment(role) is True


@pytest.mark.parametrize("role", [COORDINATOR_ROLE, None, "Otro rol"])
def test_coordinator_and_unknown_roles_cannot_manage_equipment(role):
    assert equipment_service.can_manage_equipment(role) is False


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_reserve_any_project(role):
    owner = uuid.uuid4()
    assert (
        equipment_service.can_reserve_for_project(
            role, uuid.uuid4(), owner
        )
        is True
    )


def test_coordinator_reserves_only_own_projects():
    coordinator = uuid.uuid4()
    assert (
        equipment_service.can_reserve_for_project(
            COORDINATOR_ROLE, coordinator, coordinator
        )
        is True
    )
    assert (
        equipment_service.can_reserve_for_project(
            COORDINATOR_ROLE, coordinator, uuid.uuid4()
        )
        is False
    )


def test_unknown_role_cannot_reserve():
    user = uuid.uuid4()
    assert (
        equipment_service.can_reserve_for_project("Otro rol", user, user)
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda actor: equipment_service.create_equipment_unit(
            None, EquipmentCreate(**equipment_payload()), actor
        ),
        lambda actor: equipment_service.update_equipment_unit(
            None,
            uuid.uuid4(),
            EquipmentUpdate(**update_payload()),
            actor,
        ),
    ],
)
async def test_coordinator_mutations_are_denied_before_db(call):
    with pytest.raises(HTTPException) as exc:
        await call(make_actor(COORDINATOR_ROLE))
    assert exc.value.status_code == 403
    assert exc.value.detail == "No autorizado"


# ---------------------------------------------------------------------------
# Validacion estricta
# ---------------------------------------------------------------------------


def test_equipment_create_is_strict_and_normalizes_optional_fields():
    created = EquipmentCreate(
        **equipment_payload(description="   ", model="", serial_number="  ")
    )
    assert created.description is None
    assert created.model is None
    assert created.serial_number is None

    for bad in (
        {"id": str(uuid.uuid4())},
        {"pending_reservations": 3},
        {"create_at": NOW.isoformat()},
        {"name": "x"},
        {"id_status": 0},
        {"model": "x" * 101},
        {"serial_number": "x" * 101},
        {"description": "x" * 1001},
    ):
        with pytest.raises(ValidationError):
            EquipmentCreate(**{**equipment_payload(), **bad})


def test_equipment_update_requires_expected_version_and_forbids_internals():
    valid = EquipmentUpdate(**update_payload())
    assert valid.expected_updated_at == NOW
    assert valid.model_dump(exclude_unset=True)["expected_updated_at"] == NOW

    partial = EquipmentUpdate(
        id_status=2, expected_updated_at=NOW.isoformat()
    )
    assert partial.model_dump(exclude_unset=True)["id_status"] == 2
    assert "name" not in partial.model_dump(exclude_unset=True)

    for bad in (
        {"updated_at": NOW.isoformat()},
        {"id": str(uuid.uuid4())},
        {"id_status": -1},
        {"name": "x"},
    ):
        with pytest.raises(ValidationError):
            EquipmentUpdate(**{**update_payload(), **bad})

    without_expected = update_payload()
    without_expected.pop("expected_updated_at")
    with pytest.raises(ValidationError):
        EquipmentUpdate(**without_expected)


def test_reservation_schema_rejects_internal_fields_and_bad_ranges():
    valid = ReservationCreate(**reservation_payload())
    assert valid.end_date > valid.reservation_date

    for bad in (
        {"id_user": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"created_date": NOW.isoformat()},
        {"purpose": "x" * 2001},
        {"end_date": "2026-12-10T07:59:59Z"},
        {"end_date": "2026-12-10T08:00:00Z"},
    ):
        with pytest.raises(ValidationError):
            ReservationCreate(**{**reservation_payload(), **bad})


def test_reservation_naive_dates_are_interpreted_as_utc():
    body = reservation_payload(
        reservation_date="2026-12-10T08:00:00",
        end_date="2026-12-10T10:00:00",
    )
    reservation = ReservationCreate(**body)
    assert reservation.reservation_date.tzinfo == timezone.utc
    assert reservation.end_date.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Helpers de estado, serial y bloqueo optimista
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("En mantenimiento", True),
        (" en mantenimiento ", True),
        ("EN MANTENIMIENTO", True),
        ("Disponible", False),
        ("En uso", False),
        (None, False),
    ],
)
def test_maintenance_detection(value, expected):
    assert equipment_service.is_maintenance(value) is expected


def test_serial_is_trimmed_and_blank_is_none():
    assert equipment_service.normalize_serial("  SN-1 ") == "SN-1"
    assert equipment_service.normalize_serial("   ") is None
    assert equipment_service.normalize_serial(None) is None


def test_stale_update_is_detected():
    assert equipment_service.is_stale_update(
        NOW - timedelta(seconds=1), NOW
    )


def test_same_instant_in_other_timezone_is_not_stale():
    same = NOW.astimezone(timezone(timedelta(hours=-4)))
    assert equipment_service.is_stale_update(same, NOW) is False


# ---------------------------------------------------------------------------
# Paginacion antes de tocar la BD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_pagination_is_rejected_before_db():
    with pytest.raises(HTTPException) as units_exc:
        await equipment_service.list_equipment_units(
            None, limit=0  # type: ignore[arg-type]
        )
    assert units_exc.value.status_code == 422

    with pytest.raises(HTTPException) as reservations_exc:
        await equipment_service.list_reservations(  # type: ignore[arg-type]
            None, make_actor(ADMIN_ROLE), offset=-1
        )
    assert reservations_exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Mapeo de errores del trigger de solape
# ---------------------------------------------------------------------------


class FakeIntegrityError(Exception):
    def __init__(self, sqlstate: str | None, message: str = ""):
        self.orig = SimpleNamespace(sqlstate=sqlstate, message=message)


def test_overlap_trigger_is_mapped_to_409_without_sql():
    exc = FakeIntegrityError(
        "23P01",
        "La unidad 0190... ya tiene una reserva que se solapa",
    )
    mapped = equipment_service.reservation_db_error(exc)  # type: ignore[arg-type]
    assert mapped.status_code == 409
    assert "solapa" in mapped.detail.lower()
    assert "reservation_equipament" not in mapped.detail
    assert "SELECT" not in mapped.detail


def test_overlap_message_without_sqlstate_is_also_409():
    exc = FakeIntegrityError(None, "exclusion violation: overlap")
    mapped = equipment_service.reservation_db_error(exc)  # type: ignore[arg-type]
    assert mapped.status_code == 409


def test_invalid_reservation_range_from_db_is_422():
    mapped = equipment_service.reservation_db_error(
        FakeIntegrityError("23514")  # type: ignore[arg-type]
    )
    assert mapped.status_code == 422


def test_invalid_reservation_fk_is_422():
    mapped = equipment_service.reservation_db_error(
        FakeIntegrityError("23503")  # type: ignore[arg-type]
    )
    assert mapped.status_code == 422


def test_unknown_reservation_db_error_is_generic_500():
    mapped = equipment_service.reservation_db_error(
        FakeIntegrityError("XX000", "boom")  # type: ignore[arg-type]
    )
    assert mapped.status_code == 500
    assert mapped.detail == "Error"


# ---------------------------------------------------------------------------
# Auditoria sin PII
# ---------------------------------------------------------------------------


def test_unit_snapshot_has_ids_and_no_pii():
    unit = SimpleNamespace(
        id=uuid.uuid4(),
        id_equipament=uuid.uuid4(),
        id_vendors=uuid.uuid4(),
        id_status=1,
        model="UV-1800",
        serial_number="SN-1",
        equipment=SimpleNamespace(name="Espectrofotómetro"),
        status=SimpleNamespace(name="Disponible"),
    )
    snapshot = equipment_service.unit_snapshot(unit)  # type: ignore[arg-type]
    assert snapshot["status"] == "Disponible"
    assert set(snapshot) == {
        "id",
        "equipment_id",
        "equipment_name",
        "vendor_id",
        "status_id",
        "status",
        "model",
        "serial_number",
    }
    assert "@" not in str(snapshot)


def test_reservation_snapshot_has_ids_and_no_user_pii():
    reservation = SimpleNamespace(
        id=uuid.uuid4(),
        id_equipament_supplier=uuid.uuid4(),
        id_project=uuid.uuid4(),
        id_user=uuid.uuid4(),
        reservation_date=NOW,
        end_date=NOW + timedelta(hours=2),
        purpose="Medición",
    )
    snapshot = equipment_service.reservation_snapshot(  # type: ignore[arg-type]
        reservation
    )
    assert snapshot["project_id"] == str(reservation.id_project)
    assert "@" not in str(snapshot)
    assert "email" not in snapshot
