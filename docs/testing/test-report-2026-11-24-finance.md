# Reporte de testing — Solicitudes de gasto y prorrateo de overhead

- Fecha: 2026-11-24
- Rama: `miguel-develop` (sin commit ni push)
- Alcance: RF-5 (solicitudes de gasto), RF-6 (cierre y prorrateo de overhead),
  RN-6, RN-7, RBAC, alcance por proyecto, auditoría y cuadre exacto.
- Backend: `backend-proyectos-facyt-dw`
- Frontend: `frontend-proyectos-facyt`

## 1. Cambios de base de datos

Migración aplicada a la base real del `.env`:

```text
db/migrations/20261124_expense_requester.sql
```

Resultado verificado:

```text
columna id_user: is_nullable = NO
filas con id_user NULL: 0 de 5
FK expense_approval_id_user_fkey: presente
índice idx_expense_requester: presente
```

La migración es aditiva: columna nullable, backfill desde `Project.id_user`,
FK, índice y `SET NOT NULL`. No se borraron ni modificaron filas existentes.

También se actualizaron para instalaciones nuevas:

- `db/schema/01_tables.sql`
- `db/schema/02_indexes.sql`
- `db/seeds/05_expenses_overhead.sql`

`db/scripts/verify_schema.py` ejecutó el esquema y seeders en un esquema
temporal y terminó con `RESULTADO: 40 comprobaciones OK` y rollback confirmado.

## 2. Backend — comandos y resultados

### T0. Estático y arranque

```bash
python -m compileall -q src app.py     # OK
python -c "import app"                  # import app OK
pip check                               # No broken requirements found
```

### T1/T2. Unitarios y contrato

```bash
pytest tests/test_expense_rules.py tests/test_overhead_rules.py \
       tests/test_finance_api_contract.py -q
```

Resultado: **90 passed**.

Cubre:

- Transiciones válidas e inválidas de la máquina de estados.
- Terminalidad de Rechazado y Ejecutado.
- Creación solo admin/coordinador.
- Envío a pendiente solo por solicitante o admin.
- Autoaprobación del aprobador bloqueada; admin permitido (documentado).
- Ejecución solo admin/aprobador.
- Reparto equitativo y proporcional, redondeo, suma exacta y montos no
  negativos con totales pequeños.
- Sin proyectos activos, consumo cero en proporcional y solape inclusivo.
- `extra="forbid"` y rechazo de mass assignment en gastos y overhead.
- 401 sin token y 403 por rol en todos los endpoints nuevos.

### T6. Regresión

```bash
pytest -q
```

Resultado: **305 passed, 66 skipped**.

Los 66 omitidos son pruebas de integración/live que exigen
`TEST_DATABASE_URL` o flags explícitos; no se relajó ni eliminó ninguna prueba.

### Pruebas live de solo lectura

```bash
FINANCE_LIVE_TESTS=1 pytest tests/test_finance_live.py -q
```

Resultado: **10 passed**.

Cubre contra la base real:

- Listado de gastos con alcance: admin/aprobador ven todo; coordinador solo
  solicitudes de sus proyectos.
- Detalle de gasto y 403 por IDOR al consultar una solicitud ajena.
- Filtro por proyecto ajeno → 403.
- Transición inválida (Borrador → Ejecutado) → 409 y estado sin cambios.
- Filtro de estado inválido → 422.
- Períodos existentes legibles para admin/aprobador; suma de asignaciones igual
  al total.
- Coordinador sin acceso a overhead → 403.
- Preview equitativo con suma exacta, sin escritura.
- Cierre solapado → 409 y cantidad de períodos sin cambios.
- Rango de fechas inválido → 422.
- Período inexistente → 404.

### Verificación transaccional del camino feliz (rollback)

Se ejecutó un script de verificación dentro de una transacción externa con
`ROLLBACK` final, sin persistir datos:

- Crear gasto como coordinador → Borrador, solicitante correcto.
- Coordinador envía a Pendiente.
- Aprobador aprueba y ejecuta.
- Aprobador intentando aprobar una solicitud propia → 403.
- Cierre de overhead equitativo → 2 asignaciones, suma `100.00`.
- 5 eventos de auditoría de negocio en la misma transacción.
- `ROLLBACK done`.

Conteos posteriores al rollback: 5 gastos, 1 período, 2 asignaciones,
0 despachos (sin cambios).

## 3. Frontend — comandos y resultados

```bash
npm run typecheck    # OK
npm run build        # OK, 49 páginas generadas
```

Rutas BFF compiladas:

- `/api/expenses` (GET, POST)
- `/api/expenses/[id]` (GET)
- `/api/expenses/[id]/status` (PATCH)
- `/api/overhead/periods` (GET, POST)
- `/api/overhead/periods/[id]` (GET)
- `/api/overhead/preview` (GET)

## 4. Limitaciones y pendientes

- No hay `TEST_DATABASE_URL` configurada: las pruebas de integración existentes
  siguen omitidas.
- Las pruebas live son de solo lectura. El camino feliz de cierre de período y
  de cambio de estado se validó con una transacción revertida, no con datos
  persistentes nuevos.
- `dispatch_log` está vacío en la base real; por eso no se fuerza una prueba
  live exitosa de preview proporcional sobre octubre 2026. La lógica está
  cubierta por unitarios.
- No se ejecutó revisión formal del Analista de Ciberseguridad; queda pendiente
  la firma de seguridad antes de entrega/merge según `AGENTS.md`.
- `npm run lint` no forma parte de este alcance; el README del frontend indica
  errores/advertencias preexistentes ajenos a finanzas.
