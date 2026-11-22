# Reporte de testing — Auditoría HTTP

**Fecha:** 2026-11-22
**Rama:** `miguel-develop`
**Alcance:** migración de `private.audit_log`, middleware HTTP, servicio de auditoría de negocio y configuración de conexión.

## Comandos ejecutados

```bash
python -m compileall -q src app.py
python -c "import app"
pip check
pytest -q
pytest -q --cov=src.utils.audit --cov=src.middleware.audit --cov-report=term --cov-fail-under=80
```

## Resultados

| Verificación | Resultado |
|---|---|
| Compilación | OK |
| Importación de la app | OK |
| Dependencias (`pip check`) | Sin conflictos |
| Suite completa | **37 passed** |
| Cobertura de auditoría | **100 %** (`src/utils/audit.py` y `src/middleware/audit.py`) |

## Casos cubiertos

### Unitarios

- Redacción de `Authorization`, `Cookie`, `X-Api-Key` y headers no permitidos.
- Redacción de query params sensibles (`token`, `password`, `api_key`, `session`).
- Mapeo de estados HTTP a `success` / `denied` / `failed`.
- Serialización JSON de UUID, fechas, `Decimal`, `Enum`, `Path`, dataclasses y Pydantic.
- Obtención de IP con y sin proxy confiable.
- Extracción del actor desde `request.state` y desde el JWT.
- Construcción de la fila de auditoría con metadata HTTP.
- `record_audit` dentro de una transacción y desde token.

### Integración

- `GET /` audita actor, headers redactados, user-agent, status y duración.
- Login inválido audita `401`, `outcome=denied`, `error_code=HTTP_401`.
- `/health` no genera ruido de auditoría.
- Error de validación `422` no almacena el `input` sensible.
- Evento de negocio con `before_data` y `after_data` en JSONB.
- `record_audit_standalone` confirma su propia transacción.
- Una excepción no controlada genera fila `500` y la API sigue respondiendo.
- `UPDATE` y `DELETE` sobre `audit_log` son bloqueados por trigger.
- El borrado de un usuario con auditoría es bloqueado por la FK `RESTRICT`.
- La request responde aunque falle la construcción de la auditoría.

## Limitaciones

- Las pruebas de integración requieren `RUN_AUDIT_INTEGRATION=1` y acceso a la BD.
- No se probó `TRUNCATE` de forma destructiva; el trigger existe y se verifica en el esquema.
- No hay pruebas E2E de frontend porque aún no existe.
