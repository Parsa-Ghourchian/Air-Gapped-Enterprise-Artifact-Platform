#!/usr/bin/env bash
set -euo pipefail

# Upload a backup archive to Nexus raw-backups repository.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a

NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
AUTH="admin:${NEXUS_ADMIN_PASSWORD:?NEXUS_ADMIN_PASSWORD is required}"
RAW_REPO="${NEXUS_RAW_BACKUPS:-raw-backups}"

BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
  BACKUP_FILE="$(ls -t backups/nexus-backup-*.tar.gz 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup file not found."
  echo "Usage: $0 backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz"
  exit 1
fi

BASENAME="$(basename "$BACKUP_FILE")"
TARGET_URL="${NEXUS_URL}/repository/${RAW_REPO}/nexus/${BASENAME}"

echo "Uploading backup to Nexus:"
echo "$TARGET_URL"

curl -fsS \
  -u "$AUTH" \
  --upload-file "$BACKUP_FILE" \
  "$TARGET_URL" >/dev/null

echo "Backup uploaded successfully."
