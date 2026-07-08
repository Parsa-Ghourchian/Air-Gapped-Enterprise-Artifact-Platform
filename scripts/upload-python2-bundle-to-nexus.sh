#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

NEXUS_API_URL="${NEXUS_API_URL:-http://localhost:8081}"
NEXUS_USER="${NEXUS_USER:-admin}"
NEXUS_ADMIN_PASSWORD="${NEXUS_ADMIN_PASSWORD:-}"
NEXUS_RAW_OFFLINE_BUNDLES="${NEXUS_RAW_OFFLINE_BUNDLES:-raw-offline-bundles}"

if [[ -z "$NEXUS_ADMIN_PASSWORD" ]]; then
  echo "ERROR: NEXUS_ADMIN_PASSWORD is not set in .env"
  exit 1
fi

BUNDLE_PATH="${1:-}"

if [[ -z "$BUNDLE_PATH" ]]; then
  BUNDLE_PATH="$(ls -1t offline-bundles/python2-legacy/*.tar.gz 2>/dev/null | head -1 || true)"
fi

if [[ -z "$BUNDLE_PATH" || ! -f "$BUNDLE_PATH" ]]; then
  echo "ERROR: bundle file not found."
  exit 1
fi

BUNDLE_NAME="$(basename "$BUNDLE_PATH")"

curl -fsS \
  -u "${NEXUS_USER}:${NEXUS_ADMIN_PASSWORD}" \
  -X POST \
  "${NEXUS_API_URL}/service/rest/v1/components?repository=${NEXUS_RAW_OFFLINE_BUNDLES}" \
  -F "raw.directory=/python2-legacy" \
  -F "raw.asset1=@${BUNDLE_PATH}" \
  -F "raw.asset1.filename=${BUNDLE_NAME}" \
  >/dev/null

if [[ -f "${BUNDLE_PATH}.sha256" ]]; then
  curl -fsS \
    -u "${NEXUS_USER}:${NEXUS_ADMIN_PASSWORD}" \
    -X POST \
    "${NEXUS_API_URL}/service/rest/v1/components?repository=${NEXUS_RAW_OFFLINE_BUNDLES}" \
    -F "raw.directory=/python2-legacy" \
    -F "raw.asset1=@${BUNDLE_PATH}.sha256" \
    -F "raw.asset1.filename=${BUNDLE_NAME}.sha256" \
    >/dev/null
fi

echo "Upload completed."
echo "Path in Nexus Raw: /python2-legacy/${BUNDLE_NAME}"
