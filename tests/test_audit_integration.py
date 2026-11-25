import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app import app
from src.config.db import AsyncSessionLocal
from src.middleware.audit import AuditMiddleware
from src.model.audit import AuditLogModel
from src.utils.audit import record_audit, record_audit_standalone
from src.utils.other import generate_jwt_to_frontend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL", "").strip(),
        reason="Definir TEST_DATABASE_URL (BD de pruebas aislada) para integración",
    ),
]


async def get_audit(request_id: str) -> AuditLogModel | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel).where(
                AuditLogModel.request_id == uuid.UUID(request_id),
                AuditLogModel.entity == "HTTP",
            )
        )
        return result.scalar_one_or_none()


def pg_dsn() -> dict:
    url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    parts = urlsplit(url)
    return {
        "host": parts.hostname,
        "port": parts.port or 5432,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": parts.path.lstrip("/"),
    }


@pytest.mark.asyncio
async def test_http_audit_success_with_actor_and_redacted_headers(
    monkeypatch, admin
):
    monkeypatch.setenv("AUDIT_HTTP_ALL", "true")

    token = generate_jwt_to_frontend(
        admin.id, admin.email, "Administrador del Sistema"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get(
            "/",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "audit-integration-test/1.0",
                "Cookie": "session=super-secret",
                "X-Api-Key": "super-secret-key",
            },
        )

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    row = await get_audit(request_id)
    assert row is not None

    assert row.user_id == admin.id
    assert row.user_email is None
    assert row.method == "GET"
    assert row.path == "/"
    assert row.status_code == 200
    assert row.outcome == "success"
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.user_agent == "audit-integration-test/1.0"
    assert row.request_headers["authorization"] == "[REDACTED]"
    assert row.request_headers["cookie"] == "[REDACTED]"
    assert row.request_headers["x-api-key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_login_failure_is_audited_as_denied():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/user/auth/google/login",
            json={"id_token": "token-invalido"},
        )

    assert response.status_code == 401
    request_id = response.headers["x-request-id"]
    row = await get_audit(request_id)
    assert row is not None

    assert row.method == "POST"
    assert row.path == "/api/user/auth/google/login"
    assert row.status_code == 401
    assert row.outcome == "denied"
    assert row.error_code == "HTTP_401"
    assert row.error_detail == "Error"
    assert row.user_id is None


@pytest.mark.asyncio
async def test_health_is_not_audited():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert await get_audit(response.headers["x-request-id"]) is None


@pytest.mark.asyncio
async def test_validation_error_does_not_store_input():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/user/auth/google/login",
            json={"id_token": {"secret": "super-secret"}},
        )

    assert response.status_code == 422
    row = await get_audit(response.headers["x-request-id"])
    assert row is not None
    assert row.status_code == 422
    assert row.outcome == "failed"
    assert row.error_code == "HTTP_422"
    assert "super-secret" not in (row.error_detail or "")


@pytest.mark.asyncio
async def test_business_audit_event_with_before_and_after():
    # Evento de prueba: entity="Test" y sin actor real (SEC-203).
    async with AsyncSessionLocal() as db:
        entry = await record_audit(
            db,
            entity="Test",
            action="test_business_event",
            entity_id="test-entity",
            user_id=None,
            ip_address="127.0.0.1",
            before={"progress": 0, "priority": "Media"},
            after={"progress": 10, "priority": "Alta"},
        )
        await db.commit()
        entry_id = entry.id

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel).where(AuditLogModel.id == entry_id)
        )
        row = result.scalar_one()

    assert row.entity == "Test"
    assert row.user_id is None
    assert row.action == "test_business_event"
    assert row.before_data == {"progress": 0, "priority": "Media"}
    assert row.after_data == {"progress": 10, "priority": "Alta"}
    assert row.outcome == "success"


@pytest.mark.asyncio
async def test_record_audit_standalone_commits_its_own_transaction():
    action = f"test_standalone_{uuid.uuid4().hex}"

    await record_audit_standalone(
        entity="Test",
        action=action,
        entity_id="test-entity",
        user_id=None,
        ip_address="127.0.0.1",
        after={"ok": True},
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel).where(AuditLogModel.action == action)
        )
        row = result.scalar_one()

    assert row.entity == "Test"
    assert row.after_data == {"ok": True}
    assert row.outcome == "success"


@pytest.mark.asyncio
async def test_middleware_survives_audit_build_failure(monkeypatch):
    monkeypatch.setenv("AUDIT_HTTP_ALL", "true")

    def boom(*args, **kwargs):
        raise RuntimeError("fallo simulado de auditoria")

    monkeypatch.setattr(
        "src.middleware.audit.build_http_audit_data", boom
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/")

    # La request debe responder aunque la auditoria falle.
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_middleware_audits_unhandled_exception():
    boom_app = FastAPI()
    boom_app.add_middleware(AuditMiddleware)

    @boom_app.get("/boom")
    async def boom():
        raise RuntimeError("fallo controlado")

    transport = httpx.ASGITransport(
        app=boom_app, raise_app_exceptions=False
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == 500

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel)
            .where(AuditLogModel.path == "/boom")
            .order_by(AuditLogModel.timestamp.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.status_code == 500
    assert row.outcome == "failed"
    assert row.error_code == "RuntimeError"
    # Solo tipo de excepcion + request_id: nunca el mensaje interno.
    assert row.error_detail == f"RuntimeError; request_id={row.request_id}"
    assert "fallo controlado" not in (row.error_detail or "")


@pytest.mark.asyncio
async def test_sensitive_get_is_audited_but_me_is_not(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}

    listing = await client.get("/api/user", headers=headers)
    assert listing.status_code == 200, listing.text
    row = await get_audit(listing.headers["x-request-id"])
    assert row is not None
    assert row.method == "GET"
    assert row.path == "/api/user"
    assert row.status_code == 200
    assert row.outcome == "success"

    # /api/user/me no es una lectura sensible y no debe generar ruido.
    me = await client.get("/api/user/me", headers=headers)
    assert me.status_code == 200, me.text
    assert await get_audit(me.headers["x-request-id"]) is None


@pytest.mark.asyncio
async def test_user_delete_is_blocked_by_audit_fk(admin):
    # Se crea un evento de prueba que referencia al admin de prueba, sin
    # atribuir la accion a un administrador real (SEC-203).
    async with AsyncSessionLocal() as db:
        await record_audit(
            db,
            entity="Test",
            action="test_delete_fk",
            entity_id=str(admin.id),
            user_id=admin.id,
            ip_address="127.0.0.1",
        )
        await db.commit()

    conn = await asyncpg.connect(
        **pg_dsn(), timeout=30, statement_cache_size=0
    )
    try:
        transaction = conn.transaction()
        await transaction.start()
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                'delete from private."user" where id = $1', admin.id
            )
        await transaction.rollback()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_audit_log_blocks_update_and_delete():
    conn = await asyncpg.connect(
        **pg_dsn(), timeout=30, statement_cache_size=0
    )
    try:
        row = await conn.fetchrow(
            "select id from private.audit_log order by timestamp desc limit 1"
        )
        if row is None:
            pytest.skip("No hay filas de auditoría para probar")

        for statement in (
            f"update private.audit_log set action='hacked' where id='{row['id']}'",
            f"delete from private.audit_log where id='{row['id']}'",
        ):
            transaction = conn.transaction()
            await transaction.start()
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(statement)
            await transaction.rollback()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_audit_endpoint_filters_and_pagination_validation(admin):
    token = generate_jwt_to_frontend(
        admin.id, admin.email, "Administrador del Sistema"
    )
    headers = {"Authorization": f"Bearer {token}"}
    now = datetime.now(timezone.utc)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/audit",
            headers=headers,
            params={
                "entity": "HTTP",
                "action": "http_request",
                "outcome": "success",
                "status_code": 200,
                "user_id": str(admin.id),
                # UUID aleatorio: no debe coincidir con ninguna fila.
                "request_id": str(uuid.uuid4()),
                "path": "/",
                "from_date": (now - timedelta(days=1)).isoformat(),
                "to_date": (now + timedelta(days=1)).isoformat(),
                "limit": 1,
                "offset": 0,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0
        assert body["limit"] == 1
        assert body["offset"] == 0

        for params in (
            {"limit": 0},
            {"limit": 201},
            {"offset": -1},
            {"status_code": 99},
        ):
            invalid = await client.get(
                "/api/audit", headers=headers, params=params
            )
            assert invalid.status_code == 422, params

@pytest.mark.asyncio
async def test_audit_counts_ignore_outcome_filter(client, admin_token):
    # Eventos con entidad "Test": las propias peticiones de auditoria son
    # entity="HTTP", asi que no alteran estos contadores entre requests.
    for outcome in ("success", "denied", "failed"):
        await record_audit_standalone(
            entity="Test",
            action="counts_filter_test",
            entity_id="counts-test",
            ip_address="127.0.0.1",
            outcome=outcome,
        )

    headers = {"Authorization": f"Bearer {admin_token}"}

    all_response = await client.get(
        "/api/audit?entity=Test&limit=1", headers=headers
    )
    denied_response = await client.get(
        "/api/audit?entity=Test&limit=1&outcome=denied", headers=headers
    )

    assert all_response.status_code == 200
    assert denied_response.status_code == 200

    all_counts = all_response.json()["counts"]
    denied_counts = denied_response.json()["counts"]
    assert all_counts == denied_counts
    assert denied_counts["success"] >= 1
    assert denied_counts["denied"] >= 1
    assert denied_counts["failed"] >= 1
