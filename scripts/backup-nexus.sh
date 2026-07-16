#!/usr/bin/env bash
set -euo pipefail

# Create a consistent local backup of Nexus data, portal PostgreSQL data, and project configuration.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="backups"
BACKUP_FILE="${BACKUP_DIR}/nexus-backup-${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "Stopping portal, PostgreSQL, and Nexus for a consistent filesystem backup..."
docker compose stop portal postgres nexus

echo "Creating backup: ${BACKUP_FILE}"
tar -czf "$BACKUP_FILE" \
  data/nexus \
  data/postgres \
  docker-compose.yml \
  docker-compose.override.yml \
  .env.example \
  configs \
  scripts \
  monitoring \
  traefik \
  VERSION

echo "Starting services..."
docker compose start nexus postgres portal

echo "Backup completed:"
echo "$BACKUP_FILE"
