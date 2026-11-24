-- ============================================================================
-- 03_inventory.sql
-- Proveedores, catalogo de materia prima/consumibles, stock por proveedor y
-- lotes de entrada.
--
-- El stock NO se escribe directamente: los lotes lo suman con el trigger
-- trg_lot_add_stock. Asi el seed ejercita la misma ruta que la aplicacion.
--
-- Idempotente: ON CONFLICT / WHERE NOT EXISTS. Re-ejecutar no duplica stock
-- porque los lotes no se insertan dos veces.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Proveedores ficticios
-- ----------------------------------------------------------------------------
INSERT INTO private.vendors
    (id, name, description, contact_email, contact_phone)
VALUES
    ('01900000-0000-7000-8000-000000000031',
     'Proveedor Quimico Andino, C.A.',
     'Reactivos y materias primas para laboratorios universitarios.',
     'ventas@proveedor-andino.example.com', '+58-000-0000001'),
    ('01900000-0000-7000-8000-000000000032',
     'Insumos de Laboratorio Central, C.A.',
     'Consumibles, material de vidrio y medios de cultivo.',
     'compras@insumos-central.example.com', '+58-000-0000002'),
    ('01900000-0000-7000-8000-000000000033',
     'Equipos Cientificos del Centro, C.A.',
     'Equipos de laboratorio, repuestos y servicio tecnico.',
     'soporte@equipos-centro.example.com', '+58-000-0000003')
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Catalogo de materia prima y consumibles
-- ----------------------------------------------------------------------------
INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000041',
    'Ácido clorhídrico 37%',
    'Reactivo para digestion y ajuste de pH.',
    rt.id, 'L'
FROM private.resource_type AS rt
WHERE rt.name = 'Reactivo'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Ácido clorhídrico 37%'
  )
LIMIT 1;

INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000042',
    'Hidróxido de sodio en perlas',
    'Reactivo para soluciones buffer y neutralizacion.',
    rt.id, 'kg'
FROM private.resource_type AS rt
WHERE rt.name = 'Reactivo'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Hidróxido de sodio en perlas'
  )
LIMIT 1;

INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000043',
    'Guantes de nitrilo',
    'Consumible de proteccion personal, talla M.',
    rt.id, 'caja'
FROM private.resource_type AS rt
WHERE rt.name = 'Consumible'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Guantes de nitrilo'
  )
LIMIT 1;

INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000044',
    'Papel filtro cualitativo',
    'Consumible para filtracion de soluciones.',
    rt.id, 'paquete'
FROM private.resource_type AS rt
WHERE rt.name = 'Consumible'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Papel filtro cualitativo'
  )
LIMIT 1;

INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000045',
    'Óxido de zinc en polvo',
    'Materia prima para sintesis de peliculas delgadas.',
    rt.id, 'kg'
FROM private.resource_type AS rt
WHERE rt.name = 'Materia prima'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Óxido de zinc en polvo'
  )
LIMIT 1;

INSERT INTO private.raw_material
    (id_inventroy, name, description, resource_type_id, unit)
SELECT
    '01900000-0000-7000-8000-000000000046',
    'Medio de cultivo LB deshidratado',
    'Materia prima para cultivo bacteriano.',
    rt.id, 'g'
FROM private.resource_type AS rt
WHERE rt.name = 'Materia prima'
  AND NOT EXISTS (
      SELECT 1 FROM private.raw_material WHERE name = 'Medio de cultivo LB deshidratado'
  )
LIMIT 1;

-- ----------------------------------------------------------------------------
-- Stock por materia prima y proveedor.
-- current_stock arranca en 0; los lotes de abajo lo incrementan.
-- S4 queda bajo minimo y S7 queda agotado a proposito (para la vista).
-- ----------------------------------------------------------------------------
INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000051', rm.id_inventroy, v.id,
    0.00, 5.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Ácido clorhídrico 37%'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000052', rm.id_inventroy, v.id,
    0.00, 4.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Hidróxido de sodio en perlas'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000053', rm.id_inventroy, v.id,
    0.00, 10.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Guantes de nitrilo'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000054', rm.id_inventroy, v.id,
    0.00, 8.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Papel filtro cualitativo'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000055', rm.id_inventroy, v.id,
    0.00, 2.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Óxido de zinc en polvo'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000056', rm.id_inventroy, v.id,
    0.00, 500.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Medio de cultivo LB deshidratado'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

-- Fila sin lote: queda 'Agotado' en la vista.
INSERT INTO private.raw_material_supplier
    (id, raw_materials_id, vendors_id, current_stock, minimun_stock)
SELECT
    '01900000-0000-7000-8000-000000000057', rm.id_inventroy, v.id,
    0.00, 2.00
FROM private.raw_material AS rm, private.vendors AS v
WHERE rm.name = 'Ácido clorhídrico 37%'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (raw_materials_id, vendors_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Lotes de entrada (el trigger suma al stock).
-- Saldos resultantes:
--   S1 acido/V1        = 10.00 + 2.50 = 12.50 L
--   S2 hidroxido/V1    = 8.00 kg
--   S3 guantes/V2      = 20.00 cajas
--   S4 papel/V2        = 6.00 paquetes  (bajo minimo de 8.00)
--   S5 oxido/V1        = 3.50 kg
--   S6 medio LB/V2     = 2500.00 g
--   S7 acido/V2        = 0.00 (sin lote)
-- ----------------------------------------------------------------------------
INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000061', s.id,
    '2026-10-02 09:00:00+00', 10.00, 18.00, 'FAC-AND-1001'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Ácido clorhídrico 37%'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000062', s.id,
    '2026-10-18 14:30:00+00', 2.50, 19.50, 'FAC-AND-1042'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Ácido clorhídrico 37%'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000063', s.id,
    '2026-10-05 10:00:00+00', 8.00, 22.50, 'FAC-AND-1002'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Hidróxido de sodio en perlas'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000064', s.id,
    '2026-10-06 11:15:00+00', 20.00, 9.75, 'FAC-CEN-2001'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Guantes de nitrilo'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000065', s.id,
    '2026-10-07 08:45:00+00', 6.00, 4.20, 'FAC-CEN-2002'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Papel filtro cualitativo'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000066', s.id,
    '2026-10-09 16:00:00+00', 3.50, 35.00, 'FAC-AND-1003'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Óxido de zinc en polvo'
  AND v.name = 'Proveedor Quimico Andino, C.A.'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.audith_raw_material_supplier
    (id, id_raw_materials_supplier, in_date,
     count_raw_materials_in, unit_cost, invoice_reference)
SELECT
    '01900000-0000-7000-8000-000000000067', s.id,
    '2026-10-10 09:30:00+00', 2500.00, 0.08, 'FAC-CEN-2003'
FROM private.raw_material_supplier AS s
JOIN private.raw_material AS rm ON rm.id_inventroy = s.raw_materials_id
JOIN private.vendors AS v ON v.id = s.vendors_id
WHERE rm.name = 'Medio de cultivo LB deshidratado'
  AND v.name = 'Insumos de Laboratorio Central, C.A.'
ON CONFLICT (id) DO NOTHING;
