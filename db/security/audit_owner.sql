-- ============================================================================
-- db/security/audit_owner.sql  (OPCIONAL, no forma parte del orden base)
--
-- Endurecimiento de audit_log: separa la propiedad de la tabla del rol de
-- aplicacion. El rol de la app conserva SELECT/INSERT pero no es owner, por lo
-- que no puede desactivar los triggers append-only.
--
-- Requiere un rol con superuser o CREATEROLE (en Supabase, el rol `postgres`;
-- en Docker, el usuario POSTGRES_USER). Si el proveedor no permite crear roles,
-- este paso debe hacerse desde el panel del proveedor.
--
-- Basado en db/migrations/20261123_audit_owner.sql, pero usa session_user en
-- lugar de asumir que el rol de aplicacion se llama `postgres`.
--
-- Ejecutar DESPUES de db/schema/*.sql y de los seeders, una sola vez.
-- ============================================================================

SET client_encoding = 'UTF8';

DO $$
DECLARE
    v_app_role text := session_user;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_roles
         WHERE rolname = v_app_role
           AND (rolsuper OR rolcreaterole)
    ) THEN
        RAISE EXCEPTION
            'El rol % no puede crear roles. Ejecuta este script como superuser '
            'o con un rol CREATEROLE.', v_app_role;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_owner') THEN
        CREATE ROLE audit_owner NOLOGIN;
    END IF;

    -- Acceso temporal al esquema para poder transferir la propiedad.
    EXECUTE 'GRANT USAGE, CREATE ON SCHEMA private TO audit_owner';
    EXECUTE format('GRANT audit_owner TO %I', v_app_role);

    -- El comentario se hace antes de perder la propiedad de la tabla.
    COMMENT ON TABLE private.audit_log IS
        'Bitacora append-only; owner separado (audit_owner) del rol de aplicacion.';

    ALTER TABLE private.audit_log OWNER TO audit_owner;

    -- El nuevo owner re-otorga los privilegios: al cambiar de owner, el rol
    -- anterior pierde los privilegios implicidos y necesita un GRANT explicito.
    EXECUTE 'SET ROLE audit_owner';
    EXECUTE format(
        'GRANT SELECT, INSERT ON private.audit_log TO %I', v_app_role
    );
    EXECUTE 'RESET ROLE';

    EXECUTE 'REVOKE CREATE ON SCHEMA private FROM audit_owner';
    EXECUTE format('REVOKE audit_owner FROM %I', v_app_role);
END $$;
