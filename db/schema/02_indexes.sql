-- ============================================================================
-- 02_indexes.sql
-- Indices del esquema `private`.
--
-- Los indices UNIQUE de PK/UNIQUE ya los crea 01_tables.sql. Aqui se agregan
-- los indices de consulta y de claves foraneas que no cubre una restriccion.
-- Idempotente: CREATE INDEX IF NOT EXISTS.
-- ============================================================================

SET client_encoding = 'UTF8';

-- Usuarios ---------------------------------------------------------------
-- La base real mantiene ademas un indice no unico sobre email.
CREATE INDEX IF NOT EXISTS idx_user_email
    ON private."user" (email);

-- Proyectos --------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_project_status
    ON private."Project" (id_status);
CREATE INDEX IF NOT EXISTS idx_project_priority
    ON private."Project" (id_priority);
CREATE INDEX IF NOT EXISTS idx_project_user
    ON private."Project" (id_user);
CREATE INDEX IF NOT EXISTS idx_project_created
    ON private."Project" (create_at DESC);

CREATE INDEX IF NOT EXISTS idx_audith_project_project
    ON private.audith_project (id_project);
CREATE INDEX IF NOT EXISTS idx_audith_project_created
    ON private.audith_project (created_at DESC);

-- Inventario -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_raw_material_type
    ON private.raw_material (resource_type_id);
CREATE INDEX IF NOT EXISTS idx_raw_material_name
    ON private.raw_material (lower(name));

CREATE INDEX IF NOT EXISTS idx_raw_supplier_material
    ON private.raw_material_supplier (raw_materials_id);
CREATE INDEX IF NOT EXISTS idx_raw_supplier_vendor
    ON private.raw_material_supplier (vendors_id);

CREATE INDEX IF NOT EXISTS idx_lot_supplier
    ON private.audith_raw_material_supplier (id_raw_materials_supplier);
CREATE INDEX IF NOT EXISTS idx_lot_in_date
    ON private.audith_raw_material_supplier (in_date DESC);

CREATE INDEX IF NOT EXISTS idx_dispatch_supplier
    ON private.dispatch_log (id_raw_materials_suppliers);
CREATE INDEX IF NOT EXISTS idx_dispatch_project
    ON private.dispatch_log (id_project);
CREATE INDEX IF NOT EXISTS idx_dispatch_user
    ON private.dispatch_log (id_user);
CREATE INDEX IF NOT EXISTS idx_dispatch_date
    ON private.dispatch_log (material_requested_at DESC);

-- Equipos y reservas -----------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_equip_supplier_equipment
    ON private.equipament_supplier (id_equipament);
CREATE INDEX IF NOT EXISTS idx_equip_supplier_vendor
    ON private.equipament_supplier (id_vendors);
CREATE INDEX IF NOT EXISTS idx_equip_supplier_status
    ON private.equipament_supplier (id_status);

CREATE INDEX IF NOT EXISTS idx_reservation_unit
    ON private.reservation_equipament (id_equipament_supplier);
CREATE INDEX IF NOT EXISTS idx_reservation_project
    ON private.reservation_equipament (id_project);
CREATE INDEX IF NOT EXISTS idx_reservation_user
    ON private.reservation_equipament (id_user);
-- Apoya la verificacion de solape (RN-5).
CREATE INDEX IF NOT EXISTS idx_reservation_range
    ON private.reservation_equipament
       (id_equipament_supplier, reservation_date, end_date);

-- Solicitudes de gasto ---------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_expense_project
    ON private.expense_approval (id_project);
CREATE INDEX IF NOT EXISTS idx_expense_status
    ON private.expense_approval (id_status);
CREATE INDEX IF NOT EXISTS idx_expense_supplier
    ON private.expense_approval (id_raw_materials_suppliers);
CREATE INDEX IF NOT EXISTS idx_expense_created
    ON private.expense_approval (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_expense_requester
    ON private.expense_approval (id_user);

-- Overhead ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_overhead_alloc_project
    ON private.overhead_allocation (id_project);
CREATE INDEX IF NOT EXISTS idx_overhead_period_range
    ON private.overhead_period (start_date, end_date);

-- Auditoria --------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON private.audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user
    ON private.audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON private.audit_log (entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_status
    ON private.audit_log (status_code);
CREATE INDEX IF NOT EXISTS idx_audit_request
    ON private.audit_log (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON private.audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_outcome
    ON private.audit_log (outcome);
