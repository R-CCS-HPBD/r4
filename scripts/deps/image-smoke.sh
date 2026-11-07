#!/usr/bin/env bash
# Build the umrs-server image (CPU torch, no bundled embedder) and run its
# smoke test (ADR-100). Dockerfile.server already runs the smoke test as its
# last build step; the explicit `run` below proves the TAGGED image too.
#
# Usage: scripts/deps/image-smoke.sh [<tag>]     (default r4ci/umrs-server-smoke:local)
# Env:   CONTAINER_CLI=docker|podman (default: docker if present, else podman)
#        KEEP_IMAGE=1 keeps the image afterwards (default: removed)
#
# It removes only the image it tagged, never anything else on the host.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tag="${1:-r4ci/umrs-server-smoke:local}"
cli="${CONTAINER_CLI:-$(command -v docker >/dev/null 2>&1 && echo docker || echo podman)}"
cpu_index="https://download.pytorch.org/whl/cpu"

cleanup() {
  if [ "${KEEP_IMAGE:-0}" != "1" ]; then
    "$cli" rmi "$tag" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# docker's buildx docker-container driver (the CI runner) keeps the result in
# the build cache unless told to load it; `run` then tried to pull the tag.
load=()
if [ "$cli" = docker ]; then load=(--load); fi

start=$(date +%s)
"$cli" build "${load[@]}" \
  --build-arg TORCH_INDEX_URL="$cpu_index" \
  --build-arg UMRS_BUNDLE_TEXT_EMBEDDER=0 \
  -f "$root/umrs/docker/Dockerfile.server" -t "$tag" "$root"
"$cli" run --rm --network=none "$tag" python /opt/umrs/server-image-smoke.py
echo "image smoke: PASS ($tag, $(( $(date +%s) - start )) s, $("$cli" image inspect --format '{{.Size}}' "$tag") bytes)"
