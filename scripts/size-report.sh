#!/usr/bin/env bash
set -euo pipefail

# Show project and Docker disk usage.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Project disk usage:"
du -sh . 2>/dev/null || true

echo
echo "Important directories:"
du -sh data/nexus data/postgres data/prometheus data/grafana reports offline-bundles apt-mini-repos 2>/dev/null || true

echo
echo "Docker disk usage:"
docker system df

echo
echo "Largest Docker images:"
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | head -n 30
