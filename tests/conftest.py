"""Configuracion comun y aislamiento de las pruebas de integracion.

Seguridad (SEC-203):

- Las pruebas de integracion **solo** se ejecutan si ``TEST_DATABASE_URL``
  esta definida. Nunca usan la base configurada en ``.env``.
- ``TEST_DATABASE_URL`` sustituye a ``DATABASE_URL`` **antes** de importar
  ``src.config.db``/``app``, de modo que la aplicacion y las sesiones de los
  tests apuntan a la base de pruebas.
- Si ``TEST_DATABASE_URL`` coincide con el ``DATABASE_URL`` del ``.env`` se
  aborta: escribiria en la base compartida, que tiene auditoria inmutable.
- El actor administrativo de los tests se crea en la base de pruebas con un
  email ``test-admin-*``. Nunca se firma un JWT de un administrador real.
- Los usuarios y filas de ``audit_log`` NO se borran (append-only y FK
  ``ON DELETE RESTRICT``): se desactivan y el residuo queda documentado.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Las pruebas consultan audit_log inmediatamente despues del response.
os.environ.setdefault("AUDIT_SYNC_WRITES", "true")
# Limite deterministico para el test de fuerza bruta, independiente del .env.
os.environ.setdefault("LOGIN_RATE_LIMIT", "10/minute")

_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
# Pruebas "live" de solo lectura contra la base real del .env. Se habilitan
# explicitamente con INVENTORY_LIVE_TESTS=1, EQUIPMENT_LIVE_TESTS=1 o
# FINANCE_LIVE_TESTS=1 y NUNCA insertan lotes, despachos, reservas, gastos ni
# periodos de overhead.
_LIVE_TESTS = any(
    os.getenv(name, "").strip().lower() in ("1", "true", "yes")
    for name in (
        "INVENTORY_LIVE_TESTS",
        "EQUIPMENT_LIVE_TESTS",
        "FINANCE_LIVE_TESTS",
    )
)

if _TEST_DATABASE_URL:
    # Importante: antes de cualquier import de src.config.db / app.
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL

    # Esquema opcional de la base de pruebas.
    _TEST_SCHEMA = os.getenv("TEST_SCHEMA", "").strip()
    if _TEST_SCHEMA:
        os.environ["SCHEMA"] = _TEST_SCHEMA

    # Guarda contra apuntar accidentalmente a la base compartida del .env.
    env_file = _ROOT / ".env"
    if env_file.exists():
        from dotenv import dotenv_values

        shared_url = (dotenv_values(env_file).get("DATABASE_URL") or "").strip()
        if shared_url and shared_url == _TEST_DATABASE_URL:
            raise RuntimeError(
                "TEST_DATABASE_URL es igual a DATABASE_URL del .env. "
                "Configura una base de pruebas aislada."
            )
else:
    if not _LIVE_TESTS:
        # Sin base de pruebas, cualquier acceso accidental a la BD debe fallar
        # de inmediato en vez de escribir en la base compartida del .env.
        os.environ["DATABASE_URL"] = (
            "postgresql+asyncpg://test:test@127.0.0.1:1/not_configured"
        )
    # Con INVENTORY_LIVE_TESTS=1 se conserva el DATABASE_URL del .env para las
    # pruebas live de SOLO LECTURA (ver tests/test_inventory_live.py).


@pytest.fixture
async def client():
    """Cliente HTTP contra la app configurada con la base de pruebas."""
    import httpx

    from app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client


@pytest.fixture
async def admin():
    """Administrador de prueba aleatorio creado en la base de pruebas.

    No se reutiliza ni se firma el token de un administrador real. Al
    terminar se desactiva (nunca se borra: ``audit_log`` es append-only).
    """
    import uuid

    from sqlalchemy import select

    from src.config.db import AsyncSessionLocal
    from src.model.user import RolModel, UserModel
    from src.utils.roles import ADMIN_ROLE

    async with AsyncSessionLocal() as db:
        role = await db.scalar(
            select(RolModel).where(RolModel.name == ADMIN_ROLE)
        )
        assert role is not None, (
            "La base de pruebas no tiene el rol 'Administrador del Sistema'"
        )

        user = UserModel(
            email=f"test-admin-{uuid.uuid4().hex}@example.com",
            name="Test",
            lastname="Admin",
            is_active=True,
            id_rol=role.id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    try:
        yield user
    finally:
        # Residuo controlado: se desactiva, no se elimina.
        async with AsyncSessionLocal() as db:
            stored = await db.get(UserModel, user.id)
            if stored is not None and stored.is_active:
                stored.is_active = False
                await db.commit()


@pytest.fixture
def admin_token(admin):
    """JWT del administrador de prueba (no de un admin real)."""
    from src.utils.other import generate_jwt_to_frontend
    from src.utils.roles import ADMIN_ROLE

    return generate_jwt_to_frontend(admin.id, admin.email, ADMIN_ROLE)
