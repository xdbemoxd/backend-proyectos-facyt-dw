# Revisión de seguridad — Auditoría HTTP

**Fecha:** 2026-11-22
**Rama:** `miguel-develop`
**Alcance:** `private.audit_log`, middleware de auditoría, servicio de auditoría de negocio y configuración de conexión.

## Hallazgos del cambio

### SEC-001 — Headers sensibles expuestos en auditoría
- Severidad: Alta (potencial)
- Ubicación: `src/utils/audit.py`
- Estado: **Corregido**
- Detalle: `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` y similares se guardan como `[REDACTED]`; solo se persiste una lista blanca de headers.

### SEC-002 — Contraseñas/tokens en errores de validación
- Severidad: Alta (potencial)
- Ubicación: `app.py`, `src/utils/audit.py`
- Estado: **Corregido**
- Detalle: el handler de `RequestValidationError` guarda solo `loc`, `msg` y `type`; se excluye `input`. Probado con un payload que contenía un secreto.

### SEC-003 — Auditoría mutable
- Severidad: Alta
- Ubicación: migración `20261122_improve_audit_log.sql`
- Estado: **Corregido**
- Detalle: triggers `BEFORE UPDATE/DELETE` y `BEFORE TRUNCATE`; además se revocan privilegios de escritura a roles públicos cuando existen. Probado con `UPDATE` y `DELETE`.

### SEC-004 — Borrado de usuario alteraba la auditoría
- Severidad: Alta
- Ubicación: `private.audit_log.user_id`
- Estado: **Corregido**
- Detalle: FK cambiada de `ON DELETE SET NULL` a `ON DELETE RESTRICT`; se agregó `user_email` como snapshot. Probado: el borrado de un usuario con auditoría falla.

### SEC-005 — IP falsificable
- Severidad: Media
- Ubicación: `src/utils/audit.py`
- Estado: **Mitigado**
- Detalle: `X-Forwarded-For`/`X-Real-IP` solo se usan con `TRUST_PROXY_HEADERS=true`. Por defecto se usa la IP del peer.

### SEC-006 — Fallo de auditoría tumba la request
- Severidad: Media
- Ubicación: `src/middleware/audit.py`, `src/utils/audit.py`
- Estado: **Mitigado**
- Detalle: la escritura de auditoría captura excepciones y registra un error; la request continúa. Probado con un fallo simulado.

### SEC-007 — Detalle de error interno en la tabla
- Severidad: Baja
- Ubicación: `error_detail`
- Estado: **Aceptado con observación**
- Detalle: el campo es interno y está acotado a 1000 caracteres. No debe exponerse por API sin RBAC. Evitar volcar SQL/parámetros sensibles en excepciones.

### SEC-008 — Email (PII) duplicado en auditoría
- Severidad: Media
- Ubicación: `after_data` / `user_email`
- Estado: **Corregido para nuevos registros**
- Detalle: los nuevos eventos guardan solo `user_id`, `entity_id`, `id_rol`,
  `is_active` y `rol`. El email se resuelve desde `private."user"`. La fila
  histórica de bootstrap se conserva por inmutabilidad y aún contiene el email
  en su snapshot.

### SEC-009 — Crecimiento indefinido de la tabla
- Severidad: Media
- Ubicación: `private.audit_log`
- Estado: **Pendiente**
- Detalle: definir retención/particionado antes de producción.

### SEC-010 — La app usa el rol propietario de la BD
- Severidad: Media
- Ubicación: `DATABASE_URL`
- Estado: **Pendiente**
- Detalle: el trigger protege la inmutabilidad, pero se recomienda un rol de aplicación con solo `INSERT` sobre `audit_log` y sin permisos de DDL.

## Hallazgos preexistentes (fuera del alcance de este cambio)

| ID | Severidad | Hallazgo | Estado |
|---|---|---|---|
| SEC-101 | Crítica | No existe RBAC ni `get_current_user`; ningún endpoint valida roles | Pendiente |
| SEC-102 | Crítica | `audit_log` no tenía escritura; ahora el middleware cubre HTTP, pero faltan eventos de negocio en los servicios | Parcial |
| SEC-103 | Alta | JWT sin revocación y rol embebido obsoleto | Pendiente |
| SEC-104 | Alta | `.dockerignore` ausente; `.env` podría copiarse a la imagen | Pendiente |
| SEC-105 | Alta | `/health/2` expone `str(e)` de la BD | Pendiente |
| SEC-106 | Alta | El primer login con Google puede reactivar una cuenta desactivada si su perfil está incompleto (`complete_user_profile` fuerza `is_active=True`) | Pendiente |

## Veredicto

**Aprobado con observaciones** para el cambio de auditoría. Los controles
críticos de este alcance (redacción, inmutabilidad, integridad referencial y
resiliencia) están implementados y probados. Los hallazgos preexistentes
(SEC-101, SEC-103, SEC-104, SEC-105) siguen bloqueando una entrega a producción.
