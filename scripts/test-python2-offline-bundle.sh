#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

PYTHON2_DOCKER_IMAGE="${PYTHON2_DOCKER_IMAGE:-python:2.7-slim}"

BUNDLE_PATH="${1:-}"

if [[ -z "$BUNDLE_PATH" ]]; then
  BUNDLE_PATH="$(ls -1t offline-bundles/python2-legacy/*.tar.gz 2>/dev/null | head -1 || true)"
fi

if [[ -z "$BUNDLE_PATH" || ! -f "$BUNDLE_PATH" ]]; then
  echo "ERROR: bundle file not found."
  exit 1
fi

TEST_DIR="/tmp/python2-legacy-bundle-test-$(date +%Y%m%d-%H%M%S)"
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

tar -xzf "$BUNDLE_PATH" -C "$TEST_DIR"

echo "Testing bundle: $BUNDLE_PATH"

docker run --rm \
  -v "$TEST_DIR/python2:/python2" \
  "$PYTHON2_DOCKER_IMAGE" \
  bash -lc 'python -m ensurepip || true; python -m pip install --upgrade "pip<21" "setuptools<45" "wheel<0.38"; python -m pip install --no-index --find-links /python2/wheels -r /python2/requirements.txt; python - <<PY2
import requests
import six
import urllib3
print("python2-offline-test-ok")
print("requests=" + requests.__version__)
print("six=" + six.__version__)
print("urllib3=" + urllib3.__version__)
PY2'

echo
echo "Python 2 offline bundle test passed."
