-- ============================================================================
-- 03_functions.sql
-- Funciones PL/pgSQL de integridad y reglas de negocio a nivel de BD.
--
-- Se separan de los triggers (04_triggers.sql) para poder recrearlas sin
-- tocar las tablas. Idempotente: CREATE OR REPLACE FUNCTION.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Inmutabilidad de audit_log (RN-8).
-- Bloquea UPDATE, DELETE y TRUNCATE incluso para el rol de aplicacion. El
-- owner de la tabla puede desactivar el trigger; ver db/security/audit_owner.sql
-- para separar la propiedad en instalaciones maduras.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.audit_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_log es append-only: operacion % no permitida', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION private.audit_log_append_only() IS
    'Rechaza UPDATE/DELETE/TRUNCATE sobre audit_log (append-only).';

-- ----------------------------------------------------------------------------
-- Guarda append-only generica para otras bitacoras (dispatch_log, lotes).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.append_only_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% es append-only: operacion % no permitida',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION private.append_only_guard() IS
    'Rechaza UPDATE/DELETE/TRUNCATE sobre bitacoras append-only.';

-- ----------------------------------------------------------------------------
-- Lote de entrada: suma count_raw_materials_in al stock del proveedor.
-- Se ejecuta AFTER INSERT, cuando la FK ya valido el stock.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.audith_raw_material_supplier_add_stock()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_id uuid;
BEGIN
    UPDATE private.raw_material_supplier
       SET current_stock = current_stock + NEW.count_raw_materials_in
     WHERE id = NEW.id_raw_materials_supplier
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        RAISE EXCEPTION
            'No existe raw_material_supplier % para el lote %',
            NEW.id_raw_materials_supplier, NEW.id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.audith_raw_material_supplier_add_stock() IS
    'Suma la cantidad del lote al stock del proveedor (RN-4).';

-- ----------------------------------------------------------------------------
-- Despacho: resta stock en la misma transaccion y rechaza stock insuficiente.
-- Bloquea la fila de stock para evitar carreras entre despachos concurrentes.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.dispatch_log_subtract_stock()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_stock numeric(14, 2);
BEGIN
    SELECT current_stock
      INTO v_stock
      FROM private.raw_material_supplier
     WHERE id = NEW.id_raw_materials_suppliers
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'No existe raw_material_supplier % para el despacho %',
            NEW.id_raw_materials_suppliers, NEW.id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_stock < NEW.use_raw_materials THEN
        RAISE EXCEPTION
            'Stock insuficiente: disponible %, solicitado %',
            v_stock, NEW.use_raw_materials
            USING ERRCODE = 'check_violation';
    END IF;

    UPDATE private.raw_material_supplier
       SET current_stock = current_stock - NEW.use_raw_materials
     WHERE id = NEW.id_raw_materials_suppliers;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.dispatch_log_subtract_stock() IS
    'Resta el despacho del stock y rechaza stock negativo (RN-4, RN-8).';

-- ----------------------------------------------------------------------------
-- Reservas de equipo sin solape por unidad fisica (RN-5).
-- El rango es semiabierto [reservation_date, end_date): dos reservas
-- contiguas (una termina a la hora en que la otra empieza) no se solapan.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.reservation_equipament_check_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Serializa las reservas de la misma unidad fisica.
    PERFORM 1
       FROM private.equipament_supplier
      WHERE id = NEW.id_equipament_supplier
        FOR UPDATE;

    IF EXISTS (
        SELECT 1
          FROM private.reservation_equipament r
         WHERE r.id_equipament_supplier = NEW.id_equipament_supplier
           AND r.id <> NEW.id
           AND r.reservation_date < NEW.end_date
           AND r.end_date > NEW.reservation_date
    ) THEN
        RAISE EXCEPTION
            'La unidad % ya tiene una reserva que se solapa con [%, %)',
            NEW.id_equipament_supplier, NEW.reservation_date, NEW.end_date
            USING ERRCODE = 'exclusion_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.reservation_equipament_check_overlap() IS
    'Impide reservas solapadas de la misma unidad de equipo (RN-5).';

-- ----------------------------------------------------------------------------
-- updated_at automatico para tablas que lo declaran.
-- SQLAlchemy lo actualiza en la aplicacion; este trigger cubre SQL directo.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- clock_timestamp() cambia dentro de una misma transaccion; now() no.
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.set_updated_at() IS
    'Actualiza NEW.updated_at en cada UPDATE.';

-- ----------------------------------------------------------------------------
-- expense_approval usa el nombre historico modifiet_at (sic).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.set_modifiet_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.modifiet_at := clock_timestamp();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION private.set_modifiet_at() IS
    'Actualiza NEW.modifiet_at en cada UPDATE de expense_approval.';
