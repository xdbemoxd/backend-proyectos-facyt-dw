# Autenticación y RBAC

Autenticación con **Google ID token** y sesión mediante **JWT HS256**. La
autorización se resuelve con el rol almacenado en `private."user"`, no con el
claim `rol` del token.

Alcance actual: login, consulta del usuario autenticado, administración de
usuarios y consulta de auditoría. Los endpoints de proyectos, inventario,
equipos y cierres se agregarán sobre estas mismas dependencias.

## 1. Flujo de login con Google

```text
Frontend
  │  Google Identity Services
  ▼
Google ID token (JWT firmado por Google)
  │
  ▼
POST /api/user/auth/google/login   { "id_token": "..." }
  │
  ├─ 1. Verifica firma, exp, iss y aud con GOOGLE_CLIENT_ID
  ├─ 2. Exige email presente y email_verified = true
  ├─ 3. Si GOOGLE_ALLOWED_DOMAIN está definido, exige hd == dominio
  ├─ 4. Busca el email en private."user" (debe estar pre-registrado)
  ├─ 5. Rechaza cuentas inactivas
  ├─ 6. Primer login: completa name/lastname desde Google (no activa)
  └─ 7. Emite JWT propio (60 min) y devuelve los datos del usuario
```

### Endpoint

| Método | Ruta | Autenticación |
|---|---|---|
| `POST` | `/api/user/auth/google/login` | Pública |

El login aplica `LOGIN_RATE_LIMIT` (default `10/minute`), además del límite
global `RATE_LIMIT`. Al excederse responde `429`.

**Body**

```json
{
  "id_token": "<GOOGLE_ID_TOKEN>"
}
```

**Respuesta `200`**

```json
{
  "id": "0199c0f2-7f5a-7c3e-9b7a-2f5c1d0e8a11",
  "email": "usuario@uc.edu.ve",
  "fullname": "Nombre Apellido",
  "rol": "Coordinador de Proyecto",
  "token": "<JWT>"
}
```

Reglas del login:

- No existe login con contraseña ni autorregistro. Un administrador debe crear
  el usuario previamente (`POST /api/user`).
- Un email no registrado recibe `403`, aunque el token de Google sea válido.
- Una cuenta con `is_active = false` recibe `403`, incluso en su primer login.
- El primer login solo completa `name` y `lastname`; **no activa** la cuenta.
- Si `GOOGLE_ALLOWED_DOMAIN` está configurado, el claim `hd` debe coincidir.
- Los errores de credenciales devuelven un detalle genérico (`"Error"`) y no
  exponen si el email existe.

### JWT propio

El token se genera en `src/utils/other.py` con `python-jose`.

| Aspecto | Valor actual |
|---|---|
| Algoritmo | `HS256` |
| Firma | `SECRET_KEY` |
| Vigencia | 60 minutos (`JWT_ACCESS_TTL_MINUTES`) |
| Claims | `sub`, `email`, `rol`, `iss`, `aud`, `iat`, `nbf`, `exp`, `jti`, `ver` |
| Verificación | Firma, `exp`, `iss` y `aud` con `verify_token_acces` |

La API espera el token en cada request autenticada:

```http
Authorization: Bearer <JWT>
```

`get_current_user` vuelve a consultar la base de datos en cada request. Por eso:

- un cambio de rol aplica de inmediato, aunque el token viejo conserve el rol
  anterior;
- una cuenta desactivada se rechaza en la siguiente request;
- el claim `rol` es informativo y **nunca** debe usarse para autorizar.

Notas de diseño:

- El JWT propio incluye `iss` (`JWT_ISSUER`) y `aud` (`JWT_AUDIENCE`), y ambos
  se validan al verificar el token.
- `POST /api/user/auth/logout` incrementa `private."user".token_version`; el
  token viejo deja de ser válido inmediatamente. Lo mismo ocurre al cambiar el
  rol o el estado de la cuenta.
- No hay refresh token. La sesión de NextAuth y el token del backend duran
  **60 minutos**; después se debe volver a iniciar sesión con Google.

## 2. Roles del sistema

Solo existen tres roles (RN-1). Los nombres deben coincidir exactamente con
`private.rol.name`.

| Constante | Nombre en BD | Uso |
|---|---|---|
| `ADMIN_ROLE` | `Administrador del Sistema` | Administración de usuarios, roles y auditoría |
| `APPROVER_ROLE` | `Aprobador / Gestor de Inventario` | Aprobaciones, inventario y despachos |
| `COORDINATOR_ROLE` | `Coordinador de Proyecto` | Gestión de sus propios proyectos |

```python
from src.utils.roles import (
    ADMIN_ROLE,
    APPROVER_ROLE,
    COORDINATOR_ROLE,
    ALLOWED_ROLES,
)
```

`ALLOWED_ROLES` es un `frozenset` con los tres nombres. Un usuario cuyo rol no
esté en ese conjunto recibe `403` en `get_current_user`.

## 3. Matriz de permisos

Leyenda: ✅ permitido · ❌ `403` · 🔓 público · — no aplica.

| Endpoint | Público | Administrador | Aprobador | Coordinador |
|---|---:|---:|---:|---:|
| `POST /api/user/auth/google/login` | 🔓 | — | — | — |
| `POST /api/user/auth/logout` | ❌ | ✅ | ✅ | ✅ |
| `GET /api/user/me` | ❌ | ✅ | ✅ | ✅ |
| `GET /api/user/roles` | ❌ | ✅ | ❌ | ❌ |
| `GET /api/user` | ❌ | ✅ | ❌ | ❌ |
| `POST /api/user` | ❌ | ✅ | ❌ | ❌ |
| `GET /api/user/{user_id}` | ❌ | ✅ | ❌ | ❌ |
| `PATCH /api/user/{user_id}` | ❌ | ✅ | ❌ | ❌ |
| `GET /api/audit` | ❌ | ✅ | ❌ | ❌ |
| `GET /` y `/health` | 🔓 | — | — | — |

Precisiones:

- Sin token, o con token inválido/expirado, la respuesta es `401`.
- Con token válido pero rol distinto al exigido, la respuesta es `403`.
- Un usuario inactivo recibe `401` genérico en cualquier endpoint autenticado
  (no se revela si la cuenta existe/está desactivada).
- Los endpoints de negocio futuros deben declarar explícitamente
  `require_roles(...)`; no se debe asumir que “autenticado” equivale a
  “autorizado”.

### Detalle de endpoints administrativos

`GET /api/user/roles` devuelve los tres roles permitidos, ordenados por nombre.

`GET /api/user` acepta:

| Parámetro | Tipo | Regla |
|---|---|---|
| `limit` | `int` | 1–200, por defecto 50 |
| `offset` | `int` | ≥ 0, por defecto 0 |
| `search` | `str` | Máx. 255; busca en `email`, `name` y `lastname` |
| `role_id` | `UUID` | Filtra por rol |
| `is_active` | `bool` | Filtra por estado |

Ordena por `create_at` descendente y devuelve `items`, `total`, `limit` y
`offset`.

`POST /api/user` recibe `email`, `name?`, `lastname?`, `id_rol` e `is_active`
(por defecto `true`). Responde `201`. El email se normaliza a minúsculas al
crear el usuario y no se acepta duplicado, incluso con distinta capitalización
(`409`).

`PATCH /api/user/{user_id}` recibe `name?`, `lastname?`, `id_rol?` e
`is_active?`. No permite cambiar el email. Un body sin campos devuelve `422`.

`GET /api/audit` acepta `limit`, `offset`, `entity`, `action`, `outcome`,
`status_code`, `user_id`, `request_id`, `path`, `from_date` y `to_date`. Ordena
por `timestamp` descendente.

## 4. Dependencias de autenticación y autorización

### `get_current_user`

Ubicación: `src/utils/auth.py`.

```python
from fastapi import Depends

from src.schema.user import CurrentUser
from src.utils.auth import get_current_user


@router.get("/user/me")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
):
    return current_user
```

Secuencia de validación:

1. Exige el header `Authorization: Bearer <token>`. Sin header → `401`.
2. Verifica firma, expiración, `iss` y `aud` del JWT. Token inválido o
   expirado → `401`.
3. Exige que `sub` sea un UUID válido. Si no → `401`.
4. Busca el usuario en la base de datos con su rol. Si no existe → `401`.
5. Compara el claim `ver` con `private."user".token_version`; si difiere
   (logout, cambio de rol o desactivación) → `401`.
6. Si `is_active` es falso → `401`.
7. Si el rol no existe o no está en `ALLOWED_ROLES` → `403`.
8. Devuelve un `CurrentUser` y deja `request.state.user_id`, `user_email` y
   `user_role` disponibles para la auditoría.

`CurrentUser` contiene: `id`, `email`, `name`, `lastname`, `is_active`,
`id_rol` y `rol`.

### `require_roles`

Es una fábrica de dependencias: recibe uno o más nombres de rol y devuelve un
`checker` que reutiliza `get_current_user`.

```python
import uuid

from fastapi import Depends

from src.schema.user import CurrentUser
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE


@router.get("/audit")
async def get_audit_log(
    _: CurrentUser = Depends(require_roles(ADMIN_ROLE)),
):
    ...


# Endpoint ilustrativo: aún no existe en el alcance actual.
@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_roles(APPROVER_ROLE, ADMIN_ROLE)
    ),
):
    ...
```

Reglas de uso:

- No comparar `current_user.rol` contra strings escritos a mano; usar las
  constantes de `src/utils/roles.py`.
- No leer el rol desde el payload del JWT ni desde el body de la request.
- No poner la lógica de autorización en el router más allá de la dependencia:
  las reglas de alcance y de estado van en el servicio.
- Si se necesita el usuario en el handler, asignar el resultado de
  `require_roles(...)` a `current_user` en lugar de `_`.

## 5. Alcance por proyecto

`can_access_project` está en `src/utils/permissions.py`:

```python
def can_access_project(
    user_role: str,
    user_id: uuid.UUID,
    project_owner_id: uuid.UUID,
) -> bool:
    ...
```

| Rol | Acceso |
|---|---|
| Administrador del Sistema | Todos los proyectos |
| Aprobador / Gestor de Inventario | Todos los proyectos |
| Coordinador de Proyecto | Solo los proyectos donde `Project.id_user == user_id` |

En el modelo actual, `ProjectModel.id_user` identifica al coordinador
responsable.

Ejemplo de uso en la capa de servicio:

```python
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.model.project import ProjectModel
from src.schema.user import CurrentUser
from src.utils.permissions import can_access_project


async def get_project_for_user(
    db: AsyncSession,
    project_id: uuid.UUID,
    current_user: CurrentUser,
) -> ProjectModel:
    project = await _get_project_or_404(db, project_id)

    if not can_access_project(
        current_user.rol,
        current_user.id,
        project.id_user,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )

    return project
```

Esta función es la base para prevenir IDOR en los endpoints de proyectos. Debe
aplicarse **después** de cargar el recurso y **antes** de modificarlo. En el
alcance actual todavía no existen routers de proyectos; cuando se agreguen,
toda operación de un coordinador debe pasar por esta verificación.

## 6. Auto-protección y último administrador

`PATCH /api/user/{user_id}` implementa protecciones para evitar que el sistema
quede sin administración:

| Intento | Resultado |
|---|---|
| Un admin se desactiva a sí mismo (`is_active: false`) | `409` |
| Un admin se cambia el rol a sí mismo por uno distinto | `409` |
| Se desactiva o degrada al último admin activo | `409` |
| Se editan `name`/`lastname` del propio admin | Permitido |
| Se envía el mismo rol actual en `id_rol` | Permitido (no hay cambio) |

El servicio cuenta los administradores activos y usa
`would_remove_last_admin(...)` antes de aplicar el cambio. La función devuelve
`True` cuando el objetivo es un admin activo, es el único que queda y el cambio
lo desactiva o le quita el rol de administrador.

Los usuarios **no se eliminan**: se desactivan. Esto preserva las referencias
históricas y la auditoría (`audit_log.user_id` usa `ON DELETE RESTRICT`).

## 7. Códigos de error esperados

| Código | Cuándo ocurre | Ejemplo |
|---|---|---|
| `401` | Falta el token, es inválido, está expirado o su `sub` no es un UUID | `GET /api/user/me` sin `Authorization` |
| `401` | El `id_token` de Google es inválido o el email no está verificado | Login con token alterado |
| `401` | Usuario autenticado pero inactivo o token revocado | Token de cuenta desactivada o con `ver` obsoleto |
| `403` | El rol no permite el endpoint | Coordinador en `GET /api/user` |
| `403` | Dominio de Google no permitido, email no registrado o cuenta inactiva | Login rechazado |
| `404` | El usuario solicitado no existe | `GET /api/user/{uuid_inexistente}` |
| `409` | Email duplicado | `POST /api/user` con email ya registrado |
| `409` | Auto-desactivación, auto-cambio de rol o último admin | `PATCH /api/user/{self}` |
| `422` | Body o query inválidos según Pydantic | `id_rol` inexistente, UUID mal formado, PATCH vacío |
| `429` | Se supera `LOGIN_RATE_LIMIT` o `RATE_LIMIT` | Login o consultas repetidas |
| `500` | Falla de base de datos u otro error no controlado | Respuesta genérica sin detalle interno |

Los errores no incluyen trazas, SQL ni rutas internas. Los detalles de negocio
se limitan a mensajes como `"No autenticado"`, `"No autorizado"`, `"Usuario no
encontrado"` o `"El email ya está registrado"`.

### Health check y errores internos

`GET /health/2` responde `500` con `{"detail":"Database connection failed"}` si
falla la comprobación de base de datos. La excepción completa se registra solo
en los logs internos; no se devuelve `str(e)` ni el detalle del motor.

## 8. Variables de entorno

| Variable | Obligatoria | Uso |
|---|---|---|
| `DATABASE_URL` | Sí | Conexión PostgreSQL async (`postgresql+asyncpg://...`) |
| `SCHEMA` | Sí | Esquema de la base de datos; el proyecto usa `private` |
| `SECRET_KEY` | Sí | Firma y verificación del JWT propio |
| `JWT_ISSUER` | No | `iss` del JWT propio; default `proyectos-facyt-api` |
| `JWT_AUDIENCE` | No | `aud` del JWT propio; default `proyectos-facyt-frontend` |
| `JWT_ACCESS_TTL_MINUTES` | No | Vigencia del access token; default `60` |
| `GOOGLE_CLIENT_ID` | Sí | Validación de firma, `iss` y `aud` del ID token de Google |
| `GOOGLE_ALLOWED_DOMAIN` | No | Si se define, exige que el claim `hd` coincida (ej. `uc.edu.ve`) |
| `ENVIRONMENT` | No | `development` (default) o `production` |
| `ENABLE_DOCS` | No | `true` habilita `/docs`, `/redoc` y OpenAPI; default `false` |
| `CORS_ORIGINS` | No | Orígenes permitidos, separados por coma. En desarrollo hay defaults locales |
| `RATE_LIMIT` | No | Límite global de SlowAPI; default `100/minute`, admite lista separada por coma |
| `LOGIN_RATE_LIMIT` | No | Límite específico del login; default `10/minute`. Se aplica además del global |
| `TRUST_PROXY_HEADERS` | No | `true` solo detrás de un proxy confiable; habilita `X-Forwarded-For` / `X-Real-IP` |
| `AUDIT_HTTP_ALL` | No | `true` para auditar también todos los GET exitosos |
| `AUDIT_RETENTION_DAYS` | No | Días de retención operativa de auditoría; default `365` |
| `TOKEN_CRON` | No | Token para tareas programadas; no interviene en el login de usuarios |

`SECRET_KEY`, `GOOGLE_CLIENT_ID` y `DATABASE_URL` se validan al importar la
aplicación: si faltan, el arranque falla. Nunca deben versionarse; usar `.env`
local o el gestor de secretos del despliegue.

Ejemplo de `.env` (sin valores reales):

```dotenv
DATABASE_URL=postgresql+asyncpg://usuario:password@host:5432/basedatos
SCHEMA=private
SECRET_KEY=<secreto-de-firma>
JWT_ISSUER=proyectos-facyt-api
JWT_AUDIENCE=proyectos-facyt-frontend
JWT_ACCESS_TTL_MINUTES=60
GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
GOOGLE_ALLOWED_DOMAIN=uc.edu.ve
ENVIRONMENT=development
ENABLE_DOCS=false
RATE_LIMIT=100/minute
LOGIN_RATE_LIMIT=10/minute
AUDIT_RETENTION_DAYS=365
```

### Despliegue y secretos

- `.dockerignore` excluye `.env`, `.git`, `.venv`, tests, docs, frontend y
  cachés, por lo que el `Dockerfile` no debe copiar secretos.
- `.env.example` documenta las variables sin valores reales; no contiene
  secretos y no debe usarse como `.env` de producción.
- Los secretos se inyectan en runtime mediante variables de entorno o el
  gestor de secretos del despliegue.
- Si una imagen se publicó con un `.env` dentro, hay que rotar `SECRET_KEY`, la
  contraseña de la base de datos y `GOOGLE_CLIENT_SECRET`, y reconstruir la
  imagen.
- Backend y frontend envían `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy` y CSP. `Strict-Transport-Security` y
  las cookies `Secure` requieren HTTPS; en localhost HTTP no se emiten.
- `ENABLE_DOCS=false` por defecto: Swagger/OpenAPI no se publican salvo que se
  habilite explícitamente en desarrollo.
- El owner de `audit_log` es `audit_owner`; el rol de aplicación solo tiene
  `SELECT`/`INSERT` y no puede desactivar los triggers append-only.

## 9. PII y auditoría

La autenticación y la auditoría están separadas: `private."user"` conserva el
email, mientras que `private.audit_log` minimiza la PII.

- Los nuevos eventos de auditoría guardan `user_email = NULL`. El email se
  resuelve desde `private."user"` usando `audit_log.user_id` (actor) o
  `entity_id` (usuario afectado).
- **Inventario histórico real:** existen 20 filas con `user_email` no nulo:
  11 de tipo HTTP y 9 de `Project/test_business_event`. No es solo la fila de
  bootstrap. Esas filas no se pueden corregir ni borrar porque `audit_log` es
  append-only; se conservan como histórico y su email se resuelve por
  `user_id`/`entity_id`.
- Los snapshots de usuarios (`before_data` / `after_data`) solo incluyen `id`,
  `is_active`, `id_rol` y `rol`; nunca el email.
- El middleware redacta `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` y
  headers equivalentes. Los query params con nombres sensibles también se
  redactan.
- Se auditan el login (`/api/user/auth/...`), las mutaciones, los errores
  (`>= 400`) y las lecturas de `/api/audit`.
- `audit_log` es append-only: `UPDATE`, `DELETE` y `TRUNCATE` están bloqueados
  por triggers. El borrado de un usuario con auditoría está bloqueado por la FK
  `ON DELETE RESTRICT`; por eso los usuarios se desactivan.

Para obtener el email del actor:

```sql
SELECT a.id, a.timestamp, a.action, u.email
FROM private.audit_log AS a
LEFT JOIN private."user" AS u ON u.id = a.user_id
WHERE a.entity = 'User'
ORDER BY a.timestamp DESC;
```

> **Política en curso (no verificada todavía):** las denegaciones de negocio
> (`409`/`422`) se registrarán con `record_audit_standalone`,
> `outcome="denied"` y sin PII, para que sobrevivan al rollback de la
> transacción. La suite de integración se migrará a `TEST_DATABASE_URL`
> (base aislada; si no está definida, se omite) y no firmará tokens de
> administradores reales: usará un admin de prueba y `entity="Test"`.

Ver `docs/audit.md` para el esquema completo, la migración y las variables de
auditoría.

## 10. Pruebas

```bash
# Suite local (sin integración)
pytest -q

# Integración aislada (objetivo; migración en curso)
TEST_DATABASE_URL=postgresql+asyncpg://... pytest -q
```

Si `TEST_DATABASE_URL` no está definida, los tests de integración deben
omitirse; no deben escribir en la base compartida.

El detalle de casos, resultados y limitaciones está en
`docs/testing/test-report-2026-11-22-rbac.md`.
