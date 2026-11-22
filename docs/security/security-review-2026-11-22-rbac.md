# Revisión de seguridad — Autenticación y RBAC

**Fecha del informe:** 2026-11-22
**Rama:** `miguel-develop`
**Rol:** Agente Analista de Ciberseguridad (AGENTS.md §2)
**Snapshot verificado:** 2026-11-21 21:40 (hora local), con hashes al final (§9).
Otro agente modificó `app.py`, `src/router/user.py`, `src/service/user.py`,
`src/schema/user.py`, `src/utils/auth.py` y `tests/test_auth_integration.py`
entre las 21:38 y las 21:39, mientras se hacía esta revisión. Varios hallazgos
de la primera pasada quedaron **corregidos en vuelo** (§3); el resto se
re-verificó contra el estado anterior.

**Alcance:**

- `src/utils/auth.py` (`get_current_user`, `require_roles`)
- `src/utils/roles.py`, `src/utils/permissions.py`
- `src/service/user.py` (CRUD administrado de usuarios)
- `src/service/audit.py`, `src/router/audit.py`, `src/schema/audit.py`
- `src/router/user.py`, `src/schema/user.py`, `src/controller/user.py`
- `src/config/rate_limit.py`, `app.py`
- `tests/test_auth_integration.py`, `tests/test_permissions.py`

**Método:** revisión estática del diff y de los archivos nuevos, lectura del
reporte previo (`docs/security/security-review-2026-11-22.md`), del reporte de
testing y de `docs/auth.md`, y consultas **de solo lectura** a la base
configurada en el entorno local para verificar triggers, FK, privilegios y
agregados. **No se modificó código, tests, configuración ni otros documentos.
No se ejecutaron ataques, mutaciones ni borrados.** No se imprimieron secretos
ni PII.

---

## 1. Resumen ejecutivo

| Severidad | Abiertos |
|---|---:|
| Crítica | 1 |
| Alta | 2 |
| Media | 4 |
| Baja | 9 |
| **Total abiertos** | **16** |
| Corregidos durante la revisión | 4 + 1 parcial |

**Veredicto: BLOQUEADO.**

El modelo de autorización es correcto: el rol se relee de la base en cada
request, el claim `rol` se ignora, el usuario inactivo se rechaza, los
endpoints administrativos exigen `require_roles(ADMIN_ROLE)` y los schemas de
entrada prohíben campos extra. Los bloqueos son de **infraestructura**
(secretos en la imagen Docker), **exposición de errores** (`/health/2`) y
**aislamiento de pruebas** (la suite escribe en la base compartida y deja
eventos de negocio y actores reales en una bitácora inmutable). Según el gate
de AGENTS.md §4, con Críticos/Altos abiertos no hay entrega.

---

## 2. Hallazgos abiertos

### SEC-201 — `SECRET_KEY` y credenciales de BD quedan dentro de la imagen Docker
- **Severidad:** Crítica
- **Categoría:** OWASP A02/A05 · CWE-798, CWE-522
- **Ubicación:** `dockerfile:38` (`COPY --chown=appuser:appuser . .`); no existe `.dockerignore`
- **Descripción:** el `Dockerfile` copia todo el contexto de build, incluido
  `.env` (con `DATABASE_URL`, `SECRET_KEY`, `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`, `TOKEN_CRON`), además de `.venv/`, `.git/`, `tests/`
  y `docs/`. `Test-Path .dockerignore` → `False`.
- **Impacto:** quien obtenga la imagen (registry, caché de CI, backup, capa en
  disco) obtiene la clave de firma del JWT y las credenciales de la base. Con
  `SECRET_KEY` puede **firmar un JWT para el `sub` de cualquier administrador**
  y saltarse todo el RBAC; con `DATABASE_URL` puede leer y reescribir la base
  (incluidos los triggers, ver SEC-206). Con el RBAC nuevo, la fuga de la clave
  pasa de “riesgo alto” a **bypass total del control de acceso**.
- **Reproducción:** `docker build -t x .` y luego
  `docker run --rm x cat /app/.env`. No se ejecutó.
- **Remediación:** crear `.dockerignore` (`.env`, `.env.*`, `.git`, `.venv`,
  `__pycache__`, `tests`, `docs`, `*.md`), inyectar secretos en runtime y
  **rotar `SECRET_KEY`, la contraseña de BD y `GOOGLE_CLIENT_SECRET`** si la
  imagen ya se publicó. Añadir `.env.example` sin valores.
- **Estado:** Abierto (preexistente SEC-104; se eleva a Crítica por el contexto
  de RBAC).

### SEC-202 — `/health/2` devuelve `str(e)` de la base de datos
- **Severidad:** Alta
- **Categoría:** OWASP A05 · CWE-209
- **Ubicación:** `src/router/health.py:24-29`
- **Descripción:** endpoint **público y sin autenticación** que responde
  `detail=f"Database connection failed: {str(e)}"`.
- **Impacto:** un atacante sin credenciales obtiene host, puerto, usuario, base
  y mensajes del driver. Además `/health` está excluido de auditoría
  (`src/utils/audit.py:66-72`), así que los fallos no dejan rastro.
- **Reproducción:** `curl https://<host>/health/2` con la BD inaccesible.
  No se ejecutó (no se manipuló la BD).
- **Remediación:** responder `{"status":"error"}` con `500` genérico y registrar
  el detalle solo en logs internos; o mover el chequeo a un endpoint protegido.
- **Estado:** Abierto (preexistente SEC-105, sin corregir).

### SEC-203 — Las pruebas de integración escriben en la BD compartida y contaminan la auditoría inmutable
- **Severidad:** Alta
- **Categoría:** Integridad de evidencia · CWE-1188 (falta de aislamiento)
- **Ubicación:** `tests/test_auth_integration.py:17-24, 42-53, 72-74, 77-99`;
  `tests/test_audit_integration.py:164-216`
- **Descripción:** los tests se activan con `RUN_AUDIT_INTEGRATION=1` y usan
  `AsyncSessionLocal` del `.env` real, sin verificar el destino. `get_admin()`
  toma el administrador activo real y `admin_token`/`token_for()` **firman un
  JWT de administrador real** con el `SECRET_KEY` local. Los tests crean
  usuarios (incluidos administradores de prueba), los desactivan y escriben
  eventos de negocio con `entity="Project"` y `user_id=admin.id`.
- **Impacto:**
  1. **Falsa atribución**: la BD contiene 9 filas `Project/test_business_event`
     con `user_id` del admin real y email, por acciones que nunca ocurrieron.
     En un sistema cuya RN-8 es auditoría inmutable, esto invalida la evidencia.
  2. **Datos residuales**: 29 usuarios `rbac-*@example.com` (31 inactivos de 33)
     y 263 filas de auditoría del mismo día, sin limpieza posible (append-only).
  3. **Credenciales reales en la suite**: se emiten JWTs válidos de 8 h para un
     administrador real; si un artefacto de CI los captura, son credenciales.
  4. La nueva prueba de rate limit (`tests/test_auth_integration.py:619-636`)
     añade 11 requests de login a la misma base, cada una con fila de auditoría.
  5. Contradice AGENTS.md §4/T2 (“base de datos de prueba aislada, nunca la de
     desarrollo/producción”) y el propio reporte de testing §6.1.
- **Reproducción:** `RUN_AUDIT_INTEGRATION=1 pytest -q` con el `.env`
  compartido; luego `SELECT count(*) FROM private."user" WHERE email LIKE 'rbac-%@example.com'`
  → 29 y `SELECT count(*) FROM private.audit_log WHERE action LIKE 'test_%'` → 19.
  (Consultas de solo lectura ya ejecutadas.)
- **Remediación:**
  1. `TEST_DATABASE_URL`/esquema efímero y un guard que aborte si el nombre no
     parece de pruebas.
  2. Fixtures con su propio admin aleatorio; **no** reutilizar ni firmar tokens
     de administradores reales.
  3. PostgreSQL de test con `docker compose`/`testcontainers`; limpieza por
     `DROP SCHEMA` al final.
  4. No escribir `entity` de negocio (`Project`) desde tests; usar
     `entity="Test"` y `user_id=None` si se prueba el helper.
  5. Documentar las filas `test_*` y los 29 usuarios ya existentes para que no
     se confundan con actividad real.
- **Estado:** Abierto.

### SEC-204 — JWT sin `iss`/`aud`, sin revocación y con 8 h de vigencia
- **Severidad:** Media
- **Categoría:** OWASP API2 · CWE-613, CWE-345
- **Ubicación:** `src/utils/other.py:45-51, 54-68`; `src/utils/auth.py:51-56`;
  `docs/auth.md:95-100`
- **Descripción:** el JWT propio solo incluye `sub`, `email`, `rol` y `exp`; no
  hay `iss`, `aud`, `jti`, versión de token, refresh ni logout. Expira en 8 h.
  El rol del claim se ignora (correcto).
- **Impacto:** un token robado sirve 8 h y no se puede revocar; cualquier
  servicio que comparta `SECRET_KEY` puede emitir tokens aceptados. La
  desactivación de la cuenta sí corta el acceso en la siguiente request
  (mitigación parcial verificada), pero no cubre el robo de token de un usuario
  activo.
- **Reproducción:** emitir un token y comprobar que no existe endpoint ni
  estado que lo invalide antes de `exp`.
- **Remediación:** access token corto (10–15 min) + refresh rotativo; claims
  `iss`, `aud`, `jti`; `token_version` en `private."user"` comparada en
  `get_current_user`; logout que incremente la versión; validar `iss`/`aud`.
- **Estado:** Abierto (SEC-103 queda **parcialmente** mitigado: la obsolescencia
  del rol sí está resuelta y probada).

### SEC-205 — Las acciones denegadas no generan evento de negocio en la auditoría
- **Severidad:** Media
- **Categoría:** OWASP A09 · RN-8
- **Ubicación:** `src/service/user.py:186-193, 261-268, 284-310`
  (rechazos antes de `record_audit`); `src/service/user.py:321-330`
- **Descripción:** solo los caminos exitosos llaman a `record_audit`. Los
  rechazos (auto-desactivación, último admin, rol inválido, email duplicado,
  coordinador en endpoint admin) dejan únicamente la fila HTTP del middleware,
  sin `entity`, `entity_id`, `before_data`/`after_data` ni `action` de negocio.
- **Impacto:** no se puede responder “¿quién intentó degradar al último admin y
  a quién?”. Evidencia: 12 respuestas `409`, 20 `403` y 11 `422` sobre
  `/api/user/*`, y **0 eventos de negocio con `outcome != success`**. Incumple
  AGENTS.md §2.3 y el ejemplo `outcome="denied"` de `docs/audit.md`.
- **Reproducción:** como coordinador, `GET /api/user` → `403`; la única fila es
  `HTTP/http_request/denied`, sin el recurso objetivo.
- **Remediación:** en cada rechazo, antes de lanzar la `HTTPException`, llamar
  `record_audit_standalone(..., outcome="denied", entity="User", entity_id=...,
  action="update_user_denied")` con un `reason` acotado y sin PII.
- **Estado:** Abierto.

### SEC-206 — El rol de aplicación es dueño de `audit_log` y puede desactivar los triggers
- **Severidad:** Media
- **Categoría:** CWE-732 (incorrect permission assignment)
- **Ubicación:** `DATABASE_URL`;
  `db/migrations/20261122_improve_audit_log.sql:242-260`
- **Descripción:** verificado en la BD: `current_user = postgres`, dueño de
  `private.audit_log`, RLS desactivado, y `has_table_privilege` →
  `UPDATE`, `DELETE` y `TRUNCATE` = `true`. Los triggers append-only existen y
  funcionan, pero el dueño puede `ALTER TABLE ... DISABLE TRIGGER`.
- **Impacto:** la inmutabilidad depende de que nadie use el rol de la app para
  DDL. Un SQLi, una dependencia comprometida o un operador con acceso a la app
  pueden reescribir la bitácora. `REVOKE ... FROM PUBLIC` no afecta al dueño.
- **Reproducción (no ejecutada):** `ALTER TABLE private.audit_log DISABLE TRIGGER
  trg_audit_log_append_only; DELETE FROM private.audit_log WHERE ...;`
- **Remediación:** rol de aplicación con solo `INSERT` (y `SELECT` si se expone
  `/api/audit`), tabla propiedad de otro rol, sin DDL; migraciones con un rol
  distinto. Considerar `FORCE ROW LEVEL SECURITY`.
- **Estado:** Abierto (preexistente SEC-010, confirmado con evidencia).

### SEC-207 — PII residual: 20 filas de auditoría aún contienen email
- **Severidad:** Media
- **Categoría:** Privacidad / minimización de datos
- **Ubicación:** `private.audit_log.user_email`;
  `src/utils/audit.py:245-266, 305-319`
- **Descripción:** el código nuevo escribe `user_email = NULL`; verificado que
  los 66 eventos `User/create_user|update_user` no tienen email y que solo el
  bootstrap conserva `email` en `after_data`. Pero la tabla contiene **20 filas
  con `user_email` no nulo**: 11 `HTTP/http_request` y 9
  `Project/test_business_event`, todas del 2026-11-22, escritas antes de la
  política (y varias por los tests).
- **Impacto:** el reporte previo (SEC-008) afirma que solo la fila de bootstrap
  conserva el email; la realidad es 20 filas. Al ser append-only no se pueden
  corregir sin romper la inmutabilidad. La documentación queda inexacta y el
  derecho de supresión no es aplicable a esos registros.
- **Reproducción:** `SELECT entity, action, count(*) FROM private.audit_log
  WHERE user_email IS NOT NULL GROUP BY 1,2;`
- **Remediación:** documentar el inventario real y su ventana temporal; definir
  retención/purga legal (SEC-009) y no poblar `user_email` desde ningún helper.
  No intentar `UPDATE` (el trigger lo impide).
- **Estado:** Aceptado con observación (histórico inmutable); requiere corregir
  la documentación y cerrar el plan de retención.

### SEC-208 — Rate limiting residual: clave por IP del proxy y `LOGIN_RATE_LIMIT` sin documentar
- **Severidad:** Baja
- **Categoría:** OWASP API4 · CWE-770
- **Ubicación:** `src/config/rate_limit.py:17-25`; `app.py:43-44`;
  `src/router/user.py:25-26`; `docs/auth.md:368`
- **Descripción:** ya existe un límite específico de login
  (`LOGIN_RATE_LIMIT`, 10/min) además del global (`100/minute`). Pero la clave
  sigue siendo `get_remote_address` (IP del peer): detrás de un proxy todos los
  clientes comparten bucket. `LOGIN_RATE_LIMIT` no aparece en
  `docs/auth.md` §8 ni en `.env`. Cada `429` escribe una fila de auditoría.
- **Impacto:** el login es limitado solo por IP (sin dimensión por email ni por
  usuario); detrás de un proxy el límite puede bloquear a todos o ser ineficaz
  por cliente. La amplificación de escritura en `audit_log` (sin retención)
  sigue vigente.
- **Reproducción:** superar 10 intentos/min desde una IP → `429`
  (test `test_login_rate_limit_returns_429`); repetir desde dos clientes tras
  el mismo proxy.
- **Remediación:** clave combinada IP+email/usuario, documentar
  `LOGIN_RATE_LIMIT`, alinear `TRUST_PROXY_HEADERS` con el proxy real y no
  auditar cada `429` (o muestrear).
- **Estado:** Abierto (residual; el hallazgo original de “login sin límite
  propio” está corregido).

### SEC-209 — El JWT se entrega en el cuerpo y no hay cookie `HttpOnly` ni `no-store`
- **Severidad:** Baja
- **Categoría:** CWE-522 / CWE-539
- **Ubicación:** `src/controller/user.py:104-118`
- **Descripción:** el login devuelve `token` en JSON y no fija
  `Cache-Control: no-store`. El frontend tendrá que almacenarlo (probablemente
  en `localStorage`), lo que AGENTS.md §3.2 desaconseja.
- **Impacto:** XSS en el frontend → robo de token con 8 h de validez, sin
  revocación (SEC-204). Cachés/proxies podrían retener la respuesta.
- **Remediación:** cookie `HttpOnly; Secure; SameSite=Strict` + CSRF, o token
  en memoria con refresh; añadir `Cache-Control: no-store` a las respuestas de
  auth.
- **Estado:** Abierto (decisión pendiente; el frontend no existe aún).

### SEC-210 — Cuenta inactiva responde `403` en vez de `401`
- **Severidad:** Baja
- **Categoría:** CWE-204 (observable response discrepancy)
- **Ubicación:** `src/utils/auth.py:73-74`
- **Descripción:** un token válido de una cuenta desactivada devuelve `403`; un
  token inválido devuelve `401`.
- **Impacto:** permite al portador distinguir “token válido de cuenta
  desactivada” de “token inválido” y complica clientes que reintentan auth solo
  con `401`. Es un oráculo menor (requiere tener el token).
- **Remediación:** devolver `401` genérico para cuenta inactiva/inexistente/rol
  inválido y reservar `403` para autorización por rol.
- **Estado:** Aceptado con observación (documentado en `docs/auth.md` §7).

### SEC-211 — `422` refleja el `input` original en el cuerpo de respuesta
- **Severidad:** Baja
- **Categoría:** CWE-209
- **Ubicación:** `app.py:83-97`; handler por defecto de FastAPI
- **Descripción:** la auditoría guarda solo `loc/msg/type`, pero la **respuesta**
  usa `request_validation_exception_handler`, que devuelve `exc.errors()`
  incluyendo `input` y `ctx`.
- **Impacto:** el cuerpo del error devuelve al cliente los valores enviados
  (p. ej. un `id_token` mal tipado). No hay fuga a terceros, pero facilita
  reflexión en logs/proxies y contradice `docs/auth.md` §7.
- **Remediación:** handler propio que responda solo `loc`, `msg` y `type`.
- **Estado:** Abierto.

### SEC-212 — `X-Forwarded-For` se guarda crudo aunque no se confíe en él
- **Severidad:** Baja
- **Categoría:** CWE-348 (use of less trusted source)
- **Ubicación:** `src/utils/audit.py:36-49, 111-120, 142-165`
- **Descripción:** `get_client_ip` usa la IP del peer por defecto (correcto),
  pero `sanitize_headers` persiste `x-forwarded-for`/`x-real-ip` tal como los
  envía el cliente (hasta 1000 caracteres).
- **Impacto:** un investigador puede confiar en un valor falsificado; además se
  permite inyectar cadenas arbitrarias en el JSONB de auditoría (acotadas).
  Evidencia actual: 0 filas con `x-forwarded-for` (nadie lo envió aún).
- **Remediación:** persistir esos headers solo con `TRUST_PROXY_HEADERS=true`, o
  etiquetarlos como `[UNTRUSTED]`; validar formato de IP.
- **Estado:** Abierto.

### SEC-213 — `/api/audit` expone `error_detail`, headers, IP y email histórico
- **Severidad:** Baja
- **Categoría:** CWE-200
- **Ubicación:** `src/schema/audit.py:8-31`; `src/router/audit.py:17-46`
- **Descripción:** el endpoint es solo para Administrador (correcto), pero
  devuelve `error_detail`, `request_headers`, `ip_address`, `user_agent` y
  `user_email` histórico.
- **Impacto:** el administrador es un rol de confianza, pero `error_detail`
  puede contener detalles internos. En los datos actuales no se encontraron
  patrones de SQL/DSN (6 filas 5xx, longitud 16), pero el riesgo persiste.
- **Remediación:** no devolver `error_detail` por defecto (o solo con un flag
  explícito); resolver el email con `JOIN` a `private."user"` en lugar de
  exponer la columna legada; considerar enmascarar IPs.
- **Estado:** Aceptado con observación.

### SEC-214 — Un fallo de auditoría de negocio tumba la mutación y no deja evento del intento
- **Severidad:** Baja
- **Categoría:** CWE-754
- **Ubicación:** `src/service/user.py:206-244, 321-343`;
  `src/utils/audit.py:282-322`
- **Descripción:** `record_audit` hace `flush()` dentro de la transacción y se
  commitea junto con la mutación. Si el `INSERT` de auditoría falla (tamaño,
  FK, disponibilidad), la operación completa falla con `500`; el rollback
  elimina también el evento, quedando solo la fila HTTP `500`.
- **Impacto:** disponibilidad acoplada a la auditoría y pérdida del evento de
  negocio del intento fallido (relacionado con SEC-205).
- **Remediación:** decidir explícitamente la política: si la auditoría es
  obligatoria, mantener el fallo (fail-closed) pero registrar el intento con
  `record_audit_standalone`; si no, desacoplar con cola/outbox.
- **Estado:** Aceptado con observación.

### SEC-215 — Logging con f-strings de excepciones (queda en el controller)
- **Severidad:** Baja
- **Categoría:** CWE-532 (information exposure through log files)
- **Ubicación:** `src/controller/user.py:55-58, 69-71`;
  `src/utils/other.py:58, 64`
- **Descripción:** `logger.error(f"Exception: {e}")`,
  `logger.warning(f"id_token invalido: {e}")` y
  `logger.error(f"JWTError: {e}")` pueden volcar mensajes del driver o del
  verificador. El servicio de usuarios ya fue corregido
  (`src/service/user.py:227-243, 332-343`).
- **Impacto:** los logs de aplicación pueden contener datos internos o
  fragmentos de credenciales; AGENTS.md §2.3 lo prohíbe.
- **Remediación:** `logger.error("...", exc_info=True)` solo para errores
  internos y `type(exc).__name__` en rutas de auth; nunca el token.
- **Estado:** Abierto parcialmente (servicio corregido; controller y `other.py`
  pendientes).

### SEC-216 — `UserCreate.is_active` vale `true` por defecto
- **Severidad:** Baja
- **Categoría:** CWE-1188 / política de activación
- **Ubicación:** `src/schema/user.py:50-61`
- **Descripción:** el modelo de datos define `is_active` por defecto `False` y
  la política es que un administrador activa cuentas; el schema las deja
  activas salvo que se envíe `false`.
- **Impacto:** si el admin comete un error de tipeo en el email, la cuenta queda
  habilitada para el dueño de esa cuenta de Google sin una segunda
  confirmación.
- **Remediación:** `is_active: bool = False` y activación explícita, o mantener
  el default actual y documentarlo como decisión de negocio aceptada.
- **Estado:** Aceptado con observación.

---

## 3. Hallazgos corregidos durante la revisión

Se detectaron en la primera pasada y el desarrollador los corrigió a las
21:38–21:39. Verificados **por lectura** contra el estado actual; no se
ejecutaron pruebas de integración (escriben en la BD compartida, SEC-203).

### SEC-217 — Carrera en la regla del último administrador — **Corregido**
- **Ubicación nueva:** `src/service/user.py:90-114` (`SELECT ... FOR UPDATE`,
  líneas 110-111) y `:297-310`.
- **Verificación:** la decisión de “último admin” ahora bloquea las filas de
  administradores activos antes de contar y mutar. Dos degradaciones
  concurrentes se serializan: la segunda re-evalúa el `WHERE` tras liberar el
  lock (READ COMMITTED) y ve un solo admin → `409`.
- **Residual:** no hay **prueba de concurrencia real**; el test de API
  (`tests/test_auth_integration.py:452-489`) sustituye `_count_active_admins`
  por un stub, por lo que no ejercita el `FOR UPDATE`. Añadir una prueba con
  dos sesiones/transacciones concurrentes.
- **Estado:** Corregido con observación de prueba.

### SEC-218 — `{"is_active": null}` desactivaba al usuario — **Corregido**
- **Ubicación nueva:** `src/service/user.py:261-268`.
- **Verificación:** `id_rol`/`is_active` con `null` explícito ahora devuelven
  `422`; prueba `test_empty_or_null_updates_are_rejected`
  (`tests/test_auth_integration.py:406-418`).
- **Residual:** la columna sigue siendo `boolean NULL` en la BD; añadir
  `NOT NULL` para blindar el invariante.
- **Estado:** Corregido.

### SEC-219 — `verify_token_access_top` (validación solo de firma) — **Corregido**
- **Ubicación:** eliminado de `src/utils/auth.py`; ya no hay referencias en el
  árbol. Se eliminó el camino que permitía autenticar sin consultar
  `is_active`/rol en la BD.
- **Estado:** Corregido.

### SEC-220 — Comodines `%`/`_` en `search` — **Corregido**
- **Ubicación nueva:** `src/service/user.py:52-61, 127-135`
  (`_like_pattern` + `escape="\\"`).
- **Verificación:** prueba `test_list_users_filters_and_pagination`
  (`tests/test_auth_integration.py:539-546`) exige que `search=%` devuelva 0.
- **Estado:** Corregido.

### SEC-221 — Cobertura de pruebas negativas de auth — **Corregido parcialmente**
- **Ubicación:** `tests/test_auth_integration.py:328-636`.
- **Verificación:** se añadieron casos de `sub` inválido/ausente/inexistente,
  token expirado, firma alterada, claim `rol` no confiable, `404`, PATCH vacío y
  con `null`, rol inexistente, auto-cambio de rol, último admin por API,
  mass assignment, deriva de `ALLOWED_ROLES` y `429` en login.
- **Residual:** faltan concurrencia real (SEC-217), CORS permitido/denegado,
  Aprobador como actor y la matriz completa de roles.
- **Estado:** Corregido parcialmente.

---

## 4. Controles verificados que sí funcionan

| Control | Evidencia |
|---|---|
| Rol tomado de la BD, no del claim `rol` | `src/utils/auth.py:63-86`; test `test_role_claim_in_token_is_not_trusted` |
| Usuario inactivo rechazado en cada request | `src/utils/auth.py:73-74`; test `test_inactive_user_token_is_rejected` |
| Token ausente/malformado/esquema distinto → `401` | test `test_me_requires_valid_token` |
| `sub` no UUID, ausente o inexistente → `401` | test `test_tokens_with_invalid_subject_are_rejected` |
| Token expirado y firma alterada → `401` | test `test_expired_and_wrong_signature_tokens_are_rejected` |
| Algoritmo fijado (`HS256`), sin confusión `alg=none`/RS256 | `src/utils/other.py:56` |
| Endpoints admin protegidos por dependencia reutilizable | `src/router/user.py:46,54,80,91,102`; `src/router/audit.py:19` |
| Coordinador bloqueado en `/api/user`, `/api/user/roles`, `/api/audit` | test `test_coordinator_cannot_access_admin_endpoints` |
| Auto-desactivación y auto-cambio de rol bloqueados | `src/service/user.py:284-295`; tests `test_self_deactivation_is_blocked`, `test_admin_cannot_change_own_role` |
| Último admin protegido (secuencial y con lock) | `src/utils/permissions.py:19-30`; `src/service/user.py:90-114, 297-310`; 9 tests unitarios + API |
| Solo los 3 roles de RN-1 asignables | `src/service/user.py:80-87`; BD: 3 filas en `private.rol` con nombres idénticos a `src/utils/roles.py` |
| Mass assignment bloqueado (`extra="forbid"`) | `src/schema/user.py:23, 53, 65`; test `test_mass_assignment_is_rejected` |
| `null` explícito en campos no anulables → `422` | `src/service/user.py:261-268`; test dedicado |
| Sin SQLi en filtros/listados | ORM parametrizado; comodines escapados |
| Email normalizado y sin duplicados (incluye carrera) | `src/service/user.py:184-193, 218-233`; test `409` |
| Redacción de headers sensibles | BD: 152 filas con `authorization`, **0** sin redactar; 13 con `cookie`, **0** sin redactar; 0 `set-cookie` |
| `422` no guarda `input` en auditoría | `app.py:83-97`; test `test_validation_error_does_not_store_input` |
| PII fuera de eventos nuevos | BD: 66 eventos `User` sin `user_email`; snapshots solo `id/is_active/id_rol/rol`; 1 snapshot histórico de bootstrap |
| Inmutabilidad append-only | BD: triggers `trg_audit_log_append_only` y `trg_audit_log_no_truncate` |
| FK `ON DELETE RESTRICT` | BD: `audit_log_user_id_fkey` con `confdeltype='r'` |
| CORS sin `*` con credenciales | `app.py:46-69`: orígenes explícitos |
| OpenAPI/docs deshabilitados en producción | `app.py:32-38` |
| Rate limit de login | `src/config/rate_limit.py:25`; `src/router/user.py:26`; test `429` |
| Token de cron en tiempo constante y sin `print` | `src/utils/auth.py:109-120` |
| `.env` no versionado | `.gitignore:153`; `git check-ignore` confirma |
| `requirements.txt` UTF-8, completo (`uuid6`, `google-auth`, `email-validator`) | verificado; `pip check` OK según el reporte de testing |

**SEC-106 (preexistente):** corregido. `complete_user_profile` ya no fuerza
`is_active=True` (`src/controller/user.py:43-58`) y el login rechaza cuentas
inactivas antes de completar el perfil (`:89-101`). Verificado por inspección;
sin prueba E2E con Google.

---

## 5. Estado de hallazgos preexistentes

| ID | Severidad | Hallazgo | Estado |
|---|---|---|---|
| SEC-101 | Crítica | No existía RBAC ni `get_current_user` | **Corregido** |
| SEC-102 | Crítica | `audit_log` sin escritura / sin eventos de negocio | **Parcial**: middleware + usuarios; faltan denegaciones (SEC-205) y el resto de dominios |
| SEC-103 | Alta | JWT sin revocación y rol obsoleto | **Parcial**: rol resuelto; revocación/`iss`/`aud` abiertos (SEC-204) |
| SEC-104 | Alta | `.dockerignore` ausente; `.env` en la imagen | **Abierto**, elevado a Crítica (SEC-201) |
| SEC-105 | Alta | `/health/2` expone `str(e)` | **Abierto** (SEC-202) |
| SEC-106 | Alta | El primer login reactivaba cuentas desactivadas | **Corregido** |
| SEC-008 | Media | Email (PII) en auditoría | **Corregido para nuevos**; 20 filas históricas (SEC-207) |
| SEC-009 | Media | Crecimiento indefinido de `audit_log` | **Abierto**: 263 filas de un día de pruebas, sin retención/partición |
| SEC-010 | Media | Rol de app dueño de la BD | **Abierto**, confirmado (SEC-206) |

---

## 6. Requisitos que no pude verificar

- **IDOR / alcance por proyecto:** no existen endpoints de proyectos,
  inventario ni solicitudes. `can_access_project` está implementado y probado
  como función pura, pero **no se aplica en ningún router** todavía.
- **Login con Google de punta a punta:** requiere un `id_token` real. Se
  verificó por lectura que se validan firma, `exp`, `iss`, `aud`,
  `email_verified` y `hd`.
- **Fuerza de `SECRET_KEY` y su historial de rotación:** no se inspeccionó el
  valor (prohibido imprimir secretos).
- **Imágenes Docker publicadas:** no hay evidencia en el repositorio; si
  existen, SEC-201 exige rotación.
- **Entornos distintos al local:** solo se verificó la base del `.env` local.
- **Retención/particionado** (SEC-009): no hay documento ni script.
- **Frontend:** no existe; no se puede evaluar almacenamiento del token, XSS,
  CSP ni CSRF.
- **Rate limit detrás del proxy real:** depende de la topología de despliegue.
- **Cobertura medida:** el reporte de testing la declara pendiente; no se usó
  `--cov-fail-under=80` como evidencia.

---

## 7. Handoff Seguridad → Desarrollo

```markdown
## Resultado de revisión
- Hallazgos Críticos: 1 (SEC-201)
- Hallazgos Altos: 2 (SEC-202, SEC-203)
- Hallazgos Medios: 4 (SEC-204, SEC-205, SEC-206, SEC-207)
- Hallazgos Bajos: 9 (SEC-208..SEC-216)
- Corregidos en vuelo: SEC-217, SEC-218, SEC-219, SEC-220 (verificados),
  SEC-221 (parcial)
- Requisitos no verificados: IDOR/alcance por proyecto, login Google E2E,
  fuerza/rotación de SECRET_KEY, entornos distintos, retención, frontend,
  rate limit tras proxy, cobertura medida
- Veredicto: BLOQUEADO
```

**Condición mínima para desbloquear:**

1. SEC-201: `.dockerignore` + secretos fuera de la imagen + rotación si aplica.
2. SEC-202: `/health/2` sin `str(e)`.
3. SEC-203: base de pruebas aislada, sin admin real, sin eventos `Project`.
4. SEC-205: auditar las denegaciones (SEC-205) y aprobar un plan escrito para
   SEC-204 (revocación/`iss`/`aud`), SEC-206 (rol de app restringido) y SEC-207
   (retención/PII histórica).
5. SEC-217: añadir la prueba de concurrencia real del último admin.
6. SEC-202/203 en verde y suite de seguridad re-ejecutada; luego esta revisión
   verificará **solo** los hallazgos y el impacto colateral y actualizará su
   estado a `Verificado`.

---

## 8. Evidencia de las consultas de solo lectura

Consultas ejecutadas contra la base configurada (sin escrituras, sin imprimir
credenciales ni PII), en el snapshot indicado:

| Comprobación | Resultado |
|---|---|
| Triggers en `private.audit_log` | `trg_audit_log_append_only`, `trg_audit_log_no_truncate` |
| FK `audit_log_user_id_fkey` | `ON DELETE RESTRICT` |
| `private."user".is_active` | `boolean`, **nullable = YES** (residual SEC-218) |
| Roles en `private.rol` | Exactamente los 3 de RN-1, nombres idénticos a `src/utils/roles.py` |
| Usuarios por rol/estado | Admin activo = 2; aprobadores inactivos = 6; coordinadores inactivos = 24; total 33 (31 inactivos) |
| Usuarios `rbac-%@example.com` | 29 |
| `audit_log` | 263 filas (todas del 2026-11-22); 20 con `user_email`; 19 eventos de negocio sin `request_id`; 1 snapshot con `email` (bootstrap) |
| Eventos de negocio denegados/fallidos | 0 |
| `/api/user/*` por status | 200:37, 201:31, 401:32, 403:20, 409:12, 422:11 |
| Headers sensibles | 152 filas con `authorization`, 0 sin redactar; 13 con `cookie`, 0 sin redactar; 0 `set-cookie`; 0 con `x-forwarded-for` |
| `error_detail` en 5xx | 6 filas, longitud 16; sin patrones `asyncpg`/`postgres`/`SELECT`/`password`/`Traceback` |
| Privilegios del rol de app | dueño de `audit_log`; `UPDATE`/`DELETE`/`TRUNCATE` = `true`; RLS = `false` |
| Suite local | `pytest tests/test_permissions.py tests/test_audit_utils.py tests/test_audit_helpers.py -q` → **36 passed** |

### Hashes del árbol revisado (SHA-256, 12 caracteres)

| Archivo | Hash |
|---|---|
| `app.py` | `B96439957E7F` |
| `src/utils/auth.py` | `F23A97A51EB1` |
| `src/service/user.py` | `39AF078FDD8A` |
| `src/schema/user.py` | `9E3D2C1C3CFD` |
| `src/router/user.py` | `0AE16A35D6FB` |
| `src/router/audit.py` | `A32CF0970BC6` |
| `src/schema/audit.py` | `B8A0CD358329` |
| `src/utils/audit.py` | `3C73FBA39335` |
| `src/service/audit.py` | `B0EAD6A994E2` |
| `src/controller/user.py` | `AFBB21DF65A7` |
| `src/config/rate_limit.py` | `63F9DA4CEE53` |

Si estos hashes ya no coinciden, el árbol cambió después de la revisión y los
hallazgos deben re-verificarse sobre el estado final.

---

## 9. Firma

**Analista de Ciberseguridad:** revisión completada el 2026-11-22.
**Veredicto:** Bloqueado por SEC-201 (Crítica) y SEC-202/SEC-203 (Altas).
**Observación:** el diseño de autenticación/RBAC es sólido y la reacción del
desarrollo a los hallazgos de la primera pasada fue correcta; los bloqueos
restantes son de infraestructura, exposición de errores y aislamiento de
pruebas. No se modificó código en esta revisión.
