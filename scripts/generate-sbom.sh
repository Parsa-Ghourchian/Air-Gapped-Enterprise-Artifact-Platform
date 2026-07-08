#!/usr/bin/env bash
set -euo pipefail

# Generate SBOM reports for a Docker image using Syft.
# Syft runs on-demand to avoid keeping extra heavy services in docker-compose.

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
CYCLONEDX_FILE="${REPORTS_DIR}/${SAFE_NAME}.cyclonedx.json"
SPDX_FILE="${REPORTS_DIR}/${SAFE_NAME}.spdx.json"

echo "Generating SBOM for: ${IMAGE}"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)/${REPORTS_DIR}:/reports" \
  anchore/syft:latest \
  "$IMAGE" \
  -o "cyclonedx-json=/reports/$(basename "$CYCLONEDX_FILE")" \
  -o "spdx-json=/reports/$(basename "$SPDX_FILE")"

echo
echo "SBOM generated:"
echo "- ${CYCLONEDX_FILE}"
echo "- ${SPDX_FILE}"
