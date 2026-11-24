-- Version de token para revocacion de JWT (logout, cambio de rol/estado).
ALTER TABLE private."user"
    ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN private."user".token_version IS
    'Version de JWT; se incrementa para invalidar tokens emitidos.';
