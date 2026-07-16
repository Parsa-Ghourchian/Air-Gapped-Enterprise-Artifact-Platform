#!/usr/bin/env bash
set -euo pipefail

# Run basic smoke tests against the local platform.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a

echo "Testing HTTP endpoints..."

docker exec airgap-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null
echo "OK: PostgreSQL"

curl -fsS http://localhost:8095/health | grep -q '"database":"postgresql"'
echo "OK: Portal API and PostgreSQL connection"

curl -fsS http://localhost:8081/service/rest/v1/status >/dev/null
echo "OK: Nexus API"

curl -fsS http://localhost:9090/-/ready >/dev/null
echo "OK: Prometheus"

curl -fsS http://localhost:3000/api/health >/dev/null
echo "OK: Grafana"

curl -fsS http://localhost:8080/api/rawdata >/dev/null
echo "OK: Traefik Dashboard API"

echo
echo "Testing Docker login..."
echo "$NEXUS_ADMIN_PASSWORD" | docker login localhost:5000 -u admin --password-stdin
echo "$NEXUS_ADMIN_PASSWORD" | docker login localhost:5002 -u admin --password-stdin

echo
echo "Testing Docker hosted push/pull..."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "${TMP_DIR}/Dockerfile" <<'EOF'
FROM alpine:3.20
CMD ["sh", "-c", "echo hello-from-nexus"]
EOF

docker build -t localhost:5000/demo/hello-nexus:1.0.0 "$TMP_DIR"
docker push localhost:5000/demo/hello-nexus:1.0.0

docker pull localhost:5002/demo/hello-nexus:1.0.0

echo
echo "Testing PyPI group with requests package..."
python3 -m venv /tmp/nexus-pypi-test-venv
source /tmp/nexus-pypi-test-venv/bin/activate

python -m pip install --upgrade pip
python -m pip install \
  --index-url "${NEXUS_URL}/repository/pypi-group/simple" \
  --trusted-host localhost \
  requests==2.32.3

deactivate
rm -rf /tmp/nexus-pypi-test-venv

echo
echo "All smoke tests completed successfully."
