-- Separa la propiedad de audit_log del rol de aplicacion.
-- El rol de app solo puede INSERT/SELECT; no puede desactivar triggers ni
-- alterar la tabla porque deja de ser owner.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_owner') THEN
        CREATE ROLE audit_owner NOLOGIN;
    END IF;
END $$;

-- El nuevo owner necesita acceso temporal al esquema para la transferencia.
GRANT USAGE, CREATE ON SCHEMA private TO audit_owner;

-- Membresia temporal para poder transferir la propiedad.
GRANT audit_owner TO postgres;

-- Privilegios del rol de aplicacion mientras todavia es owner.
GRANT SELECT, INSERT ON private.audit_log TO postgres;
REVOKE UPDATE, DELETE, TRUNCATE ON private.audit_log FROM postgres;
REVOKE UPDATE, DELETE, TRUNCATE ON private.audit_log FROM PUBLIC;

COMMENT ON TABLE private.audit_log IS
    'Bitacora append-only; owner separado (audit_owner) del rol de aplicacion.';

ALTER TABLE private.audit_log OWNER TO audit_owner;

-- El GRANT previo a un owner es no-op: se repite como nuevo owner.
SET ROLE audit_owner;
GRANT SELECT, INSERT ON private.audit_log TO postgres;
RESET ROLE;

REVOKE CREATE ON SCHEMA private FROM audit_owner;
REVOKE audit_owner FROM postgres;

-- Asegura que no quede ADMIN OPTION aunque la membresia se haya retirado.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.roleid
        JOIN pg_roles u ON u.oid = m.member
        WHERE r.rolname = 'audit_owner'
          AND u.rolname = 'postgres'
          AND m.admin_option
    ) THEN
        EXECUTE 'REVOKE ADMIN OPTION FOR audit_owner FROM postgres';
    END IF;
END $$;

-- Si el rol de app conservara ADMIN OPTION, se le retira.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.roleid
        JOIN pg_roles u ON u.oid = m.member
        WHERE r.rolname = 'audit_owner'
          AND u.rolname = 'postgres'
          AND m.admin_option
    ) THEN
        EXECUTE 'REVOKE ADMIN OPTION FOR audit_owner FROM postgres';
    END IF;
END $$;
