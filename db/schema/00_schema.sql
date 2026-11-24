-- ============================================================================
-- 00_schema.sql
-- Sistema de Control de Proyectos y Recursos (FACYT)
--
-- Crea el esquema `private` y las funciones base que usan las tablas.
-- Es idempotente: se puede ejecutar varias veces sin efectos secundarios.
--
-- Orden de ejecucion:
--   00_schema.sql -> 01_tables.sql -> 02_indexes.sql -> 03_functions.sql
--   -> 04_triggers.sql -> 05_views.sql
--   Despues: db/seeds/01_catalogs.sql ... db/seeds/05_expenses_overhead.sql
--
-- Requiere PostgreSQL >= 14 (probado contra PostgreSQL 15/16 de Supabase).
-- ============================================================================

SET client_encoding = 'UTF8';

CREATE SCHEMA IF NOT EXISTS private;

COMMENT ON SCHEMA private IS
    'Esquema privado del Sistema de Control de Proyectos y Recursos de FACYT.';


-- ----------------------------------------------------------------------------
-- UUID v7 generado en la base de datos.
--
-- Los modelos de SQLAlchemy generan el UUID v7 en Python (uuid6.uuid7), pero
-- las filas Insertadas por SQL directo (seeders, scripts de mantenimiento o
-- clientes externos) necesitan un DEFAULT. Se construye sin pgcrypto:
--   * 6 bytes: milisegundos desde epoch (48 bits)
--   * resto : aleatorio de gen_random_uuid() (PostgreSQL >= 13)
--   * version = 7, variante RFC 4122
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.uuid_generate_v7()
RETURNS uuid
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    ts bytea;
    u  bytea;
BEGIN
    ts := substring(
        int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint)
        FROM 3
    );

    u := uuid_send(gen_random_uuid());

    -- Bytes 0..5: timestamp en milisegundos (big endian).
    u := set_byte(u, 0, get_byte(ts, 0));
    u := set_byte(u, 1, get_byte(ts, 1));
    u := set_byte(u, 2, get_byte(ts, 2));
    u := set_byte(u, 3, get_byte(ts, 3));
    u := set_byte(u, 4, get_byte(ts, 4));
    u := set_byte(u, 5, get_byte(ts, 5));

    -- Byte 6: version 7 en el nibble alto.
    u := set_byte(u, 6, (get_byte(u, 6) & 15) | 112);
    -- Byte 8: variante RFC 4122 (10xx xxxx).
    u := set_byte(u, 8, (get_byte(u, 8) & 63) | 128);

    RETURN encode(u, 'hex')::uuid;
END;
$$;

COMMENT ON FUNCTION private.uuid_generate_v7() IS
    'Genera un UUID v7 (timestamp + aleatorio) sin depender de pgcrypto.';
