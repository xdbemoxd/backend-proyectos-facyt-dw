-- Agrega la version de token a los usuarios (invalida JWT al cerrar sesion
-- o al cambiar rol/estado). Seguro de ejecutar mas de una vez.
ALTER TABLE private."user"
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
