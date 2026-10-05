#!/bin/sh
# Runs once, when the database volume is first created (docker-entrypoint-initdb.d): creates the
# schema owner and the runtime role with their passwords, and the empty database, using the
# same script as any other environment (backend/scripts/bootstrap_db.sql). The image's entrypoint
# runs this with `set -e`; ON_ERROR_STOP makes any SQL error fatal too.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$DB_OWNER_PASSWORD" \
  -v app_password="$DB_APP_PASSWORD" \
  -f /bootstrap/bootstrap_db.sql
