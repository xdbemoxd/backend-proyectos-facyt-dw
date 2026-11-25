"""Contrato estatico de la migracion de endurecimiento (sin base de datos).

La validacion real de DDL/triggers vive en db/scripts/verify_schema.py, que la
aplica en un esquema temporal con ROLLBACK. Aqui se protegen las decisiones que
no deben perderse en una edicion futura.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20261125_security_hardening.sql"
)


def sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists_and_is_idempotent():
    text = sql()
    assert "IF NOT EXISTS" in text
    assert text.count("DROP TRIGGER IF EXISTS") >= 6
    assert "CREATE OR REPLACE FUNCTION private.overhead_period_no_overlap()" in text


def test_unique_indexes_are_normalized_and_concurrent():
    text = sql()
    assert "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS" in text
    assert "uq_raw_material_name_normalized" in text
    assert "ON private.raw_material (lower(btrim(name)))" in text
    assert "uq_equipament_supplier_serial_normalized" in text
    assert "ON private.equipament_supplier (lower(btrim(serial_number)))" in text
    assert "WHERE serial_number IS NOT NULL" in text
    # Documentado: hay que ejecutarla fuera de una transaccion.
    assert "autocommit" in text
    assert "lock_timeout" in text


def test_append_only_triggers_cover_the_three_tables():
    text = sql()
    for table in (
        "audith_project",
        "overhead_period",
        "overhead_allocation",
    ):
        assert f"ON private.{table}" in text
    assert text.count("EXECUTE FUNCTION private.append_only_guard()") >= 6
    assert "BEFORE TRUNCATE" in text
    assert "FOR EACH STATEMENT" in text


def test_duplicates_abort_with_clear_message_and_do_not_delete_data():
    text = sql()
    assert "Migracion abortada" in text
    assert "raw_material duplicados" in text
    assert "serial_number duplicados" in text
    assert "no borra datos" in text
    # No debe haber limpieza destructiva de duplicados.
    assert "DELETE FROM private.raw_material" not in text
    assert "DELETE FROM private.equipament_supplier" not in text


def test_overhead_overlap_is_blocked_without_extensions():
    text = sql()
    assert "pg_advisory_xact_lock" in text
    assert "p.start_date <= NEW.end_date" in text
    assert "p.end_date >= NEW.start_date" in text
    assert "exclusion_violation" in text
    assert "BEFORE INSERT OR UPDATE ON private.overhead_period" in text
    # Sin depender de btree_gist ni EXCLUDE.
    assert "CREATE EXTENSION" not in text
    assert "EXCLUDE USING gist" not in text
