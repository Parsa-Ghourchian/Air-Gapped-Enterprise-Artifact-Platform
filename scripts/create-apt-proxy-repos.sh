#!/usr/bin/env bash
set -euo pipefail

# Create Ubuntu/Debian APT proxy repositories in Nexus.
# This script is idempotent and safe to run multiple times.

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
AUTH="admin:${NEXUS_ADMIN_PASSWORD:?NEXUS_ADMIN_PASSWORD is required}"
REPO_FILE="configs/apt/apt-proxy-repositories.tsv"

if [[ ! -f "$REPO_FILE" ]]; then
  echo "ERROR: Repository definition file not found: $REPO_FILE"
  exit 1
fi

echo "Checking Nexus API..."
curl -fsS -u "$AUTH" "${NEXUS_URL}/service/rest/v1/status" >/dev/null

repo_exists() {
  local repo_name="$1"
  curl -fsS -u "$AUTH" "${NEXUS_URL}/service/rest/v1/repositories/${repo_name}" >/dev/null 2>&1
}

create_apt_proxy_repo() {
  local name="$1"
  local remote_url="$2"
  local distribution="$3"

  if repo_exists "$name"; then
    echo "SKIP: ${name} already exists."
    return 0
  fi

  echo "CREATE: ${name} -> ${distribution}"

  local payload
  payload="$(jq -n \
    --arg name "$name" \
    --arg remote_url "$remote_url" \
    --arg distribution "$distribution" \
    '{
      name: $name,
      online: true,
      storage: {
        blobStoreName: "default",
        strictContentTypeValidation: true
      },
      proxy: {
        remoteUrl: $remote_url,
        contentMaxAge: 1440,
        metadataMaxAge: 1440
      },
      negativeCache: {
        enabled: true,
        timeToLive: 1440
      },
      httpClient: {
        blocked: false,
        autoBlock: true
      },
      apt: {
        distribution: $distribution,
        flat: false
      }
    }'
  )"

  local response_file
  response_file="$(mktemp)"

  local status_code
  status_code="$(
    curl -sS \
      -u "$AUTH" \
      -X POST \
      -H "Content-Type: application/json" \
      --data "$payload" \
      -o "$response_file" \
      -w "%{http_code}" \
      "${NEXUS_URL}/service/rest/v1/repositories/apt/proxy"
  )"

  if [[ "$status_code" != "201" && "$status_code" != "204" ]]; then
    echo "ERROR: Failed to create ${name}. HTTP ${status_code}"
    cat "$response_file"
    rm -f "$response_file"
    exit 1
  fi

  rm -f "$response_file"
}

while IFS=$'\t' read -r name remote_url distribution; do
  [[ -z "${name:-}" ]] && continue
  [[ "$name" =~ ^# ]] && continue

  create_apt_proxy_repo "$name" "$remote_url" "$distribution"
done < "$REPO_FILE"

echo
echo "APT proxy repositories are ready."
