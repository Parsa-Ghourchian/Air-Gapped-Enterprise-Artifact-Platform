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

nexus_image_path() {
  local image="$1"
  local first="${image%%/*}"
  local rest=""

  if [[ "$image" == */* && ( "$first" == *.* || "$first" == *:* || "$first" == "localhost" ) ]]; then
    rest="${image#*/}"
    case "$first" in
      docker.io|index.docker.io|registry-1.docker.io)
        image="$rest"
        ;;
      *)
        image="${first}/${rest}"
        ;;
    esac
  fi

  local name_without_digest="${image%%@*}"
  local name_without_tag="$name_without_digest"
  local last_component="${name_without_digest##*/}"
  if [[ "$last_component" == *:* ]]; then
    name_without_tag="${name_without_digest%:*}"
  fi

  if [[ "$name_without_tag" != */* ]]; then
    image="library/${image}"
  fi

  printf '%s\n' "$image"
}

while IFS= read -r image || [[ -n "$image" ]]; do
  [[ -z "$image" ]] && continue
  [[ "$image" =~ ^# ]] && continue

  target="${TARGET_REGISTRY}/$(nexus_image_path "$image")"

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
