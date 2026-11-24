-- ============================================================================
-- 2026-11-24 — Solicitante de la solicitud de gasto (RN-7 / autoaprobacion)
--
-- Agrega `expense_approval.id_user` para saber quien creo la solicitud y poder
-- impedir que un aprobador apruebe o rechace la suya propia.
--
-- Es una migracion NO destructiva:
--   1. columna nullable,
--   2. backfill desde el coordinador del proyecto,
--   3. FK e indice,
--   4. NOT NULL solo si no quedan nulos.
-- No elimina ni modifica filas existentes.
-- ============================================================================

BEGIN;

ALTER TABLE private.expense_approval
    ADD COLUMN IF NOT EXISTS id_user uuid;

-- Backfill seguro: el coordinador responsable del proyecto es el solicitante
-- historico hasta que exista trazabilidad propia.
UPDATE private.expense_approval AS e
   SET id_user = p.id_user
  FROM private."Project" AS p
 WHERE p.id = e.id_project
   AND e.id_user IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'expense_approval_id_user_fkey'
           AND conrelid = 'private.expense_approval'::regclass
    ) THEN
        ALTER TABLE private.expense_approval
            ADD CONSTRAINT expense_approval_id_user_fkey
            FOREIGN KEY (id_user) REFERENCES private."user" (id)
            ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_expense_requester
    ON private.expense_approval (id_user);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM private.expense_approval WHERE id_user IS NULL
    ) THEN
        RAISE EXCEPTION
            'expense_approval.id_user conserva nulos; revisar proyectos sin coordinador';
    END IF;
END $$;

ALTER TABLE private.expense_approval
    ALTER COLUMN id_user SET NOT NULL;

COMMENT ON COLUMN private.expense_approval.id_user IS
    'Usuario que creo la solicitud de gasto; impide la autoaprobacion del aprobador.';

COMMIT;
