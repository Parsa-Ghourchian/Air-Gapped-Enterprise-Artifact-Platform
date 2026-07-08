#!/usr/bin/env bash
set -euo pipefail

# Audit a Docker image for known vulnerabilities using Grype.
# Grype runs on-demand and does not run as a permanent service.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE="${1:-}"

if [[ -z "$IMAGE" ]]; then
  echo "Usage: $0 <docker-image>"
  echo "Example: $0 localhost:5002/library/nginx:1.27"
  exit 1
fi

REPORTS_DIR="${REPORTS_DIR:-reports}"
mkdir -p "$REPORTS_DIR"

SAFE_NAME="$(echo "$IMAGE" | tr '/:@' '____')"
TABLE_FILE="${REPORTS_DIR}/${SAFE_NAME}.grype.txt"
JSON_FILE="${REPORTS_DIR}/${SAFE_NAME}.grype.json"

echo "Running vulnerability audit for: ${IMAGE}"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  anchore/grype:latest \
  "$IMAGE" \
  -o table | tee "$TABLE_FILE"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)/${REPORTS_DIR}:/reports" \
  anchore/grype:latest \
  "$IMAGE" \
  -o json > "$JSON_FILE"

echo
echo "Audit reports generated:"
echo "- ${TABLE_FILE}"
echo "- ${JSON_FILE}"
