-- ============================================================================
-- 20261125_security_hardening.sql
-- Endurecimiento de integridad para bases existentes.
--
-- Contenido:
--   1. UNIQUE de equipament_supplier(serial_number) normalizado.
--   2. UNIQUE de raw_material(lower(btrim(name))).
--   3. Append-only (UPDATE/DELETE/TRUNCATE) en audith_project,
--      overhead_period y overhead_allocation.
--   4. Prohibicion de periodos de overhead solapados.
--
-- Idempotente: IF NOT EXISTS, CREATE OR REPLACE y DROP TRIGGER IF EXISTS.
--
-- Operacion en produccion (tablas grandes):
--   * Los indices usan CREATE INDEX CONCURRENTLY: no bloquean escrituras.
--   * CONCURRENTLY NO puede ejecutarse dentro de una transaccion. Aplica este
--     archivo con psql en autocommit (por ejemplo: psql "$DATABASE_URL" -f ...)
--     y nunca dentro de BEGIN/COMMIT ni de una herramienta que envuelva todo
--     el archivo en una transaccion.
--   * Si un intento previo dejo un indice marcado INVALID, dropealo con
--     DROP INDEX CONCURRENTLY private.<nombre>; y vuelve a ejecutar.
--   * Un lock_timeout acotado evita que los DROP/CREATE TRIGGER esperen
--     indefinidamente por un lock. Los triggers se crean en milisegundos
--     (solo catalogo). No se aplica a los indices concurrentes: un timeout ahi
--     dejaria un indice INVALID.
--
-- Esta migracion NO borra ni corrige datos. Si hay duplicados, aborta con un
-- mensaje que indica cuantos hay y ejemplos, para resolverlos manualmente.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- 1. Pre-chequeo de duplicados (falla con mensaje claro, sin tocar datos)
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_count integer;
    v_sample text;
BEGIN
    SELECT count(*)
      INTO v_count
      FROM (
          SELECT lower(btrim(name)) AS key
            FROM private.raw_material
           GROUP BY lower(btrim(name))
          HAVING count(*) > 1
      ) AS duplicados;

    IF v_count > 0 THEN
        SELECT string_agg(key, ', ')
          INTO v_sample
          FROM (
              SELECT lower(btrim(name)) AS key
                FROM private.raw_material
               GROUP BY lower(btrim(name))
              HAVING count(*) > 1
               ORDER BY lower(btrim(name))
               LIMIT 10
          ) AS muestra;

        RAISE EXCEPTION
            'Migracion abortada: % nombre(s) de raw_material duplicados tras normalizar (lower/btrim). Ejemplos: %. Resuelvelos manualmente; la migracion no borra datos.',
            v_count, v_sample
            USING ERRCODE = 'unique_violation';
    END IF;

    SELECT count(*)
      INTO v_count
      FROM (
          SELECT lower(btrim(serial_number)) AS key
            FROM private.equipament_supplier
           WHERE serial_number IS NOT NULL
           GROUP BY lower(btrim(serial_number))
          HAVING count(*) > 1
      ) AS duplicados;

    IF v_count > 0 THEN
        SELECT string_agg(key, ', ')
          INTO v_sample
          FROM (
              SELECT lower(btrim(serial_number)) AS key
                FROM private.equipament_supplier
               WHERE serial_number IS NOT NULL
               GROUP BY lower(btrim(serial_number))
              HAVING count(*) > 1
               ORDER BY lower(btrim(serial_number))
               LIMIT 10
          ) AS muestra;

        RAISE EXCEPTION
            'Migracion abortada: % serial_number duplicados en equipament_supplier tras normalizar (lower/btrim). Ejemplos: %. Resuelvelos manualmente; la migracion no borra datos.',
            v_count, v_sample
            USING ERRCODE = 'unique_violation';
    END IF;
END
$$;

-- ----------------------------------------------------------------------------
-- 2. Indices UNIQUE normalizados (sin bloquear escrituras)
-- ----------------------------------------------------------------------------
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
    uq_raw_material_name_normalized
    ON private.raw_material (lower(btrim(name)));

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
    uq_equipament_supplier_serial_normalized
    ON private.equipament_supplier (lower(btrim(serial_number)))
    WHERE serial_number IS NOT NULL;

-- Variante sin CONCURRENTLY (solo si la herramienta obliga a una transaccion
-- y se acepta el lock de escritura durante la creacion):
--   CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_material_name_normalized
--       ON private.raw_material (lower(btrim(name)));
--   CREATE UNIQUE INDEX IF NOT EXISTS uq_equipament_supplier_serial_normalized
--       ON private.equipament_supplier (lower(btrim(serial_number)))
--       WHERE serial_number IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. Append-only para historial de prioridad y cierres de overhead
--
-- Solo los DDL de triggers (catalogo, milisegundos) usan lock_timeout; asi una
-- sesion bloqueada no retiene la migracion indefinidamente.
-- ----------------------------------------------------------------------------
SET lock_timeout = '5s';

DROP TRIGGER IF EXISTS trg_audith_project_append_only
    ON private.audith_project;
CREATE TRIGGER trg_audith_project_append_only
    BEFORE UPDATE OR DELETE ON private.audith_project
    FOR EACH ROW
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_audith_project_no_truncate
    ON private.audith_project;
CREATE TRIGGER trg_audith_project_no_truncate
    BEFORE TRUNCATE ON private.audith_project
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_overhead_period_append_only
    ON private.overhead_period;
CREATE TRIGGER trg_overhead_period_append_only
    BEFORE UPDATE OR DELETE ON private.overhead_period
    FOR EACH ROW
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_overhead_period_no_truncate
    ON private.overhead_period;
CREATE TRIGGER trg_overhead_period_no_truncate
    BEFORE TRUNCATE ON private.overhead_period
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_overhead_allocation_append_only
    ON private.overhead_allocation;
CREATE TRIGGER trg_overhead_allocation_append_only
    BEFORE UPDATE OR DELETE ON private.overhead_allocation
    FOR EACH ROW
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_overhead_allocation_no_truncate
    ON private.overhead_allocation;
CREATE TRIGGER trg_overhead_allocation_no_truncate
    BEFORE TRUNCATE ON private.overhead_allocation
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.append_only_guard();

-- ----------------------------------------------------------------------------
-- 4. Periodos de overhead sin solape
--
-- Rango inclusivo [start_date, end_date]. Un periodo nuevo no puede
-- intersectar otro ya cerrado. Se serializa con un advisory lock de
-- transaccion (barato y sin bloquear la tabla completa).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.overhead_period_no_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('private.overhead_period_no_overlap')
    );

    IF EXISTS (
        SELECT 1
          FROM private.overhead_period p
         WHERE p.id <> NEW.id
           AND p.start_date <= NEW.end_date
           AND p.end_date >= NEW.start_date
    ) THEN
        RAISE EXCEPTION
            'El periodo [%, %] se solapa con otro periodo de overhead ya cerrado',
            NEW.start_date, NEW.end_date
            USING ERRCODE = 'exclusion_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.overhead_period_no_overlap() IS
    'Rechaza periodos de overhead solapados (RN-6).';

DROP TRIGGER IF EXISTS trg_overhead_period_no_overlap
    ON private.overhead_period;
CREATE TRIGGER trg_overhead_period_no_overlap
    BEFORE INSERT OR UPDATE ON private.overhead_period
    FOR EACH ROW
    EXECUTE FUNCTION private.overhead_period_no_overlap();

RESET lock_timeout;
