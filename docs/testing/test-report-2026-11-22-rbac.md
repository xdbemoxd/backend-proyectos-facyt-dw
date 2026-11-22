# Reporte de testing — Autenticación y RBAC

**Fecha:** 2026-11-22
**Rama:** `miguel-develop`
**Alcance:** login con Google, JWT, dependencias `get_current_user` y
`require_roles`, administración de usuarios, roles, alcance por proyecto y
consulta de auditoría.

## 1. Alcance del reporte

- Login `POST /api/user/auth/google/login` con validación del ID token de
  Google.
- Verificación del JWT propio en endpoints autenticados.
- `GET /api/user/me` con token válido, ausente o inválido.
- RBAC en endpoints administrativos:
  - `GET /api/user/roles`
  - `GET /api/user`
  - `POST /api/user`
  - `GET /api/user/{user_id}`
  - `PATCH /api/user/{user_id}`
  - `GET /api/audit`
- Reglas de administración: email duplicado, rol inválido,
  auto-desactivación, auto-cambio de rol y último administrador activo.
- Rechazo de usuarios inactivos con token vigente.
- Alcance por proyecto con `can_access_project`.
- Minimización de PII en `audit_log`.
- Inmutabilidad de `audit_log` (append-only).

Fuera de alcance: frontend, endpoints de proyectos/inventario/equipos,
reservas, prorrateo y exportaciones. Tampoco se evalúa aquí el porcentaje final
de cobertura de servicios async.

## 2. Trazabilidad

| Requerimiento | Evidencia |
|---|---|
| RF-1.1 — Gestión de usuarios y roles | `tests/test_auth_integration.py` |
| RN-1 — Solo tres roles | `tests/test_auth_integration.py::test_roles_endpoint_returns_three_roles`, `src/utils/roles.py` |
| RNF-5 / RNF-6 / RNF-7 — Control de acceso y protección de datos | `tests/test_auth_integration.py`, `tests/test_permissions.py` |
| RN-8 — Auditoría append-only | `tests/test_audit_integration.py`, `docs/audit.md` |
| Alcance por proyecto (anti-IDOR) | `tests/test_permissions.py`, `src/utils/permissions.py` |

## 3. Comandos y resultados

### 3.1 Verificaciones estáticas

Ejecutadas en esta pasada:

```bash
.\.venv\Scripts\python.exe -m compileall -q src app.py
.\.venv\Scripts\python.exe -c "import app"
.\.venv\Scripts\python.exe -m pip check
```

| Verificación | Resultado |
|---|---|
| Compilación (`compileall`) | OK |
| Importación de la app | OK |
| Dependencias (`pip check`) | Sin conflictos |

### 3.2 Suite local, sin base de datos

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

Resultado:

```text
36 passed, 19 skipped in 2.65s
```

En la corrida local registrada, los 19 tests omitidos son los de integración,
que requerían `RUN_AUDIT_INTEGRATION=1` y una base PostgreSQL accesible. No son
fallos. La política en curso migra ese gate a `TEST_DATABASE_URL`.

Desglose de la parte local:

| Grupo | Comando | Resultado |
|---|---|---|
| Auditoría (unitarias) | `pytest tests/test_audit_utils.py tests/test_audit_helpers.py -q` | **27 passed** |
| Permisos (unitarias) | `pytest tests/test_permissions.py -q` | **9 passed** |
| Total local | `pytest -q` | **36 passed, 19 skipped** |

### 3.3 Suite con integración (corrida histórica)

Comando de la corrida histórica:

```bash
RUN_AUDIT_INTEGRATION=1 pytest -q
```

Resultado histórico con la base compartida:

| Grupo | Tests | Resultado |
|---|---:|---|
| Auditoría (27 unitarias + 10 integración) | 37 | **37 passed** |
| Autenticación / RBAC (integración) | 9 | **9 passed** |
| Permisos (unitarias) | 9 | **9 passed** |
| **Total** | **55** | **55 passed** |

La suma es consistente con la corrida local: 36 tests locales + 19 de
integración = 55.

> **55 passed es una corrida histórica**, anterior al aislamiento por
> `TEST_DATABASE_URL`. No se repitió para no seguir escribiendo en la base
> compartida. La corrida final con base aislada está pendiente.

### 3.4 Política de aislamiento en curso (no verificada)

Estos cambios están en curso; se documentan como política, no como resultado
obtenido:

- Los tests de integración deben usar `TEST_DATABASE_URL` (base aislada). Si no
  está definida, se omiten; no deben escribir en la base compartida.
- No deben firmarse tokens de administradores reales. Se usará un
  administrador de prueba.
- Los eventos de prueba usarán `entity="Test"`, no `entity="Project"`.
- Las denegaciones de negocio (`409`/`422`) se auditarán con
  `record_audit_standalone`, `outcome="denied"` y sin PII.

La suite aún no está migrada por completo. No hay resultado final de esta
política.

## 4. Casos cubiertos

### 4.1 Autenticación y sesión

| Caso | Resultado esperado | Evidencia |
|---|---|---|
| `GET /api/user/me` sin token | `401` | `test_me_requires_valid_token` |
| `GET /api/user/me` con token malformado (`Bearer no-es-un-jwt`) | `401` | `test_me_requires_valid_token` |
| `GET /api/user/me` con esquema distinto (`Basic`) | `401` | `test_me_requires_valid_token` |
| `GET /api/user/me` con JWT válido | `200` y datos del usuario | `test_me_returns_role_from_database` |
| El rol devuelto por `/me` coincide con el rol de la BD | `Administrador del Sistema` | `test_me_returns_role_from_database` |
| Usuario desactivado con token aún vigente | `403` | `test_inactive_user_token_is_rejected` |

### 4.2 RBAC y endpoints administrativos

| Caso | Resultado esperado | Evidencia |
|---|---|---|
| Coordinador en `GET /api/user/me` | `200` | `test_coordinator_cannot_access_admin_endpoints` |
| Coordinador en `GET /api/user` | `403` | `test_coordinator_cannot_access_admin_endpoints` |
| Coordinador en `GET /api/user/roles` | `403` | `test_coordinator_cannot_access_admin_endpoints` |
| Coordinador en `GET /api/audit` | `403` | `test_coordinator_cannot_access_admin_endpoints` |
| Admin crea usuario | `201` | `test_admin_manages_users_and_audits` |
| Admin consulta usuario por ID | `200` | `test_admin_manages_users_and_audits` |
| Admin lista usuarios con `search` | `200`, `total >= 1` | `test_admin_manages_users_and_audits` |
| Admin actualiza `name`/`lastname` | `200` y datos nuevos | `test_admin_manages_users_and_audits` |
| `GET /api/user/roles` devuelve exactamente los tres roles | `200` | `test_roles_endpoint_returns_three_roles` |
| Admin consulta auditoría con filtros | `200`, solo filas filtradas | `test_audit_endpoint_is_admin_only_and_filters` |

### 4.3 Reglas de negocio y validación

| Caso | Resultado esperado | Evidencia |
|---|---|---|
| `POST /api/user` con email ya registrado | `409` | `test_duplicate_email_and_invalid_role` |
| `POST /api/user` con `id_rol` inexistente | `422` | `test_duplicate_email_and_invalid_role` |
| Admin intenta desactivarse a sí mismo | `409` y sigue activo | `test_self_deactivation_is_blocked` |
| Token de usuario desactivado | `403` | `test_inactive_user_token_is_rejected` |
| Admin y Aprobador acceden a cualquier proyecto | `True` | `test_admin_and_approver_access_any_project` |
| Coordinador accede solo a su proyecto | `True` en el propio, `False` en otro | `test_coordinator_only_accesses_own_project` |
| Último admin activo: desactivarlo o degradarlo | Bloqueado | `test_would_remove_last_admin` |
| Hay más de un admin activo: desactivar o degradar a uno | Permitido | `test_would_remove_last_admin` |
| El objetivo no es admin activo | Nunca bloquea por “último admin” | `test_would_remove_last_admin` |

### 4.4 Auditoría y PII

| Caso | Resultado esperado | Evidencia |
|---|---|---|
| Eventos `create_user` y `update_user` quedan en `audit_log` | Al menos 2 filas | `test_admin_manages_users_and_audits` |
| `audit_log.user_email` en eventos nuevos | `NULL` | `test_admin_manages_users_and_audits` |
| Snapshots de usuario sin email | Solo `id`, `is_active`, `id_rol`, `rol` | `test_admin_manages_users_and_audits` |
| `audit_log` bloquea `UPDATE` y `DELETE` | Error de base de datos | `test_audit_log_blocks_update_and_delete` |
| Borrado de usuario con auditoría | Bloqueado por FK `RESTRICT` | `test_user_delete_is_blocked_by_audit_fk` |
| Error `422` no almacena el `input` | Solo `loc`, `msg`, `type` | `test_validation_error_does_not_store_input` |
| Redacción de headers y query params sensibles | `[REDACTED]` | Suite de auditoría (27 unitarias) |

**Inventario histórico real de PII:** existen 20 filas con `user_email` no nulo:
11 HTTP y 9 de `Project/test_business_event`. No es solo la fila de bootstrap.
No se pueden corregir ni borrar por la política append-only de `audit_log`; se
conservan como histórico y el email se resuelve por `user_id`/`entity_id`.

La suite de auditoría se detalla en
`docs/testing/test-report-2026-11-22.md`.

## 5. Cobertura

No se reporta un porcentaje final de cobertura.

- La corrida histórica de 55 tests funcionales quedó en verde con la base
  compartida.
- La medición de cobertura en módulos async con SQLAlchemy presenta un problema
  que está siendo investigado por otro agente.
- No se usa `--cov-fail-under=80` como evidencia de aprobación en este reporte;
  hacerlo sin resolver la medición produciría un resultado engañoso.
- La cobertura de las reglas críticas se evidencia por casos funcionales
  (tablas anteriores), no por un porcentaje.

**Estado del gate de cobertura:** pendiente. No se declara cumplido.

## 6. Limitaciones y datos de prueba

### 6.1 Ejecución

- **Política en curso:** los tests de integración deben usar
  `TEST_DATABASE_URL` (base aislada). Si no está definida, se omiten; no deben
  escribir en la base compartida.
- La corrida histórica de 55 passed usó la base compartida y se conserva solo
  como referencia. La corrida final con aislamiento está pendiente.
- **Política en curso:** no se firmarán tokens de administradores reales; se
  usará un administrador de prueba y `entity="Test"`.
- El login exitoso con Google no se prueba de punta a punta: requiere un ID
  token real de Google. Los tests de RBAC generan el JWT propio directamente
  para el usuario de prueba.
- No hay pruebas E2E de frontend porque el frontend aún no existe.

### 6.2 Datos de prueba

- En la base compartida ya existen usuarios `rbac-*@example.com` y filas
  `test_*` generadas por corridas anteriores.
- Al finalizar, los usuarios se **desactivan**; no se borran. Esto es
  intencional: `audit_log.user_id` usa `ON DELETE RESTRICT` y los usuarios no
  deben eliminarse.
- Los eventos de auditoría generados por las pruebas permanecen en
  `audit_log` porque la tabla es append-only; tampoco se pueden borrar.
- No hay limpieza automática de estos datos. La base compartida acumula usuarios
  inactivos y filas de auditoría entre corridas.
- Como política en curso, a partir de ahora la integración requiere
  `TEST_DATABASE_URL`; no debe reutilizar la base compartida.
- Varias pruebas requieren que exista al menos un administrador activo; el
  fixture toma el más antiguo por `create_at`.

### 6.3 Casos no cubiertos por pruebas de API

Estos comportamientos están implementados, pero no tienen una prueba de
integración dedicada en el alcance actual:

- `409` al intentar cambiar el propio rol (solo se prueba la
  auto-desactivación).
- `409` por último administrador activo invocado desde el endpoint (la regla
  está cubierta unitariamente con `would_remove_last_admin`).
- `404` al consultar o actualizar un usuario inexistente.
- Token expirado, con firma alterada o con `sub` no UUID (solo se prueba el
  token malformado).
- Token con un claim `rol` obsoleto después de un cambio de rol en la BD. El
  código lo ignora porque relee el rol, pero no hay un test específico que
  emita un token con rol viejo y luego cambie el rol.
- Rate limit `429` en login (`LOGIN_RATE_LIMIT` y global `RATE_LIMIT`).
- CORS con orígenes permitidos y no permitidos.
- Matriz completa Aprobador × endpoints administrativos; el Aprobador se usa
  como usuario objetivo del CRUD, no como actor.
- `TRUNCATE` de `audit_log` no se prueba de forma destructiva; el trigger existe
  y se verifica en el esquema.

## 7. Fallos

No se registraron fallos en las corridas reportadas.

- Los 19 tests omitidos en la corrida local son integraciones gated, no fallos.
- El problema de medición de cobertura async es una limitación de herramienta,
  no un test rojo.
- No se eliminaron, omitieron ni relajaron tests para obtener el resultado.

## 8. Evidencia

| Artefacto | Ruta |
|---|---|
| Tests de autenticación y RBAC | `tests/test_auth_integration.py` |
| Tests de permisos y último admin | `tests/test_permissions.py` |
| Tests de auditoría | `tests/test_audit_utils.py`, `tests/test_audit_helpers.py`, `tests/test_audit_integration.py` |
| Documentación de autenticación | `docs/auth.md` |
| Documentación de auditoría | `docs/audit.md` |
| Reporte de auditoría previo | `docs/testing/test-report-2026-11-22.md` |

## 9. Veredicto del desarrollador

| Criterio | Estado |
|---|---|
| Corrida histórica con integración (base compartida) | **55 passed** |
| Corrida final con `TEST_DATABASE_URL` (base aislada) | **Pendiente** |
| Fallos en las corridas reportadas | Ninguno |
| Reglas RBAC principales | Cubiertas |
| Auto-protección del admin | Cubierta (API) |
| Regla del último admin | Cubierta (unitaria); falta prueba de API |
| PII en eventos nuevos | Cubierta |
| PII histórica | 20 filas no modificables (11 HTTP + 9 `Project/test_business_event`) |
| Cobertura final de servicios async | **Pendiente de investigación** |
| Revisión de seguridad | Pendiente de firma del analista |

Este reporte no sustituye la revisión de seguridad ni declara cumplido el gate
de cobertura. Quedan pendientes la corrida final con `TEST_DATABASE_URL`, la
medición de cobertura async y el handoff al analista de ciberseguridad.
