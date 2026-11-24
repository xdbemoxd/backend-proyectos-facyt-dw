# Pruebas

## Unitarias

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

No requieren base de datos. Las pruebas de integración se marcan como
`integration` y se omiten si `TEST_DATABASE_URL` no está definida.

## Integración (base de pruebas aislada)

Nunca uses la base de `DATABASE_URL` del `.env`: la auditoría es append-only y
las pruebas dejan residuo. Define una base de pruebas dedicada:

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://usuario:password@host:5432/basedatos_test"
# Opcional, si el esquema no es `private`:
$env:TEST_SCHEMA = "private"
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/conftest.py` sustituye `DATABASE_URL` por `TEST_DATABASE_URL` antes de
importar `app` y aborta si ambas apuntan a la misma base. La base de pruebas
debe tener el esquema y los seeders aplicados (los 3 roles de RN-1).

## Actores y residuo

- Cada prueba crea un administrador `test-admin-*@example.com`; **no** se firma
  el JWT de un administrador real.
- Los usuarios creados (`test-rbac-*`, `test-actor-*`) se **desactivan**, no se
  borran: `audit_log` es append-only y la FK es `ON DELETE RESTRICT`.
- Los eventos de auditoría usan `entity="Test"` o `entity="User"` con el actor
  de prueba. El residuo histórico (usuarios inactivos, filas `test_*` y el rol
  `__Test Last Admin__`) queda en la base de pruebas; para limpiarlo, recrea la
  base/schema de pruebas.
- La prueba de concurrencia del último administrador usa el rol aislado
  `__Test Last Admin__` y parchea `ADMIN_ROLE` solo durante ese test, para no
  depender de que la base esté vacía ni tocar administradores del seed.

## Residuo previo

El residuo de corridas anteriores contra la base compartida (usuarios
`rbac-*` inactivos y eventos `Project/test_*` en `audit_log`) **no se
modificó**: la auditoría es append-only. Queda inventariado para que el
analista de seguridad lo distinga de la actividad real; las corridas nuevas
solo escriben en `TEST_DATABASE_URL`.
