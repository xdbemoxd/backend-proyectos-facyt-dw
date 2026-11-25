# Sistema de Control de Proyectos y Recursos — Backend

API REST para la gestión de proyectos científicos, inventario de materia prima
y consumibles, equipos y reservas, solicitudes de gasto, prorrateo de overhead y
auditoría inmutable de la Facultad de Ciencias y Tecnología (FACYT).

Este repositorio contiene **solo el backend** (FastAPI + PostgreSQL). El
frontend vive en el repositorio hermano `../frontend-proyectos-facyt`.

---

## 1. Estado actual

Implementado:

- Login con Google Identity + JWT propio (HS256).
- RBAC con tres roles y verificación del usuario en cada request.
- Administración de usuarios y consulta de auditoría.
- Auditoría HTTP append-only.
- Reportes exportables en Excel (XLSX) y PDF: gastos, prorrateo y equipos.
- Base de datos completa: esquema, vistas, triggers y seeders.
- Despliegue local con Docker Compose (PostgreSQL + backend + frontend).

Pendiente (no implementado todavía):

- CRUD y reglas de negocio de proyectos, inventario, despachos, equipos,
  solicitudes y overhead. Los modelos y la base existen, pero los endpoints
  de negocio aún no.
- Frontend de los módulos de negocio (el repositorio frontend ya tiene login,
  layout y BFF de usuarios/auditoría).

Endpoints actuales:

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Estado básico de la API |
| `GET` | `/health` | Health check público (sin BD) |
| `GET` | `/health/2` | Health check con consulta a la BD |
| `POST` | `/api/user/auth/google/login` | Login con ID token de Google |
| `POST` | `/api/user/auth/logout` | Revoca el token (incrementa `token_version`) |
| `GET` | `/api/user/me` | Usuario autenticado |
| `GET` | `/api/user/roles` | Roles permitidos (admin) |
| `GET/POST` | `/api/user` | Listar / crear usuarios (admin) |
| `GET/PATCH` | `/api/user/{id}` | Consultar / editar usuario (admin) |
| `GET` | `/api/audit` | Consultar auditoría (admin) |
| `GET` | `/api/reports/expenses.xlsx` / `.pdf` | Reporte de gastos (XLSX/PDF) |
| `GET` | `/api/reports/overhead.xlsx` / `.pdf` | Reporte de prorrateo (admin/aprobador) |
| `GET` | `/api/reports/equipment.xlsx` / `.pdf` | Reporte de equipos y reservas |

Documentación detallada: [`docs/auth.md`](docs/auth.md),
[`docs/audit.md`](docs/audit.md) y [`docs/reports.md`](docs/reports.md).

---

## 2. Stack

- Python 3.12+ (probado con 3.13).
- FastAPI, Starlette, Uvicorn.
- SQLAlchemy 2 async + `asyncpg`.
- PostgreSQL 14+ (Supabase o Docker `postgres:16-alpine`).
- JWT con `python-jose`; login con `google-auth`.
- SlowAPI para rate limiting.
- Pytest + pytest-asyncio + httpx para pruebas.

Dependencias: `requirements.txt`. Herramientas de desarrollo:
`requirements-dev.txt`.

---

## 3. Estructura

```text
.
├── app.py                     # FastAPI: middlewares, CORS, handlers, routers
├── dockerfile                 # Imagen del backend
├── docker-compose.yml         # PostgreSQL + db-init + backend + frontend
├── src/
│   ├── config/                # engine async, sesión, rate limit
│   ├── controller/            # login con Google
│   ├── middleware/            # auditoría y cabeceras de seguridad
│   ├── model/                 # tablas y vista (SQLAlchemy)
│   ├── router/                # endpoints HTTP
│   ├── schema/                # Pydantic entrada/salida
│   ├── service/               # lógica de aplicación
│   └── utils/                 # JWT, roles, permisos, auditoría
├── db/
│   ├── schema/                # creación de estructura (00..05)
│   ├── seeds/                 # datos ficticios (01..05 + 99_verify)
│   ├── scripts/               # init_db.sh y verify_schema.py
│   ├── security/              # hardening opcional de audit_log
│   └── migrations/            # cambios para bases ya existentes
├── sql/                       # SQL legado (no ejecutar; ver sql/README.md)
├── docs/                      # auth, auditoría y reportes
└── tests/                     # unitarias e integración
```

Arquitectura de capas:

```text
router (HTTP + dependencias)
  -> schema (validación)
    -> service (reglas de negocio + transacciones)
      -> model / repository (acceso a datos)
```

---

## 4. Requisitos previos

- Python 3.12 o superior.
- PostgreSQL 14 o superior, o una cuenta de Supabase.
- `pip` y, opcionalmente, `psql` para aplicar SQL a mano.
- Docker Desktop / Docker Engine + Compose v2 (para el despliegue local).
- Node.js 20.9+ y npm (solo si vas a correr el frontend).

---

## 5. Variables de entorno

Copia `.env.example` a `.env` y completa los valores:

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

| Variable | Obligatoria | Uso |
|---|---|---|
| `DATABASE_URL` | Sí | URL async: `postgresql+asyncpg://usuario:password@host:5432/bd` |
| `SCHEMA` | Sí | Esquema de trabajo; el proyecto usa `private` |
| `SECRET_KEY` | Sí | Firma del JWT propio (mínimo 32 caracteres aleatorios) |
| `JWT_ISSUER` | No | `iss` del JWT; default `proyectos-facyt-api` |
| `JWT_AUDIENCE` | No | `aud` del JWT; default `proyectos-facyt-frontend` |
| `JWT_ACCESS_TTL_MINUTES` | No | Vigencia del token; default `60` |
| `GOOGLE_CLIENT_ID` | Sí | Validación del ID token de Google |
| `GOOGLE_CLIENT_SECRET` | No* | Lo usa el frontend (NextAuth); el backend solo valida el ID token |
| `GOOGLE_ALLOWED_DOMAIN` | No | Si se define, exige el claim `hd` |
| `ENVIRONMENT` | No | `development` (default) o `production` |
| `ENABLE_DOCS` | No | `true` habilita Swagger/OpenAPI fuera de producción |
| `CORS_ORIGINS` | No | Orígenes separados por coma |
| `RATE_LIMIT` | No | Límite global SlowAPI; default `100/minute` |
| `LOGIN_RATE_LIMIT` | No | Límite del login; default `10/minute` |
| `TRUST_PROXY_HEADERS` | No | `true` solo detrás de un proxy confiable |
| `AUDIT_HTTP_ALL` | No | `true` audita también todos los GET exitosos |
| `AUDIT_SYNC_WRITES` | No | `true` fuerza escritura síncrona (solo pruebas); default `false` |
| `AUDIT_QUEUE_MAXSIZE` | No | Tamaño de la cola en memoria; default `10000` |
| `AUDIT_WORKER_CONCURRENCY` | No | Workers de persistencia; default `2` |
| `AUDIT_BATCH_SIZE` | No | Eventos por commit del worker; default `50` |
| `AUDIT_WRITE_RETRIES` | No | Reintentos de persistencia de auditoría; default `2` |
| `AUDIT_RETRY_BACKOFF_MS` | No | Backoff base entre reintentos; default `200` |
| `AUDIT_RETENTION_DAYS` | No | Retención operativa; default `365` |
| `TOKEN_CRON` | No | Token para tareas programadas |
| `TEST_DATABASE_URL` | No | Base aislada para pruebas de integración |
| `TEST_SCHEMA` | No | Esquema de la base de pruebas |

`*` El backend no usa `GOOGLE_CLIENT_SECRET`; el frontend sí. Se incluye en
`.env.example` para el despliegue con Compose.

Nunca versiones `.env`. En producción inyecta los secretos desde el gestor de
secretos o las variables del proveedor.

Si la contraseña de la base contiene caracteres especiales (`@`, `:`, `/`,
`#`), codifícala para URL (`%40`, `%3A`, `%2F`, `%23`) dentro de
`DATABASE_URL`. En Docker Compose, `POSTGRES_PASSWORD` se inserta tal cual en la
URL; usa una contraseña alfanumérica en local o define `DATABASE_URL`
explícitamente.

### Variables para Docker Compose

`docker-compose.yml` reutiliza el mismo `.env` y añade:

| Variable | Default local | Uso |
|---|---|---|
| `POSTGRES_DB` | `proyectos_facyt` | Nombre de la base local |
| `POSTGRES_USER` | `proyectos` | Usuario local |
| `POSTGRES_PASSWORD` | **sin default** | Obligatoria; compose falla si falta |
| `SECRET_KEY` | **sin default** | Obligatoria, ≥32 bytes; rechaza placeholders |
| `AUTH_SECRET` / `NEXTAUTH_SECRET` | **sin default** | Obligatorias, ≥32 bytes (deben coincidir) |
| `SEED_DEMO_DATA` | `true` | `false` salta `db/seeds` (sin datos ficticios) |
| `FRONTEND_PORT` | `3000` | Único puerto publicado al host |
| `AUTH_URL` / `NEXTAUTH_URL` | `http://localhost:3000` | URL pública del frontend |
| `NEXT_PUBLIC_ALLOWED_EMAIL_DOMAIN` | vacío | Dominio validado en el formulario |

PostgreSQL y el backend **no publican puertos**: el navegador solo habla con el
frontend, que reenvía las llamadas al backend por la red interna de compose.

---

## 6. Base de datos

### 6.1 Crear la base

#### Opción A — Supabase

1. Crea un proyecto en Supabase.
2. Copia el connection string del **Session pooler** (puerto `5432`) para DDL y
   migraciones.
3. Para la aplicación puedes usar el **Transaction pooler** (puerto `6543`);
   `src/config/db.py` desactiva el cache de prepared statements para PgBouncer.
4. Ajusta `DATABASE_URL`:
   `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`
5. Ejecuta el esquema y los seeders (sección 6.2).
6. Si el proyecto usa un esquema distinto de `private`, cambia `SCHEMA` y
   reemplaza `private.` en los scripts. El código espera `private`.

#### Opción B — PostgreSQL local con Docker

```bash
docker run --name proyectos-facyt-db \
  -e POSTGRES_DB=proyectos_facyt \
  -e POSTGRES_USER=proyectos \
  -e POSTGRES_PASSWORD=proyectos_local \
  -p 5432:5432 -d postgres:16-alpine
```

`DATABASE_URL=postgresql+asyncpg://proyectos:proyectos_local@localhost:5432/proyectos_facyt`

### 6.2 Orden de ejecución

Esquema (siempre en este orden):

```text
db/schema/00_schema.sql        esquema private + uuid_generate_v7()
db/schema/01_tables.sql        tablas, PK, FK, UNIQUE, CHECK
db/schema/02_indexes.sql       índices de consulta y FKs
db/schema/03_functions.sql     funciones de stock, reservas e inmutabilidad
db/schema/04_triggers.sql      triggers
db/schema/05_views.sql         v_inventory_stock_status
```

Seeders:

```text
db/seeds/01_catalogs.sql             roles, estados, prioridades, tipos
db/seeds/02_users_projects.sql       usuarios demo, proyectos, historial
db/seeds/03_inventory.sql            proveedores, materiales, stock, lotes
db/seeds/04_equipment.sql            equipos, unidades, reservas
db/seeds/05_expenses_overhead.sql    solicitudes y período de overhead
db/seeds/99_verify.sql               verificación (solo lectura)
```

Bash / WSL / Git Bash:

```bash
PGURL="postgresql://usuario:password@host:5432/basedatos"
for f in db/schema/*.sql; do psql "$PGURL" -v ON_ERROR_STOP=1 -f "$f"; done
for f in db/seeds/*.sql;  do psql "$PGURL" -v ON_ERROR_STOP=1 -f "$f"; done
```

PowerShell:

```powershell
$env:PGURL = "postgresql://usuario:password@host:5432/basedatos"
Get-ChildItem db/schema/*.sql | Sort-Object Name | ForEach-Object {
    psql $env:PGURL -v ON_ERROR_STOP=1 -f $_.FullName
}
Get-ChildItem db/seeds/*.sql | Sort-Object Name | ForEach-Object {
    psql $env:PGURL -v ON_ERROR_STOP=1 -f $_.FullName
}
```

> `psql` no entiende el prefijo `+asyncpg`. Usa la misma URL cambiando
> `postgresql+asyncpg://` por `postgresql://`.

En Docker Compose esto es automático: el servicio `db-init` ejecuta
`db/scripts/init_db.sh`, que aplica los archivos en orden.

### 6.3 Qué crea el esquema

- 21 tablas en `private`, con los nombres reales del modelo:
  - `"Project"` (con mayúscula y comillas), `"user"` (palabra reservada).
  - `raw_material.id_inventroy` (sic), `expense_approval.modifiet_at` (sic).
  - `dispatch_log`, `expense_approval` en minúsculas.
- PK UUID v7 (`private.uuid_generate_v7()` como default para SQL directo).
- FK con `ON DELETE RESTRICT` (no se borran usuarios con historial).
- CHECK de progreso (0–100), presupuesto (0–100), stock no negativo, cantidades
  positivas, rangos de fechas, criterio de overhead y tamaño de JSON de
  auditoría.
- UNIQUE de código de proyecto, email, material/proveedor, período/proyecto y
  nombre/código de estado de gasto.
- La vista `private.v_inventory_stock_status` con
  `Agotado` / `Alerta Stock Mínimo` / `Normal`.

### 6.4 Triggers y reglas en la base

| Trigger | Tabla | Efecto |
|---|---|---|
| `trg_audit_log_append_only` | `audit_log` | Rechaza `UPDATE`/`DELETE` (SQLSTATE `23000`) |
| `trg_audit_log_no_truncate` | `audit_log` | Rechaza `TRUNCATE` |
| `trg_dispatch_log_append_only` | `dispatch_log` | Rechaza `UPDATE`/`DELETE` |
| `trg_dispatch_log_no_truncate` | `dispatch_log` | Rechaza `TRUNCATE` |
| `trg_dispatch_log_subtract_stock` | `dispatch_log` | Resta stock y rechaza stock insuficiente (`23514`) |
| `trg_lot_append_only` / `trg_lot_no_truncate` | `audith_raw_material_supplier` | Lotes append-only |
| `trg_lot_add_stock` | `audith_raw_material_supplier` | Suma la cantidad del lote al stock |
| `trg_reservation_no_overlap` | `reservation_equipament` | Rechaza solapes por unidad (`23P01`) |
| `trg_*_set_updated_at` | tablas con `updated_at` | Actualiza `updated_at` con `clock_timestamp()` |
| `trg_expense_set_modifiet_at` | `expense_approval` | Actualiza `modifiet_at` |

La migración `20261125_security_hardening.sql` añade además append-only en
`audith_project`, `overhead_period` y `overhead_allocation`, y rechaza períodos
de overhead solapados (`23P01`). No forma parte del esquema base.

Los triggers de stock usan `SELECT ... FOR UPDATE`, por lo que dos despachos
concurrentes no pueden dejar el stock negativo.

### 6.5 Seeders

Datos **ficticios**, sin PII real. Emails en el dominio reservado
`example.com`. No hay contraseñas: el login es solo con Google.

| Email | Rol | Uso |
|---|---|---|
| `admin@example.com` | Administrador del Sistema | Administración y auditoría |
| `coordinador1@example.com` | Coordinador de Proyecto | Proyectos FIS-2026-001 y BIO-2026-003 |
| `coordinador2@example.com` | Coordinador de Proyecto | Proyectos QUI-2026-002 y FIS-2026-004 |
| `aprobador@example.com` | Aprobador / Gestor de Inventario | Aprobaciones e inventario |

Otros datos:

- 4 proyectos (física, química y biología) en distintos estados y prioridades.
- 3 proveedores, 6 materiales, 7 filas de stock por proveedor y 7 lotes.
- Stock resultante: 12.50 L de ácido, 8.00 kg de hidróxido, 20 cajas de
  guantes, 6 paquetes de papel filtro (bajo mínimo), 3.50 kg de óxido de zinc,
  2500 g de medio LB y una fila agotada.
- 3 equipos, 4 unidades físicas y 3 reservas sin solape.
- 5 solicitudes de gasto en estados `draft`, `pending`, `approved`,
  `rejected` y `executed` (nombres visibles en español).
- 1 período de overhead (octubre 2026) con reparto exacto:
  450.00 + 750.00 = 1200.00.

Para iniciar sesión con una cuenta distinta de las de prueba, un administrador
debe crearla primero (`POST /api/user`) y activarla. El login no autorregistra.

### 6.6 Idempotencia

- El esquema usa `CREATE ... IF NOT EXISTS`, `CREATE OR REPLACE` y
  `DROP TRIGGER IF EXISTS`.
- Los seeders usan `ON CONFLICT` / `WHERE NOT EXISTS` y referencias por
  nombre, código o email.
- Re-ejecutar los seeders **no duplica** usuarios, proyectos, lotes ni stock.
- Los catálogos `SMALLSERIAL` sincronizan su secuencia al final del seed.

En una base ya existente, `CREATE TABLE IF NOT EXISTS` no altera tablas. Los
cambios posteriores se aplican con los archivos de `db/migrations/`.

### 6.7 Verificación automática del esquema y los seeders

`db/scripts/verify_schema.py` valida los scripts contra la base configurada
**sin tocar `private`**:

1. Crea un esquema temporal único `verify_schema_<hex>`.
2. Reemplaza `private.` por ese esquema en memoria.
3. Aplica esquema y seeders.
4. Comprueba stock, triggers, inmutabilidad, reservas, duplicados, UUID v7 e
   idempotencia.
5. Aplica también la migración de endurecimiento (sin `CONCURRENTLY`, porque
   corre dentro de la transacción) y verifica índices, triggers append-only y
   solape de overhead.
6. Hace `ROLLBACK` y confirma que el esquema temporal desapareció.

```bash
.venv/Scripts/python.exe db/scripts/verify_schema.py
# o:
python db/scripts/verify_schema.py --database-url "postgresql+asyncpg://..."
```

Este script **no** debe ejecutarse contra producción con un usuario que pueda
crear esquemas si no se acepta ese uso; solo crea objetos temporales y los
revierte.

### 6.8 Migraciones para bases existentes

`db/migrations/` contiene cambios incrementales para bases que ya tenían el
esquema anterior:

1. `20261122_improve_audit_log.sql` — columnas HTTP, JSONB, índices y triggers
   append-only de `audit_log`.
2. `20261123_add_token_version.sql` — `user.token_version`.
3. `20261123_audit_owner.sql` — separación de propiedad de `audit_log`.
4. `20261125_security_hardening.sql` — UNIQUE normalizados de material y serial
   de equipo, append-only de `audith_project`/overhead y bloqueo de períodos
   solapados. Usa `CREATE INDEX CONCURRENTLY`: ejecutar en autocommit (psql),
   no dentro de una transacción. Falla con mensaje claro si hay duplicados y no
   borra datos.

En una instalación nueva **no** hace falta ejecutarlas: `db/schema/` ya incluye
su estado final. `sql/` es legado y no debe ejecutarse.

`db/security/audit_owner.sql` es opcional y requiere un rol con `CREATEROLE` o
superuser. Separa el owner de `audit_log` del rol de aplicación para que la app
no pueda desactivar los triggers.

---

## 7. Ejecución del backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env   # completar valores
uvicorn app:app --reload --port 8000
```

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app:app --reload --port 8000
```

Comprobaciones:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/2
```

`/health/2` devuelve `500` genérico si la base no responde. `ENABLE_DOCS=true`
habilita `/docs` solo fuera de producción.

---

## 8. Frontend

El frontend está en `../frontend-proyectos-facyt` (Next.js 16 + NextAuth v5).
Su README explica variables y arranque. Resumen:

```bash
cd ../frontend-proyectos-facyt
npm ci
cp .env.example .env.local   # completar
npm run dev                  # http://localhost:3000
```

El frontend llama al backend **desde el servidor** mediante `BACKEND_URL`
(`http://localhost:8000` en desarrollo). El navegador no recibe el JWT del
backend: la sesión de NextAuth lo guarda y los BFF routes lo reenvían.

Para el login con Google, configura en Google Cloud Console el origen
`http://localhost:3000` y el redirect URI
`http://localhost:3000/api/auth/callback/google`. El `GOOGLE_CLIENT_ID` debe
ser el mismo en backend y frontend.

---

## 9. Despliegue local con Docker Compose

```bash
cp .env.example .env
# Editar .env: SECRET_KEY, AUTH_SECRET, GOOGLE_CLIENT_ID, etc.
docker compose up --build
```

Servicios:

| Servicio | Acceso local | Notas |
|---|---|---|
| `db` | solo red interna (`db:5432`) | PostgreSQL 16, volumen `proyectos-facyt-pgdata` |
| `db-init` | — | Aplica esquema + seeders (si `SEED_DEMO_DATA=true`) y termina |
| `backend` | solo red interna (`backend:8000`) | FastAPI/Uvicorn |
| `frontend` | http://localhost:3000 | Next.js, único puerto publicado |

El frontend se construye desde `../frontend-proyectos-facyt`. Si mueves el
`docker-compose.yml`, ajusta `frontend.build.context`.

Comandos útiles:

```bash
docker compose ps
docker compose logs -f backend
docker compose exec db psql -U proyectos -d proyectos_facyt -c '\dt private.*'
docker compose exec backend python -c "import app; print('ok')"
docker compose down            # conserva el volumen
docker compose down -v         # borra la base local (destructivo)
```

Para depurar desde el host, usa `docker compose exec` o un túnel SSH. No
descomentes `ports` en `db`/`backend` en un entorno compartido.

Para producción:

- Elimina el servicio `db-init` y los seeders del flujo automático.
- Define secretos reales por variables de entorno o gestor de secretos.
- Usa HTTPS y `ENVIRONMENT=production`; deja `ENABLE_DOCS=false`.
- Restringe `CORS_ORIGINS` al dominio real.
- Activa `TRUST_PROXY_HEADERS=true` solo detrás de un proxy confiable.
- Aplica `db/security/audit_owner.sql` si el proveedor lo permite.

---

## 10. Pruebas

```bash
# Suite local (no toca la base del .env)
pytest -q

# Cobertura
pytest --cov=src --cov-fail-under=80

# Integración contra una base aislada
$env:TEST_DATABASE_URL = "postgresql+asyncpg://usuario:password@host:5432/basedatos_test"
$env:TEST_SCHEMA = "private"
pytest -q
```

Reglas:

- `tests/conftest.py` sustituye `DATABASE_URL` por `TEST_DATABASE_URL` antes de
  importar la app y aborta si ambas apuntan a la misma base.
- Si `TEST_DATABASE_URL` no está definida, las pruebas de integración se
  omiten y cualquier acceso accidental a la BD falla de inmediato.
- La base de pruebas debe tener el esquema y los seeders aplicados (los tres
  roles de RN-1).
- Los usuarios de prueba se desactivan, no se borran (auditoría append-only).

Validación específica de los SQL:

```bash
python db/scripts/verify_schema.py
```

Ver también `tests/README.md` y `docs/testing/`.

---

## 11. Seguridad

- Todos los endpoints salvo `/`, `/health` y el login exigen JWT.
- El rol y el estado se leen de la base en cada request; el claim `rol` es
  informativo. `token_version` permite revocar tokens al cerrar sesión o
  cambiar rol/estado.
- RBAC con `require_roles(...)` y alcance por proyecto con
  `can_access_project(...)` (evita IDOR).
- Rate limit global y específico en login.
- CORS restringido por origen; no se usa `*` con credenciales.
- Errores genéricos: sin trazas, SQL ni rutas internas.
- `SECRET_KEY` se valida una sola vez al importar: rechaza placeholders y
  claves de menos de 32 bytes (el arranque falla; no hay coste por request).
- Auditoría redacta `Authorization`, `Cookie`, `Set-Cookie` y `X-Api-Key`.
  Las excepciones no guardan su mensaje: solo tipo + `request_id`.
- La escritura de auditoría HTTP es no bloqueante (cola acotada + worker con
  reintentos y drenaje en shutdown). Las lecturas de `/api/audit`,
  `/api/audit/users` y `/api/user` quedan registradas sin consultas extra.
- La exportación de reportes es fail-closed: si no se puede auditar, responde
  `503` y no entrega el archivo.
- Un coordinador solo edita `name`/`description`; estado, progreso y ejecución
  presupuestaria son exclusivos del administrador (`403` si los envía).
- `audit_log`, `dispatch_log`, lotes y (con la migración de endurecimiento)
  `audith_project`/overhead son append-only por triggers.
- El contenedor corre con usuario no privilegiado (`appuser`).
- `.dockerignore` excluye `.env`, `.git`, `.venv`, tests y docs.

Nunca se versionan secretos. Si un `.env` se filtró, rota `SECRET_KEY`, la
contraseña de la base y el `GOOGLE_CLIENT_SECRET`.

---

## 12. Limitaciones conocidas

1. **Endpoints de negocio pendientes.** Los modelos y triggers existen, pero no
   hay servicios/CRUD de proyectos, inventario, despachos, equipos, solicitudes
   ni overhead.
2. **`dispatch_log` no referencia una solicitud de gasto (pendiente).** El
   modelo actual no tiene `id_expense_approval`, por lo que la base no puede
   impedir por sí sola un despacho sin solicitud `Aprobada`. Esa validación
   debe vivir en el servicio cuando se implemente RF-5; no forma parte de este
   endurecimiento.
3. **La suma del overhead no está impuesta por la BD.** El seed la cumple y el
   script de verificación la comprueba, pero no hay un constraint diferido que
   la obligue en cada cierre. Debe validarse en el servicio transaccional.
4. **La reserva no cambia el estado del equipo.** El trigger impide solapes,
   pero no marca la unidad como `En uso` ni la libera; eso corresponde al
   servicio.
5. **Solape de períodos de overhead.** El esquema base no lo impedía. La
   migración `20261125_security_hardening.sql` lo bloquea con un trigger, pero
   el rediseño del cierre de overhead sigue pendiente (no se implementó aquí).
6. **El esquema de la base real de Supabase no tiene todos los CHECK del
   modelo.** En particular faltan `ck_project_progress` y
   `ck_project_budget_execution` en la base existente; los scripts nuevos sí
   los incluyen para instalaciones limpias. Para alinear una base existente
   hace falta una migración adicional.
7. **`Project` y `user` requieren comillas dobles** en SQL directo por sus
   nombres. Los scripts y el ORM ya lo hacen.
8. **Los seeders son para desarrollo/demo.** No deben ejecutarse en producción:
   crean usuarios y datos ficticios.
9. **El frontend depende de Docker para el despliegue reproducible.** Sin
   Docker, se levanta con `npm run dev` y `BACKEND_URL` apuntando al backend.

---

## 13. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| `La variable de entorno DATABASE_URL no esta configurada` | Falta `.env` o la variable | Copia `.env.example` y completa |
| `DuplicatePreparedStatement` en Supabase | Pooler en modo transacción | Usa la URL correcta; `src/config/db.py` ya desactiva el cache |
| `schema "private" does not exist` | No se aplicó `00_schema.sql` | Ejecuta el esquema en orden |
| `audit_log es append-only` al migrar | Trigger activo | Usa `db/migrations/20261122_improve_audit_log.sql`, que lo desactiva temporalmente |
| `Stock insuficiente` al sembrar | Se insertó un lote dos veces con otro ID | No inventes IDs de lotes; re-ejecuta el seed idempotente |
| `La unidad ya tiene una reserva que se solapa` | RN-5 | Elige otro rango o unidad |
| `401` con token válido | Usuario inactivo, rol no permitido o `token_version` cambió | Revisa el usuario y vuelve a iniciar sesión |
| `403` en login | Email no registrado, dominio distinto o cuenta inactiva | Créala como admin y actívala |
| `password authentication failed` tras cambiar `POSTGRES_PASSWORD` | El volumen conserva el usuario/contraseña anteriores | `docker compose down -v` (borra la base local) y vuelve a levantar |

---

## 14. Documentación relacionada

- [`docs/auth.md`](docs/auth.md) — login, JWT, RBAC, alcance y errores.
- [`docs/audit.md`](docs/audit.md) — esquema y middleware de auditoría.
- [`docs/reports.md`](docs/reports.md) — reportes XLSX/PDF, filtros y permisos.
- [`docs/testing/test-report-2026-11-24-reports.md`](docs/testing/test-report-2026-11-24-reports.md)
  — evidencia del ciclo de pruebas de los reportes.
- [`db/README.md`](db/README.md) — detalle de scripts SQL y verificación.
- [`db/migrations/`](db/migrations) — migraciones incrementales.
- [`docs/testing/test-report-2026-11-24-db-setup.md`](docs/testing/test-report-2026-11-24-db-setup.md)
  — evidencia de las pruebas de este entregable.
- [`AGENTS.md`](AGENTS.md) — protocolo de desarrollo y ciclo de pruebas.
