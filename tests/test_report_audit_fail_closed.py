"""Exportacion fail-closed: sin auditoria no se entrega el archivo.

La generacion real (XLSX/PDF) se prueba aparte. Aqui se sustituyen documento y
render para aislar la transaccion de auditoria.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.schema.report import ReportFilters
from src.schema.user import CurrentUser
from src.service import report as report_service
from src.utils.roles import ADMIN_ROLE


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None):
        self.commit_error = commit_error
        self.committed = 0
        self.rolled_back = 0

    async def commit(self):
        self.committed += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rolled_back += 1


def make_actor() -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="actor@example.com",
        name="Actor",
        lastname="Prueba",
        is_active=True,
        id_rol=uuid.uuid4(),
        rol=ADMIN_ROLE,
    )


@pytest.fixture
def stub_document(monkeypatch):
    document = SimpleNamespace(
        generated_at=datetime(2026, 11, 25, tzinfo=timezone.utc),
        table=SimpleNamespace(rows=[{"a": 1}]),
    )

    async def fake_build(db, report_type, actor, filters, **kwargs):  # noqa: ARG001
        return document

    def fake_render(document, report_format):  # noqa: ARG001
        return b"contenido-sensible"

    monkeypatch.setattr(report_service, "build_document", fake_build)
    monkeypatch.setattr(report_service, "render_document", fake_render)
    return document


@pytest.mark.asyncio
async def test_export_fails_closed_when_commit_fails(
    monkeypatch, stub_document
):
    async def fake_record_audit(db, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(report_service, "record_audit", fake_record_audit)
    db = FakeSession(commit_error=RuntimeError("db caida"))

    with pytest.raises(HTTPException) as exc:
        await report_service.export_report(
            db,
            "expenses",
            "xlsx",
            make_actor(),
            ReportFilters(),
        )

    assert exc.value.status_code == 503
    assert "registrar la exportación" in exc.value.detail
    assert db.rolled_back == 1


@pytest.mark.asyncio
async def test_export_fails_closed_when_audit_write_fails(
    monkeypatch, stub_document
):
    async def failing_record_audit(db, **kwargs):  # noqa: ARG001
        raise RuntimeError("auditoria no disponible")

    monkeypatch.setattr(
        report_service, "record_audit", failing_record_audit
    )
    db = FakeSession()

    with pytest.raises(HTTPException) as exc:
        await report_service.export_report(
            db,
            "overhead",
            "pdf",
            make_actor(),
            ReportFilters(),
        )

    assert exc.value.status_code == 503
    assert db.rolled_back == 1


@pytest.mark.asyncio
async def test_export_returns_file_when_audit_commits(
    monkeypatch, stub_document
):
    async def fake_record_audit(db, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(report_service, "record_audit", fake_record_audit)
    db = FakeSession()

    rendered = await report_service.export_report(
        db,
        "equipment",
        "xlsx",
        make_actor(),
        ReportFilters(),
    )

    assert rendered.content == b"contenido-sensible"
    assert rendered.filename.startswith("equipos-")
    assert db.committed == 1
    assert db.rolled_back == 0
