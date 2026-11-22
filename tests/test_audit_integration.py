import os
import uuid
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
from src.model.user import RolModel, UserModel
from src.utils.audit import record_audit, record_audit_standalone
from src.utils.other import generate_jwt_to_frontend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_AUDIT_INTEGRATION", "false").lower()
        not in ("1", "true", "yes"),
        reason="Definir RUN_AUDIT_INTEGRATION=1 para probar contra la BD real",
    ),
]


async def get_admin() -> UserModel | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserModel)
            .join(RolModel, UserModel.id_rol == RolModel.id)
            .where(RolModel.name == "Administrador del Sistema")
            .order_by(UserModel.create_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_audit(request_id: str) -> AuditLogModel | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel).where(
                AuditLogModel.request_id == uuid.UUID(request_id)
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
async def test_http_audit_success_with_actor_and_redacted_headers(monkeypatch):
    monkeypatch.setenv("AUDIT_HTTP_ALL", "true")
    admin = await get_admin()
    assert admin is not None, "No existe el usuario admin de prueba"

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
    assert row.user_email == admin.email
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
    admin = await get_admin()
    assert admin is not None

    async with AsyncSessionLocal() as db:
        entry = await record_audit(
            db,
            entity="Project",
            action="test_business_event",
            entity_id="project-test",
            user_id=admin.id,
            user_email=admin.email,
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

    assert row.entity == "Project"
    assert row.action == "test_business_event"
    assert row.before_data == {"progress": 0, "priority": "Media"}
    assert row.after_data == {"progress": 10, "priority": "Alta"}
    assert row.outcome == "success"


@pytest.mark.asyncio
async def test_record_audit_standalone_commits_its_own_transaction():
    action = f"test_standalone_{uuid.uuid4().hex}"

    await record_audit_standalone(
        entity="Project",
        action=action,
        entity_id="project-test",
        ip_address="127.0.0.1",
        after={"ok": True},
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLogModel).where(AuditLogModel.action == action)
        )
        row = result.scalar_one()

    assert row.entity == "Project"
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
    assert "fallo controlado" in row.error_detail


@pytest.mark.asyncio
async def test_user_delete_is_blocked_by_audit_fk():
    admin = await get_admin()
    assert admin is not None

    conn = await asyncpg.connect(
        **pg_dsn(), timeout=30, statement_cache_size=0
    )
    try:
        count = await conn.fetchval(
            "select count(*) from private.audit_log where user_id = $1",
            admin.id,
        )
        if not count:
            pytest.skip("El admin no tiene filas de auditoría")

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
