#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

REQ_FILE="${1:-configs/python2-requirements.txt}"

if [[ ! -f "$REQ_FILE" ]]; then
  echo "ERROR: requirements file not found: $REQ_FILE"
  exit 1
fi

PYTHON2_DOCKER_IMAGE="${PYTHON2_DOCKER_IMAGE:-python:2.7-slim}"
PYTHON2_DOCKER_NETWORK="${PYTHON2_DOCKER_NETWORK:-airgap-artifacts-platform}"
PYTHON2_PIP_INDEX_URL="${PYTHON2_PIP_INDEX_URL:-http://nexus:8081/repository/pypi2-group/simple}"

if ! docker network inspect "$PYTHON2_DOCKER_NETWORK" >/dev/null 2>&1; then
  DETECTED_NETWORK="$(docker network ls --format '{{.Name}}' | grep -E 'airgap|artifact|platform' | head -1 || true)"

  if [[ -n "$DETECTED_NETWORK" ]]; then
    echo "WARN: Network $PYTHON2_DOCKER_NETWORK not found. Using detected network: $DETECTED_NETWORK"
    PYTHON2_DOCKER_NETWORK="$DETECTED_NETWORK"
  else
    echo "ERROR: Docker network not found: $PYTHON2_DOCKER_NETWORK"
    docker network ls
    exit 1
  fi
fi

JOB_ID="python2-legacy-$(date +%Y%m%d-%H%M%S)"
WORK_DIR="offline-bundles/python2-legacy/${JOB_ID}"
ARCHIVE="offline-bundles/python2-legacy/${JOB_ID}.tar.gz"

mkdir -p "$WORK_DIR/python2/wheels"
cp "$REQ_FILE" "$WORK_DIR/python2/requirements.txt"

PIP_HOST="$(echo "$PYTHON2_PIP_INDEX_URL" | sed -E 's#https?://([^/:]+).*#\1#')"

echo "Python 2 Docker image: $PYTHON2_DOCKER_IMAGE"
echo "Docker network: $PYTHON2_DOCKER_NETWORK"
echo "Pip index: $PYTHON2_PIP_INDEX_URL"
echo "Output dir: $WORK_DIR"
echo

if ! docker image inspect "$PYTHON2_DOCKER_IMAGE" >/dev/null 2>&1; then
  echo "Docker image not found locally. Pulling: $PYTHON2_DOCKER_IMAGE"
  docker pull "$PYTHON2_DOCKER_IMAGE"
fi

docker run --rm \
  --network "$PYTHON2_DOCKER_NETWORK" \
  -v "$PWD/$WORK_DIR/python2:/python2" \
  "$PYTHON2_DOCKER_IMAGE" \
  bash -lc "python -m ensurepip || true; python -m pip install --upgrade 'pip<21' 'setuptools<45' 'wheel<0.38'; python -m pip download --no-cache-dir --index-url '$PYTHON2_PIP_INDEX_URL' --trusted-host '$PIP_HOST' -r /python2/requirements.txt -d /python2/wheels"

cat > "$WORK_DIR/manifest.txt" <<EOF
name=python2-legacy-offline-bundle
job_id=$JOB_ID
created_at=$(date -Iseconds)
python2_docker_image=$PYTHON2_DOCKER_IMAGE
pip_index=$PYTHON2_PIP_INDEX_URL
requirements=python2/requirements.txt
wheels=python2/wheels
EOF

find "$WORK_DIR" -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK_DIR/SHA256SUMS"

tar -C "$WORK_DIR" -czf "$ARCHIVE" .
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

echo
echo "Bundle created:"
ls -lh "$ARCHIVE" "${ARCHIVE}.sha256"
echo
echo "Bundle path:"
echo "$ARCHIVE"
