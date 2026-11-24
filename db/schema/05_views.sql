-- ============================================================================
-- 05_views.sql
-- Vistas de solo lectura mapeadas en src/model/views.py.
--
-- Idempotente: CREATE OR REPLACE VIEW.
-- ============================================================================

SET client_encoding = 'UTF8';

-- ----------------------------------------------------------------------------
-- Stock por proveedor con su estado operativo.
-- La usa InventoryStockStatusView (src/model/views.py).
--
-- stock_status:
--   * 'Agotado'              -> current_stock <= 0
--   * 'Alerta Stock Mínimo'  -> 0 < current_stock <= minimun_stock
--   * 'Normal'               -> current_stock > minimun_stock
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW private.v_inventory_stock_status AS
SELECT
    rms.id              AS supplier_item_id,
    rm.id_inventroy     AS resource_id,
    rm.name             AS resource_name,
    rt.name             AS resource_type,
    rm.unit             AS unit,
    v.name              AS vendor_name,
    rms.current_stock   AS current_stock,
    rms.minimun_stock   AS minimun_stock,
    CASE
        WHEN rms.current_stock <= 0 THEN 'Agotado'
        WHEN rms.current_stock <= rms.minimun_stock THEN 'Alerta Stock Mínimo'
        ELSE 'Normal'
    END                 AS stock_status,
    (rms.current_stock <= rms.minimun_stock) AS is_critical
FROM private.raw_material_supplier AS rms
JOIN private.raw_material AS rm
    ON rm.id_inventroy = rms.raw_materials_id
JOIN private.resource_type AS rt
    ON rt.id = rm.resource_type_id
JOIN private.vendors AS v
    ON v.id = rms.vendors_id;

COMMENT ON VIEW private.v_inventory_stock_status IS
    'Stock por materia prima y proveedor con estado (Agotado, Alerta Stock Mínimo, Normal).';
