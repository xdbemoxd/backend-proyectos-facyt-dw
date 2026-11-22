# Auditoría (`private.audit_log`)

Bitácora **append-only** de acciones de negocio y requests HTTP.

## Esquema

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | `uuid` | PK, UUID v7 |
| `timestamp` | `timestamptz` | Fecha/hora del evento |
| `request_id` | `uuid` | Correlaciona la request con eventos de negocio |
| `user_id` | `uuid` | Actor (FK a `private.user`, `ON DELETE RESTRICT`) |
| `user_email` | `varchar(255)` | Snapshot del email del actor |
| `ip_address` | `varchar(255)` | IP del cliente |
| `method` | `varchar(10)` | Método HTTP |
| `path` | `varchar(500)` | Endpoint solicitado |
| `status_code` | `smallint` | Código HTTP (100–599) |
| `user_agent` | `varchar(512)` | User-Agent |
| `request_headers` | `jsonb` | Headers permitidos y redactados |
| `query_params` | `jsonb` | Query params con claves sensibles redactadas |
| `duration_ms` | `integer` | Duración de la request |
| `entity` | `varchar(255)` | Entidad afectada (`HTTP`, `Project`, `User`, …) |
| `action` | `varchar(255)` | Acción (`http_request`, `update_priority`, …) |
| `entity_id` | `varchar(100)` | ID del recurso |
| `outcome` | `varchar(20)` | `success`, `denied` o `failed` |
| `error_code` | `varchar(100)` | `HTTP_<status>` o nombre de la excepción |
| `error_detail` | `varchar(1000)` | Detalle acotado (sin credenciales) |
| `before_data` | `jsonb` | Estado anterior |
| `after_data` | `jsonb` | Estado nuevo |

### Reglas de integridad

- `UPDATE`, `DELETE` y `TRUNCATE` están bloqueados por triggers.
- `user_id` usa `ON DELETE RESTRICT`: no se puede borrar un usuario con
  auditoría; se debe desactivar.
- `request_headers` y `query_params` tienen límite de 32 KB.
- `before_data` y `after_data` tienen límite de 64 KB.
- No se guardan `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` ni
  similares: se registran como `[REDACTED]`.
- En errores de validación (`422`) no se guarda el `input`, solo `loc`, `msg` y
  `type`.

## Migración

```bash
psql "$DATABASE_URL" -f db/migrations/20261122_improve_audit_log.sql
```

La migración es idempotente. Quita temporalmente los triggers para el backfill y
los recrea al final.

## Middleware HTTP

`src/middleware/audit.py` se encarga de:

1. Generar `X-Request-ID`.
2. Medir la duración.
3. Capturar método, path, query params, headers redactados, user-agent, IP y
   actor desde `request.state` o desde el JWT.
4. Escribir la fila al finalizar la request.

Se auditan:

- Todas las mutaciones (`POST`, `PUT`, `PATCH`, `DELETE`).
- Login (`/api/user/auth/...`).
- Cualquier respuesta `>= 400`.
- `GET` exitosos solo si `AUDIT_HTTP_ALL=true`.

No se auditan `/health`, `/docs`, `/redoc` ni `/openapi.json`.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `AUDIT_HTTP_ALL` | `false` | Auditar también todos los GET exitosos |
| `TRUST_PROXY_HEADERS` | `false` | Confiar en `X-Forwarded-For` / `X-Real-IP` |

`TRUST_PROXY_HEADERS` solo debe activarse si la API está detrás de un proxy
confiable; de lo contrario, un cliente podría falsificar su IP.

## Auditoría de negocio

Dentro de una transacción:

```python
from src.utils.audit import record_audit

await record_audit(
    db,
    entity="Project",
    action="update_priority",
    entity_id=project.id,
    user_id=current_user.id,
    user_email=current_user.email,
    request=request,
    before={"priority": "Media"},
    after={"priority": "Alta"},
)
```

Si el evento debe sobrevivir a un rollback (por ejemplo, una acción denegada):

```python
from src.utils.audit import record_audit_standalone

await record_audit_standalone(
    entity="Project",
    action="update_priority_denied",
    entity_id=project.id,
    user_id=current_user.id,
    user_email=current_user.email,
    outcome="denied",
    request=request,
)
```

`record_audit` no hace `commit`; se suma a la transacción actual. Los servicios
son responsables de confirmar o revertir.

## Pruebas

```bash
# Unitarias
pytest tests/test_audit_utils.py tests/test_audit_helpers.py -q

# Integración contra la BD real
RUN_AUDIT_INTEGRATION=1 pytest tests/test_audit_integration.py -q

# Cobertura de los módulos de auditoría
pytest --cov=src.utils.audit --cov=src.middleware.audit --cov-fail-under=80
```
