-- ============================================================================
-- 01_catalogs.sql
-- Datos de referencia: roles (RN-1), estados, prioridades y tipos de recurso.
--
-- Idempotente: inserta solo si el nombre/codigo no existe. No usa IDs fijos
-- para los catalogos SMALLSERIAL; el resto de seeders los referencia por
-- nombre o codigo.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Roles del sistema (deben coincidir con src/utils/roles.py)
-- ----------------------------------------------------------------------------
INSERT INTO private.rol (id, name, description)
SELECT '01900000-0000-7000-8000-000000000001', 'Administrador del Sistema',
       'Administra usuarios, roles, parametros y consulta la auditoria.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.rol WHERE name = 'Administrador del Sistema'
);

INSERT INTO private.rol (id, name, description)
SELECT '01900000-0000-7000-8000-000000000002', 'Coordinador de Proyecto',
       'Gestiona unicamente los proyectos que tiene asignados.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.rol WHERE name = 'Coordinador de Proyecto'
);

INSERT INTO private.rol (id, name, description)
SELECT '01900000-0000-7000-8000-000000000003', 'Aprobador / Gestor de Inventario',
       'Aprueba solicitudes y gestiona inventario, lotes y despachos.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.rol WHERE name = 'Aprobador / Gestor de Inventario'
);

-- ----------------------------------------------------------------------------
-- Estados operativos de proyecto
-- ----------------------------------------------------------------------------
INSERT INTO private.status_project (name, description)
SELECT 'Propuesta', 'Proyecto registrado, pendiente de aprobacion o inicio.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_project WHERE name = 'Propuesta'
);

INSERT INTO private.status_project (name, description)
SELECT 'En ejecución', 'Proyecto activo con actividades y consumo en curso.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_project WHERE name = 'En ejecución'
);

INSERT INTO private.status_project (name, description)
SELECT 'Suspendido', 'Proyecto temporalmente detenido por decision institucional.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_project WHERE name = 'Suspendido'
);

INSERT INTO private.status_project (name, description)
SELECT 'Finalizado', 'Proyecto cerrado; no admite nuevos consumos.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_project WHERE name = 'Finalizado'
);

-- ----------------------------------------------------------------------------
-- Prioridades de proyecto (RN-3: el cambio exige justificacion)
-- ----------------------------------------------------------------------------
INSERT INTO private.priority_project (name, description)
SELECT 'Crítica', 'Atencion inmediata; riesgo alto para los objetivos.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.priority_project WHERE name = 'Crítica'
);

INSERT INTO private.priority_project (name, description)
SELECT 'Alta', 'Debe atenderse antes que la operacion ordinaria.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.priority_project WHERE name = 'Alta'
);

INSERT INTO private.priority_project (name, description)
SELECT 'Media', 'Prioridad operativa habitual.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.priority_project WHERE name = 'Media'
);

INSERT INTO private.priority_project (name, description)
SELECT 'Baja', 'Puede planificarse sin urgencia.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.priority_project WHERE name = 'Baja'
);

-- ----------------------------------------------------------------------------
-- Estados del equipo
-- ----------------------------------------------------------------------------
INSERT INTO private.status_equipament (name, description)
SELECT 'Disponible', 'Unidad operativa y sin reserva activa.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_equipament WHERE name = 'Disponible'
);

INSERT INTO private.status_equipament (name, description)
SELECT 'En uso', 'Unidad asignada a una actividad en curso.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_equipament WHERE name = 'En uso'
);

INSERT INTO private.status_equipament (name, description)
SELECT 'En mantenimiento', 'Unidad fuera de servicio por mantenimiento.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_equipament WHERE name = 'En mantenimiento'
);

-- ----------------------------------------------------------------------------
-- Estados de solicitud de gasto (RN-7)
-- `code` es el identificador estable para la logica; `name` es para mostrar.
-- ----------------------------------------------------------------------------
INSERT INTO private.status_expense_approval (name, code, description)
SELECT 'Borrador', 'draft', 'Solicitud en edicion; aun no enviada.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_expense_approval
     WHERE name = 'Borrador' OR code = 'draft'
);

INSERT INTO private.status_expense_approval (name, code, description)
SELECT 'Pendiente de Aprobación', 'pending',
       'Enviada y a la espera de decision del aprobador.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_expense_approval
     WHERE name = 'Pendiente de Aprobación' OR code = 'pending'
);

INSERT INTO private.status_expense_approval (name, code, description)
SELECT 'Aprobado', 'approved', 'Aprobada; pendiente de despacho o ejecucion.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_expense_approval
     WHERE name = 'Aprobado' OR code = 'approved'
);

INSERT INTO private.status_expense_approval (name, code, description)
SELECT 'Rechazado', 'rejected', 'Rechazada; no puede despacharse.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_expense_approval
     WHERE name = 'Rechazado' OR code = 'rejected'
);

INSERT INTO private.status_expense_approval (name, code, description)
SELECT 'Ejecutado', 'executed', 'Despacho o gasto ya ejecutado.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.status_expense_approval
     WHERE name = 'Ejecutado' OR code = 'executed'
);

-- ----------------------------------------------------------------------------
-- Tipos de recurso del inventario
-- ----------------------------------------------------------------------------
INSERT INTO private.resource_type (name, description)
SELECT 'Materia prima', 'Insumo base que se transforma en el proyecto.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.resource_type WHERE name = 'Materia prima'
);

INSERT INTO private.resource_type (name, description)
SELECT 'Consumible', 'Material de uso rapido que no forma parte del producto.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.resource_type WHERE name = 'Consumible'
);

INSERT INTO private.resource_type (name, description)
SELECT 'Reactivo', 'Sustancia quimica o biologica para analisis o sintesis.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.resource_type WHERE name = 'Reactivo'
);

-- ----------------------------------------------------------------------------
-- Sincroniza las secuencias SMALLSERIAL con los IDs realmente insertados.
-- Evita colisiones cuando la tabla ya tenia filas o cuando se insertaron IDs
-- explicitos en una corrida anterior.
-- ----------------------------------------------------------------------------
SELECT setval(
    pg_get_serial_sequence('private.status_project', 'id'),
    COALESCE((SELECT max(id) FROM private.status_project), 1),
    true
);
SELECT setval(
    pg_get_serial_sequence('private.priority_project', 'id'),
    COALESCE((SELECT max(id) FROM private.priority_project), 1),
    true
);
SELECT setval(
    pg_get_serial_sequence('private.status_equipament', 'id'),
    COALESCE((SELECT max(id) FROM private.status_equipament), 1),
    true
);
SELECT setval(
    pg_get_serial_sequence('private.status_expense_approval', 'id'),
    COALESCE((SELECT max(id) FROM private.status_expense_approval), 1),
    true
);
SELECT setval(
    pg_get_serial_sequence('private.resource_type', 'id'),
    COALESCE((SELECT max(id) FROM private.resource_type), 1),
    true
);
