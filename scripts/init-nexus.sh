#!/usr/bin/env bash
set -euo pipefail

# Initialize Nexus repositories for the air-gapped artifact platform.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
else
  echo "ERROR: .env file not found."
  exit 1
fi

NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
NEXUS_CONTAINER="airgap-nexus"
NEW_PASSWORD="${NEXUS_ADMIN_PASSWORD:?NEXUS_ADMIN_PASSWORD is required}"

echo "Waiting for Nexus to become ready at ${NEXUS_URL} ..."

for i in {1..120}; do
  if curl -fsS "${NEXUS_URL}/service/rest/v1/status" >/dev/null 2>&1; then
    echo "Nexus is reachable."
    break
  fi

  if [[ "$i" -eq 120 ]]; then
    echo "ERROR: Nexus did not become ready in time."
    docker logs "$NEXUS_CONTAINER" --tail=100 || true
    exit 1
  fi

  sleep 5
done

DEFAULT_PASSWORD="$(docker exec "$NEXUS_CONTAINER" sh -c 'cat /nexus-data/admin.password 2>/dev/null || true')"

AUTH_PASSWORD=""

if curl -fsS -u "admin:${NEW_PASSWORD}" "${NEXUS_URL}/service/rest/v1/status" >/dev/null 2>&1; then
  AUTH_PASSWORD="$NEW_PASSWORD"
  echo "Using configured admin password."
elif [[ -n "$DEFAULT_PASSWORD" ]] && curl -fsS -u "admin:${DEFAULT_PASSWORD}" "${NEXUS_URL}/service/rest/v1/status" >/dev/null 2>&1; then
  AUTH_PASSWORD="$DEFAULT_PASSWORD"
  echo "Using initial admin password from Nexus."
else
  echo "ERROR: Could not authenticate to Nexus."
  echo "Try checking the admin password:"
  echo "docker exec ${NEXUS_CONTAINER} cat /nexus-data/admin.password"
  exit 1
fi

# Change initial admin password when needed.
if [[ "$AUTH_PASSWORD" != "$NEW_PASSWORD" ]]; then
  echo "Changing Nexus admin password..."
  curl -fsS \
    -u "admin:${AUTH_PASSWORD}" \
    -X PUT \
    -H "Content-Type: text/plain" \
    --data "${NEW_PASSWORD}" \
    "${NEXUS_URL}/service/rest/v1/security/users/admin/change-password" >/dev/null

  AUTH_PASSWORD="$NEW_PASSWORD"
  echo "Admin password changed."
fi

AUTH="admin:${AUTH_PASSWORD}"

# Enable Docker Bearer Token Realm for Docker login/pull/push.
echo "Enabling Docker Bearer Token Realm..."

AVAILABLE_REALMS="$(curl -fsS -u "$AUTH" "${NEXUS_URL}/service/rest/v1/security/realms/available")"
ACTIVE_REALMS="$(curl -fsS -u "$AUTH" "${NEXUS_URL}/service/rest/v1/security/realms/active")"

if ! printf '%s' "$AVAILABLE_REALMS" | jq -e '.[] | select(.id == "DockerToken")' >/dev/null 2>&1; then
  echo "WARNING: DockerToken realm is not available in this Nexus instance."
  echo "Available realms:"
  printf '%s' "$AVAILABLE_REALMS" | jq -r '.[] | "- " + (.id // .name // tostring)' || true
else
  # Keep existing active realms and only append DockerToken if it is not active yet.
  UPDATED_REALMS="$(printf '%s' "$ACTIVE_REALMS" | jq 'if index("DockerToken") then . else . + ["DockerToken"] end')"

  response_file="$(mktemp)"
  status_code="$(
    curl -sS \
      -u "$AUTH" \
      -X PUT \
      -H "Content-Type: application/json" \
      --data "$UPDATED_REALMS" \
      -o "$response_file" \
      -w "%{http_code}" \
      "${NEXUS_URL}/service/rest/v1/security/realms/active"
  )"

  if [[ "$status_code" != "200" && "$status_code" != "204" ]]; then
    echo "ERROR: Failed to enable DockerToken realm. HTTP ${status_code}"
    cat "$response_file"
    rm -f "$response_file"
    exit 1
  fi

  rm -f "$response_file"
  echo "DockerToken realm is active."
fi

repo_exists() {
  local repo_name="$1"
  curl -fsS -u "$AUTH" "${NEXUS_URL}/service/rest/v1/repositories/${repo_name}" >/dev/null 2>&1
}

create_repo() {
  local repo_name="$1"
  local endpoint="$2"
  local json_file="$3"
  local method="POST"
  local url="${NEXUS_URL}/service/rest/v1/repositories/${endpoint}"
  local expected_a="201"
  local expected_b="204"

  if repo_exists "$repo_name"; then
    echo "UPDATE: ${repo_name}"
    method="PUT"
    url="${NEXUS_URL}/service/rest/v1/repositories/${endpoint}/${repo_name}"
    expected_a="200"
    expected_b="204"
  else
    echo "CREATE: ${repo_name}"
  fi

  local response_file
  response_file="$(mktemp)"

  local status_code
  status_code="$(
    curl -sS \
      -u "$AUTH" \
      -X "$method" \
      -H "Content-Type: application/json" \
      --data @"${json_file}" \
      -o "$response_file" \
      -w "%{http_code}" \
      "$url"
  )"

  if [[ "$status_code" != "$expected_a" && "$status_code" != "$expected_b" ]]; then
    echo "ERROR: Failed to ${method} ${repo_name}. HTTP ${status_code}"
    cat "$response_file"
    rm -f "$response_file"
    exit 1
  fi

  rm -f "$response_file"
}

echo "Disabling Nexus anonymous access..."
curl -fsS \
  -u "$AUTH" \
  -X PUT \
  -H "Content-Type: application/json" \
  --data '{"enabled":false,"userId":"anonymous","realmName":"NexusAuthorizingRealm"}' \
  "${NEXUS_URL}/service/rest/v1/security/anonymous" >/dev/null
echo "Anonymous access is disabled."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "${TMP_DIR}/docker-hosted.json" <<'JSON'
{
  "name": "docker-hosted",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "ALLOW"
  },
  "docker": {
    "v1Enabled": false,
    "forceBasicAuth": true,
    "httpPort": 5000
  }
}
JSON

cat > "${TMP_DIR}/docker-proxy.json" <<'JSON'
{
  "name": "docker-proxy",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "proxy": {
    "remoteUrl": "https://registry-1.docker.io",
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
  },
  "dockerProxy": {
    "indexType": "HUB"
  },
  "docker": {
    "v1Enabled": false,
    "forceBasicAuth": true,
    "httpPort": 5001
  }
}
JSON

cat > "${TMP_DIR}/docker-group.json" <<'JSON'
{
  "name": "docker-group",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "group": {
    "memberNames": [
      "docker-hosted",
      "docker-proxy"
    ]
  },
  "docker": {
    "v1Enabled": false,
    "forceBasicAuth": true,
    "httpPort": 5002
  }
}
JSON

cat > "${TMP_DIR}/pypi-hosted.json" <<'JSON'
{
  "name": "pypi-hosted",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "ALLOW"
  }
}
JSON

cat > "${TMP_DIR}/pypi-proxy.json" <<'JSON'
{
  "name": "pypi-proxy",
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
}
JSON

cat > "${TMP_DIR}/pypi-group.json" <<'JSON'
{
  "name": "pypi-group",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "group": {
    "memberNames": [
      "pypi-hosted",
      "pypi-proxy"
    ]
  }
}
JSON

cat > "${TMP_DIR}/apt-ubuntu-noble-proxy.json" <<'JSON'
{
  "name": "apt-ubuntu-noble-proxy",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "proxy": {
    "remoteUrl": "http://archive.ubuntu.com/ubuntu/",
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
  },
  "apt": {
    "distribution": "noble",
    "flat": false
  }
}
JSON

cat > "${TMP_DIR}/apt-ubuntu-jammy-proxy.json" <<'JSON'
{
  "name": "apt-ubuntu-jammy-proxy",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "proxy": {
    "remoteUrl": "http://archive.ubuntu.com/ubuntu/",
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
  },
  "apt": {
    "distribution": "jammy",
    "flat": false
  }
}
JSON

cat > "${TMP_DIR}/apt-debian-bookworm-proxy.json" <<'JSON'
{
  "name": "apt-debian-bookworm-proxy",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true
  },
  "proxy": {
    "remoteUrl": "http://deb.debian.org/debian/",
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
  },
  "apt": {
    "distribution": "bookworm",
    "flat": false
  }
}
JSON

cat > "${TMP_DIR}/raw-releases.json" <<'JSON'
{
  "name": "raw-releases",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "ALLOW"
  },
  "raw": {
    "contentDisposition": "ATTACHMENT"
  }
}
JSON

cat > "${TMP_DIR}/raw-offline-bundles.json" <<'JSON'
{
  "name": "raw-offline-bundles",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "ALLOW"
  },
  "raw": {
    "contentDisposition": "ATTACHMENT"
  }
}
JSON

cat > "${TMP_DIR}/raw-backups.json" <<'JSON'
{
  "name": "raw-backups",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "ALLOW"
  },
  "raw": {
    "contentDisposition": "ATTACHMENT"
  }
}
JSON

create_repo "docker-hosted" "docker/hosted" "${TMP_DIR}/docker-hosted.json"
create_repo "docker-proxy" "docker/proxy" "${TMP_DIR}/docker-proxy.json"
create_repo "docker-group" "docker/group" "${TMP_DIR}/docker-group.json"

create_repo "pypi-hosted" "pypi/hosted" "${TMP_DIR}/pypi-hosted.json"
create_repo "pypi-proxy" "pypi/proxy" "${TMP_DIR}/pypi-proxy.json"
create_repo "pypi-group" "pypi/group" "${TMP_DIR}/pypi-group.json"

create_repo "apt-ubuntu-noble-proxy" "apt/proxy" "${TMP_DIR}/apt-ubuntu-noble-proxy.json"
create_repo "apt-ubuntu-jammy-proxy" "apt/proxy" "${TMP_DIR}/apt-ubuntu-jammy-proxy.json"
create_repo "apt-debian-bookworm-proxy" "apt/proxy" "${TMP_DIR}/apt-debian-bookworm-proxy.json"

create_repo "raw-releases" "raw/hosted" "${TMP_DIR}/raw-releases.json"
create_repo "raw-offline-bundles" "raw/hosted" "${TMP_DIR}/raw-offline-bundles.json"
create_repo "raw-backups" "raw/hosted" "${TMP_DIR}/raw-backups.json"

echo
echo "Nexus initialization completed."
echo
echo "Repositories:"
echo "- docker-hosted: localhost:5000"
echo "- docker-proxy:  localhost:5001"
echo "- docker-group:  localhost:5002"
echo "- pypi-group:    ${NEXUS_URL}/repository/pypi-group/simple"
echo "- apt noble:     ${NEXUS_URL}/repository/apt-ubuntu-noble-proxy"
echo "- apt jammy:     ${NEXUS_URL}/repository/apt-ubuntu-jammy-proxy"
echo "- apt bookworm:  ${NEXUS_URL}/repository/apt-debian-bookworm-proxy"
