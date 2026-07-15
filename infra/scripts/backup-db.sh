#!/bin/bash
# Backup PostgreSQL to Cloudflare R2
# Usage: bash infra/scripts/backup-db.sh
set -e

source "$(dirname "$0")/../../.env"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/tmp/luminacast_backup_${TIMESTAMP}.sql.gz"

echo "📦 Backing up PostgreSQL..."
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "$BACKUP_FILE"

echo "☁️  Uploading to R2..."
# Using AWS CLI with R2 endpoint
AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
aws s3 cp "$BACKUP_FILE" "s3://${R2_BUCKET}/backups/postgres/${TIMESTAMP}.sql.gz" \
    --endpoint-url "${R2_ENDPOINT}"

rm "$BACKUP_FILE"
echo "✅ Backup complete: backups/postgres/${TIMESTAMP}.sql.gz"
