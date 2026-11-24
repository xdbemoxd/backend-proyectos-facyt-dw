"""Reglas unitarias de prorrateo de overhead (RF-6, RN-6), sin base de datos.

Cubre reparto equitativo y proporcional, redondeo/cuadre exacto, ausencia de
proyectos, consumo cero, solape de periodos y validacion estricta del schema.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schema.overhead import OverheadPeriodCreate
from src.service.overhead import (
    AllocationRow,
    ConsumptionRow,
    allocate_amounts,
    periods_overlap,
)


def rows(*consumptions: str) -> list[ConsumptionRow]:
    return [
        ConsumptionRow(
            project_id=uuid.uuid4(),
            unique_code=f"P-{index}",
            name=f"Proyecto {index}",
            consumption=Decimal(value),
        )
        for index, value in enumerate(consumptions, start=1)
    ]


def total_of(allocations: list[AllocationRow]) -> Decimal:
    return sum((row.amount for row in allocations), Decimal("0.00"))


# ---------------------------------------------------------------------------
# Reparto equitativo
# ---------------------------------------------------------------------------


def test_equitativo_splits_evenly_and_sums_exact():
    allocations = allocate_amounts(
        Decimal("100.00"), "equitativo", rows("10", "0", "999")
    )
    assert [row.amount for row in allocations] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert total_of(allocations) == Decimal("100.00")


def test_equitativo_handles_one_project():
    allocations = allocate_amounts(
        Decimal("1200.00"), "equitativo", rows("0")
    )
    assert len(allocations) == 1
    assert allocations[0].amount == Decimal("1200.00")
    assert allocations[0].share == Decimal("1.000000")


def test_equitativo_does_not_produce_negative_amounts_with_tiny_total():
    allocations = allocate_amounts(
        Decimal("0.02"), "equitativo", rows("0", "0", "0", "0")
    )
    assert all(row.amount >= 0 for row in allocations)
    assert total_of(allocations) == Decimal("0.02")


def test_equitativo_with_many_projects_keeps_exact_total():
    allocations = allocate_amounts(
        Decimal("10.00"), "equitativo", rows(*["0"] * 7)
    )
    assert total_of(allocations) == Decimal("10.00")
    assert all(row.amount == Decimal("1.42") for row in allocations[:-1])
    assert allocations[-1].amount == Decimal("1.48")


# ---------------------------------------------------------------------------
# Reparto proporcional
# ---------------------------------------------------------------------------


def test_proporcional_matches_seeded_example():
    allocations = allocate_amounts(
        Decimal("1200.00"), "proporcional", rows("150.00", "250.00")
    )
    assert [row.amount for row in allocations] == [
        Decimal("450.00"),
        Decimal("750.00"),
    ]
    assert total_of(allocations) == Decimal("1200.00")


def test_proporcional_with_rounding_adjusts_last_cent():
    allocations = allocate_amounts(
        Decimal("100.00"), "proporcional", rows("1", "1", "1")
    )
    assert [row.amount for row in allocations] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert total_of(allocations) == Decimal("100.00")


def test_proporcional_with_zero_consumption_project():
    allocations = allocate_amounts(
        Decimal("50.00"), "proporcional", rows("100", "0")
    )
    assert allocations[0].amount == Decimal("50.00")
    assert allocations[1].amount == Decimal("0.00")


def test_proporcional_requires_consumption():
    with pytest.raises(ValueError, match="consumo"):
        allocate_amounts(
            Decimal("100.00"), "proporcional", rows("0", "0")
        )


# ---------------------------------------------------------------------------
# Validaciones
# ---------------------------------------------------------------------------


def test_no_active_projects_is_rejected():
    with pytest.raises(ValueError, match="proyectos activos"):
        allocate_amounts(Decimal("100.00"), "equitativo", [])


def test_invalid_criterion_is_rejected():
    with pytest.raises(ValueError, match="Criterio"):
        allocate_amounts(Decimal("100.00"), "otro", rows("1"))


def test_non_positive_total_is_rejected():
    with pytest.raises(ValueError, match="mayor que 0"):
        allocate_amounts(Decimal("0.00"), "equitativo", rows("1"))


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 10, 31), date(2026, 11, 15)), True),
        ((date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 30)), False),
        ((date(2026, 10, 10), date(2026, 10, 20)), (date(2026, 10, 1), date(2026, 10, 31)), True),
        ((date(2026, 10, 1), date(2026, 10, 5)), (date(2026, 10, 6), date(2026, 10, 10)), False),
        ((date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 10, 1), date(2026, 10, 31)), True),
    ],
)
def test_period_overlap_is_inclusive(a, b, expected):
    assert (
        periods_overlap(a[0], a[1], b[0], b[1]) is expected
    )


def test_period_snapshot_includes_allocation_breakdown():
    from types import SimpleNamespace

    from src.service.overhead import period_snapshot

    period = SimpleNamespace(
        id=uuid.uuid4(),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        criterion="proporcional",
        total_cost=Decimal("1200.00"),
        closed_by=uuid.uuid4(),
        closed_at="2026-11-01T00:00:00Z",
        allocations=[
            SimpleNamespace(
                id_project=uuid.uuid4(),
                amount=Decimal("450.00"),
                consumption=Decimal("150.00"),
            ),
            SimpleNamespace(
                id_project=uuid.uuid4(),
                amount=Decimal("750.00"),
                consumption=Decimal("250.00"),
            ),
        ],
    )
    snapshot = period_snapshot(period)

    assert snapshot["criterion"] == "proporcional"
    assert snapshot["total_cost"] == Decimal("1200.00")
    assert len(snapshot["allocations"]) == 2
    assert snapshot["allocations"][0]["amount"] == Decimal("450.00")
    assert "user_email" not in snapshot


# ---------------------------------------------------------------------------
# Schema estricto
# ---------------------------------------------------------------------------


def period_payload(**overrides) -> dict:
    data = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "criterion": "proporcional",
        "total_cost": "1200.00",
    }
    data.update(overrides)
    return data


def test_period_create_accepts_valid_payload():
    payload = OverheadPeriodCreate.model_validate(period_payload())
    assert payload.total_cost == Decimal("1200.00")


@pytest.mark.parametrize(
    "forbidden",
    ["closed_by", "closed_at", "allocations", "id"],
)
def test_period_create_rejects_mass_assignment(forbidden):
    with pytest.raises(ValidationError):
        OverheadPeriodCreate.model_validate(
            period_payload(**{forbidden: "x"})
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"criterion": "otro"},
        {"total_cost": "0"},
        {"total_cost": "-5"},
        {"total_cost": "1.234"},
    ],
)
def test_period_create_rejects_invalid_values(overrides):
    with pytest.raises(ValidationError):
        OverheadPeriodCreate.model_validate(period_payload(**overrides))
