-- ============================================================================
-- 04_equipment.sql
-- Equipos, unidades fisicas por proveedor y reservas.
--
-- Las reservas no se solapan: el trigger trg_reservation_no_overlap lo impide
-- (RN-5). Las fechas son futuras respecto a la fecha de referencia del seed.
--
-- Idempotente por id / serial.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Catalogo de equipos
-- ----------------------------------------------------------------------------
INSERT INTO private.equipment (id, name, description)
SELECT
    '01900000-0000-7000-8000-000000000071',
    'Espectrofotometro UV-Vis',
    'Equipo de absorcion molecular para analisis cuantitativo.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.equipment WHERE name = 'Espectrofotometro UV-Vis'
);

INSERT INTO private.equipment (id, name, description)
SELECT
    '01900000-0000-7000-8000-000000000072',
    'Centrifuga refrigerada',
    'Centrifuga de sobremesa con control de temperatura.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.equipment WHERE name = 'Centrifuga refrigerada'
);

INSERT INTO private.equipment (id, name, description)
SELECT
    '01900000-0000-7000-8000-000000000073',
    'Microscopio de fluorescencia',
    'Microscopio invertido con modulo de fluorescencia.'
WHERE NOT EXISTS (
    SELECT 1 FROM private.equipment WHERE name = 'Microscopio de fluorescencia'
);

-- ----------------------------------------------------------------------------
-- Unidades fisicas compradas a proveedores
-- ----------------------------------------------------------------------------
INSERT INTO private.equipament_supplier
    (id, id_equipament, id_vendors, id_status, model, serial_number)
SELECT
    '01900000-0000-7000-8000-000000000081', e.id, v.id, se.id,
    'UV-1800', 'SN-EQ-0001'
FROM private.equipment AS e, private.vendors AS v,
     private.status_equipament AS se
WHERE e.name = 'Espectrofotometro UV-Vis'
  AND v.name = 'Equipos Cientificos del Centro, C.A.'
  AND se.name = 'Disponible'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.equipament_supplier
    (id, id_equipament, id_vendors, id_status, model, serial_number)
SELECT
    '01900000-0000-7000-8000-000000000082', e.id, v.id, se.id,
    'CR-4000', 'SN-EQ-0002'
FROM private.equipment AS e, private.vendors AS v,
     private.status_equipament AS se
WHERE e.name = 'Centrifuga refrigerada'
  AND v.name = 'Equipos Cientificos del Centro, C.A.'
  AND se.name = 'En uso'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.equipament_supplier
    (id, id_equipament, id_vendors, id_status, model, serial_number)
SELECT
    '01900000-0000-7000-8000-000000000083', e.id, v.id, se.id,
    'FL-200', 'SN-EQ-0003'
FROM private.equipment AS e, private.vendors AS v,
     private.status_equipament AS se
WHERE e.name = 'Microscopio de fluorescencia'
  AND v.name = 'Equipos Cientificos del Centro, C.A.'
  AND se.name = 'Disponible'
ON CONFLICT (id) DO NOTHING;

-- Segunda unidad del espectrofotometro, fuera de servicio.
INSERT INTO private.equipament_supplier
    (id, id_equipament, id_vendors, id_status, model, serial_number)
SELECT
    '01900000-0000-7000-8000-000000000084', e.id, v.id, se.id,
    'UV-1800', 'SN-EQ-0004'
FROM private.equipment AS e, private.vendors AS v,
     private.status_equipament AS se
WHERE e.name = 'Espectrofotometro UV-Vis'
  AND v.name = 'Equipos Cientificos del Centro, C.A.'
  AND se.name = 'En mantenimiento'
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Reservas (rangos semiabiertos, sin solape por unidad)
-- ----------------------------------------------------------------------------
INSERT INTO private.reservation_equipament
    (id, id_equipament_supplier, id_project, id_user,
     reservation_date, end_date, purpose)
SELECT
    '01900000-0000-7000-8000-000000000091', es.id, p.id, u.id,
    '2026-12-01 08:00:00+00', '2026-12-01 12:00:00+00',
    'Medicion de transmitancia de peliculas delgadas.'
FROM private.equipament_supplier AS es, private."Project" AS p,
     private."user" AS u
WHERE es.serial_number = 'SN-EQ-0001'
  AND p.unique_code = 'FIS-2026-001'
  AND u.email = 'coordinador1@example.com'
ON CONFLICT (id) DO NOTHING;

INSERT INTO private.reservation_equipament
    (id, id_equipament_supplier, id_project, id_user,
     reservation_date, end_date, purpose)
SELECT
    '01900000-0000-7000-8000-000000000092', es.id, p.id, u.id,
    '2026-12-02 13:00:00+00', '2026-12-02 17:00:00+00',
    'Separacion de catalizador por centrifugacion.'
FROM private.equipament_supplier AS es, private."Project" AS p,
     private."user" AS u
WHERE es.serial_number = 'SN-EQ-0002'
  AND p.unique_code = 'QUI-2026-002'
  AND u.email = 'coordinador2@example.com'
ON CONFLICT (id) DO NOTHING;

-- Misma unidad que la reserva 91, pero en otro dia: no hay solape.
INSERT INTO private.reservation_equipament
    (id, id_equipament_supplier, id_project, id_user,
     reservation_date, end_date, purpose)
SELECT
    '01900000-0000-7000-8000-000000000093', es.id, p.id, u.id,
    '2026-12-03 08:00:00+00', '2026-12-03 10:00:00+00',
    'Calibracion de linea base del espectrofotometro.'
FROM private.equipament_supplier AS es, private."Project" AS p,
     private."user" AS u
WHERE es.serial_number = 'SN-EQ-0001'
  AND p.unique_code = 'FIS-2026-004'
  AND u.email = 'coordinador2@example.com'
ON CONFLICT (id) DO NOTHING;
