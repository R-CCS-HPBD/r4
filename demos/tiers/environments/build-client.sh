#!/bin/bash
# =============================================================================
# Build R4 Client Environment Image
# =============================================================================
# Builds container image for Podman/Docker or Singularity.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source runtime detection library
if [[ -f "$DEMOS_DIR/lib/runtime.sh" ]]; then
    source "$DEMOS_DIR/lib/runtime.sh"
    detect_container_runtime
else
    echo "ERROR: Runtime library not found at $DEMOS_DIR/lib/runtime.sh"
    exit 1
fi

IMAGE_NAME="${IMAGE_NAME:-r4-client}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "=== Building R4 Client Environment ==="
echo "Runtime: ${CONTAINER_RUNTIME}"
echo "Image:   ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""

cd "${REPO_ROOT}"

${CONTAINER_RUNTIME} build \
    -f demos/tiers/environments/client/Containerfile \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    .

echo ""
echo "=== Build Complete ==="
echo ""
echo "To run:"
echo "  ${CONTAINER_RUNTIME} run -it --rm ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "With gateway access:"
echo "  ${CONTAINER_RUNTIME} run -it --rm \\"
echo "    --network=host \\"
echo "    -e GATEWAY_URL=http://localhost:8080 \\"
echo "    ${IMAGE_NAME}:${IMAGE_TAG}"
