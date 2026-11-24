-- ============================================================================
-- Mejora de private.audit_log
-- Sistema de Control de Proyectos y Recursos
--
-- Objetivos:
--   1. Auditar metadata HTTP: request_id, método, endpoint, status, user-agent,
--      headers redactados, query params, duración y detalle de error.
--   2. Guardar snapshot del email del actor (sobrevive a bajas de usuario).
--   3. Convertir before/after a JSONB para consultas estructuradas.
--   4. Impedir UPDATE, DELETE y TRUNCATE (append-only real).
--   5. Agregar índices para consultas de auditoría.
--
-- Nota: la migración quita temporalmente los triggers de inmutabilidad para
-- poder hacer el backfill y la conversión de tipos. Los recrea al final.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. Quitar triggers para permitir la migración idempotente
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_audit_log_append_only ON private.audit_log;
DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON private.audit_log;

-- ----------------------------------------------------------------------------
-- 1. Columnas de metadata HTTP y snapshot del actor
-- ----------------------------------------------------------------------------
ALTER TABLE private.audit_log
    ADD COLUMN IF NOT EXISTS request_id      uuid,
    ADD COLUMN IF NOT EXISTS method          varchar(10),
    ADD COLUMN IF NOT EXISTS path            varchar(500),
    ADD COLUMN IF NOT EXISTS status_code     smallint,
    ADD COLUMN IF NOT EXISTS user_agent      varchar(512),
    ADD COLUMN IF NOT EXISTS request_headers jsonb,
    ADD COLUMN IF NOT EXISTS query_params    jsonb,
    ADD COLUMN IF NOT EXISTS duration_ms     integer,
    ADD COLUMN IF NOT EXISTS error_code      varchar(100),
    ADD COLUMN IF NOT EXISTS error_detail    varchar(1000),
    ADD COLUMN IF NOT EXISTS user_email      varchar(255);

COMMENT ON COLUMN private.audit_log.request_id      IS 'UUID que correlaciona la request HTTP con eventos de negocio.';
COMMENT ON COLUMN private.audit_log.method          IS 'Método HTTP: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS.';
COMMENT ON COLUMN private.audit_log.path            IS 'Endpoint o ruta solicitada, sin query string.';
COMMENT ON COLUMN private.audit_log.status_code     IS 'Código de respuesta HTTP (100-599).';
COMMENT ON COLUMN private.audit_log.user_agent      IS 'User-Agent del cliente.';
COMMENT ON COLUMN private.audit_log.request_headers IS 'Headers HTTP permitidos y redactados (sin Authorization/Cookie).';
COMMENT ON COLUMN private.audit_log.query_params    IS 'Query params con claves sensibles redactadas.';
COMMENT ON COLUMN private.audit_log.duration_ms     IS 'Duración de la request en milisegundos.';
COMMENT ON COLUMN private.audit_log.error_code      IS 'Código interno o HTTP_<status> del error.';
COMMENT ON COLUMN private.audit_log.error_detail    IS 'Detalle acotado del error (sin credenciales).';
COMMENT ON COLUMN private.audit_log.user_email      IS 'Snapshot del email del actor al momento del evento.';

-- ----------------------------------------------------------------------------
-- 2. Backfill del email del actor en registros históricos
-- ----------------------------------------------------------------------------
UPDATE private.audit_log a
SET user_email = u.email
FROM private."user" u
WHERE a.user_id = u.id
  AND a.user_email IS NULL;

-- ----------------------------------------------------------------------------
-- 3. Renombrar before_str/after_str a before_data/after_data
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'private' AND table_name = 'audit_log'
          AND column_name = 'before_str'
    ) THEN
        ALTER TABLE private.audit_log RENAME COLUMN before_str TO before_data;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'private' AND table_name = 'audit_log'
          AND column_name = 'after_str'
    ) THEN
        ALTER TABLE private.audit_log RENAME COLUMN after_str TO after_data;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 4. Validar y convertir before_data/after_data a JSONB
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    invalid_rows bigint;
BEGIN
    SELECT count(*) INTO invalid_rows
    FROM private.audit_log
    WHERE (before_data IS NOT NULL AND NOT pg_input_is_valid(before_data::text, 'jsonb'))
       OR (after_data IS NOT NULL AND NOT pg_input_is_valid(after_data::text, 'jsonb'));

    IF invalid_rows > 0 THEN
        RAISE EXCEPTION
            'audit_log: % registros tienen before_data/after_data que no son JSON válido. Corrígelos antes de migrar.',
            invalid_rows;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'private' AND table_name = 'audit_log'
          AND column_name = 'before_data' AND data_type <> 'jsonb'
    ) THEN
        ALTER TABLE private.audit_log
            ALTER COLUMN before_data TYPE jsonb USING before_data::jsonb;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'private' AND table_name = 'audit_log'
          AND column_name = 'after_data' AND data_type <> 'jsonb'
    ) THEN
        ALTER TABLE private.audit_log
            ALTER COLUMN after_data TYPE jsonb USING after_data::jsonb;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 5. FK sin SET NULL: borrar un usuario no debe modificar la auditoría
-- ----------------------------------------------------------------------------
ALTER TABLE private.audit_log DROP CONSTRAINT IF EXISTS audit_log_user_id_fkey;

ALTER TABLE private.audit_log
    ADD CONSTRAINT audit_log_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES private."user"(id) ON DELETE RESTRICT;

-- ----------------------------------------------------------------------------
-- 6. Constraints de validación y tamaño
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_status'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_status
            CHECK (status_code IS NULL OR status_code BETWEEN 100 AND 599);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_method'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_method
            CHECK (method IS NULL OR method IN ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_duration'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_duration
            CHECK (duration_ms IS NULL OR duration_ms >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_request_headers_size'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_request_headers_size
            CHECK (request_headers IS NULL OR pg_column_size(request_headers) <= 32768);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_query_params_size'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_query_params_size
            CHECK (query_params IS NULL OR pg_column_size(query_params) <= 32768);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_before_data_size'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_before_data_size
            CHECK (before_data IS NULL OR pg_column_size(before_data) <= 65536);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_audit_after_data_size'
          AND conrelid = 'private.audit_log'::regclass
    ) THEN
        ALTER TABLE private.audit_log
            ADD CONSTRAINT ck_audit_after_data_size
            CHECK (after_data IS NULL OR pg_column_size(after_data) <= 65536);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 7. Índices para consultas de auditoría
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON private.audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user      ON private.audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity    ON private.audit_log (entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_status    ON private.audit_log (status_code);
CREATE INDEX IF NOT EXISTS idx_audit_request   ON private.audit_log (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON private.audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_outcome   ON private.audit_log (outcome);

-- ----------------------------------------------------------------------------
-- 8. Inmutabilidad append-only
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.audit_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_log es append-only: operación % no permitida', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_audit_log_append_only
BEFORE UPDATE OR DELETE ON private.audit_log
FOR EACH ROW EXECUTE FUNCTION private.audit_log_append_only();

CREATE TRIGGER trg_audit_log_no_truncate
BEFORE TRUNCATE ON private.audit_log
FOR EACH STATEMENT EXECUTE FUNCTION private.audit_log_append_only();

-- ----------------------------------------------------------------------------
-- 9. Privilegios: solo INSERT para roles de aplicación
-- ----------------------------------------------------------------------------
REVOKE UPDATE, DELETE, TRUNCATE ON private.audit_log FROM PUBLIC;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT rolname FROM pg_roles
        WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE ON private.audit_log FROM %I',
            r.rolname
        );
    END LOOP;
END $$;

COMMENT ON TABLE private.audit_log IS
    'Bitácora inmutable (append-only) de acciones y requests HTTP del sistema.';

COMMIT;
