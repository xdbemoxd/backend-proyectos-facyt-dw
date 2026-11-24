#!/bin/sh
# ============================================================================
# db/scripts/init_db.sh
# Aplica esquema + seeders a una base PostgreSQL vacia.
#
# Lo usa el servicio `db-init` de docker-compose.yml. Tambien se puede usar
# fuera de Docker si se monta/copia el directorio db en /db.
#
# Variables (las pone docker compose o el entorno):
#   PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
#
# No borra datos. Los scripts son idempotentes; en una base existente solo
# completan lo que falte.
# ============================================================================
set -eu

: "${PGHOST:?PGHOST no definido}"
: "${PGUSER:?PGUSER no definido}"
: "${PGDATABASE:?PGDATABASE no definido}"

DB_ROOT="${DB_ROOT:-/db}"

echo "Base destino: ${PGUSER}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE}"

for file in "$DB_ROOT"/schema/*.sql; do
    echo "==> $file"
    psql -v ON_ERROR_STOP=1 -X -q -f "$file"
done

for file in "$DB_ROOT"/seeds/*.sql; do
    echo "==> $file"
    psql -v ON_ERROR_STOP=1 -X -q -f "$file"
done

echo "Esquema y seeders aplicados correctamente."
