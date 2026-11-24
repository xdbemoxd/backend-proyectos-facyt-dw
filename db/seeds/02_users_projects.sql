-- ============================================================================
-- 02_users_projects.sql
-- Usuarios de prueba, proyectos e historial de prioridad.
--
-- Datos ficticios, sin PII real. Los emails usan dominios reservados
-- (@example.com). No hay contrasenas: el login es solo con Google y estas
-- cuentas deben existir y estar activas para probar el flujo.
--
-- Idempotente por email / unique_code / id.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Usuarios de prueba
--   admin@example.com         -> Administrador del Sistema
--   coordinador1@example.com  -> Coordinador de Proyecto
--   coordinador2@example.com  -> Coordinador de Proyecto (pruebas de alcance)
--   aprobador@example.com     -> Aprobador / Gestor de Inventario
-- ----------------------------------------------------------------------------
INSERT INTO private."user"
    (id, name, lastname, email, is_active, token_version, id_rol)
SELECT
    '01900000-0000-7000-8000-000000000011',
    'Admin', 'Demo', 'admin@example.com', true, 0, r.id
FROM private.rol AS r
WHERE r.name = 'Administrador del Sistema'
ON CONFLICT (email) DO NOTHING;

INSERT INTO private."user"
    (id, name, lastname, email, is_active, token_version, id_rol)
SELECT
    '01900000-0000-7000-8000-000000000012',
    'Carla', 'Coordinadora', 'coordinador1@example.com', true, 0, r.id
FROM private.rol AS r
WHERE r.name = 'Coordinador de Proyecto'
ON CONFLICT (email) DO NOTHING;

INSERT INTO private."user"
    (id, name, lastname, email, is_active, token_version, id_rol)
SELECT
    '01900000-0000-7000-8000-000000000013',
    'Diego', 'Coordinador', 'coordinador2@example.com', true, 0, r.id
FROM private.rol AS r
WHERE r.name = 'Coordinador de Proyecto'
ON CONFLICT (email) DO NOTHING;

INSERT INTO private."user"
    (id, name, lastname, email, is_active, token_version, id_rol)
SELECT
    '01900000-0000-7000-8000-000000000014',
    'Andrea', 'Aprobadora', 'aprobador@example.com', true, 0, r.id
FROM private.rol AS r
WHERE r.name = 'Aprobador / Gestor de Inventario'
ON CONFLICT (email) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Proyectos (catalogo de fisica, quimica y biologia)
-- ----------------------------------------------------------------------------
INSERT INTO private."Project"
    (id, unique_code, name, description, id_status, id_priority,
     progress, budget_execution, id_user)
SELECT
    '01900000-0000-7000-8000-000000000021',
    'FIS-2026-001',
    'Caracterizacion de materiales semiconductores',
    'Estudio de propiedades electricas de peliculas delgadas.',
    (SELECT id FROM private.status_project WHERE name = 'En ejecución' ORDER BY id LIMIT 1),
    (SELECT id FROM private.priority_project WHERE name = 'Alta' ORDER BY id LIMIT 1),
    35.00, 42.50,
    (SELECT id FROM private."user" WHERE email = 'coordinador1@example.com')
ON CONFLICT (unique_code) DO NOTHING;

INSERT INTO private."Project"
    (id, unique_code, name, description, id_status, id_priority,
     progress, budget_execution, id_user)
SELECT
    '01900000-0000-7000-8000-000000000022',
    'QUI-2026-002',
    'Sintesis de catalizadores heterogeneos',
    'Preparacion y evaluacion de catalizadores para oxidacion.',
    (SELECT id FROM private.status_project WHERE name = 'En ejecución' ORDER BY id LIMIT 1),
    (SELECT id FROM private.priority_project WHERE name = 'Crítica' ORDER BY id LIMIT 1),
    52.00, 61.00,
    (SELECT id FROM private."user" WHERE email = 'coordinador2@example.com')
ON CONFLICT (unique_code) DO NOTHING;

INSERT INTO private."Project"
    (id, unique_code, name, description, id_status, id_priority,
     progress, budget_execution, id_user)
SELECT
    '01900000-0000-7000-8000-000000000023',
    'BIO-2026-003',
    'Cultivo de microorganismos extremofilos',
    'Aislamiento y caracterizacion de cepas de ambientes salinos.',
    (SELECT id FROM private.status_project WHERE name = 'Propuesta' ORDER BY id LIMIT 1),
    (SELECT id FROM private.priority_project WHERE name = 'Media' ORDER BY id LIMIT 1),
    0.00, 0.00,
    (SELECT id FROM private."user" WHERE email = 'coordinador1@example.com')
ON CONFLICT (unique_code) DO NOTHING;

INSERT INTO private."Project"
    (id, unique_code, name, description, id_status, id_priority,
     progress, budget_execution, id_user)
SELECT
    '01900000-0000-7000-8000-000000000024',
    'FIS-2026-004',
    'Calibracion de espectrometros',
    'Mantenimiento y calibracion del parque de espectrometros.',
    (SELECT id FROM private.status_project WHERE name = 'Finalizado' ORDER BY id LIMIT 1),
    (SELECT id FROM private.priority_project WHERE name = 'Baja' ORDER BY id LIMIT 1),
    100.00, 95.00,
    (SELECT id FROM private."user" WHERE email = 'coordinador2@example.com')
ON CONFLICT (unique_code) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Historial de cambio de prioridad (RN-3: justificacion obligatoria)
-- ----------------------------------------------------------------------------
INSERT INTO private.audith_project
    (id, id_project, unique_code, id_status, id_priority, id_user,
     explaining)
SELECT
    '01900000-0000-7000-8000-0000000000c1',
    p.id,
    p.unique_code,
    p.id_status,
    p.id_priority,
    (SELECT id FROM private."user" WHERE email = 'admin@example.com'),
    'Se eleva la prioridad por compromiso de entrega con el laboratorio docente.'
FROM private."Project" AS p
WHERE p.unique_code = 'FIS-2026-001'
ON CONFLICT (id) DO NOTHING;
