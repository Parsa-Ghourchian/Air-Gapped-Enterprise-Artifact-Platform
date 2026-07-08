#!/usr/bin/env bash
set -euo pipefail

# Download Python packages and upload distributions into Nexus pypi-hosted.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a

REQ_FILE="${1:-configs/python-requirements.txt}"
BUNDLE_DIR="offline-bundles/python/wheels"
NEXUS_PYPI_UPLOAD_URL="${NEXUS_URL}/repository/pypi-hosted/"

if [[ ! -f "$REQ_FILE" ]]; then
  echo "ERROR: Requirements file not found: $REQ_FILE"
  exit 1
fi

mkdir -p "$BUNDLE_DIR"

echo "Creating local Python tooling virtualenv..."
python3 -m venv .venv-tools
source .venv-tools/bin/activate

python -m pip install --upgrade pip wheel twine

echo "Downloading Python packages into ${BUNDLE_DIR} ..."
python -m pip download -r "$REQ_FILE" -d "$BUNDLE_DIR"

echo "Uploading packages to Nexus PyPI hosted repository..."
python -m twine upload \
  --repository-url "$NEXUS_PYPI_UPLOAD_URL" \
  -u admin \
  -p "$NEXUS_ADMIN_PASSWORD" \
  "$BUNDLE_DIR"/*

echo "Python package sync completed."
