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

if [[ -z "$NEXUS_ADMIN_PASSWORD" ]]; then
  echo "ERROR: NEXUS_ADMIN_PASSWORD is not set in .env"
  exit 1
fi

AUTH="${NEXUS_USER}:${NEXUS_ADMIN_PASSWORD}"

wait_for_nexus() {
  echo "Waiting for Nexus API: $NEXUS_API_URL"

  for i in $(seq 1 90); do
    if curl -fsS -u "$AUTH" "$NEXUS_API_URL/service/rest/v1/status" >/dev/null 2>&1; then
      echo "Nexus API is ready."
      return 0
    fi
    sleep 2
  done

  echo "ERROR: Nexus API is not ready."
  exit 1
}

repo_exists() {
  local repo="$1"
  curl -fsS -u "$AUTH" "$NEXUS_API_URL/service/rest/v1/repositories/${repo}" >/dev/null 2>&1
}

create_hosted() {
  if repo_exists "pypi2-hosted"; then
    echo "Repository exists: pypi2-hosted"
    return 0
  fi

  echo "Creating repository: pypi2-hosted"

  curl -fsS \
    -u "$AUTH" \
    -H "Content-Type: application/json" \
    -X POST \
    "$NEXUS_API_URL/service/rest/v1/repositories/pypi/hosted" \
    -d '{
      "name": "pypi2-hosted",
      "online": true,
      "storage": {
        "blobStoreName": "default",
        "strictContentTypeValidation": true,
        "writePolicy": "allow"
      },
      "component": {
        "proprietaryComponents": false
      }
    }' >/dev/null

  echo "Created: pypi2-hosted"
}

create_proxy() {
  if repo_exists "pypi2-proxy"; then
    echo "Repository exists: pypi2-proxy"
    return 0
  fi

  echo "Creating repository: pypi2-proxy"

  curl -fsS \
    -u "$AUTH" \
    -H "Content-Type: application/json" \
    -X POST \
    "$NEXUS_API_URL/service/rest/v1/repositories/pypi/proxy" \
    -d '{
      "name": "pypi2-proxy",
      "online": true,
      "storage": {
        "blobStoreName": "default",
        "strictContentTypeValidation": true
      },
      "proxy": {
        "remoteUrl": "https://pypi.org/",
        "contentMaxAge": 1440,
        "metadataMaxAge": 1440
      },
      "negativeCache": {
        "enabled": true,
        "timeToLive": 1440
      },
      "httpClient": {
        "blocked": false,
        "autoBlock": true
      }
    }' >/dev/null

  echo "Created: pypi2-proxy"
}

create_group() {
  if repo_exists "pypi2-group"; then
    echo "Repository exists: pypi2-group"
    return 0
  fi

  echo "Creating repository: pypi2-group"

  curl -fsS \
    -u "$AUTH" \
    -H "Content-Type: application/json" \
    -X POST \
    "$NEXUS_API_URL/service/rest/v1/repositories/pypi/group" \
    -d '{
      "name": "pypi2-group",
      "online": true,
      "storage": {
        "blobStoreName": "default",
        "strictContentTypeValidation": true
      },
      "group": {
        "memberNames": [
          "pypi2-hosted",
          "pypi2-proxy"
        ]
      }
    }' >/dev/null

  echo "Created: pypi2-group"
}

wait_for_nexus
create_hosted
create_proxy
create_group

echo
echo "Python 2 Nexus repositories are ready:"
echo "- pypi2-hosted"
echo "- pypi2-proxy"
echo "- pypi2-group"
