"""Reglas puras de proyectos, priorizacion y alcance (sin BD).

Cubre las reglas que el backend no puede delegar a la base de datos:

- maquina de estados de proyecto;
- permisos por rol para editar y priorizar;
- bloqueo optimista (``expected_updated_at``);
- justificacion obligatoria de prioridad (RN-3);
- alcance del coordinador (no puede escapar con ``?user_id=``);
- snapshots de auditoria sin PII.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.schema.project import PriorityChangeIn, ProjectUpdate
from src.schema.user import CurrentUser
from src.model.project import AudithProjectModel
from src.service import project as project_service
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


def make_project(
    *,
    status_name: str = "En ejecución",
    id_status: int = 2,
    id_priority: int = 2,
    priority_name: str = "Alta",
) -> SimpleNamespace:
    owner_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        unique_code="FIS-2026-001",
        name="Proyecto de prueba",
        description=None,
        id_status=id_status,
        id_priority=id_priority,
        progress=Decimal("10.00"),
        budget_execution=Decimal("20.00"),
        id_user=owner_id,
        create_at=NOW - timedelta(days=30),
        updated_at=NOW,
        status=SimpleNamespace(
            id=id_status, name=status_name, description=None
        ),
        priority=SimpleNamespace(
            id=id_priority, name=priority_name, description=None
        ),
        user=SimpleNamespace(
            id=owner_id,
            name="Ana",
            lastname="Pérez",
            email="ana@example.com",
        ),
    )


class FakeSession:
    """Sesion minima para los caminos que solo leen un catalogo."""

    def __init__(self, get_value=None):
        self._get_value = get_value
        self.added: list = []
        self.commits = 0

    async def get(self, model, key):  # noqa: ARG002
        return self._get_value

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


# ---------------------------------------------------------------------------
# Maquina de estados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("Propuesta", "Propuesta"),
        ("Propuesta", "En ejecución"),
        ("Propuesta", "Suspendido"),
        ("En ejecución", "Suspendido"),
        ("En ejecución", "Finalizado"),
        ("Suspendido", "En ejecución"),
        ("Suspendido", "Finalizado"),
        ("Finalizado", "Finalizado"),
    ],
)
def test_allowed_status_transitions(current, target):
    assert project_service.can_transition_status(current, target) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("Propuesta", "Finalizado"),
        ("En ejecución", "Propuesta"),
        ("Suspendido", "Propuesta"),
        ("Finalizado", "En ejecución"),
        ("Finalizado", "Suspendido"),
        ("Finalizado", "Propuesta"),
        ("Desconocido", "Finalizado"),
        (None, "Finalizado"),
    ],
)
def test_forbidden_status_transitions(current, target):
    assert project_service.can_transition_status(current, target) is False


def test_status_names_without_accent_are_recognized():
    assert project_service.canonical_status_name("en ejecucion") == "En ejecución"
    assert (
        project_service.can_transition_status("en ejecucion", "FINALIZADO")
        is True
    )


def test_finalized_project_is_locked():
    assert project_service.is_project_locked(
        make_project(status_name="Finalizado")
    )
    assert not project_service.is_project_locked(
        make_project(status_name="En ejecución")
    )


# ---------------------------------------------------------------------------
# Permisos por rol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [ADMIN_ROLE, COORDINATOR_ROLE])
def test_admin_and_coordinator_can_edit_project_data(role):
    assert project_service.can_edit_project(role) is True


@pytest.mark.parametrize("role", [APPROVER_ROLE, None])
def test_approver_cannot_edit_project_data(role):
    assert project_service.can_edit_project(role) is False


@pytest.mark.parametrize("role", [ADMIN_ROLE, APPROVER_ROLE])
def test_admin_and_approver_can_change_priority(role):
    assert project_service.can_change_priority(role) is True


@pytest.mark.parametrize("role", [COORDINATOR_ROLE, None])
def test_coordinator_cannot_change_priority(role):
    assert project_service.can_change_priority(role) is False


# ---------------------------------------------------------------------------
# Bloqueo optimista
# ---------------------------------------------------------------------------


def test_stale_update_is_detected():
    assert project_service.is_stale_update(NOW - timedelta(seconds=1), NOW)


def test_same_instant_in_other_timezone_is_not_stale():
    same = NOW.astimezone(timezone(timedelta(hours=-4)))
    assert project_service.is_stale_update(same, NOW) is False


def test_naive_datetime_is_interpreted_as_utc():
    naive = NOW.replace(tzinfo=None)
    assert project_service.is_stale_update(naive, NOW) is False


# ---------------------------------------------------------------------------
# Schemas de entrada (validacion estricta, sin mass assignment)
# ---------------------------------------------------------------------------


def test_priority_requires_explaining_of_at_least_five_characters():
    with pytest.raises(ValidationError):
        PriorityChangeIn(id_priority=2, explaining="  a  ")
    assert PriorityChangeIn(id_priority=2, explaining="  motivo  ").explaining == "motivo"


def test_priority_explaining_max_length():
    with pytest.raises(ValidationError):
        PriorityChangeIn(id_priority=2, explaining="x" * 2001)


def test_priority_rejects_extra_fields_and_invalid_ids():
    with pytest.raises(ValidationError):
        PriorityChangeIn(
            id_priority=2, explaining="motivo", id_project=str(uuid.uuid4())
        )
    with pytest.raises(ValidationError):
        PriorityChangeIn(id_priority=0, explaining="motivo")


def test_project_update_requires_expected_updated_at():
    with pytest.raises(ValidationError):
        ProjectUpdate(name="Nuevo nombre")


def test_project_update_rejects_internal_fields_and_out_of_range():
    with pytest.raises(ValidationError):
        ProjectUpdate(
            name="Nuevo nombre",
            expected_updated_at=NOW,
            unique_code="HACK-001",
        )
    with pytest.raises(ValidationError):
        ProjectUpdate(expected_updated_at=NOW, progress=101)
    with pytest.raises(ValidationError):
        ProjectUpdate(expected_updated_at=NOW, budget_execution=-1)
    with pytest.raises(ValidationError):
        ProjectUpdate(expected_updated_at=NOW, name="ab")


def test_project_update_accepts_percentages_at_bounds():
    payload = ProjectUpdate(
        expected_updated_at=NOW, progress=0, budget_execution=100
    )
    assert payload.progress == 0
    assert payload.budget_execution == 100


# ---------------------------------------------------------------------------
# Reglas aplicadas por el servicio (con dobles, sin BD)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_cannot_list_other_coordinator_projects():
    actor = make_actor(COORDINATOR_ROLE)
    with pytest.raises(HTTPException) as exc:
        await project_service.list_projects(
            None,  # type: ignore[arg-type]
            actor,
            user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_approver_cannot_update_project_data(monkeypatch):
    project = make_project()
    actor = make_actor(APPROVER_ROLE)

    async def fake_get(db, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.update_project(
            None,  # type: ignore[arg-type]
            project.id,
            ProjectUpdate(expected_updated_at=NOW, name="Otro nombre"),
            actor,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_coordinator_cannot_change_priority(monkeypatch):
    project = make_project()
    actor = make_actor(COORDINATOR_ROLE)

    async def fake_get(db, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.change_priority(
            None,  # type: ignore[arg-type]
            project.id,
            PriorityChangeIn(id_priority=1, explaining="motivo válido"),
            actor,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_finalized_project_cannot_be_edited(monkeypatch):
    project = make_project(status_name="Finalizado", id_status=4)
    actor = make_actor(ADMIN_ROLE)

    async def fake_get(db, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.update_project(
            None,  # type: ignore[arg-type]
            project.id,
            ProjectUpdate(expected_updated_at=NOW, name="Otro nombre"),
            actor,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_expected_updated_at_is_rejected(monkeypatch):
    project = make_project()
    actor = make_actor(ADMIN_ROLE)

    async def fake_get(db, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.update_project(
            None,  # type: ignore[arg-type]
            project.id,
            ProjectUpdate(
                expected_updated_at=NOW - timedelta(minutes=5),
                name="Otro nombre",
            ),
            actor,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_forbidden_status_transition_is_rejected(monkeypatch):
    project = make_project(status_name="Propuesta", id_status=1)
    actor = make_actor(ADMIN_ROLE)
    target_status = SimpleNamespace(id=4, name="Finalizado", description=None)
    db = FakeSession(get_value=target_status)

    async def fake_get(session, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.update_project(
            db,
            project.id,
            ProjectUpdate(expected_updated_at=NOW, id_status=4),
            actor,
        )
    assert exc.value.status_code == 422
    assert db.commits == 0


@pytest.mark.asyncio
async def test_priority_must_actually_change(monkeypatch):
    project = make_project(id_priority=2, priority_name="Alta")
    actor = make_actor(APPROVER_ROLE)
    same_priority = SimpleNamespace(id=2, name="Alta", description=None)
    db = FakeSession(get_value=same_priority)

    async def fake_get(session, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    monkeypatch.setattr(
        project_service, "_get_project_for_actor", fake_get
    )

    with pytest.raises(HTTPException) as exc:
        await project_service.change_priority(
            db,
            project.id,
            PriorityChangeIn(id_priority=2, explaining="motivo válido"),
            actor,
        )
    assert exc.value.status_code == 409
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_priority_change_writes_history_and_commits_once(monkeypatch):
    project = make_project(id_priority=2, priority_name="Alta")
    actor = make_actor(APPROVER_ROLE)
    new_priority = SimpleNamespace(id=1, name="Crítica", description=None)
    db = FakeSession(get_value=new_priority)
    recorded: list[dict] = []

    async def fake_get(session, project_id, current_user, **kwargs):  # noqa: ARG001
        return project

    async def fake_record_audit(session, **kwargs):  # noqa: ARG001
        recorded.append(kwargs)

    monkeypatch.setattr(project_service, "_get_project_for_actor", fake_get)
    monkeypatch.setattr(project_service, "record_audit", fake_record_audit)

    result = await project_service.change_priority(
        db,
        project.id,
        PriorityChangeIn(id_priority=1, explaining="motivo válido"),
        actor,
    )

    assert result.priority.id == 1
    assert result.priority.name == "Crítica"
    assert db.commits == 1
    assert len(db.added) == 1

    history = db.added[0]
    assert isinstance(history, AudithProjectModel)
    assert history.id_project == project.id
    assert history.unique_code == project.unique_code
    assert history.id_status == project.id_status
    assert history.id_priority == 1
    assert history.id_user == actor.id
    assert history.explaining == "motivo válido"

    assert len(recorded) == 1
    assert recorded[0]["entity"] == "Project"
    assert recorded[0]["action"] == "update_priority"
    assert recorded[0]["before"]["priority"] == "Alta"
    assert recorded[0]["after"]["priority"] == "Crítica"


def test_project_snapshot_has_no_pii():
    project = make_project()
    snapshot = project_service.project_snapshot(project)

    assert snapshot["id"] == str(project.id)
    assert snapshot["id_user"] == str(project.id_user)
    assert snapshot["status"] == "En ejecución"
    assert snapshot["priority"] == "Alta"
    assert "@" not in str(snapshot)
    assert "ana@example.com" not in str(snapshot)
