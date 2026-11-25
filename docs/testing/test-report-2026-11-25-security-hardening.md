# Reporte de pruebas — Endurecimiento de seguridad (2026-11-25)

Rama: `miguel-develop` (sin commit ni push).
Alcance: mitigaciones del informe white-box (secretos, compose, auditoría,
exportaciones, migración, autorización de proyectos).

## Comandos y resultados

| Comando | Resultado |
|---|---|
| `python -m compileall -q src app.py db/scripts` | OK |
| `SECRET_KEY=<válida> python -c "import app"` | OK (~1.9–2.0 s) |
| `python -m pip check` | `No broken requirements found` |
| `pytest -q` | **374 passed, 72 skipped** en 234.49 s |
| `pytest` focalizado (secretos, auditoría, proyectos, reportes, migración) | **91 passed** |
| `python db/scripts/verify_schema.py` | **50 comprobaciones OK**, `ROLLBACK confirmado` |

Los 72 omitidos son las pruebas de integración/live que requieren
`TEST_DATABASE_URL` o flags `*_LIVE_TESTS`; no se relajó ni marcó ningún test.

## Migración `20261125_security_hardening.sql`

- Validada con `verify_schema.py` en un esquema temporal
  (`verify_schema_<hex>`) con **ROLLBACK**: no se tocó `private`.
- Comprobado: índices UNIQUE normalizados, 7 triggers nuevos, append-only en
  `audith_project`/`overhead_period`/`overhead_allocation`, duplicados
  rechazados (`23505`), solape de overhead rechazado (`23P01`) y período
  contiguo aceptado.
- Validación adicional del camino real `CREATE INDEX CONCURRENTLY` en un
  esquema temporal en autocommit: 2 índices creados y **válidos**, esquema
  temporal eliminado. No se aplicó al esquema `private` real.

## Rendimiento

| Medición | Resultado |
|---|---|
| Import de `app` (3 ejecuciones) | 1.913 s / 2.011 s / 1.989 s |
| `should_audit_request` (200 000 llamadas) | 0.139 s (~0.70 µs/llamada) |
| Encolado de auditoría (20 000 eventos, worker sin BD) | 0.132 s (~6.6 µs/evento) |

Decisiones para no degradar el camino de respuesta:

- Auditoría HTTP asíncrona por defecto (cola acotada + pool de 2 workers +
  lotes de 50 eventos/commit). No hay espera de BD por request.
- Reintentos con backoff y drenaje acotado en shutdown; si el lote falla, se
  reintenta fila por fila.
- Lecturas sensibles auditadas solo por ruta exacta, sin consultas extra.
- Validación de `SECRET_KEY` una sola vez al importar, nunca por request.
- La migración usa `CONCURRENTLY` para no bloquear escrituras en tablas grandes
  y `lock_timeout` solo alrededor de DDL de triggers.

## Riesgos y limitaciones verificadas

- **Docker no está instalado** en este entorno: no se pudo ejecutar
  `docker compose config`. El YAML se parseó con PyYAML en un directorio
  temporal (sin añadirlo al proyecto): 4 servicios, `db`/`backend` sin `ports`
  y con `expose`, frontend en 3000, y `${VAR:?}` para los secretos obligatorios.
- **`bash`/`ruff` no están instalados**: `init_db.sh` no se validó con `bash -n`
  ni se ejecutó `ruff check`.
- La cola de auditoría es en memoria: un `SIGKILL` puede perder eventos
  pendientes. Bajo saturación extrema se descartan eventos con log de error.
- Un índice creado con `CONCURRENTLY` que falle puede quedar `INVALID`; el
  archivo documenta cómo eliminarlo y reintentar.
- Pendientes explícitos: integración despacho↔solicitud aprobada y rediseño del
  cierre de overhead (no implementados en este cambio).
