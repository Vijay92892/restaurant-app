#!/bin/sh
# Replace the database (and optionally the menu photos) with a backup made by backup.sh.
#   ./restore.sh backups/restaurant-20261101-041500.dump [backups/media-20261101-041500.tar.gz]
# Everything entered since that backup is lost. The API and the web front are stopped meanwhile.
set -eu
cd "$(dirname "$0")"
dump="${1:?usage: restore.sh <restaurant-*.dump> [media-*.tar.gz]}"
media="${2:-}"
[ -f "$dump" ] || { echo "No such file: $dump" >&2; exit 1; }
[ -z "$media" ] || [ -f "$media" ] || { echo "No such file: $media" >&2; exit 1; }

printf 'This REPLACES the live database with %s. Type RESTORE to continue: ' "$dump"
read -r answer
[ "$answer" = "RESTORE" ] || { echo "Nothing changed."; exit 1; }

docker compose stop web api
docker compose up -d db
# Wait for the database, then recreate it empty (as bootstrap_db.sql does) and load the backup.
until docker compose exec -T db pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'restaurant' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS restaurant;
CREATE DATABASE restaurant OWNER restaurant_owner;
REVOKE ALL ON DATABASE restaurant FROM PUBLIC;
GRANT CONNECT ON DATABASE restaurant TO restaurant_owner, restaurant_app;
\connect restaurant
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO restaurant_owner;
SQL
docker compose exec -T db pg_restore --username=postgres --dbname=restaurant --exit-on-error \
  < "$dump"

if [ -n "$media" ]; then
  docker compose run --rm --no-deps -T --entrypoint sh api -c \
    'find /var/lib/restaurant/media -mindepth 1 -delete && tar -xzf - -C /var/lib/restaurant/media' \
    < "$media"
fi

# Migrations bring an older backup up to the current schema; then everything starts again.
docker compose up -d
echo "Restored $dump. Check the site, then sign in on a staff device."
