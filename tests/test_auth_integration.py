import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from jose import jwt
from sqlalchemy import select

from src.config.db import AsyncSessionLocal
from src.model.audit import AuditLogModel
from src.model.user import RolModel
from src.utils.other import SECRET_KEY, generate_jwt_to_frontend
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL", "").strip(),
        reason="Definir TEST_DATABASE_URL (BD de pruebas aislada) para integración",
    ),
]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(user: dict) -> str:
    return generate_jwt_to_frontend(
        uuid.UUID(user["id"]), user["email"], user["rol"]
    )


async def get_role(name: str) -> RolModel | None:
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(RolModel).where(RolModel.name == name))


async def create_user(
    client: httpx.AsyncClient,
    admin_token: str,
    role_name: str,
    *,
    email: str | None = None,
    is_active: bool = True,
    **extra,
) -> dict:
    role = await get_role(role_name)
    assert role is not None

    payload = {
        "email": email or f"test-rbac-{uuid.uuid4().hex}@example.com",
        "id_rol": str(role.id),
        "is_active": is_active,
        **extra,
    }
    response = await client.post(
        "/api/user", headers=auth(admin_token), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def deactivate(
    client: httpx.AsyncClient, admin_token: str, user_id: str
) -> None:
    response = await client.patch(
        f"/api/user/{user_id}",
        headers=auth(admin_token),
        json={"is_active": False},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_me_requires_valid_token(client):
    assert (await client.get("/api/user/me")).status_code == 401
    assert (
        await client.get(
            "/api/user/me",
            headers={"Authorization": "Bearer no-es-un-jwt"},
        )
    ).status_code == 401
    assert (
        await client.get(
            "/api/user/me",
            headers={"Authorization": "Basic abc"},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_me_returns_role_from_database(client, admin, admin_token):
    response = await client.get(
        "/api/user/me", headers=auth(admin_token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(admin.id)
    assert body["email"] == admin.email
    assert body["rol"] == ADMIN_ROLE
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_coordinator_cannot_access_admin_endpoints(client, admin_token):
    coordinator = await create_user(
        client, admin_token, COORDINATOR_ROLE
    )
    try:
        token = token_for(coordinator)

        assert (
            await client.get("/api/user/me", headers=auth(token))
        ).status_code == 200
        assert (
            await client.get("/api/user", headers=auth(token))
        ).status_code == 403
        assert (
            await client.get("/api/user/roles", headers=auth(token))
        ).status_code == 403
        assert (
            await client.get("/api/audit", headers=auth(token))
        ).status_code == 403
    finally:
        await deactivate(client, admin_token, coordinator["id"])


@pytest.mark.asyncio
async def test_admin_manages_users_and_audits(client, admin_token):
    approver = await create_user(
        client, admin_token, APPROVER_ROLE
    )
    try:
        assert (
            await client.get(
                f"/api/user/{approver['id']}", headers=auth(admin_token)
            )
        ).status_code == 200

        listed = await client.get(
            f"/api/user?search={approver['email']}",
            headers=auth(admin_token),
        )
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1

        updated = await client.patch(
            f"/api/user/{approver['id']}",
            headers=auth(admin_token),
            json={"name": "Aprobador", "lastname": "Prueba"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Aprobador"

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AuditLogModel)
                .where(
                    AuditLogModel.entity == "User",
                    AuditLogModel.entity_id == approver["id"],
                    AuditLogModel.action.in_(
                        ("create_user", "update_user")
                    ),
                )
                .order_by(AuditLogModel.timestamp.asc())
            )
            rows = result.scalars().all()

        assert len(rows) >= 2
        for row in rows:
            assert row.user_email is None
            assert isinstance(row.after_data, dict)
            assert "email" not in row.after_data
            assert set(row.after_data) <= {
                "id",
                "is_active",
                "id_rol",
                "rol",
            }
    finally:
        await deactivate(client, admin_token, approver["id"])


@pytest.mark.asyncio
async def test_duplicate_email_and_invalid_role(client, admin_token):
    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        duplicate = await client.post(
            "/api/user",
            headers=auth(admin_token),
            json={
                "email": user["email"],
                "id_rol": user["id_rol"],
                "is_active": True,
            },
        )
        assert duplicate.status_code == 409

        invalid = await client.post(
            "/api/user",
            headers=auth(admin_token),
            json={
                "email": f"invalid-{uuid.uuid4().hex}@example.com",
                "id_rol": str(uuid.uuid4()),
                "is_active": True,
            },
        )
        assert invalid.status_code == 422
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_self_deactivation_is_blocked(client, admin, admin_token):
    response = await client.patch(
        f"/api/user/{admin.id}",
        headers=auth(admin_token),
        json={"is_active": False},
    )

    assert response.status_code == 409
    assert (
        await client.get("/api/user/me", headers=auth(admin_token))
    ).status_code == 200


@pytest.mark.asyncio
async def test_inactive_user_token_is_rejected(client, admin_token):
    coordinator = await create_user(
        client, admin_token, COORDINATOR_ROLE
    )
    token = token_for(coordinator)
    await deactivate(client, admin_token, coordinator["id"])

    response = await client.get("/api/user/me", headers=auth(token))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_backend_token(client, admin_token):
    coordinator = await create_user(client, admin_token, COORDINATOR_ROLE)
    token = token_for(coordinator)
    try:
        assert (
            await client.get("/api/user/me", headers=auth(token))
        ).status_code == 200

        response = await client.post(
            "/api/user/auth/logout", headers=auth(token)
        )
        assert response.status_code == 200

        assert (
            await client.get("/api/user/me", headers=auth(token))
        ).status_code == 401
    finally:
        await deactivate(client, admin_token, coordinator["id"])


@pytest.mark.asyncio
async def test_roles_endpoint_returns_three_roles(client, admin_token):
    response = await client.get(
        "/api/user/roles", headers=auth(admin_token)
    )

    assert response.status_code == 200
    names = {role["name"] for role in response.json()}
    assert names == {ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE}


@pytest.mark.asyncio
async def test_audit_endpoint_is_admin_only_and_filters(client, admin_token):
    coordinator = await create_user(
        client, admin_token, COORDINATOR_ROLE
    )
    try:
        coordinator_token = token_for(coordinator)
        assert (
            await client.get(
                "/api/audit", headers=auth(coordinator_token)
            )
        ).status_code == 403

        response = await client.get(
            "/api/audit?entity=User&action=create_user&limit=5",
            headers=auth(admin_token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert all(
            item["entity"] == "User"
            and item["action"] == "create_user"
            for item in body["items"]
        )
    finally:
        await deactivate(client, admin_token, coordinator["id"])


def raw_token(claims: dict) -> str:
    return jwt.encode(claims, SECRET_KEY, algorithm="HS256")


def unique_email(prefix: str = "test-rbac") -> str:
    return f"{prefix}-{uuid.uuid4().hex}@example.com"


@pytest.mark.asyncio
async def test_tokens_with_invalid_subject_are_rejected(client):
    tokens = [
        # `sub` no es UUID.
        raw_token({"sub": "no-es-uuid", "rol": ADMIN_ROLE}),
        # Token sin `sub`.
        raw_token({"email": "x@example.com", "rol": ADMIN_ROLE}),
        # `sub` nulo.
        raw_token({"sub": None}),
        # UUID valido pero inexistente en la BD.
        raw_token({"sub": str(uuid.uuid4()), "rol": ADMIN_ROLE}),
    ]
    for token in tokens:
        response = await client.get("/api/user/me", headers=auth(token))
        assert response.status_code == 401, token


@pytest.mark.asyncio
async def test_expired_and_wrong_signature_tokens_are_rejected(client):
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    wrong_signature = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "otra-clave",
        algorithm="HS256",
    )

    for token in (expired, wrong_signature):
        response = await client.get("/api/user/me", headers=auth(token))
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_role_claim_in_token_is_not_trusted(client, admin_token):
    coordinator = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        # Token firmado correctamente, pero con un rol elevado en el claim.
        forged = generate_jwt_to_frontend(
            uuid.UUID(coordinator["id"]), coordinator["email"], ADMIN_ROLE
        )

        # El rol se resuelve desde la BD: no se concede acceso de admin.
        assert (
            await client.get("/api/user", headers=auth(forged))
        ).status_code == 403
        assert (
            await client.get("/api/user/roles", headers=auth(forged))
        ).status_code == 403

        me = await client.get("/api/user/me", headers=auth(forged))
        assert me.status_code == 200
        assert me.json()["rol"] == COORDINATOR_ROLE
    finally:
        await deactivate(client, admin_token, coordinator["id"])


@pytest.mark.asyncio
async def test_unknown_user_returns_404(client, admin_token):
    missing = uuid.uuid4()

    assert (
        await client.get(f"/api/user/{missing}", headers=auth(admin_token))
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/user/{missing}",
            headers=auth(admin_token),
            json={"name": "Fantasma"},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_empty_or_null_updates_are_rejected(client, admin_token):
    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        for payload in ({}, {"id_rol": None}, {"is_active": None}):
            response = await client.patch(
                f"/api/user/{user['id']}",
                headers=auth(admin_token),
                json=payload,
            )
            assert response.status_code == 422, (payload, response.text)
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_update_with_unknown_role_is_rejected(client, admin_token):
    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        response = await client.patch(
            f"/api/user/{user['id']}",
            headers=auth(admin_token),
            json={"id_rol": str(uuid.uuid4())},
        )
        assert response.status_code == 422
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_admin_cannot_change_own_role(client, admin, admin_token):
    coordinator_role = await get_role(COORDINATOR_ROLE)
    assert coordinator_role is not None

    response = await client.patch(
        f"/api/user/{admin.id}",
        headers=auth(admin_token),
        json={"id_rol": str(coordinator_role.id)},
    )

    assert response.status_code == 409
    me = await client.get("/api/user/me", headers=auth(admin_token))
    assert me.status_code == 200
    assert me.json()["rol"] == ADMIN_ROLE


@pytest.mark.asyncio
async def test_last_admin_cannot_be_deactivated_or_demoted(
    client, admin_token, monkeypatch
):
    from src.service import user as user_service

    original = user_service._count_active_admins

    async def only_one_admin(db, *, lock=False):
        return 1

    second_admin = await create_user(client, admin_token, ADMIN_ROLE)
    coordinator_role = await get_role(COORDINATOR_ROLE)
    assert coordinator_role is not None

    monkeypatch.setattr(
        user_service, "_count_active_admins", only_one_admin
    )
    try:
        deactivation = await client.patch(
            f"/api/user/{second_admin['id']}",
            headers=auth(admin_token),
            json={"is_active": False},
        )
        assert deactivation.status_code == 409

        demotion = await client.patch(
            f"/api/user/{second_admin['id']}",
            headers=auth(admin_token),
            json={"id_rol": str(coordinator_role.id)},
        )
        assert demotion.status_code == 409
    finally:
        # Restaurar antes de limpiar para no chocar con la guarda real.
        monkeypatch.setattr(
            user_service, "_count_active_admins", original
        )
        await deactivate(client, admin_token, second_admin["id"])


@pytest.mark.asyncio
async def test_admin_can_be_demoted_when_other_admins_exist(
    client, admin_token
):
    second_admin = await create_user(client, admin_token, ADMIN_ROLE)
    coordinator_role = await get_role(COORDINATOR_ROLE)
    assert coordinator_role is not None

    try:
        response = await client.patch(
            f"/api/user/{second_admin['id']}",
            headers=auth(admin_token),
            json={"id_rol": str(coordinator_role.id)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["rol"] == COORDINATOR_ROLE
    finally:
        await deactivate(client, admin_token, second_admin["id"])


@pytest.mark.asyncio
async def test_list_users_filters_and_pagination(client, admin_token):
    coordinator_role = await get_role(COORDINATOR_ROLE)
    assert coordinator_role is not None

    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        filtered = await client.get(
            "/api/user",
            headers=auth(admin_token),
            params={
                "role_id": str(coordinator_role.id),
                "is_active": "true",
                "limit": 5,
                "offset": 0,
            },
        )
        assert filtered.status_code == 200
        body = filtered.json()
        assert body["total"] >= 1
        assert body["limit"] == 5
        assert body["offset"] == 0
        assert all(
            item["rol"] == COORDINATOR_ROLE and item["is_active"]
            for item in body["items"]
        )

        # `%` debe tratarse como texto literal, no como comodin LIKE.
        wildcard = await client.get(
            "/api/user",
            headers=auth(admin_token),
            params={"search": "%"},
        )
        assert wildcard.status_code == 200
        assert wildcard.json()["total"] == 0

        for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
            invalid = await client.get(
                "/api/user", headers=auth(admin_token), params=params
            )
            assert invalid.status_code == 422, params
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_mass_assignment_is_rejected(client, admin_token):
    role = await get_role(COORDINATOR_ROLE)
    assert role is not None

    created = await client.post(
        "/api/user",
        headers=auth(admin_token),
        json={
            "email": unique_email("mass"),
            "id_rol": str(role.id),
            "rol": ADMIN_ROLE,
            "id": str(uuid.uuid4()),
        },
    )
    assert created.status_code == 422

    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        for payload in (
            {"email": unique_email("mass")},
            {"id": str(uuid.uuid4())},
            {"rol": ADMIN_ROLE},
        ):
            response = await client.patch(
                f"/api/user/{user['id']}",
                headers=auth(admin_token),
                json=payload,
            )
            assert response.status_code == 422, (payload, response.text)
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_disallowed_role_is_rejected_by_service(
    client, admin_token, monkeypatch
):
    role = await get_role(COORDINATOR_ROLE)
    assert role is not None

    monkeypatch.setattr("src.service.user.ALLOWED_ROLES", frozenset())

    response = await client.post(
        "/api/user",
        headers=auth(admin_token),
        json={"email": unique_email("role"), "id_rol": str(role.id)},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_session_is_rejected_when_role_is_not_allowed(
    client, admin_token, monkeypatch
):
    # Simula deriva de datos: el rol del usuario ya no es uno de los 3 de RN-1.
    monkeypatch.setattr("src.utils.auth.ALLOWED_ROLES", frozenset())

    response = await client.get("/api/user/me", headers=auth(admin_token))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_rate_limit_returns_429(client):
    from src.config.rate_limit import limiter

    limiter.reset()
    try:
        statuses = []
        for _ in range(11):
            response = await client.post(
                "/api/user/auth/google/login",
                json={"id_token": "token-invalido"},
            )
            statuses.append(response.status_code)

        assert statuses[0] == 401
        assert 429 in statuses
    finally:
        limiter.reset()


async def get_denial_event(request_id: str, action: str):
    async with AsyncSessionLocal() as db:
        return await db.scalar(
            select(AuditLogModel).where(
                AuditLogModel.request_id == uuid.UUID(request_id),
                AuditLogModel.entity == "User",
                AuditLogModel.action == action,
                AuditLogModel.outcome == "denied",
            )
        )


@pytest.mark.asyncio
async def test_duplicate_email_denial_is_audited_without_pii(
    client, admin, admin_token
):
    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        response = await client.post(
            "/api/user",
            headers=auth(admin_token),
            json={"email": user["email"], "id_rol": user["id_rol"]},
        )
        assert response.status_code == 409

        event = await get_denial_event(
            response.headers["x-request-id"], "create_user_denied"
        )
        assert event is not None
        assert event.user_id == admin.id
        assert event.entity_id == user["id"]
        assert event.after_data == {"reason": "duplicate_email"}
        assert "@" not in str(event.after_data)

        # El evento HTTP del middleware y el de negocio comparten request_id.
        async with AsyncSessionLocal() as db:
            http_event = await db.scalar(
                select(AuditLogModel).where(
                    AuditLogModel.request_id
                    == uuid.UUID(response.headers["x-request-id"]),
                    AuditLogModel.entity == "HTTP",
                )
            )
        assert http_event is not None
        assert http_event.status_code == 409
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_invalid_role_denial_is_audited(client, admin, admin_token):
    response = await client.post(
        "/api/user",
        headers=auth(admin_token),
        json={"email": unique_email("test-role"), "id_rol": str(uuid.uuid4())},
    )
    assert response.status_code == 422

    event = await get_denial_event(
        response.headers["x-request-id"], "create_user_denied"
    )
    assert event is not None
    assert event.user_id == admin.id
    assert event.after_data["reason"] == "invalid_role"
    assert event.after_data["id_rol"]


@pytest.mark.asyncio
async def test_self_deactivation_denial_is_audited(
    client, admin, admin_token
):
    response = await client.patch(
        f"/api/user/{admin.id}",
        headers=auth(admin_token),
        json={"is_active": False},
    )
    assert response.status_code == 409

    event = await get_denial_event(
        response.headers["x-request-id"], "update_user_denied"
    )
    assert event is not None
    assert event.user_id == admin.id
    assert event.entity_id == str(admin.id)
    assert event.after_data == {"reason": "self_deactivation"}


@pytest.mark.asyncio
async def test_empty_update_denial_is_audited(client, admin, admin_token):
    user = await create_user(client, admin_token, COORDINATOR_ROLE)
    try:
        response = await client.patch(
            f"/api/user/{user['id']}",
            headers=auth(admin_token),
            json={},
        )
        assert response.status_code == 422

        event = await get_denial_event(
            response.headers["x-request-id"], "update_user_denied"
        )
        assert event is not None
        assert event.user_id == admin.id
        assert event.entity_id == user["id"]
        assert event.after_data == {"reason": "empty_update"}
    finally:
        await deactivate(client, admin_token, user["id"])


@pytest.mark.asyncio
async def test_concurrent_last_admin_demotion_leaves_one_admin(monkeypatch):
    """SEC-217: dos degradaciones concurrentes, exactamente una falla.

    Para no depender de que la BD de pruebas este vacia ni tocar a los
    administradores reales del seed, se usa un rol administrativo de prueba
    (``__Test Last Admin__``) y se parchea ``ADMIN_ROLE`` **solo** para este
    test. El conteo, el lock y la regla son los reales: si el lock no
    serializara, ambas transacciones verian 2 administradores y ambas
    terminarian en 200.
    """
    import asyncio

    from fastapi import HTTPException
    from sqlalchemy import func

    from src.config.db import AsyncSessionLocal
    from src.model.user import RolModel, UserModel
    from src.schema.user import CurrentUser, UserUpdate
    from src.service import user as user_service
    from src.utils.roles import ALLOWED_ROLES

    test_admin_role_name = "__Test Last Admin__"

    async with AsyncSessionLocal() as db:
        admin_role = await db.scalar(
            select(RolModel).where(RolModel.name == test_admin_role_name)
        )
        if admin_role is None:
            admin_role = RolModel(
                name=test_admin_role_name,
                description="Rol aislado para la prueba de concurrencia",
            )
            db.add(admin_role)
            await db.commit()
            await db.refresh(admin_role)

        coordinator_role = await db.scalar(
            select(RolModel).where(RolModel.name == COORDINATOR_ROLE)
        )
        assert coordinator_role is not None

        # Limpieza de residuo de una corrida anterior del mismo test:
        # solo afecta a usuarios con el rol aislado de prueba.
        stale = await db.execute(
            select(UserModel).where(
                UserModel.id_rol == admin_role.id,
                UserModel.is_active.is_(True),
            )
        )
        for stale_user in stale.scalars().all():
            stale_user.is_active = False
        await db.commit()

        first = UserModel(
            email=unique_email("test-admin-race"),
            is_active=True,
            id_rol=admin_role.id,
        )
        second = UserModel(
            email=unique_email("test-admin-race"),
            is_active=True,
            id_rol=admin_role.id,
        )
        actor_user = UserModel(
            email=unique_email("test-actor"),
            is_active=False,
            id_rol=coordinator_role.id,
        )
        db.add_all([first, second, actor_user])
        await db.commit()
        await db.refresh(first)
        await db.refresh(second)
        await db.refresh(actor_user)

        first_id, second_id, actor_id = first.id, second.id, actor_user.id
        actor = CurrentUser(
            id=actor_user.id,
            email=actor_user.email,
            name=actor_user.name,
            lastname=actor_user.lastname,
            is_active=False,
            id_rol=actor_user.id_rol,
            rol=COORDINATOR_ROLE,
        )

    # Solo este test considera administrador al rol de prueba.
    monkeypatch.setattr(user_service, "ADMIN_ROLE", test_admin_role_name)
    monkeypatch.setattr(
        user_service,
        "ALLOWED_ROLES",
        ALLOWED_ROLES | {test_admin_role_name},
    )

    barrier = asyncio.Barrier(2)

    async def demote(user_id):
        async with AsyncSessionLocal() as db:
            await barrier.wait()
            try:
                await user_service.update_user(
                    db,
                    user_id,
                    UserUpdate(id_rol=coordinator_role.id),
                    actor=actor,
                    request=None,
                )
                return 200
            except HTTPException as exc:
                return exc.status_code

    try:
        statuses = sorted(
            await asyncio.gather(demote(first_id), demote(second_id))
        )
        assert statuses == [200, 409], statuses

        async with AsyncSessionLocal() as db:
            active_admins = int(
                await db.scalar(
                    select(func.count())
                    .select_from(UserModel)
                    .where(
                        UserModel.is_active.is_(True),
                        UserModel.id_rol == admin_role.id,
                    )
                )
                or 0
            )
        assert active_admins == 1
    finally:
        # Residuo controlado: se desactiva, nunca se borra.
        async with AsyncSessionLocal() as db:
            for user_id in (first_id, second_id, actor_id):
                stored = await db.get(UserModel, user_id)
                if stored is not None and stored.is_active:
                    stored.is_active = False
            await db.commit()
