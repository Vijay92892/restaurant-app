#!/bin/sh
# Backup of the database and the menu photos, kept for KEEP_DAYS (default 14) in deploy/backups/.
# Run nightly from cron (see docs/07-deployment.md):
#   15 4 * * *  /opt/restaurant/deploy/backup.sh >> /opt/restaurant/deploy/backups/backup.log 2>&1
# Copy deploy/backups/ off the server too (another machine or cloud storage): a backup on the
# same disk does not survive losing the server.
set -eu
cd "$(dirname "$0")"
KEEP_DAYS="${KEEP_DAYS:-14}"
umask 077
mkdir -p backups
stamp="$(date -u +%Y%m%d-%H%M%S)"

# The whole database: schema, row-level security, grants and data (custom format, compressed).
docker compose exec -T db pg_dump --username=postgres --dbname=restaurant --format=custom \
  > "backups/restaurant-$stamp.dump.partial"
mv "backups/restaurant-$stamp.dump.partial" "backups/restaurant-$stamp.dump"

# Menu photos.
docker compose run --rm --no-deps -T --entrypoint tar api -czf - -C /var/lib/restaurant/media . \
  > "backups/media-$stamp.tar.gz.partial"
mv "backups/media-$stamp.tar.gz.partial" "backups/media-$stamp.tar.gz"

find backups -name 'restaurant-*.dump' -mtime "+$KEEP_DAYS" -delete
find backups -name 'media-*.tar.gz' -mtime "+$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) backup ok: restaurant-$stamp.dump, media-$stamp.tar.gz"
