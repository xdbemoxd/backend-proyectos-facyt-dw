-- ============================================================================
-- 04_triggers.sql
-- Triggers de integridad, stock, reservas e inmutabilidad.
--
-- Idempotente: cada trigger se elimina con DROP TRIGGER IF EXISTS y se vuelve
-- a crear. Requiere 03_functions.sql.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- audit_log: append-only (UPDATE/DELETE/TRUNCATE).
-- Conserva los mismos nombres que db/migrations/20261122_improve_audit_log.sql.
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_audit_log_append_only ON private.audit_log;
CREATE TRIGGER trg_audit_log_append_only
    BEFORE UPDATE OR DELETE ON private.audit_log
    FOR EACH ROW
    EXECUTE FUNCTION private.audit_log_append_only();

DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON private.audit_log;
CREATE TRIGGER trg_audit_log_no_truncate
    BEFORE TRUNCATE ON private.audit_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.audit_log_append_only();

-- ----------------------------------------------------------------------------
-- dispatch_log: append-only + descuento de stock.
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_dispatch_log_append_only ON private.dispatch_log;
CREATE TRIGGER trg_dispatch_log_append_only
    BEFORE UPDATE OR DELETE ON private.dispatch_log
    FOR EACH ROW
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_dispatch_log_no_truncate ON private.dispatch_log;
CREATE TRIGGER trg_dispatch_log_no_truncate
    BEFORE TRUNCATE ON private.dispatch_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_dispatch_log_subtract_stock ON private.dispatch_log;
CREATE TRIGGER trg_dispatch_log_subtract_stock
    BEFORE INSERT ON private.dispatch_log
    FOR EACH ROW
    EXECUTE FUNCTION private.dispatch_log_subtract_stock();

-- ----------------------------------------------------------------------------
-- Lotes de entrada: append-only + suma de stock.
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_lot_append_only
    ON private.audith_raw_material_supplier;
CREATE TRIGGER trg_lot_append_only
    BEFORE UPDATE OR DELETE ON private.audith_raw_material_supplier
    FOR EACH ROW
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_lot_no_truncate
    ON private.audith_raw_material_supplier;
CREATE TRIGGER trg_lot_no_truncate
    BEFORE TRUNCATE ON private.audith_raw_material_supplier
    FOR EACH STATEMENT
    EXECUTE FUNCTION private.append_only_guard();

DROP TRIGGER IF EXISTS trg_lot_add_stock
    ON private.audith_raw_material_supplier;
CREATE TRIGGER trg_lot_add_stock
    AFTER INSERT ON private.audith_raw_material_supplier
    FOR EACH ROW
    EXECUTE FUNCTION private.audith_raw_material_supplier_add_stock();

-- ----------------------------------------------------------------------------
-- Reservas de equipo: sin solape (RN-5).
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_reservation_no_overlap
    ON private.reservation_equipament;
CREATE TRIGGER trg_reservation_no_overlap
    BEFORE INSERT OR UPDATE ON private.reservation_equipament
    FOR EACH ROW
    EXECUTE FUNCTION private.reservation_equipament_check_overlap();

-- ----------------------------------------------------------------------------
-- updated_at automatico.
-- ----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_project_set_updated_at ON private."Project";
CREATE TRIGGER trg_project_set_updated_at
    BEFORE UPDATE ON private."Project"
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_vendors_set_updated_at ON private.vendors;
CREATE TRIGGER trg_vendors_set_updated_at
    BEFORE UPDATE ON private.vendors
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_resource_type_set_updated_at ON private.resource_type;
CREATE TRIGGER trg_resource_type_set_updated_at
    BEFORE UPDATE ON private.resource_type
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_raw_material_set_updated_at ON private.raw_material;
CREATE TRIGGER trg_raw_material_set_updated_at
    BEFORE UPDATE ON private.raw_material
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_raw_supplier_set_updated_at
    ON private.raw_material_supplier;
CREATE TRIGGER trg_raw_supplier_set_updated_at
    BEFORE UPDATE ON private.raw_material_supplier
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_equipment_set_updated_at ON private.equipment;
CREATE TRIGGER trg_equipment_set_updated_at
    BEFORE UPDATE ON private.equipment
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_equip_supplier_set_updated_at
    ON private.equipament_supplier;
CREATE TRIGGER trg_equip_supplier_set_updated_at
    BEFORE UPDATE ON private.equipament_supplier
    FOR EACH ROW EXECUTE FUNCTION private.set_updated_at();

DROP TRIGGER IF EXISTS trg_expense_set_modifiet_at
    ON private.expense_approval;
CREATE TRIGGER trg_expense_set_modifiet_at
    BEFORE UPDATE ON private.expense_approval
    FOR EACH ROW EXECUTE FUNCTION private.set_modifiet_at();
