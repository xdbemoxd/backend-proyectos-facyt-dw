# Reporte de testing — Inventario, lotes, despachos y proveedores

- Fecha: 2026-11-24
- Alcance: RF-4, RN-4, RN-8 (inventario, lotes, despachos y proveedores)
- Backend: `backend-proyectos-facyt-dw` (rama `miguel-develop`)
- Frontend: `frontend-proyectos-facyt` (rama `miguel-develop`)

## Comandos ejecutados y resultados

| Comando | Resultado |
|---|---|
| `.\.venv\Scripts\python.exe -m compileall -q src app.py` | OK |
| `.\.venv\Scripts\python.exe -c "import app; configure_mappers()"` | OK |
| `.\.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` |
| `.\.venv\Scripts\python.exe -m pytest -q` | **164 passed, 49 skipped** (89.35 s) |
| `$env:INVENTORY_LIVE_TESTS="1"; .\.venv\Scripts\python.exe -m pytest tests/test_inventory_live.py -q` | **8 passed** (253.47 s) |
| `npm run typecheck` | OK |
| `npm run build` | OK (43 rutas compiladas) |
| Smoke BFF (`next start`, sin sesión) | `/api/inventory/{stock,catalogs,lots,dispatch}` y `/api/vendors` → **401** |
| `npm run lint` | **Falla por 5 errores preexistentes** en `reports-view.tsx`, `close-period-form.tsx`, `use-audit.ts` y `sidenav.tsx`; ningún archivo nuevo de inventario aparece en el reporte. |
| `ruff check .` | **No ejecutado**: `ruff` no está instalado en `.venv` |

## Cobertura nueva

### Unitarias y de contrato (sin BD)

- `tests/test_inventory_rules.py` (29 pruebas):
  - matriz de permisos (`can_manage_inventory`, `can_manage_vendors`);
  - denegación 403 antes de tocar la BD para coordinador;
  - validación estricta `extra="forbid"`, cantidades/costos positivos,
    fecha de lote no futura, notas y facturas con longitud máxima;
  - mass assignment de `current_stock`, `id_user`, `updated_at`, etc.;
  - paginación inválida → 422;
  - mapeo de errores de BD: stock insuficiente → 409, FK → 422, desconocido →
    500 genérico, sin SQL ni trazas;
  - snapshots de auditoría sin PII.
- `tests/test_inventory_api_contract.py` (29 pruebas):
  - los 13 endpoints responden 401 sin token;
  - coordinador recibe 403 en todas las mutaciones y en proveedores;
  - admin/aprobador pasan el guard de rol y reciben 422 ante bodies inválidos.

### Integración live de solo lectura

`tests/test_inventory_live.py` usa la base real del `.env` únicamente con
`GET` y con 403/422 que se resuelven antes de escribir en tablas de negocio:

- stock, detalle de stock, catálogos y lotes legibles por los 3 roles;
- bitácora de despachos legible por los 3 roles;
- filtros de stock (`only_critical`, `resource_type`, `search`);
- proveedores solo para admin/aprobador; coordinador 403;
- coordinador 403 en POST/PATCH de inventario, lotes, despachos y proveedores;
- IDOR: coordinador filtrando despachos por un proyecto ajeno → 403;
- bodies inválidos (cantidad 0/negativa, mass assignment) → 422 sin insertar.

Verificación posterior a las pruebas live:

| Dato | Antes | Después |
|---|---|---|
| Filas de stock | 7 | 7 |
| Unidades totales | 2550.00 | 2550.00 |
| Lotes | 7 | 7 |
| Despachos | 0 | 0 |
| Proveedores | 3 | 3 |

Los 403 de las pruebas live sí generan eventos en `audit_log` (append-only),
como cualquier denegación real. No se modificó ni borró auditoría.

Prueba puntual del trigger (sin residuo): un despacho de `999999` unidades
contra la base real falló con `HTTP 409 Stock insuficiente…`; la transacción se
revirtió y `dispatch_log` siguió en 0 filas.

## Limitaciones declaradas

- No se ejecutó el alta real de un lote ni de un despacho contra la base
  compartida para no alterar stock ni dejar residuo. El camino de éxito del
  trigger está cubierto indirectamente por el SQL ya aplicado y por el mapeo
  de errores; el alta real queda para la base de pruebas aislada
  (`TEST_DATABASE_URL`), que no está configurada en este entorno.
- No se hizo prueba E2E en navegador con login de Google. El BFF se validó con
  `npm run typecheck` y `npm run build`; los endpoints reales se validaron por
  HTTP en las pruebas live.
- Gastos y prorrateo siguen usando datos mock; conservan `getStockSnapshot` y
  `getDispatchSnapshot`. Por eso sus totales no reflejan todavía el stock ni
  los despachos reales.
- `ruff` no está instalado en el entorno virtual.
