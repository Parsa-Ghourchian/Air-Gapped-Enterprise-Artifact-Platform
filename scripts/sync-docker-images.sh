#!/usr/bin/env bash
set -euo pipefail

# Pull images from public registries and push them into Nexus docker-hosted.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a

IMAGE_FILE="${1:-configs/docker-images.txt}"
TARGET_REGISTRY="${NEXUS_DOCKER_HOSTED:-localhost:5000}"

if [[ ! -f "$IMAGE_FILE" ]]; then
  echo "ERROR: Image list not found: $IMAGE_FILE"
  exit 1
fi

echo "Target Nexus Docker hosted registry: ${TARGET_REGISTRY}"
echo "Image list: ${IMAGE_FILE}"
echo

while IFS= read -r image || [[ -n "$image" ]]; do
  [[ -z "$image" ]] && continue
  [[ "$image" =~ ^# ]] && continue

  target="${TARGET_REGISTRY}/${image}"

  echo "Pulling: ${image}"
  docker pull "$image"

  echo "Tagging: ${image} -> ${target}"
  docker tag "$image" "$target"

  echo "Pushing: ${target}"
  docker push "$target"

  echo "Done: ${image}"
  echo
done < "$IMAGE_FILE"

echo "Docker image sync completed."
