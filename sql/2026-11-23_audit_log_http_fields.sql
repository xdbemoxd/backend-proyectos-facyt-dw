-- Amplia private.audit_log para el nuevo servicio de auditoria (src/utils/audit.py).
-- Seguro de ejecutar mas de una vez.

ALTER TABLE private.audit_log
    ADD COLUMN IF NOT EXISTS request_id      UUID,
    ADD COLUMN IF NOT EXISTS user_email      VARCHAR(255),
    ADD COLUMN IF NOT EXISTS method          VARCHAR(10),
    ADD COLUMN IF NOT EXISTS path            VARCHAR(500),
    ADD COLUMN IF NOT EXISTS status_code     INTEGER,
    ADD COLUMN IF NOT EXISTS user_agent      VARCHAR(512),
    ADD COLUMN IF NOT EXISTS request_headers JSONB,
    ADD COLUMN IF NOT EXISTS query_params    JSONB,
    ADD COLUMN IF NOT EXISTS duration_ms     INTEGER,
    ADD COLUMN IF NOT EXISTS error_code      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS error_detail    VARCHAR(1000),
    ADD COLUMN IF NOT EXISTS before_data     JSONB,
    ADD COLUMN IF NOT EXISTS after_data      JSONB;

-- Copia lo que ya habia en before_str / after_str (texto JSON) a las columnas nuevas.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'private' AND table_name = 'audit_log' AND column_name = 'before_str') THEN
        UPDATE private.audit_log SET
            before_data = CASE
                WHEN before_str IS NULL THEN NULL
                WHEN before_str ~ '^\s*[\{\[]' THEN before_str::jsonb
                ELSE to_jsonb(before_str) END,
            after_data = CASE
                WHEN after_str IS NULL THEN NULL
                WHEN after_str ~ '^\s*[\{\[]' THEN after_str::jsonb
                ELSE to_jsonb(after_str) END
        WHERE before_data IS NULL AND after_data IS NULL
          AND (before_str IS NOT NULL OR after_str IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_audit_log_timestamp  ON private.audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_user_id    ON private.audit_log (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_request_id ON private.audit_log (request_id);

-- Cuando confirmes que todo funciona, puedes borrar las columnas viejas:
-- ALTER TABLE private.audit_log DROP COLUMN before_str, DROP COLUMN after_str;
