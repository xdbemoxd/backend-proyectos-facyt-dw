# Política de retención y PII de auditoría

**Sistema:** Control de Proyectos y Recursos — Facultad de Ciencias
**Tabla:** `private.audit_log`
**Última revisión:** 2026-11-23

## 1. Principio

`audit_log` es una bitácora **append-only** (RN-8/RNF-5). No se edita ni se
borra durante la operación normal. La retención se gestiona por **archivado y
revisión**, no por `UPDATE`/`DELETE` directo.

## 2. Períodos de retención

| Tipo de registro | Retención operativa | Destino posterior |
|---|---|---|
| Peticiones HTTP (`entity = HTTP`) | 180 días | Archivo comprimido + hash SHA-256 |
| Eventos de negocio (`User`, `Project`, `Inventory`, etc.) | 5 años | Archivo de cumplimiento |
| Eventos de autenticación/denegaciones | 5 años | Archivo de cumplimiento |
| Incidentes de seguridad | 7 años | Evidencia legal / cadena de custodia |

`AUDIT_RETENTION_DAYS` (default `365`) define el umbral usado por las
herramientas de revisión; no autoriza borrados automáticos.

## 3. PII

- Los eventos nuevos **no guardan email**; el actor se resuelve con
  `user_id`/`entity_id`.
- Quedan filas históricas con email (ventana previa a la política) y el
  snapshot de bootstrap. Se consideran **legal hold** y no se modifican.
- No se deben añadir nuevos campos de PII a `audit_log`.
- Los headers sensibles (`Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`)
  se guardan como `[REDACTED]`.

## 4. Proceso de revisión

1. **Mensual:** revisar crecimiento, índices y alertas de stock de auditoría.
2. **Trimestral:** identificar filas fuera de la retención operativa y
   exportarlas a almacenamiento frío con hash.
3. **Anual:** revisar la política con el responsable de seguridad.
4. La exportación no borra la tabla; el borrado solo procede por orden legal o
   incidente, con doble aprobación (Administrador + Seguridad).

## 5. Borrado excepcional (break-glass)

Si una autoridad competente exige suprimir datos:

1. Registrar la solicitud y el alcance.
2. Aprobar por dos responsables.
3. Crear un respaldo firmado/hash del estado actual.
4. Desactivar temporalmente el trigger en una ventana controlada y ejecutar el
   borrado mínimo necesario.
5. Recrear el trigger, registrar el incidente y conservar la evidencia de la
   autorización.

Este procedimiento **no** forma parte de la operación normal y no debe
automatizarse.

## 6. Controles técnicos relacionados

- Triggers `trg_audit_log_append_only` y `trg_audit_log_no_truncate`.
- FK `audit_log_user_id_fkey` con `ON DELETE RESTRICT`.
- Owner de la tabla: `audit_owner`; el rol de la app solo tiene
  `SELECT`/`INSERT`.
- Redacción de headers y query params sensibles.
- Índices en `timestamp`, `user_id`, `entity`, `status_code`, `request_id`,
  `action` y `outcome`.
