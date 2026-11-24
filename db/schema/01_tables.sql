-- ============================================================================
-- 01_tables.sql
-- Tablas, PK, FK, UNIQUE y CHECK del esquema `private`.
--
-- Alineado con src/model/*.py y con la base real de Supabase, incluyendo los
-- nombres historicos:
--   * "Project" (con mayuscula y comillas)
--   * "user"    (palabra reservada, siempre entre comillas)
--   * raw_material.id_inventroy (sic, asi aparece en el modelo y la BD)
--   * expense_approval.modifiet_at (sic)
--   * dispatch_log y expense_approval en minusculas
--
-- Es idempotente (CREATE TABLE IF NOT EXISTS). En una base existente no
-- modifica tablas ya creadas; los cambios posteriores viven en db/migrations/.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Roles (RN-1): solo Administrador, Coordinador y Aprobador.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.rol (
    id          uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    name        varchar(255) NOT NULL,
    description varchar(1000)
);

-- ----------------------------------------------------------------------------
-- Usuarios pre-registrados (login solo con Google; no hay autorregistro).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private."user" (
    id            uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    name          varchar(255),
    lastname      varchar(255),
    email         varchar(255) NOT NULL,
    create_at     timestamptz DEFAULT CURRENT_TIMESTAMP,
    is_active     boolean DEFAULT false,
    token_version integer NOT NULL DEFAULT 0,
    id_rol        uuid NOT NULL,
    CONSTRAINT user_email_key UNIQUE (email),
    CONSTRAINT fk_user_rol FOREIGN KEY (id_rol)
        REFERENCES private.rol (id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Catalogos de proyecto
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.status_project (
    id          smallserial PRIMARY KEY,
    name        varchar(50) NOT NULL,
    description varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS private.priority_project (
    id          smallserial PRIMARY KEY,
    name        varchar(50) NOT NULL,
    description varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS private."Project" (
    id               uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    unique_code      varchar(50) NOT NULL,
    name             varchar(150) NOT NULL,
    description      varchar(255),
    id_status        smallint NOT NULL,
    id_priority      smallint NOT NULL,
    progress         numeric(5, 2) NOT NULL DEFAULT 0.00,
    budget_execution numeric(5, 2) NOT NULL DEFAULT 0.00,
    id_user          uuid NOT NULL,
    create_at        timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT Project_unique_code_key UNIQUE (unique_code),
    CONSTRAINT ck_project_progress
        CHECK (progress BETWEEN 0 AND 100),
    CONSTRAINT ck_project_budget_execution
        CHECK (budget_execution BETWEEN 0 AND 100),
    CONSTRAINT "Project_id_status_fkey" FOREIGN KEY (id_status)
        REFERENCES private.status_project (id) ON DELETE RESTRICT,
    CONSTRAINT "Project_id_priority_fkey" FOREIGN KEY (id_priority)
        REFERENCES private.priority_project (id) ON DELETE RESTRICT,
    CONSTRAINT "Project_id_user_fkey" FOREIGN KEY (id_user)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

-- Historial de cambios de estatus/prioridad (INSERT-only).
CREATE TABLE IF NOT EXISTS private.audith_project (
    id          uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_project  uuid NOT NULL,
    unique_code varchar(50) NOT NULL,
    id_status   smallint NOT NULL,
    id_priority smallint NOT NULL,
    id_user     uuid NOT NULL,
    explaining  varchar(2000) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_audith_project_explaining
        CHECK (char_length(trim(explaining)) >= 5),
    CONSTRAINT audith_project_id_project_fkey FOREIGN KEY (id_project)
        REFERENCES private."Project" (id) ON DELETE RESTRICT,
    CONSTRAINT audith_project_id_status_fkey FOREIGN KEY (id_status)
        REFERENCES private.status_project (id) ON DELETE RESTRICT,
    CONSTRAINT audith_project_id_priority_fkey FOREIGN KEY (id_priority)
        REFERENCES private.priority_project (id) ON DELETE RESTRICT,
    CONSTRAINT audith_project_id_user_fkey FOREIGN KEY (id_user)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Proveedores
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.vendors (
    id            uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    name          varchar(255) NOT NULL,
    description   varchar(255),
    contact_email varchar(255),
    contact_phone varchar(255),
    create_at     timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- Inventario: catalogo, stock por proveedor y lotes de entrada
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.resource_type (
    id          smallserial PRIMARY KEY,
    name        varchar(255) NOT NULL,
    description varchar(1000),
    create_at   timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS private.raw_material (
    id_inventroy    uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    name            varchar(255) NOT NULL,
    description     varchar(1000),
    resource_type_id smallint NOT NULL,
    unit            varchar(30) NOT NULL DEFAULT 'unidad',
    create_at       timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT raw_material_resource_type_id_fkey FOREIGN KEY (resource_type_id)
        REFERENCES private.resource_type (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS private.raw_material_supplier (
    id             uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    raw_materials_id uuid NOT NULL,
    vendors_id     uuid NOT NULL,
    current_stock  numeric(14, 2) NOT NULL DEFAULT 0.00,
    minimun_stock  numeric(14, 2) NOT NULL DEFAULT 0.00,
    create_at      timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_raw_supplier_stock_nonneg CHECK (current_stock >= 0.00),
    CONSTRAINT ck_raw_supplier_min_stock_nonneg CHECK (minimun_stock >= 0.00),
    CONSTRAINT uq_raw_material_vendor UNIQUE (raw_materials_id, vendors_id),
    CONSTRAINT raw_material_supplier_raw_materials_id_fkey
        FOREIGN KEY (raw_materials_id)
        REFERENCES private.raw_material (id_inventroy) ON DELETE RESTRICT,
    CONSTRAINT raw_material_supplier_vendors_id_fkey
        FOREIGN KEY (vendors_id)
        REFERENCES private.vendors (id) ON DELETE RESTRICT
);

-- Lote de compra / entrada de almacen. INSERT-only; suma stock por trigger.
CREATE TABLE IF NOT EXISTS private.audith_raw_material_supplier (
    id                        uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_raw_materials_supplier uuid NOT NULL,
    in_date                   timestamptz NOT NULL DEFAULT now(),
    count_raw_materials_in    numeric(14, 2) NOT NULL,
    unit_cost                 numeric(14, 2) NOT NULL DEFAULT 0.00,
    invoice_reference         varchar(100),
    created_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_lot_quantity_positive CHECK (count_raw_materials_in > 0.00),
    CONSTRAINT ck_lot_cost_nonneg CHECK (unit_cost >= 0.00),
    CONSTRAINT audith_raw_material_supplier_id_raw_materials_supplier_fkey
        FOREIGN KEY (id_raw_materials_supplier)
        REFERENCES private.raw_material_supplier (id) ON DELETE RESTRICT
);

-- Bitacora de despachos. INSERT-only; resta stock por trigger.
CREATE TABLE IF NOT EXISTS private.dispatch_log (
    id                        uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_raw_materials_suppliers uuid NOT NULL,
    id_project                uuid NOT NULL,
    id_user                   uuid NOT NULL,
    use_raw_materials         numeric(14, 2) NOT NULL,
    material_requested_at     timestamptz NOT NULL DEFAULT now(),
    notes                     varchar(1000),
    CONSTRAINT ck_dispatch_quantity_positive CHECK (use_raw_materials > 0.00),
    CONSTRAINT dispatch_log_id_raw_materials_suppliers_fkey
        FOREIGN KEY (id_raw_materials_suppliers)
        REFERENCES private.raw_material_supplier (id) ON DELETE RESTRICT,
    CONSTRAINT dispatch_log_id_project_fkey FOREIGN KEY (id_project)
        REFERENCES private."Project" (id) ON DELETE RESTRICT,
    CONSTRAINT dispatch_log_id_user_fkey FOREIGN KEY (id_user)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Equipos y reservas (RN-5: sin solape por unidad fisica)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.status_equipament (
    id          smallserial PRIMARY KEY,
    name        varchar(50) NOT NULL,
    description varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS private.equipment (
    id          uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    name        varchar(150) NOT NULL,
    description varchar(1000),
    create_at   timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS private.equipament_supplier (
    id            uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_equipament uuid NOT NULL,
    id_vendors    uuid NOT NULL,
    id_status     smallint NOT NULL,
    model         varchar(100),
    serial_number varchar(100),
    create_at     timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT equipament_supplier_id_equipament_fkey
        FOREIGN KEY (id_equipament)
        REFERENCES private.equipment (id) ON DELETE RESTRICT,
    CONSTRAINT equipament_supplier_id_vendors_fkey
        FOREIGN KEY (id_vendors)
        REFERENCES private.vendors (id) ON DELETE RESTRICT,
    CONSTRAINT equipament_supplier_id_status_fkey
        FOREIGN KEY (id_status)
        REFERENCES private.status_equipament (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS private.reservation_equipament (
    id                    uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_equipament_supplier uuid NOT NULL,
    id_project            uuid NOT NULL,
    id_user               uuid NOT NULL,
    reservation_date      timestamptz NOT NULL,
    end_date              timestamptz NOT NULL,
    purpose               varchar(2000),
    created_date          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_reservation_dates CHECK (end_date > reservation_date),
    CONSTRAINT reservation_equipament_id_equipament_supplier_fkey
        FOREIGN KEY (id_equipament_supplier)
        REFERENCES private.equipament_supplier (id) ON DELETE RESTRICT,
    CONSTRAINT reservation_equipament_id_project_fkey
        FOREIGN KEY (id_project)
        REFERENCES private."Project" (id) ON DELETE RESTRICT,
    CONSTRAINT reservation_equipament_id_user_fkey FOREIGN KEY (id_user)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Solicitudes de gasto (RN-7: maquina de estados estricta)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.status_expense_approval (
    id          smallserial PRIMARY KEY,
    name        varchar(50) NOT NULL,
    code        varchar(30) NOT NULL,
    description varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT status_expense_approval_name_key UNIQUE (name),
    CONSTRAINT status_expense_approval_code_key UNIQUE (code)
);

CREATE TABLE IF NOT EXISTS private.expense_approval (
    id                        uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_project                uuid NOT NULL,
    id_raw_materials_suppliers uuid,
    id_status                 smallint NOT NULL,
    id_user                   uuid NOT NULL,
    description               varchar(255) NOT NULL,
    count_raw_materials       numeric(14, 2) NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    modifiet_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_expense_amount_positive CHECK (count_raw_materials > 0.00),
    CONSTRAINT expense_approval_id_project_fkey FOREIGN KEY (id_project)
        REFERENCES private."Project" (id) ON DELETE RESTRICT,
    CONSTRAINT expense_approval_id_raw_materials_suppliers_fkey
        FOREIGN KEY (id_raw_materials_suppliers)
        REFERENCES private.raw_material_supplier (id) ON DELETE RESTRICT,
    CONSTRAINT expense_approval_id_status_fkey FOREIGN KEY (id_status)
        REFERENCES private.status_expense_approval (id) ON DELETE RESTRICT,
    CONSTRAINT expense_approval_id_user_fkey FOREIGN KEY (id_user)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Overhead: periodo cerrado y reparto por proyecto (RN-6)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.overhead_period (
    id         uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    start_date date NOT NULL,
    end_date   date NOT NULL,
    criterion  varchar(20) NOT NULL,
    total_cost numeric(14, 2) NOT NULL,
    closed_by  uuid NOT NULL,
    closed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_overhead_period_range CHECK (end_date >= start_date),
    CONSTRAINT ck_overhead_criterion
        CHECK (criterion IN ('proporcional', 'equitativo')),
    CONSTRAINT ck_overhead_total_nonneg CHECK (total_cost >= 0.00),
    CONSTRAINT overhead_period_closed_by_fkey FOREIGN KEY (closed_by)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS private.overhead_allocation (
    id                uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    id_overhead_period uuid NOT NULL,
    id_project        uuid NOT NULL,
    amount            numeric(14, 2) NOT NULL,
    consumption       numeric(14, 2) NOT NULL DEFAULT 0.00,
    CONSTRAINT ck_overhead_alloc_amount_nonneg CHECK (amount >= 0.00),
    CONSTRAINT ck_overhead_alloc_consump_nonneg CHECK (consumption >= 0.00),
    CONSTRAINT uq_overhead_period_project
        UNIQUE (id_overhead_period, id_project),
    CONSTRAINT overhead_allocation_id_overhead_period_fkey
        FOREIGN KEY (id_overhead_period)
        REFERENCES private.overhead_period (id) ON DELETE RESTRICT,
    CONSTRAINT overhead_allocation_id_project_fkey FOREIGN KEY (id_project)
        REFERENCES private."Project" (id) ON DELETE RESTRICT
);

-- ----------------------------------------------------------------------------
-- Auditoria inmutable (append-only)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private.audit_log (
    id              uuid PRIMARY KEY DEFAULT private.uuid_generate_v7(),
    timestamp       timestamptz NOT NULL DEFAULT now(),
    user_id         uuid,
    user_email      varchar(255),
    ip_address      varchar(255) NOT NULL,
    method          varchar(10),
    path            varchar(500),
    status_code     smallint,
    user_agent      varchar(512),
    request_headers jsonb,
    query_params    jsonb,
    duration_ms     integer,
    request_id      uuid,
    entity          varchar(255) NOT NULL,
    action          varchar(255) NOT NULL,
    entity_id       varchar(100),
    outcome         varchar(20) NOT NULL DEFAULT 'success',
    error_code      varchar(100),
    error_detail    varchar(1000),
    before_data     jsonb,
    after_data      jsonb,
    CONSTRAINT ck_audit_outcome
        CHECK (outcome IN ('success', 'denied', 'failed')),
    CONSTRAINT ck_audit_status
        CHECK (status_code IS NULL OR status_code BETWEEN 100 AND 599),
    CONSTRAINT ck_audit_method
        CHECK (method IS NULL OR method IN
            ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS')),
    CONSTRAINT ck_audit_duration
        CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT ck_audit_request_headers_size
        CHECK (request_headers IS NULL OR pg_column_size(request_headers) <= 32768),
    CONSTRAINT ck_audit_query_params_size
        CHECK (query_params IS NULL OR pg_column_size(query_params) <= 32768),
    CONSTRAINT ck_audit_before_data_size
        CHECK (before_data IS NULL OR pg_column_size(before_data) <= 65536),
    CONSTRAINT ck_audit_after_data_size
        CHECK (after_data IS NULL OR pg_column_size(after_data) <= 65536),
    CONSTRAINT audit_log_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES private."user" (id) ON DELETE RESTRICT
);

COMMENT ON TABLE private.audit_log IS
    'Bitacora inmutable (append-only) de acciones y requests HTTP del sistema.';

COMMENT ON COLUMN private.audit_log.user_email IS
    'Campo historico. Los registros nuevos lo dejan NULL y resuelven el email por user_id.';
COMMENT ON COLUMN private.audit_log.request_headers IS
    'Headers HTTP permitidos y redactados (sin Authorization/Cookie).';
COMMENT ON COLUMN private.audit_log.query_params IS
    'Query params con claves sensibles redactadas.';
COMMENT ON COLUMN private.audit_log.request_id IS
    'UUID que correlaciona la request HTTP con eventos de negocio.';
COMMENT ON COLUMN private."user".token_version IS
    'Version de JWT; se incrementa para invalidar tokens emitidos.';
