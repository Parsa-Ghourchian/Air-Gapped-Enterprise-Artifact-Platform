#!/usr/bin/env bash
set -euo pipefail

# Upload generated mini APT repositories to Nexus raw-offline-bundles.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a

NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
AUTH="admin:${NEXUS_ADMIN_PASSWORD:?NEXUS_ADMIN_PASSWORD is required}"
RAW_REPO="${NEXUS_RAW_OFFLINE_BUNDLES:-raw-offline-bundles}"
SRC_ROOT="${1:-apt-mini-repos}"

if [[ ! -d "$SRC_ROOT" ]]; then
  echo "ERROR: Source directory not found: $SRC_ROOT"
  exit 1
fi

echo "Uploading mini APT repositories to Nexus raw repo: ${RAW_REPO}"

find "$SRC_ROOT" -type f | while read -r file; do
  rel="${file#${SRC_ROOT}/}"
  target_url="${NEXUS_URL}/repository/${RAW_REPO}/apt-mini/${rel}"

  echo "UPLOAD: ${rel}"

  curl -fsS \
    -u "$AUTH" \
    --upload-file "$file" \
    "$target_url" >/dev/null
done

echo
echo "APT mini repositories uploaded."
echo
echo "Client source example:"
echo "deb [trusted=yes] ${NEXUS_URL}/repository/${RAW_REPO}/apt-mini/ubuntu-noble noble main"
