# Reporte de pruebas — Reportes XLSX/PDF

- Fecha: 2026-11-24
- Alcance: exportación de gastos, prorrateo y equipos en XLSX/PDF; BFF y UI
  de descarga en el frontend.
- Entorno backend: Python 3.13.14 en `.venv`, PostgreSQL real del `.env`.
- Entorno frontend: Node.js/npm del repositorio, `npm run typecheck` y
  `npm run build`.

## Backend

| Comando | Resultado |
|---|---|
| `python -m compileall -q src app.py` | OK |
| `python -c "import app"` | `import app OK` |
| `pip check` | `No broken requirements found.` |
| `pytest tests/test_report_generation.py tests/test_report_api_contract.py -q` | `29 passed` |
| `REPORTS_LIVE_TESTS=1 pytest tests/test_reports_live.py -q` | `5 passed` |
| `pytest -q` (suite completa) | `334 passed, 71 skipped` |
| `REPORTS_LIVE_TESTS=1 pytest tests/test_report_generation.py tests/test_report_api_contract.py tests/test_reports_live.py -q --cov=src.service.report --cov=src.router.report --cov=src.schema.report` | `34 passed`; cobertura: servicio 90 %, router 96 %, schema 92 %, total 90 % |

Los 71 omitidos son pruebas existentes que requieren `TEST_DATABASE_URL` o
banderas live distintas de `REPORTS_LIVE_TESTS`; no son fallos.

## Frontend

| Comando | Resultado |
|---|---|
| `npm run typecheck` | Sin errores |
| `npm run build` | Compilación correcta; incluye `/api/reports/[type]/[format]` |

## Evidencia funcional

- Los seis archivos se descargaron con tokens reales de Administrador y
  Aprobador; XLSX con firma `PK` y ZIP válido; PDF con `%PDF` y `%%EOF`.
- El XLSX de prorrateo de la base real muestra `Diferencia = 0.00`.
- El coordinador descargó gastos y equipos, y recibió `403` en prorrateo.
- Los códigos de proyecto de los XLSX del coordinador pertenecen solo a sus
  proyectos.
- Cada exportación incrementó en uno el conteo de `audit_log` con
  `entity=Report`, `action=export`.

## Limitaciones

- Las pruebas live solo hacen `GET`; la única escritura es el evento
  append-only de auditoría de la propia exportación.
- La vista previa de equipos en pantalla resume por unidad; el archivo
  descargado detalla una fila por reserva/uso.
- No se ejecutó revisión formal de seguridad ni `docker build` (no se tocó
  Docker).
