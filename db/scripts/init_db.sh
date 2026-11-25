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
#   SEED_DEMO_DATA  true|false (default true). En false solo aplica el esquema.
#
# No borra datos. Los scripts son idempotentes; en una base existente solo
# completan lo que falte.
# ============================================================================
set -eu

: "${PGHOST:?PGHOST no definido}"
: "${PGUSER:?PGUSER no definido}"
: "${PGDATABASE:?PGDATABASE no definido}"

DB_ROOT="${DB_ROOT:-/db}"
SEED_DEMO_DATA="${SEED_DEMO_DATA:-true}"

echo "Base destino: ${PGUSER}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE}"

for file in "$DB_ROOT"/schema/*.sql; do
    echo "==> $file"
    psql -v ON_ERROR_STOP=1 -X -q -f "$file"
done

case "$(echo "$SEED_DEMO_DATA" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|y)
        for file in "$DB_ROOT"/seeds/*.sql; do
            echo "==> $file"
            psql -v ON_ERROR_STOP=1 -X -q -f "$file"
        done
        echo "Esquema y seeders aplicados correctamente."
        ;;
    false|0|no|n)
        echo "SEED_DEMO_DATA=false: se omite db/seeds (sin datos ficticios)."
        echo "Esquema aplicado correctamente."
        ;;
    *)
        echo "SEED_DEMO_DATA invalido: '$SEED_DEMO_DATA' (use true o false)" >&2
        exit 2
        ;;
esac
