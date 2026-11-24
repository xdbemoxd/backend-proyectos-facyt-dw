# SQL legado

La fuente canónica de migraciones es **`db/migrations/`**.

Los archivos de esta carpeta se conservan como historial de la rama que los
introdujo, pero **no deben ejecutarse sobre la base actual**:

- `2026-11-23_add_token_version.sql` está duplicado en
  `db/migrations/20261123_add_token_version.sql`.
- `2026-11-23_audit_log_http_fields.sql` fue reemplazado por
  `db/migrations/20261122_improve_audit_log.sql`. Su `UPDATE` de backfill choca
  con el trigger append-only de `audit_log`.

Para una instalación nueva, ejecutar únicamente los archivos de
`db/migrations/` en orden.
