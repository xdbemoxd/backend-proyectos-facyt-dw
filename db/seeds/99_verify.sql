-- ============================================================================
-- 99_verify.sql
-- Verificacion posterior al seed. No modifica datos.
--
-- Se puede ejecutar a mano:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/seeds/99_verify.sql
--
-- Falla con excepcion si falta un dato base o si el reparto de overhead no
-- cuadra con el total del periodo.
-- ============================================================================

SET client_encoding = 'UTF8';

DO $$
DECLARE
    v_roles integer;
    v_projects integer;
    v_total numeric(14, 2);
    v_alloc numeric(14, 2);
BEGIN
    SELECT count(*) INTO v_roles
      FROM private.rol
     WHERE name IN (
         'Administrador del Sistema',
         'Coordinador de Proyecto',
         'Aprobador / Gestor de Inventario'
     );
    IF v_roles <> 3 THEN
        RAISE EXCEPTION 'Se esperaban 3 roles (RN-1), encontrados %', v_roles;
    END IF;

    SELECT count(*) INTO v_projects
      FROM private."Project"
     WHERE unique_code IN (
         'FIS-2026-001', 'QUI-2026-002', 'BIO-2026-003', 'FIS-2026-004'
     );
    IF v_projects <> 4 THEN
        RAISE EXCEPTION
            'Se esperaban 4 proyectos de ejemplo, encontrados %', v_projects;
    END IF;

    SELECT total_cost INTO v_total
      FROM private.overhead_period
     WHERE id = '01900000-0000-7000-8000-0000000000b1';

    SELECT COALESCE(sum(amount), 0) INTO v_alloc
      FROM private.overhead_allocation
     WHERE id_overhead_period = '01900000-0000-7000-8000-0000000000b1';

    IF v_total IS NULL THEN
        RAISE EXCEPTION 'Falta el periodo de overhead de ejemplo';
    END IF;
    IF v_total <> v_alloc THEN
        RAISE EXCEPTION
            'El reparto de overhead no cuadra: total %, asignado %',
            v_total, v_alloc;
    END IF;
END $$;

-- Stock por proveedor (debe reflejar los lotes del seed).
SELECT resource_name,
       vendor_name,
       current_stock,
       minimun_stock,
       stock_status
  FROM private.v_inventory_stock_status
 ORDER BY resource_name, vendor_name;

-- Resumen del periodo de overhead.
SELECT op.start_date,
       op.end_date,
       op.criterion,
       op.total_cost,
       sum(oa.amount) AS asignado
  FROM private.overhead_period AS op
  JOIN private.overhead_allocation AS oa
    ON oa.id_overhead_period = op.id
 WHERE op.id = '01900000-0000-7000-8000-0000000000b1'
 GROUP BY op.id, op.start_date, op.end_date, op.criterion, op.total_cost;
