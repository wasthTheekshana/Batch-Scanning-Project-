#!/bin/sh
# Back up the central database: one compressed dump per day, the newest KEEP
# kept. Run from central/deploy (where docker-compose.yml is), e.g. nightly:
#   0 2 * * *  cd /opt/dokscanner-central/central/deploy && ./backup.sh
# The dump holds the projects, templates, PC list and audit log (no scanned
# documents, no PC API keys in clear: only their hashes). Keep the backups
# somewhere other than this server, and treat them as confidential.
set -eu

# the dumps are confidential: only the owner may read what this script creates
umask 077

KEEP="${KEEP:-14}"
DEST="${BACKUP_DIR:-./backups}"
mkdir -p "$DEST"
chmod 700 "$DEST"

# shellcheck disable=SC1091
[ -f .env ] && . ./.env
USER_NAME="${POSTGRES_USER:-dokcentral}"
DB_NAME="${POSTGRES_DB:-dokcentral}"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
FILE="$DEST/dokcentral-$STAMP.sql.gz"
TMP="$FILE.part"

docker compose exec -T db pg_dump -U "$USER_NAME" -d "$DB_NAME" --no-owner | gzip -9 > "$TMP"

# a dump that is empty or not a valid gzip is a failed backup, not a file to keep
if ! gzip -t "$TMP" || [ "$(wc -c < "$TMP")" -lt 200 ]; then
    rm -f "$TMP"
    echo "backup FAILED: the dump is empty or damaged" >&2
    exit 1
fi
chmod 600 "$TMP"
mv "$TMP" "$FILE"
echo "backup written: $FILE"

# keep the newest $KEEP, delete older ones
ls -1t "$DEST"/dokcentral-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
done
