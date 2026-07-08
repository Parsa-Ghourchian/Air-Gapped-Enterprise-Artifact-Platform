#!/usr/bin/env bash
set -euo pipefail

# Create a consistent local backup of Nexus data and project configuration.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="backups"
BACKUP_FILE="${BACKUP_DIR}/nexus-backup-${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "Stopping Nexus for a consistent filesystem backup..."
docker compose stop nexus

echo "Creating backup: ${BACKUP_FILE}"
tar -czf "$BACKUP_FILE" \
  data/nexus \
  docker-compose.yml \
  .env.example \
  configs \
  scripts \
  monitoring \
  traefik \
  VERSION

echo "Starting Nexus..."
docker compose start nexus

echo "Backup completed:"
echo "$BACKUP_FILE"
