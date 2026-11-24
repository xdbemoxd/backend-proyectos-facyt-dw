# Reporte de pruebas — Base de datos y puesta en marcha

- Fecha: 2026-11-24
- Rama: `miguel-develop`
- Alcance: scripts SQL del esquema `private`, seeders, README, Docker Compose,
  Dockerfile del frontend y verificación de arranque.
- Autor: agente desarrollador (subagente).

---

## 1. Entorno

| Elemento | Valor |
|---|---|
| SO | Windows (PowerShell) |
| Python | 3.13.14 (`.venv`) |
| PostgreSQL | Supabase, PostgreSQL 15/16 vía pooler (puerto 6543) |
| Node.js | `node:22` local (build de Next 16.4.5) |
| Docker | **No disponible** (`docker` no está en PATH) |
| `psql` | **No disponible** en PATH |
| `sh` | **No disponible** en PATH |

La validación SQL se hizo con `asyncpg` contra la base configurada en
`DATABASE_URL`, usando un esquema temporal y `ROLLBACK`. **No se ejecutó DDL ni
DML sobre `private` real.**

---

## 2. Archivos creados/modificados

### Backend

Creados:

- `db/schema/00_schema.sql`
- `db/schema/01_tables.sql`
- `db/schema/02_indexes.sql`
- `db/schema/03_functions.sql`
- `db/schema/04_triggers.sql`
- `db/schema/05_views.sql`
- `db/seeds/01_catalogs.sql`
- `db/seeds/02_users_projects.sql`
- `db/seeds/03_inventory.sql`
- `db/seeds/04_equipment.sql`
- `db/seeds/05_expenses_overhead.sql`
- `db/seeds/99_verify.sql`
- `db/scripts/init_db.sh`
- `db/scripts/verify_schema.py`
- `db/security/audit_owner.sql` (opcional)
- `db/README.md`
- `README.md`
- `docker-compose.yml`

Modificados:

- `.env.example` (variables de Compose, frontend y pruebas)
- `sql/README.md` (remite a `db/schema` para instalaciones nuevas)

### Frontend

Creados:

- `Dockerfile`
- `.dockerignore`
- `.env.example`

Modificados:

- `README.md` (reemplaza el template de Next.js)
- `.gitignore` (`!.env.example`)
- `lib/dispatch/api.ts` (nombre del trigger en un comentario)

---

## 3. Pruebas ejecutadas

### 3.1 Validación SQL contra PostgreSQL (esquema temporal + ROLLBACK)

Comando:

```powershell
.\.venv\Scripts\python.exe db\scripts\verify_schema.py
```

Resultado: **40 comprobaciones OK**.

Cubre:

- 21 tablas, 1 vista, 17 triggers, 8 funciones.
- Seeders: 3 roles, 4 usuarios, 4 proyectos, 7 lotes, 4 unidades de equipo,
  3 reservas, 5 solicitudes, 2 asignaciones de overhead.
- Lote suma stock: `12.50 -> 14.50`.
- Despacho resta stock: `14.50 -> 13.00`.
- Despacho mayor al stock: rechazado con SQLSTATE `23514`, stock intacto.
- `audit_log`: INSERT permitido; UPDATE/DELETE/TRUNCATE rechazados (`23000`).
- `dispatch_log`: UPDATE/DELETE/TRUNCATE rechazados (`23000`).
- Lotes: UPDATE/DELETE/TRUNCATE rechazados (`23000`).
- Reserva solapada rechazada (`23P01`); reserva contigua aceptada.
- Código de proyecto duplicado rechazado (`23505`).
- `updated_at` actualizado por trigger.
- UUID v7 válido y 200 UUID únicos.
- Re-ejecución de seeders: stock, proyectos y lotes sin duplicados.
- `99_verify.sql` ejecutado sin errores.
- `ROLLBACK` confirmado; el esquema temporal desapareció.

Verificación posterior de la base real (solo lectura):

```text
temp schemas: 0
private triggers: 2   (los mismos que antes; no se aplicaron los nuevos)
private tables: 21
```

### 3.2 Endurecimiento opcional `audit_owner`

Se ejecutó `db/security/audit_owner.sql` en un esquema temporal con un rol
temporal y `ROLLBACK`:

```text
owner: audit_owner_verify_<hex>
SELECT: True
INSERT: True
UPDATE: False
DELETE: False
TRUNCATE: False
role persists after rollback: None
```

No se ejecutó contra `private` real.

### 3.3 Backend

```powershell
.\.venv\Scripts\python.exe -m compileall -q src app.py db\scripts\verify_schema.py
.\.venv\Scripts\python.exe -c "import app"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
```

Resultados:

- `compileall`: sin errores.
- `import app`: `import app OK`.
- `pip check`: `No broken requirements found.`
- `pytest -q`: **58 passed, 41 skipped** en 1.50 s.

Los 41 tests omitidos son de integración porque `TEST_DATABASE_URL` no está
definida; no se ejecutaron contra la base compartida.

### 3.4 Frontend

```powershell
npm run typecheck
npm run build
```

Resultados:

- `tsc --noEmit`: sin errores.
- `next build`: compilación exitosa, 34 rutas generadas.
- Build limpio adicional **sin `.env.local`** (simulando la imagen Docker):
  compilación exitosa. El archivo se restauró después.
- `npm run lint`: **falla con 5 errores y 11 advertencias preexistentes** en
  `components/equipment/reservations-table.tsx`,
  `components/layout/app-topnav.tsx`,
  `components/overhead/close-period-form.tsx`,
  `components/reports/reports-view.tsx` y `lib/audit/use-audit.ts`
  (`Date.now()` en render, `setState` en `useEffect`, imports/variables sin
  usar). No se tocaron esos archivos ni se corrigieron en este entregable.
- `docker build`: **no ejecutado** (Docker no disponible).

### 3.5 Docker Compose

`docker compose config` no se pudo ejecutar porque Docker no está instalado.
Como verificación alternativa, `docker-compose.yml` se parseó con `js-yaml`:

```text
YAML OK
services: db, db-init, backend, frontend
db -> postgres:16-alpine
db-init -> postgres:16-alpine
backend -> . / dockerfile
frontend -> ../frontend-proyectos-facyt / Dockerfile
```

`db/scripts/init_db.sh` no se pudo validar con `sh -n` (no hay `sh` en PATH).
Es un script POSIX simple; se usará dentro del contenedor `postgres:16-alpine`.

---

## 4. Cobertura funcional de los scripts

| Requisito | Evidencia |
|---|---|
| Esquema, tablas, PK/FK/CHECK/UNIQUE/índices | `01_tables.sql`, `02_indexes.sql`, verificación de 21 tablas |
| Vista de stock | `05_views.sql`, estados `Agotado` y `Alerta Stock Mínimo` comprobados |
| Inmutabilidad de `audit_log` | Triggers + 3 pruebas de rechazo |
| Inmutabilidad de `dispatch_log` | Triggers + 3 pruebas de rechazo |
| Lote suma stock | Trigger + prueba de `+2.00` |
| Despacho resta stock y rechaza negativo | Trigger + pruebas de `-1.50` y sobregiro |
| `updated_at` | Trigger + prueba |
| Reservas sin solape | Trigger + pruebas de solape y rango contiguo |
| Seeders coherentes | Conteos y datos verificados |
| Idempotencia | Re-ejecución de seeders sin duplicados |
| Orden de ejecución | `db/README.md`, README principal y `init_db.sh` |

---

## 5. Limitaciones y pendientes

1. **Docker no está disponible en este entorno.** No se ejecutó
   `docker compose config`, `docker build` ni `docker compose up`. El YAML se
   validó sintácticamente, pero la construcción real de las imágenes queda sin
   probar aquí.
2. **`psql` y `sh` no están disponibles.** Los comandos de `psql` del README no
   se ejecutaron localmente; la ruta equivalente con `asyncpg` sí.
3. **La base real no se actualizó.** Los triggers nuevos existen en los scripts
   y se probaron en un esquema temporal, pero `private` real conserva solo los
   2 triggers de auditoría previos. Aplicarlos requiere una ventana de
   mantenimiento y respaldo.
4. **La base real no tiene los CHECK `ck_project_progress` ni
   `ck_project_budget_execution`.** Los scripts nuevos los incluyen para
   instalaciones limpias; alinear una base existente requiere migración.
5. **Sin endpoints de negocio.** No se pueden probar flujos HTTP de proyectos,
   inventario, despachos, equipos, solicitudes ni overhead; solo existen los
   endpoints de salud, autenticación, usuarios y auditoría.
6. **`dispatch_log` no está ligado a `expense_approval`.** La regla “no
   despachar sin solicitud aprobada” no puede imponerse en la BD con el modelo
   actual; debe validarse en el servicio.
7. **La suma del overhead no está impuesta por constraint.** El seed y
   `99_verify.sql` la comprueban, pero no hay un constraint diferido.
8. **Pruebas de integración omitidas.** No se definió `TEST_DATABASE_URL` para
   no escribir en la base compartida.
9. **No se hizo commit ni push**, ni cambio de rama, según lo solicitado.

---

## 6. Conclusión

Los scripts de esquema y seeders son ejecutables contra PostgreSQL real, pasan
40 comprobaciones funcionales en un esquema temporal y son idempotentes. El
backend importa y su suite local está en verde. El frontend compila y pasa
`typecheck`. Queda sin verificar la construcción/ejecución de contenedores por
falta de Docker en el entorno.
