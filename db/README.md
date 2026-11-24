# Base de datos — scripts SQL

Esquema `private` del Sistema de Control de Proyectos y Recursos.

La fuente canónica para una **instalación nueva** es `db/schema/` +
`db/seeds/`. `db/migrations/` conserva los cambios incrementales para bases que
ya existían antes de estos scripts. `sql/` es legado y no debe ejecutarse.

---

## 1. Orden de ejecución

```text
db/schema/00_schema.sql
db/schema/01_tables.sql
db/schema/02_indexes.sql
db/schema/03_functions.sql
db/schema/04_triggers.sql
db/schema/05_views.sql

db/seeds/01_catalogs.sql
db/seeds/02_users_projects.sql
db/seeds/03_inventory.sql
db/seeds/04_equipment.sql
db/seeds/05_expenses_overhead.sql
db/seeds/99_verify.sql          # solo lectura, opcional
```

### psql

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

`psql` no acepta el prefijo `+asyncpg`; sustituye
`postgresql+asyncpg://` por `postgresql://`.

### Docker Compose

El servicio `db-init` ejecuta `db/scripts/init_db.sh`, que aplica todos los
archivos en el orden anterior. Es idempotente y se puede volver a ejecutar.

---

## 2. Qué contiene cada archivo

### `db/schema/00_schema.sql`

- Crea el esquema `private`.
- Define `private.uuid_generate_v7()` sin depender de `pgcrypto`
  (timestamp de 48 bits + `gen_random_uuid()`).

### `db/schema/01_tables.sql`

21 tablas, con los nombres reales del modelo:

| Tabla | Notas |
|---|---|
| `rol`, `"user"` | Tres roles (RN-1), `token_version`, email único |
| `status_project`, `priority_project`, `"Project"` | `Project` va entre comillas |
| `audith_project` | Historial de prioridad con justificación |
| `vendors` | Proveedores |
| `resource_type`, `raw_material` | `id_inventroy` (sic) |
| `raw_material_supplier` | Stock por material/proveedor |
| `audith_raw_material_supplier` | Lotes de entrada |
| `dispatch_log` | Despachos |
| `status_equipament`, `equipment`, `equipament_supplier` | Equipos y unidades |
| `reservation_equipament` | Reservas |
| `status_expense_approval`, `expense_approval` | Solicitudes (`modifiet_at`, sic) |
| `overhead_period`, `overhead_allocation` | Cierre y reparto |
| `audit_log` | Auditoría append-only |

Incluye PK, FK con `ON DELETE RESTRICT`, UNIQUE y CHECK. Los nombres de
constraints siguen el modelo y la base real.

### `db/schema/02_indexes.sql`

Índices de consulta y de claves foráneas. Los UNIQUE de PK/restricciones ya
los crea `01_tables.sql`.

### `db/schema/03_functions.sql`

| Función | Uso |
|---|---|
| `audit_log_append_only()` | Rechaza `UPDATE`/`DELETE`/`TRUNCATE` de auditoría |
| `append_only_guard()` | Igual para despachos y lotes |
| `audith_raw_material_supplier_add_stock()` | Suma el lote al stock |
| `dispatch_log_subtract_stock()` | Resta el despacho y rechaza stock insuficiente |
| `reservation_equipament_check_overlap()` | Rechaza solapes por unidad |
| `set_updated_at()` | Actualiza `updated_at` con `clock_timestamp()` |
| `set_modifiet_at()` | Actualiza `modifiet_at` de solicitudes |

### `db/schema/04_triggers.sql`

17 triggers: 2 de auditoría, 3 de despachos, 3 de lotes, 1 de reservas y 8 de
fechas de modificación. Todos se recrean con `DROP TRIGGER IF EXISTS`.

### `db/schema/05_views.sql`

`private.v_inventory_stock_status`, mapeada por
`src/model/views.py`:

- `Agotado`: `current_stock <= 0`
- `Alerta Stock Mínimo`: `0 < current_stock <= minimun_stock`
- `Normal`: `current_stock > minimun_stock`

---

## 3. Seeders

| Archivo | Contenido |
|---|---|
| `01_catalogs.sql` | 3 roles, estados de proyecto/equipo/gasto, prioridades y tipos de recurso |
| `02_users_projects.sql` | 4 usuarios demo, 4 proyectos y un historial de prioridad |
| `03_inventory.sql` | 3 proveedores, 6 materiales, 7 filas de stock y 7 lotes |
| `04_equipment.sql` | 3 equipos, 4 unidades y 3 reservas sin solape |
| `05_expenses_overhead.sql` | 5 solicitudes y 1 período de overhead con reparto exacto |
| `99_verify.sql` | Verificación de roles, proyectos y suma de overhead; imprime stock |

### Cuentas de prueba

No hay contraseñas. El login es solo con Google.

| Email | Rol |
|---|---|
| `admin@example.com` | Administrador del Sistema |
| `coordinador1@example.com` | Coordinador de Proyecto |
| `coordinador2@example.com` | Coordinador de Proyecto |
| `aprobador@example.com` | Aprobador / Gestor de Inventario |

Los emails usan `example.com`, dominio reservado para documentación. No son
cuentas reales de Google: para iniciar sesión, un administrador debe crear y
activar la cuenta con el email de Google que se vaya a usar.

### Stock sembrado

| Material / proveedor | Lotes | Stock | Mínimo | Estado |
|---|---|---:|---:|---|
| Ácido clorhídrico / Andino | 10.00 + 2.50 | 12.50 L | 5.00 | Normal |
| Hidróxido de sodio / Andino | 8.00 | 8.00 kg | 4.00 | Normal |
| Guantes de nitrilo / Central | 20.00 | 20.00 cajas | 10.00 | Normal |
| Papel filtro / Central | 6.00 | 6.00 paquetes | 8.00 | Alerta Stock Mínimo |
| Óxido de zinc / Andino | 3.50 | 3.50 kg | 2.00 | Normal |
| Medio LB / Central | 2500.00 | 2500.00 g | 500.00 | Normal |
| Ácido clorhídrico / Central | — | 0.00 L | 2.00 | Agotado |

El stock **no** se escribe directamente: los lotes lo suman con
`trg_lot_add_stock`. Re-ejecutar el seed no vuelve a sumar porque los lotes no
se insertan dos veces.

### Overhead

Período `2026-10-01` a `2026-10-31`, criterio `proporcional`, total `1200.00`:

- FIS-2026-001: consumo 150 → 450.00
- QUI-2026-002: consumo 250 → 750.00
- Suma = 1200.00

Solo se incluyen proyectos activos; BIO-2026-003 está en propuesta y
FIS-2026-004 finalizado (RN-6).

---

## 4. Verificación automática

`db/scripts/verify_schema.py` prueba los scripts sin tocar `private`:

```bash
python db/scripts/verify_schema.py
python db/scripts/verify_schema.py --database-url "postgresql+asyncpg://..."
```

Pasos:

1. Crea `verify_schema_<hex>`.
2. Sustituye `private.` por ese esquema en memoria.
3. Aplica esquema y seeders.
4. Verifica:
   - 21 tablas, 1 vista, 17 triggers, 8 funciones.
   - 3 roles, 4 usuarios, 4 proyectos, 7 lotes, 4 unidades, 3 reservas,
     5 solicitudes.
   - Lote suma stock, despacho resta stock, stock insuficiente rechazado.
   - `UPDATE`/`DELETE`/`TRUNCATE` fallan en `audit_log`, `dispatch_log` y lotes.
   - Reservas solapadas rechazadas y contiguas aceptadas.
   - Código de proyecto duplicado rechazado.
   - `updated_at`, UUID v7 e idempotencia de los seeders.
5. `ROLLBACK` + comprobación de que el esquema temporal desapareció.

Salida esperada: `RESULTADO: 40 comprobaciones OK` y
`ROLLBACK confirmado`.

---

## 5. Idempotencia y bases existentes

- `CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`, `CREATE OR REPLACE` y
  `DROP TRIGGER IF EXISTS`.
- Seeders con `ON CONFLICT`, `WHERE NOT EXISTS` y referencias por nombre,
  código o email.
- Las secuencias `SMALLSERIAL` se sincronizan con `setval` al final de
  `01_catalogs.sql`.
- En una base existente, las tablas no se alteran. Los cambios se aplican con
  `db/migrations/`.

### Diferencias conocidas con la base real de Supabase

La base real ya tenía las 21 tablas y la vista, pero:

- No tenía los triggers de stock, reservas ni `updated_at`; los añaden estos
  scripts.
- No tenía `ck_project_progress` ni `ck_project_budget_execution`; los scripts
  nuevos sí los incluyen para instalaciones limpias.
- `vendors.name` es `varchar(255)` en la base real; el modelo declara 150. Los
  scripts usan 255 para no romper datos existentes.
- `resource_type.id` aparece al final en la base real; el orden de columnas no
  afecta al ORM.

Para añadir solo los triggers a una base existente (operación **no
destructiva**, pero cambia el comportamiento de escritura), ejecuta en una
ventana de mantenimiento y con respaldo:

```bash
psql "$PGURL" -v ON_ERROR_STOP=1 -f db/schema/03_functions.sql
psql "$PGURL" -v ON_ERROR_STOP=1 -f db/schema/04_triggers.sql
```

No ejecutes `01_tables.sql` sobre una base con datos esperando que agregue los
CHECK faltantes: `CREATE TABLE IF NOT EXISTS` no altera tablas existentes. Para
eso hace falta una migración con `ALTER TABLE ... ADD CONSTRAINT`.

---

## 6. Operaciones prohibidas

- `UPDATE`, `DELETE` o `TRUNCATE` sobre `audit_log`, `dispatch_log` y lotes:
  los triggers lo impiden (`SQLSTATE 23000`).
- Borrar usuarios con historial: la FK `ON DELETE RESTRICT` lo impide. Los
  usuarios se desactivan.
- Ejecutar seeders en producción: crean datos ficticios.
- Aplicar `sql/` (legado): usa `db/schema/` o `db/migrations/`.
- Ejecutar DDL destructivo sobre `private` sin respaldo.

---

## 7. Hardening opcional

`db/security/audit_owner.sql` transfiere la propiedad de `audit_log` a
`audit_owner`, dejando al rol de aplicación solo `SELECT`/`INSERT`. Requiere
superuser o `CREATEROLE`; en Supabase puede requerir hacerlo desde el panel.
No forma parte del orden base.
