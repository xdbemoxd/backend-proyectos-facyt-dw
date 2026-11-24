-- ============================================================================
-- 05_expenses_overhead.sql
-- Solicitudes de gasto en distintos estados y un periodo de overhead cerrado.
--
-- Los estados se referencian por `code` (estable) y los proyectos por
-- `unique_code`. Idempotente por id / unique (periodo, proyecto).
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Solicitudes de gasto (maquina de estados RN-7)
-- ----------------------------------------------------------------------------
INSERT INTO private.expense_approval
    (id, id_project, id_raw_materials_suppliers, id_status, id_user,
     description, count_raw_materials)
SELECT
    '01900000-0000-7000-8000-0000000000a1',
    p.id,
    (SELECT s.id
       FROM private.raw_material_supplier AS s
       JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
       JOIN private.vendors AS v ON v.id = s.vendors_id
      WHERE rm.name = 'Ácido clorhídrico 37%'
        AND v.name = 'Proveedor Quimico Andino, C.A.'
      LIMIT 1),
    st.id,
    p.id_user,
    'Compra de acido clorhidrico para digestion de muestras.',
    10.00
FROM private."Project" AS p, private.status_expense_approval AS st
WHERE p.unique_code = 'FIS-2026-001'
  AND st.code = 'approved'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.expense_approval
    (id, id_project, id_raw_materials_suppliers, id_status, id_user,
     description, count_raw_materials)
SELECT
    '01900000-0000-7000-8000-0000000000a2',
    p.id,
    (SELECT s.id
       FROM private.raw_material_supplier AS s
       JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
       JOIN private.vendors AS v ON v.id = s.vendors_id
      WHERE rm.name = 'Guantes de nitrilo'
        AND v.name = 'Insumos de Laboratorio Central, C.A.'
      LIMIT 1),
    st.id,
    p.id_user,
    'Reposicion de guantes de nitrilo para el area de sintesis.',
    15.00
FROM private."Project" AS p, private.status_expense_approval AS st
WHERE p.unique_code = 'QUI-2026-002'
  AND st.code = 'pending'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.expense_approval
    (id, id_project, id_raw_materials_suppliers, id_status, id_user,
     description, count_raw_materials)
SELECT
    '01900000-0000-7000-8000-0000000000a3',
    p.id,
    (SELECT s.id
       FROM private.raw_material_supplier AS s
       JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
       JOIN private.vendors AS v ON v.id = s.vendors_id
      WHERE rm.name = 'Medio de cultivo LB deshidratado'
        AND v.name = 'Insumos de Laboratorio Central, C.A.'
      LIMIT 1),
    st.id,
    p.id_user,
    'Adquisicion de medio de cultivo para el aislamiento de cepas.',
    2000.00
FROM private."Project" AS p, private.status_expense_approval AS st
WHERE p.unique_code = 'BIO-2026-003'
  AND st.code = 'draft'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.expense_approval
    (id, id_project, id_raw_materials_suppliers, id_status, id_user,
     description, count_raw_materials)
SELECT
    '01900000-0000-7000-8000-0000000000a4',
    p.id,
    NULL,
    st.id,
    p.id_user,
    'Servicio externo de calibracion del espectrofotometro.',
    1.00
FROM private."Project" AS p, private.status_expense_approval AS st
WHERE p.unique_code = 'FIS-2026-004'
  AND st.code = 'executed'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.expense_approval
    (id, id_project, id_raw_materials_suppliers, id_status, id_user,
     description, count_raw_materials)
SELECT
    '01900000-0000-7000-8000-0000000000a5',
    p.id,
    (SELECT s.id
       FROM private.raw_material_supplier AS s
       JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
       JOIN private.vendors AS v ON v.id = s.vendors_id
      WHERE rm.name = 'Óxido de zinc en polvo'
        AND v.name = 'Proveedor Quimico Andino, C.A.'
      LIMIT 1),
    st.id,
    p.id_user,
    'Reactivo no presupuestado para el periodo en curso.',
    5.00
FROM private."Project" AS p, private.status_expense_approval AS st
WHERE p.unique_code = 'FIS-2026-001'
  AND st.code = 'rejected'
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Periodo de overhead cerrado (octubre 2026), criterio proporcional.
--
--   P1 consumo 150 -> 1200.00 * 150 / 400 = 450.00
--   P2 consumo 250 -> 1200.00 * 250 / 400 = 750.00
--   suma de asignaciones        = 1200.00 = total_cost
--
-- Solo se incluyen proyectos activos (En ejecución); P3 esta en propuesta y
-- P4 finalizado, por lo que quedan fuera (RN-6).
-- ----------------------------------------------------------------------------
INSERT INTO private.overhead_period
    (id, start_date, end_date, criterion, total_cost, closed_by)
SELECT
    '01900000-0000-7000-8000-0000000000b1',
    DATE '2026-10-01', DATE '2026-10-31', 'proporcional', 1200.00, u.id
FROM private."user" AS u
WHERE u.email = 'admin@example.com'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.overhead_allocation
    (id, id_overhead_period, id_project, amount, consumption)
SELECT
    '01900000-0000-7000-8000-0000000000b2',
    '01900000-0000-7000-8000-0000000000b1',
    p.id, 450.00, 150.00
FROM private."Project" AS p
WHERE p.unique_code = 'FIS-2026-001'
ON CONFLICT (id_overhead_period, id_project) DO NOTHING;

INSERT INTO private.overhead_allocation
    (id, id_overhead_period, id_project, amount, consumption)
SELECT
    '01900000-0000-7000-8000-0000000000b3',
    '01900000-0000-7000-8000-0000000000b1',
    p.id, 750.00, 250.00
FROM private."Project" AS p
WHERE p.unique_code = 'QUI-2026-002'
ON CONFLICT (id_overhead_period, id_project) DO NOTHING;
