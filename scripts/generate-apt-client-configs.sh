#!/usr/bin/env bash
set -euo pipefail

# Generate APT client source list examples for Nexus APT proxy repositories.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

OUT_DIR="examples/debian-client"
NEXUS_BASE_URL="${1:-http://nexus.local:8081}"

mkdir -p "$OUT_DIR"

cat > "${OUT_DIR}/ubuntu-24.04-noble-nexus.list" <<EOF
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-noble noble main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-noble-updates noble-updates main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-noble-security noble-security main universe multiverse restricted
EOF

cat > "${OUT_DIR}/ubuntu-25.04-plucky-nexus.list" <<EOF
# Ubuntu 25.04 is EOL. This points to Nexus proxies backed by old-releases.ubuntu.com.
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-plucky plucky main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-plucky-updates plucky-updates main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-plucky-security plucky-security main universe multiverse restricted
EOF

cat > "${OUT_DIR}/ubuntu-26.04-resolute-nexus.list" <<EOF
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-resolute resolute main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-resolute-updates resolute-updates main universe multiverse restricted
deb ${NEXUS_BASE_URL}/repository/apt-ubuntu-resolute-security resolute-security main universe multiverse restricted
EOF

cat > "${OUT_DIR}/debian-11-bullseye-nexus.list" <<EOF
deb ${NEXUS_BASE_URL}/repository/apt-debian-bullseye bullseye main contrib non-free
deb ${NEXUS_BASE_URL}/repository/apt-debian-bullseye-updates bullseye-updates main contrib non-free
deb ${NEXUS_BASE_URL}/repository/apt-debian-bullseye-security bullseye-security main contrib non-free
EOF

cat > "${OUT_DIR}/debian-12-bookworm-nexus.list" <<EOF
deb ${NEXUS_BASE_URL}/repository/apt-debian-bookworm bookworm main contrib non-free non-free-firmware
deb ${NEXUS_BASE_URL}/repository/apt-debian-bookworm-updates bookworm-updates main contrib non-free non-free-firmware
deb ${NEXUS_BASE_URL}/repository/apt-debian-bookworm-security bookworm-security main contrib non-free non-free-firmware
EOF

cat > "${OUT_DIR}/debian-13-trixie-nexus.list" <<EOF
deb ${NEXUS_BASE_URL}/repository/apt-debian-trixie trixie main contrib non-free non-free-firmware
deb ${NEXUS_BASE_URL}/repository/apt-debian-trixie-updates trixie-updates main contrib non-free non-free-firmware
deb ${NEXUS_BASE_URL}/repository/apt-debian-trixie-security trixie-security main contrib non-free non-free-firmware
EOF

echo "APT client configs generated in ${OUT_DIR}"
