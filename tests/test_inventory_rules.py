"""Reglas unitarias de inventario, lotes, despachos y proveedores (sin BD).

Cubre validacion estricta de schemas, permisos por rol, paginacion y el mapeo
de errores de la base de datos (stock insuficiente / FK invalida) sin filtrar
SQL ni trazas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.schema.inventory import (
    DispatchCreate,
    LotCreate,
    RawMaterialCreate,
    RawMaterialUpdate,
    StockItemOut,
)
from src.schema.user import CurrentUser
from src.schema.vendor import VendorCreate, VendorUpdate
from src.service import inventory as inventory_service
from src.service import vendor as vendor_service
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


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


def raw_material_payload(**overrides) -> dict:
    payload = {
        "name": "Ácido sulfúrico 98%",
        "description": "Reactivo de laboratorio",
        "resource_type_id": 3,
        "unit": "L",
        "vendors_id": str(uuid.uuid4()),
        "minimun_stock": 5,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_manage_inventory(role):
    assert inventory_service.can_manage_inventory(role) is True


@pytest.mark.parametrize("role", [COORDINATOR_ROLE, None, "Otro rol"])
def test_coordinator_and_unknown_roles_cannot_manage(role):
    assert inventory_service.can_manage_inventory(role) is False


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_manage_vendors(role):
    assert vendor_service.can_manage_vendors(role) is True


@pytest.mark.parametrize("role", [COORDINATOR_ROLE, None])
def test_coordinator_cannot_manage_vendors(role):
    assert vendor_service.can_manage_vendors(role) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda actor: inventory_service.create_raw_material(
            None, RawMaterialCreate(**raw_material_payload()), actor
        ),
        lambda actor: inventory_service.update_raw_material(
            None,
            uuid.uuid4(),
            RawMaterialUpdate(name="Otro nombre"),
            actor,
        ),
        lambda actor: inventory_service.create_lot(
            None,
            LotCreate(
                id_raw_materials_supplier=uuid.uuid4(),
                count_raw_materials_in=1,
                unit_cost=1,
            ),
            actor,
        ),
        lambda actor: inventory_service.create_dispatch(
            None,
            DispatchCreate(
                id_raw_materials_suppliers=uuid.uuid4(),
                id_project=uuid.uuid4(),
                use_raw_materials=1,
            ),
            actor,
        ),
    ],
)
async def test_coordinator_mutations_are_denied_before_db(call):
    with pytest.raises(HTTPException) as exc:
        await call(make_actor(COORDINATOR_ROLE))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_vendor_mutations_are_denied_for_coordinator():
    actor = make_actor(COORDINATOR_ROLE)
    with pytest.raises(HTTPException) as create_exc:
        await vendor_service.create_vendor(
            None, VendorCreate(name="Proveedor X"), actor
        )
    assert create_exc.value.status_code == 403

    with pytest.raises(HTTPException) as update_exc:
        await vendor_service.update_vendor(
            None,
            uuid.uuid4(),
            VendorUpdate(name="Proveedor Y", expected_updated_at=NOW),
            actor,
        )
    assert update_exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Validacion estricta (Pydantic)
# ---------------------------------------------------------------------------


def test_raw_material_create_rejects_internal_and_invalid_values():
    RawMaterialCreate(**raw_material_payload())

    with pytest.raises(ValidationError):
        RawMaterialCreate(**raw_material_payload(current_stock=100))
    with pytest.raises(ValidationError):
        RawMaterialCreate(**raw_material_payload(name="x"))
    with pytest.raises(ValidationError):
        RawMaterialCreate(**raw_material_payload(resource_type_id=0))
    with pytest.raises(ValidationError):
        RawMaterialCreate(**raw_material_payload(minimun_stock=-1))
    with pytest.raises(ValidationError):
        RawMaterialCreate(**raw_material_payload(unit="x" * 31))


def test_raw_material_update_is_patch_and_forbids_stock():
    payload = RawMaterialUpdate(name="Nuevo nombre")
    assert payload.model_dump(exclude_unset=True) == {"name": "Nuevo nombre"}

    with pytest.raises(ValidationError):
        RawMaterialUpdate(current_stock=10)
    with pytest.raises(ValidationError):
        RawMaterialUpdate(minimun_stock=-0.01)


def test_lot_create_enforces_positive_quantity_and_cost():
    valid = LotCreate(
        id_raw_materials_supplier=uuid.uuid4(),
        in_date=NOW - timedelta(days=1),
        count_raw_materials_in="2.50",
        unit_cost="1.25",
    )
    assert str(valid.count_raw_materials_in) == "2.50"

    for bad in (
        {"count_raw_materials_in": 0},
        {"count_raw_materials_in": -1},
        {"unit_cost": -0.01},
        {"invoice_reference": "x" * 101},
        {"current_stock": 5},
    ):
        body = {
            "id_raw_materials_supplier": str(uuid.uuid4()),
            "count_raw_materials_in": 1,
            "unit_cost": 0,
            **bad,
        }
        with pytest.raises(ValidationError):
            LotCreate(**body)


def test_lot_create_rejects_future_entry_date():
    with pytest.raises(ValidationError):
        LotCreate(
            id_raw_materials_supplier=uuid.uuid4(),
            in_date=datetime.now(timezone.utc) + timedelta(days=1),
            count_raw_materials_in=1,
            unit_cost=0,
        )


def test_lot_create_accepts_naive_date_as_utc():
    lot = LotCreate(
        id_raw_materials_supplier=uuid.uuid4(),
        in_date=datetime(2026, 11, 1, 10, 0),
        count_raw_materials_in=1,
        unit_cost=0,
    )
    assert lot.in_date is not None
    assert lot.in_date.tzinfo == timezone.utc


def test_dispatch_create_rejects_mass_assignment_and_invalid_quantity():
    valid = DispatchCreate(
        id_raw_materials_suppliers=str(uuid.uuid4()),
        id_project=str(uuid.uuid4()),
        use_raw_materials="1.50",
        notes="Retiro para síntesis",
    )
    assert valid.use_raw_materials > 0

    base = {
        "id_raw_materials_suppliers": str(uuid.uuid4()),
        "id_project": str(uuid.uuid4()),
        "use_raw_materials": 1,
    }
    for bad in (
        {"use_raw_materials": 0},
        {"use_raw_materials": -1},
        {"id_user": str(uuid.uuid4())},
        {"material_requested_at": NOW.isoformat()},
        {"notes": "x" * 1001},
    ):
        with pytest.raises(ValidationError):
            DispatchCreate(**{**base, **bad})


def test_vendor_schemas_are_strict():
    create = VendorCreate(
        name="Proveedor Nuevo",
        description="Reactivos",
        contact_email="ventas@example.com",
        contact_phone="+58 414-1234567",
    )
    assert create.name == "Proveedor Nuevo"

    with pytest.raises(ValidationError):
        VendorCreate(name="Proveedor Nuevo", id=str(uuid.uuid4()))
    with pytest.raises(ValidationError):
        VendorCreate(name="x")
    with pytest.raises(ValidationError):
        VendorCreate(name="Proveedor", contact_email="no-es-correo")

    with pytest.raises(ValidationError):
        VendorUpdate(name="Proveedor", expected_updated_at="2026-11-24T00:00:00Z", id=str(uuid.uuid4()))
    with pytest.raises(ValidationError):
        VendorUpdate(name="Proveedor")


def test_stock_item_accepts_view_shape_and_decimal_strings():
    item = StockItemOut.model_validate(
        {
            "supplier_item_id": uuid.uuid4(),
            "resource_id": uuid.uuid4(),
            "resource_name": "Ácido clorhídrico",
            "resource_type": "Reactivo",
            "unit": "L",
            "vendor_name": "Proveedor",
            "current_stock": "12.50",
            "minimun_stock": "5.00",
            "stock_status": "Normal",
            "is_critical": False,
        }
    )
    assert item.current_stock == 12.5
    assert item.has_movements is False


# ---------------------------------------------------------------------------
# Paginacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_pagination_is_rejected_before_db():
    with pytest.raises(HTTPException) as stock_exc:
        await inventory_service.list_stock(None, limit=0)  # type: ignore[arg-type]
    assert stock_exc.value.status_code == 422

    with pytest.raises(HTTPException) as lots_exc:
        await inventory_service.list_lots(None, limit=201)  # type: ignore[arg-type]
    assert lots_exc.value.status_code == 422

    with pytest.raises(HTTPException) as dispatch_exc:
        await inventory_service.list_dispatches(  # type: ignore[arg-type]
            None, make_actor(ADMIN_ROLE), offset=-1
        )
    assert dispatch_exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Mapeo de errores de la BD
# ---------------------------------------------------------------------------


class FakeIntegrityError(Exception):
    def __init__(self, sqlstate: str | None, message: str = ""):
        self.orig = SimpleNamespace(sqlstate=sqlstate, message=message)


def test_insufficient_stock_is_mapped_to_409_without_sql():
    exc = FakeIntegrityError(
        "23514", "Stock insuficiente: disponible 2.00, solicitado 5.00"
    )
    mapped = inventory_service.dispatch_db_error(exc)  # type: ignore[arg-type]
    assert mapped.status_code == 409
    assert "insuficiente" in mapped.detail.lower()
    assert "SELECT" not in mapped.detail
    assert "dispatch_log" not in mapped.detail


def test_stock_message_without_sqlstate_is_also_409():
    exc = FakeIntegrityError(None, "Stock insuficiente: disponible 0.00")
    mapped = inventory_service.dispatch_db_error(exc)  # type: ignore[arg-type]
    assert mapped.status_code == 409


def test_foreign_key_error_is_mapped_to_422():
    mapped = inventory_service.dispatch_db_error(
        FakeIntegrityError("23503")  # type: ignore[arg-type]
    )
    assert mapped.status_code == 422


def test_unknown_db_error_is_generic_500():
    mapped = inventory_service.dispatch_db_error(
        FakeIntegrityError("XX000", "boom")  # type: ignore[arg-type]
    )
    assert mapped.status_code == 500
    assert mapped.detail == "Error"


# ---------------------------------------------------------------------------
# Auditoria sin PII
# ---------------------------------------------------------------------------


def test_dispatch_snapshot_has_ids_and_no_user_pii():
    dispatch = SimpleNamespace(
        id=uuid.uuid4(),
        id_raw_materials_suppliers=uuid.uuid4(),
        id_project=uuid.uuid4(),
        id_user=uuid.uuid4(),
        use_raw_materials=3,
        material_requested_at=NOW,
        notes="Retiro",
    )
    snapshot = inventory_service.dispatch_snapshot(dispatch)  # type: ignore[arg-type]
    assert snapshot["use_raw_materials"] == 3
    assert set(snapshot) == {
        "id",
        "supplier_item_id",
        "project_id",
        "user_id",
        "use_raw_materials",
        "material_requested_at",
        "notes",
    }
    assert "@" not in str(snapshot)


def test_raw_material_snapshot_has_no_contact_or_pii():
    material = SimpleNamespace(
        id_inventroy=uuid.uuid4(),
        name="Ácido clorhídrico",
        description=None,
        resource_type_id=3,
        unit="L",
    )
    supplier = SimpleNamespace(
        id=uuid.uuid4(),
        vendors_id=uuid.uuid4(),
        current_stock=10,
        minimun_stock=2,
    )
    snapshot = inventory_service.raw_material_snapshot(material, supplier)  # type: ignore[arg-type]
    assert snapshot["name"] == "Ácido clorhídrico"
    assert "@" not in str(snapshot)
