#!/usr/bin/env bash
# Daily logical backup of the database. Run from the repository root, for example from cron:
#   0 3 * * * cd /opt/medication-doc-verifier && ./deploy/backup.sh /var/backups/medication-doc-verifier
set -euo pipefail
dest="${1:?usage: backup.sh <directory>}"
mkdir -p "$dest"
file="$dest/medication-doc-verifier-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U medication_doc_verifier medication_doc_verifier | gzip > "$file"
# keep 14 days
find "$dest" -name 'medication-doc-verifier-*.sql.gz' -mtime +14 -delete
echo "wrote $file"
