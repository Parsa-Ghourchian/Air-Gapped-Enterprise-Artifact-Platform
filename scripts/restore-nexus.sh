#!/usr/bin/env bash
set -euo pipefail

# Restore Nexus and portal PostgreSQL data from a backup archive.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
  echo "Usage: $0 backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz"
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

echo "Stopping stack..."
docker compose down

if [[ -d data/nexus ]]; then
  echo "Moving current Nexus data to data/nexus.before-${TIMESTAMP}"
  mv data/nexus "data/nexus.before-${TIMESTAMP}"
fi

if [[ -d data/postgres ]]; then
  echo "Moving current PostgreSQL data to data/postgres.before-${TIMESTAMP}"
  mv data/postgres "data/postgres.before-${TIMESTAMP}"
fi

echo "Restoring backup..."
tar -xzf "$BACKUP_FILE"

echo "Fixing permissions..."
if [[ -d data/nexus ]]; then
  sudo chown -R 200:200 data/nexus
fi

echo "Starting stack..."
docker compose up -d

echo "Restore completed."
