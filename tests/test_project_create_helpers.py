from src.service.project import build_unique_code, can_create_project, next_sequence
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE


def test_build_unique_code_pads_sequence():
    assert build_unique_code("qui", 2026, 7) == "QUI-2026-007"


def test_next_sequence_starts_at_one():
    assert next_sequence([], "QUI-2026-") == 1


def test_next_sequence_uses_highest_numeric_suffix():
    codes = ["QUI-2026-001", "QUI-2026-010", "QUI-2026-abc", "BIO-2026-099"]
    assert next_sequence(codes, "QUI-2026-") == 11


def test_only_admin_and_coordinator_create_projects():
    assert can_create_project(ADMIN_ROLE)
    assert can_create_project(COORDINATOR_ROLE)
    assert not can_create_project(APPROVER_ROLE)
    assert not can_create_project(None)
