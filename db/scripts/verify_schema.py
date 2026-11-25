"""Valida db/schema y db/seeds contra PostgreSQL en un esquema temporal.

Seguridad:
  * Nunca toca el esquema `private` real.
  * Crea un esquema unico `verify_schema_<hex>` y reemplaza `private.` por ese
    esquema en memoria antes de ejecutar cada script.
  * Todo corre dentro de una transaccion que termina en ROLLBACK, de modo que
    tablas, triggers, seeds y filas de prueba desaparecen al finalizar.

Uso:
    python db/scripts/verify_schema.py
    python db/scripts/verify_schema.py --database-url "postgresql+asyncpg://..."

Requiere: asyncpg y python-dotenv (ya estan en requirements.txt).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import asyncpg
from dotenv import dotenv_values

DB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DB_DIR.parent
MIGRATIONS_DIR = DB_DIR / "migrations"
SECURITY_MIGRATION = MIGRATIONS_DIR / "20261125_security_hardening.sql"


def _database_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    env_value = os.getenv("DATABASE_URL", "").strip()
    if env_value:
        return env_value
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        value = (dotenv_values(env_file).get("DATABASE_URL") or "").strip()
        if value:
            return value
    raise SystemExit("Falta DATABASE_URL (variable de entorno o .env).")


def _connect_kwargs(url: str) -> dict:
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parts = urlsplit(url)
    if not parts.hostname:
        raise SystemExit("DATABASE_URL no contiene un host valido.")
    local = parts.hostname in {"localhost", "127.0.0.1", "::1", "db"}
    return {
        "host": parts.hostname,
        "port": parts.port or 5432,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": parts.path.lstrip("/") or "postgres",
        "ssl": "prefer" if local else "require",
        "statement_cache_size": 0,
        "command_timeout": 120,
    }


def _read_for_schema(path: Path, schema: str) -> str:
    sql = path.read_text(encoding="utf-8")
    # Referencias cualificadas: private.tabla -> "verify_x".tabla
    sql = sql.replace("private.", f'"{schema}".')
    # DDL sobre el propio esquema: CREATE/COMMENT ON SCHEMA private
    sql = re.sub(
        r"\bSCHEMA\s+(IF\s+NOT\s+EXISTS\s+)?private\b",
        lambda m: f'SCHEMA {m.group(1) or ""}"{schema}"',
        sql,
    )
    if "private." in sql:
        raise AssertionError(f"{path}: quedo una referencia a private.")
    return sql


def _q(sql: str, schema: str) -> str:
    return (
        sql.replace("private.", f'"{schema}".')
        .replace("'private'", f"'{schema}'")
    )


class Checker:
    def __init__(self, conn: asyncpg.Connection, schema: str) -> None:
        self.conn = conn
        self.schema = schema
        self.passed = 0

    async def scalar(self, sql: str, *args):
        return await self.conn.fetchval(_q(sql, self.schema), *args)

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise AssertionError(f"FALLO: {label} {detail}".strip())
        self.passed += 1
        suffix = f" ({detail})" if detail else ""
        print(f"  [ok] {label}{suffix}")

    async def eq(self, label: str, sql: str, expected, *args) -> None:
        value = await self.scalar(sql, *args)
        if isinstance(expected, Decimal):
            value = Decimal(value) if value is not None else None
        self.ok(label, value == expected, f"valor={value!r}")

    async def expect_error(self, label: str, sql: str, sqlstate: str) -> None:
        """Ejecuta dentro de un SAVEPOINT y exige el SQLSTATE indicado."""
        tr = self.conn.transaction()
        await tr.start()
        seen: str | None = None
        message = ""
        try:
            await self.conn.execute(_q(sql, self.schema))
        except asyncpg.PostgresError as exc:
            seen = exc.sqlstate
            message = str(exc).splitlines()[0]
        finally:
            await tr.rollback()

        self.ok(
            label,
            seen == sqlstate,
            f"sqlstate={seen!r}, esperado={sqlstate!r}: {message}",
        )

    async def exec(self, sql: str) -> None:
        await self.conn.execute(_q(sql, self.schema))


async def run_checks(conn: asyncpg.Connection, schema: str) -> None:
    c = Checker(conn, schema)

    print("\n[1/9] Estructura creada")
    await c.eq(
        "tablas base",
        """
        SELECT count(*) FROM information_schema.tables
         WHERE table_schema = 'private' AND table_type = 'BASE TABLE'
        """,
        21,
    )
    await c.eq(
        "vista de stock",
        """
        SELECT count(*) FROM information_schema.views
         WHERE table_schema = 'private'
           AND table_name = 'v_inventory_stock_status'
        """,
        1,
    )
    await c.eq(
        "triggers creados",
        """
        SELECT count(*)
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'private' AND NOT t.tgisinternal
        """,
        17,
    )
    await c.eq(
        "funciones creadas",
        """
        SELECT count(*) FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'private'
        """,
        8,
    )

    print("\n[2/9] Seeders")
    for seed in sorted((DB_DIR / "seeds").glob("0*.sql")):
        print(f"  -> {seed.name}")
        await c.exec(seed.read_text(encoding="utf-8").replace(
            "private.", f'"{schema}".'
        ))

    await c.eq("roles (RN-1)", "SELECT count(*) FROM private.rol", 3)
    await c.eq("usuarios demo", 'SELECT count(*) FROM private."user"', 4)
    await c.eq("proyectos demo", 'SELECT count(*) FROM private."Project"', 4)
    await c.eq(
        "lotes demo",
        "SELECT count(*) FROM private.audith_raw_material_supplier",
        7,
    )
    await c.eq("unidades de equipo", "SELECT count(*) FROM private.equipament_supplier", 4)
    await c.eq("reservas demo", "SELECT count(*) FROM private.reservation_equipament", 3)
    await c.eq("solicitudes demo", "SELECT count(*) FROM private.expense_approval", 5)
    await c.eq(
        "asignaciones overhead",
        "SELECT count(*) FROM private.overhead_allocation",
        2,
    )
    await c.eq(
        "stock inicial S1 (acido/V1)",
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """,
        Decimal("12.50"),
    )
    await c.eq(
        "vista: S4 bajo minimo",
        """
        SELECT stock_status FROM private.v_inventory_stock_status
         WHERE resource_name = 'Papel filtro cualitativo'
           AND vendor_name = 'Insumos de Laboratorio Central, C.A.'
        """,
        "Alerta Stock Mínimo",
    )
    await c.eq(
        "vista: S7 agotado",
        """
        SELECT stock_status FROM private.v_inventory_stock_status
         WHERE resource_name = 'Ácido clorhídrico 37%'
           AND vendor_name = 'Insumos de Laboratorio Central, C.A.'
        """,
        "Agotado",
    )
    await c.eq(
        "overhead: reparto = total",
        """
        SELECT op.total_cost - COALESCE(sum(oa.amount), 0)
          FROM private.overhead_period op
          LEFT JOIN private.overhead_allocation oa
            ON oa.id_overhead_period = op.id
         WHERE op.id = '01900000-0000-7000-8000-0000000000b1'
         GROUP BY op.id, op.total_cost
        """,
        Decimal("0.00"),
    )

    print("\n[3/9] Lote suma stock y despacho resta stock")
    await c.exec(
        """
        INSERT INTO private.audith_raw_material_supplier
            (id_raw_materials_supplier, in_date,
             count_raw_materials_in, unit_cost, invoice_reference)
        SELECT s.id, '2026-11-20 09:00:00+00', 2.00, 1.00, 'VERIFY-LOT'
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """
    )
    await c.eq(
        "lote +2.00 -> 14.50",
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """,
        Decimal("14.50"),
    )

    await c.exec(
        """
        INSERT INTO private.dispatch_log
            (id_raw_materials_suppliers, id_project, id_user,
             use_raw_materials, notes)
        SELECT s.id, p.id, u.id, 1.50, 'VERIFY-DISPATCH'
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id,
               private."Project" p,
               private."user" u
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
           AND p.unique_code = 'FIS-2026-001'
           AND u.email = 'admin@example.com'
        """
    )
    await c.eq(
        "despacho -1.50 -> 13.00",
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """,
        Decimal("13.00"),
    )
    await c.expect_error(
        "despacho mayor al stock rechazado",
        """
        INSERT INTO private.dispatch_log
            (id_raw_materials_suppliers, id_project, id_user,
             use_raw_materials, notes)
        SELECT s.id, p.id, u.id, 9999.00, 'VERIFY-OVERDRAW'
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id,
               private."Project" p,
               private."user" u
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
           AND p.unique_code = 'FIS-2026-001'
           AND u.email = 'admin@example.com'
        """,
        "23514",
    )
    await c.eq(
        "stock intacto tras rechazo",
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """,
        Decimal("13.00"),
    )

    print("\n[4/9] Inmutabilidad de audit_log")
    await c.exec(
        """
        INSERT INTO private.audit_log
            (user_id, ip_address, entity, action, outcome)
        SELECT u.id, '127.0.0.1', 'Test', 'verify_schema', 'success'
          FROM private."user" u
         WHERE u.email = 'admin@example.com'
        """
    )
    await c.eq(
        "audit_log acepta INSERT",
        "SELECT count(*) FROM private.audit_log WHERE action = 'verify_schema'",
        1,
    )
    await c.expect_error(
        "audit_log rechaza UPDATE",
        "UPDATE private.audit_log SET action = 'alterado' WHERE action = 'verify_schema'",
        "23000",
    )
    await c.expect_error(
        "audit_log rechaza DELETE",
        "DELETE FROM private.audit_log WHERE action = 'verify_schema'",
        "23000",
    )
    await c.expect_error(
        "audit_log rechaza TRUNCATE",
        "TRUNCATE private.audit_log",
        "23000",
    )

    print("\n[5/9] Inmutabilidad de dispatch_log y lotes")
    await c.expect_error(
        "dispatch_log rechaza UPDATE",
        "UPDATE private.dispatch_log SET notes = 'alterado'",
        "23000",
    )
    await c.expect_error(
        "dispatch_log rechaza DELETE",
        "DELETE FROM private.dispatch_log",
        "23000",
    )
    await c.expect_error(
        "dispatch_log rechaza TRUNCATE",
        "TRUNCATE private.dispatch_log",
        "23000",
    )
    await c.expect_error(
        "lote rechaza UPDATE",
        "UPDATE private.audith_raw_material_supplier SET unit_cost = 0",
        "23000",
    )
    await c.expect_error(
        "lote rechaza DELETE",
        "DELETE FROM private.audith_raw_material_supplier",
        "23000",
    )
    await c.expect_error(
        "lote rechaza TRUNCATE",
        "TRUNCATE private.audith_raw_material_supplier",
        "23000",
    )

    print("\n[6/9] Reservas sin solape (RN-5)")
    await c.expect_error(
        "reserva solapada rechazada",
        """
        INSERT INTO private.reservation_equipament
            (id_equipament_supplier, id_project, id_user,
             reservation_date, end_date, purpose)
        SELECT es.id, p.id, u.id,
               '2026-12-01 09:00:00+00', '2026-12-01 10:00:00+00', 'VERIFY-OVERLAP'
          FROM private.equipament_supplier es,
               private."Project" p,
               private."user" u
         WHERE es.serial_number = 'SN-EQ-0001'
           AND p.unique_code = 'BIO-2026-003'
           AND u.email = 'coordinador1@example.com'
        """,
        "23P01",
    )
    await c.exec(
        """
        INSERT INTO private.reservation_equipament
            (id_equipament_supplier, id_project, id_user,
             reservation_date, end_date, purpose)
        SELECT es.id, p.id, u.id,
               '2026-12-01 12:00:00+00', '2026-12-01 13:00:00+00', 'VERIFY-ADJACENT'
          FROM private.equipament_supplier es,
               private."Project" p,
               private."user" u
         WHERE es.serial_number = 'SN-EQ-0001'
           AND p.unique_code = 'BIO-2026-003'
           AND u.email = 'coordinador1@example.com'
        """
    )
    await c.eq(
        "reserva contigua aceptada",
        """
        SELECT count(*) FROM private.reservation_equipament
         WHERE purpose = 'VERIFY-ADJACENT'
        """,
        1,
    )

    print("\n[7/9] Restricciones y updated_at")
    await c.expect_error(
        "codigo de proyecto duplicado rechazado (RN-2)",
        """
        INSERT INTO private."Project"
            (unique_code, name, id_status, id_priority, id_user)
        SELECT 'FIS-2026-001', 'Duplicado',
               sp.id, pp.id, u.id
          FROM private.status_project sp,
               private.priority_project pp,
               private."user" u
         WHERE sp.name = 'Propuesta'
           AND pp.name = 'Baja'
           AND u.email = 'admin@example.com'
        """,
        "23505",
    )
    await c.exec(
        """
        UPDATE private."Project"
           SET updated_at = '2000-01-01 00:00:00+00'
         WHERE unique_code = 'FIS-2026-001'
        """
    )
    await c.exec(
        """
        UPDATE private."Project"
           SET name = name || ' (verificado)'
         WHERE unique_code = 'FIS-2026-001'
        """
    )
    c.ok(
        "trigger actualiza updated_at",
        await c.scalar(
            """
            SELECT updated_at > '2001-01-01 00:00:00+00'
              FROM private."Project"
             WHERE unique_code = 'FIS-2026-001'
            """
        )
        is True,
    )
    await c.eq(
        "UUID v7 en la base",
        "SELECT substring(private.uuid_generate_v7()::text, 15, 1)",
        "7",
    )
    await c.eq(
        "UUID v7 unico en 200 generaciones",
        "SELECT count(DISTINCT private.uuid_generate_v7()) FROM generate_series(1, 200)",
        200,
    )

    print("\n[8/9] Idempotencia de seeders")
    stock_before = await c.scalar(
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """
    )
    projects_before = await c.scalar('SELECT count(*) FROM private."Project"')
    lots_before = await c.scalar(
        "SELECT count(*) FROM private.audith_raw_material_supplier"
    )
    for seed in sorted((DB_DIR / "seeds").glob("0*.sql")):
        await c.exec(seed.read_text(encoding="utf-8").replace(
            "private.", f'"{schema}".'
        ))
    await c.eq(
        "stock sin cambios al re-sembrar",
        """
        SELECT s.current_stock
          FROM private.raw_material_supplier s
          JOIN private.raw_material rm ON rm.id_inventroy = s.raw_materials_id
          JOIN private.vendors v ON v.id = s.vendors_id
         WHERE rm.name = 'Ácido clorhídrico 37%'
           AND v.name = 'Proveedor Quimico Andino, C.A.'
        """,
        stock_before,
    )
    await c.eq(
        "proyectos sin duplicados al re-sembrar",
        'SELECT count(*) FROM private."Project"',
        projects_before,
    )
    await c.eq(
        "lotes sin duplicados al re-sembrar",
        "SELECT count(*) FROM private.audith_raw_material_supplier",
        lots_before,
    )

    print("\n[9/9] Verificacion SQL del seed (99_verify.sql)")
    await c.exec((DB_DIR / "seeds" / "99_verify.sql").read_text(encoding="utf-8")
                 .replace("private.", f'"{schema}".'))
    c.ok("99_verify.sql ejecutado sin errores", True)

    await run_migration_checks(conn, schema, c)

    print(f"\nRESULTADO: {c.passed} comprobaciones OK")


def _migration_sql(path: Path, schema: str) -> str:
    """Carga una migracion para el esquema temporal.

    ``CREATE INDEX CONCURRENTLY`` no puede ejecutarse dentro de la transaccion
    que este script usa para el ROLLBACK final. En produccion la migracion SI
    conserva CONCURRENTLY (ver el archivo); aqui solo se valida su DDL.
    """
    sql = _read_for_schema(path, schema)
    return sql.replace("CONCURRENTLY ", "")


async def run_migration_checks(
    conn: asyncpg.Connection, schema: str, c: Checker
) -> None:
    """Valida la migracion de endurecimiento dentro del esquema temporal."""
    print("\n[10/10] Migracion 20261125_security_hardening.sql (idempotente)")

    if not SECURITY_MIGRATION.exists():
        raise AssertionError(f"No existe {SECURITY_MIGRATION}")

    migration = _migration_sql(SECURITY_MIGRATION, schema)
    # Dos ejecuciones: valida idempotencia de indices y triggers.
    await conn.execute(migration)
    await conn.execute(migration)

    await c.eq(
        "indice UNIQUE de raw_material normalizado",
        """
        SELECT count(*) FROM pg_indexes
         WHERE schemaname = 'private'
           AND indexname = 'uq_raw_material_name_normalized'
        """,
        1,
    )
    await c.eq(
        "indice UNIQUE de serial normalizado",
        """
        SELECT count(*) FROM pg_indexes
         WHERE schemaname = 'private'
           AND indexname = 'uq_equipament_supplier_serial_normalized'
        """,
        1,
    )
    await c.eq(
        "triggers append-only y de solape creados",
        """
        SELECT count(*)
          FROM pg_trigger t
          JOIN pg_class cl ON cl.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = cl.relnamespace
         WHERE n.nspname = 'private'
           AND t.tgname IN (
               'trg_audith_project_append_only',
               'trg_audith_project_no_truncate',
               'trg_overhead_period_append_only',
               'trg_overhead_period_no_truncate',
               'trg_overhead_allocation_append_only',
               'trg_overhead_allocation_no_truncate',
               'trg_overhead_period_no_overlap'
           )
        """,
        7,
    )

    await c.expect_error(
        "audith_project rechaza UPDATE",
        "UPDATE private.audith_project SET explaining = 'alterado'",
        "23000",
    )
    await c.expect_error(
        "overhead_period rechaza DELETE",
        "DELETE FROM private.overhead_period",
        "23000",
    )
    await c.expect_error(
        "overhead_allocation rechaza TRUNCATE",
        "TRUNCATE private.overhead_allocation",
        "23000",
    )
    await c.expect_error(
        "nombre de material duplicado rechazado",
        """
        INSERT INTO private.raw_material
            (id_inventroy, name, description, resource_type_id, unit)
        SELECT private.uuid_generate_v7(), '  ' || upper(rm.name) || '  ',
               NULL, rm.resource_type_id, rm.unit
          FROM private.raw_material rm
         ORDER BY rm.name
         LIMIT 1
        """,
        "23505",
    )
    await c.expect_error(
        "serial de equipo duplicado rechazado",
        """
        INSERT INTO private.equipament_supplier
            (id, id_equipament, id_vendors, id_status, model, serial_number)
        SELECT private.uuid_generate_v7(), es.id_equipament, es.id_vendors,
               es.id_status, es.model, lower(es.serial_number)
          FROM private.equipament_supplier es
         WHERE es.serial_number = 'SN-EQ-0001'
        """,
        "23505",
    )
    await c.expect_error(
        "periodo de overhead solapado rechazado",
        """
        INSERT INTO private.overhead_period
            (id, start_date, end_date, criterion, total_cost, closed_by)
        SELECT private.uuid_generate_v7(), DATE '2026-10-15',
               DATE '2026-10-20', 'proporcional', 100.00, u.id
          FROM private."user" u
         WHERE u.email = 'admin@example.com'
        """,
        "23P01",
    )
    await c.exec(
        """
        INSERT INTO private.overhead_period
            (id, start_date, end_date, criterion, total_cost, closed_by)
        SELECT private.uuid_generate_v7(), DATE '2026-11-01',
               DATE '2026-11-30', 'equitativo', 100.00, u.id
          FROM private."user" u
         WHERE u.email = 'admin@example.com'
        """
    )
    await c.eq(
        "periodo contiguo aceptado",
        """
        SELECT count(*) FROM private.overhead_period
         WHERE start_date = DATE '2026-11-01'
        """,
        1,
    )


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Valida esquema y seeders en un esquema temporal con ROLLBACK."
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    url = _database_url(args.database_url)
    kwargs = _connect_kwargs(url)
    schema = f"verify_schema_{uuid4().hex[:12]}"

    print(
        "Destino: "
        f"{kwargs['user']}@{kwargs['host']}:{kwargs['port']}/{kwargs['database']} "
        f"(sslmode={kwargs['ssl']})"
    )
    print(f"Esquema temporal: {schema} (se eliminara con ROLLBACK)")

    conn = await asyncpg.connect(**kwargs)
    tx = conn.transaction()
    await tx.start()
    try:
        for path in sorted((DB_DIR / "schema").glob("*.sql")):
            print(f"\n-> {path.name}")
            await conn.execute(_read_for_schema(path, schema))
        await run_checks(conn, schema)
    finally:
        await tx.rollback()
        remaining = await conn.fetchval(
            "SELECT to_regnamespace($1)::text", schema
        )
        await conn.close()

    if remaining is not None:
        raise AssertionError(
            f"El esquema temporal {schema} no se elimino con ROLLBACK."
        )

    print("\nROLLBACK confirmado: el esquema temporal ya no existe.")
    print("No se modifico el esquema private real.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (AssertionError, asyncpg.PostgresError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
