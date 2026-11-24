"""Reglas unitarias de solicitudes de gasto (RF-5, RN-7), sin base de datos.

Cubre la maquina de estados, los permisos de creacion/envio/revision, la
prohibicion de autoaprobacion del aprobador y la validacion estricta de los
schemas (sin mass assignment).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from src.schema.expense import (
    ExpenseCreate,
    ExpenseStatusUpdate,
)
from src.service import expense as expense_service
from src.service.expense import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_EXECUTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    can_create_expense,
    can_execute_expense,
    can_review_expense,
    can_send_to_pending,
    can_transition_expense,
)
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

ALL_CODES = [
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_EXECUTED,
]


def payload(**overrides) -> dict:
    data = {
        "id_project": str(uuid.uuid4()),
        "id_raw_materials_suppliers": None,
        "description": "Compra de reactivo para el laboratorio.",
        "count_raw_materials": "10.00",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Maquina de estados (RN-7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (STATUS_DRAFT, STATUS_PENDING),
        (STATUS_PENDING, STATUS_APPROVED),
        (STATUS_PENDING, STATUS_REJECTED),
        (STATUS_APPROVED, STATUS_EXECUTED),
    ],
)
def test_valid_transitions(current, target):
    assert can_transition_expense(current, target) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (STATUS_DRAFT, STATUS_APPROVED),
        (STATUS_DRAFT, STATUS_REJECTED),
        (STATUS_DRAFT, STATUS_EXECUTED),
        (STATUS_PENDING, STATUS_EXECUTED),
        (STATUS_APPROVED, STATUS_PENDING),
        (STATUS_APPROVED, STATUS_REJECTED),
        (STATUS_REJECTED, STATUS_PENDING),
        (STATUS_REJECTED, STATUS_APPROVED),
        (STATUS_EXECUTED, STATUS_APPROVED),
        (STATUS_DRAFT, STATUS_DRAFT),
        (STATUS_PENDING, STATUS_PENDING),
    ],
)
def test_invalid_transitions(current, target):
    assert can_transition_expense(current, target) is False


def test_unknown_codes_are_rejected():
    assert can_transition_expense("no-existe", STATUS_PENDING) is False
    assert can_transition_expense(STATUS_DRAFT, "no-existe") is False
    assert can_transition_expense(None, STATUS_PENDING) is False


def test_rejected_and_executed_are_terminal():
    for code in (STATUS_REJECTED, STATUS_EXECUTED):
        assert all(
            not can_transition_expense(code, target)
            for target in ALL_CODES
        )


# ---------------------------------------------------------------------------
# Permisos y autoaprobacion
# ---------------------------------------------------------------------------


def test_only_admin_and_coordinator_create():
    assert can_create_expense(ADMIN_ROLE) is True
    assert can_create_expense(COORDINATOR_ROLE) is True
    assert can_create_expense(APPROVER_ROLE) is False
    assert can_create_expense(None) is False


def test_only_creator_or_admin_sends_to_pending():
    creator = uuid.uuid4()
    other = uuid.uuid4()

    assert can_send_to_pending(ADMIN_ROLE, other, creator) is True
    assert can_send_to_pending(COORDINATOR_ROLE, creator, creator) is True
    assert can_send_to_pending(APPROVER_ROLE, creator, creator) is True
    assert can_send_to_pending(COORDINATOR_ROLE, other, creator) is False
    assert can_send_to_pending(APPROVER_ROLE, other, creator) is False


def test_approver_cannot_review_own_request_but_admin_can():
    requester = uuid.uuid4()
    other = uuid.uuid4()

    assert can_review_expense(APPROVER_ROLE, requester, requester) is False
    assert can_review_expense(APPROVER_ROLE, other, requester) is True
    # El administrador si puede aprobar su propia solicitud (documentado).
    assert can_review_expense(ADMIN_ROLE, requester, requester) is True
    assert can_review_expense(ADMIN_ROLE, other, requester) is True
    assert can_review_expense(COORDINATOR_ROLE, other, requester) is False


def test_only_manager_roles_execute():
    assert can_execute_expense(ADMIN_ROLE) is True
    assert can_execute_expense(APPROVER_ROLE) is True
    assert can_execute_expense(COORDINATOR_ROLE) is False


# ---------------------------------------------------------------------------
# Schemas estrictos
# ---------------------------------------------------------------------------


def test_expense_create_accepts_valid_payload():
    created = ExpenseCreate.model_validate(payload())
    assert created.count_raw_materials == 10


@pytest.mark.parametrize(
    "forbidden",
    ["id", "id_user", "id_status", "status", "created_at", "modifiet_at"],
)
def test_expense_create_rejects_internal_fields(forbidden):
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(
            payload(**{forbidden: str(uuid.uuid4())})
        )


@pytest.mark.parametrize(
    "count", ["0", "-1", "0.001", "1000000000000.00"]
)
def test_expense_create_rejects_invalid_quantities(count):
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload(count_raw_materials=count))


def test_expense_create_requires_meaningful_description():
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload(description="abc"))


def test_status_update_accepts_only_target_codes():
    for code in ("pending", "approved", "rejected", "executed"):
        assert ExpenseStatusUpdate.model_validate({"status": code}).status == code

    with pytest.raises(ValidationError):
        ExpenseStatusUpdate.model_validate({"status": "draft"})
    with pytest.raises(ValidationError):
        ExpenseStatusUpdate.model_validate({"status": "pending", "id_user": str(uuid.uuid4())})


def test_snapshot_serializes_state_without_pii():
    from types import SimpleNamespace

    expense = SimpleNamespace(
        id=uuid.uuid4(),
        id_project=uuid.uuid4(),
        id_raw_materials_suppliers=uuid.uuid4(),
        id_status=2,
        status=SimpleNamespace(code=STATUS_PENDING),
        id_user=uuid.uuid4(),
        description="Compra de reactivo",
        count_raw_materials="10.00",
        created_at="2026-11-24T12:00:00Z",
        modifiet_at="2026-11-24T12:30:00Z",
    )
    snapshot = expense_service.expense_snapshot(expense)

    assert snapshot["status"] == STATUS_PENDING
    assert snapshot["status_id"] == 2
    assert snapshot["requester_id"] == str(expense.id_user)
    assert snapshot["count_raw_materials"] == "10.00"
    assert "email" not in snapshot
    assert "name" not in snapshot
    assert "lastname" not in snapshot
