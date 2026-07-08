#!/usr/bin/env bash
set -euo pipefail

# Build lightweight offline APT repositories for selected package lists.
# This avoids full Ubuntu/Debian mirrors and keeps the project portable.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TARGETS_FILE="${1:-configs/apt/apt-mini-targets.tsv}"
PACKAGE_FILE="${2:-configs/apt-packages.txt}"
OUT_ROOT="apt-mini-repos"

if [[ ! -f "$TARGETS_FILE" ]]; then
  echo "ERROR: Target file not found: $TARGETS_FILE"
  exit 1
fi

if [[ ! -f "$PACKAGE_FILE" ]]; then
  echo "ERROR: Package file not found: $PACKAGE_FILE"
  exit 1
fi

PACKAGES="$(grep -vE '^\s*#|^\s*$' "$PACKAGE_FILE" | xargs || true)"

if [[ -z "$PACKAGES" ]]; then
  echo "ERROR: No packages found in $PACKAGE_FILE"
  exit 1
fi

mkdir -p "$OUT_ROOT"

build_one_repo() {
  local distro="$1"
  local codename="$2"
  local image="$3"
  local components="$4"

  local target_name="${distro}-${codename}"
  local out_dir="${OUT_ROOT}/${target_name}"

  echo
  echo "Building mini APT repo: ${target_name}"
  echo "Image: ${image}"
  echo "Packages: ${PACKAGES}"

  rm -rf "$out_dir"
  mkdir -p "$out_dir"

  docker run --rm \
    -e DISTRO="$distro" \
    -e CODENAME="$codename" \
    -e COMPONENTS="$components" \
    -e PACKAGES="$PACKAGES" \
    -v "$(pwd)/${out_dir}:/out" \
    "$image" \
    bash -lc '
      set -euo pipefail

      configure_sources() {
        if [[ "$DISTRO" == "ubuntu" ]]; then
          if [[ "$CODENAME" == "plucky" ]]; then
            BASE_URL="http://old-releases.ubuntu.com/ubuntu"
            SECURITY_URL="http://old-releases.ubuntu.com/ubuntu"
          else
            BASE_URL="http://archive.ubuntu.com/ubuntu"
            SECURITY_URL="http://security.ubuntu.com/ubuntu"
          fi

          cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] ${BASE_URL} ${CODENAME} ${COMPONENTS}
deb [trusted=yes] ${BASE_URL} ${CODENAME}-updates ${COMPONENTS}
deb [trusted=yes] ${SECURITY_URL} ${CODENAME}-security ${COMPONENTS}
EOF
        else
          BASE_URL="http://deb.debian.org/debian"
          SECURITY_URL="http://security.debian.org/debian-security"

          cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] ${BASE_URL} ${CODENAME} ${COMPONENTS}
deb [trusted=yes] ${BASE_URL} ${CODENAME}-updates ${COMPONENTS}
deb [trusted=yes] ${SECURITY_URL} ${CODENAME}-security ${COMPONENTS}
EOF
        fi
      }

      configure_sources

      apt-get update

      # dpkg-dev provides dpkg-scanpackages.
      apt-get install -y --no-install-recommends dpkg-dev ca-certificates gzip

      mkdir -p /out/pool/main
      mkdir -p /out/dists/${CODENAME}/main/binary-amd64

      # Download selected packages and dependencies without installing them.
      apt-get install -y --download-only --no-install-recommends ${PACKAGES}

      cp -a /var/cache/apt/archives/*.deb /out/pool/main/ 2>/dev/null || true

      cd /out
      dpkg-scanpackages pool /dev/null > dists/${CODENAME}/main/binary-amd64/Packages
      gzip -9kf dists/${CODENAME}/main/binary-amd64/Packages

      cat > dists/${CODENAME}/Release <<EOF
Origin: Air-Gapped Enterprise Artifact Platform
Label: ${DISTRO}-${CODENAME}-mini
Suite: ${CODENAME}
Codename: ${CODENAME}
Architectures: amd64
Components: main
Description: Lightweight offline APT mini repository
EOF
    '

  echo "Mini repo created: ${out_dir}"
}

while IFS=$'\t' read -r distro codename image components; do
  [[ -z "${distro:-}" ]] && continue
  [[ "$distro" =~ ^# ]] && continue

  build_one_repo "$distro" "$codename" "$image" "$components"
done < "$TARGETS_FILE"

echo
echo "All mini APT repositories are ready under ${OUT_ROOT}"
