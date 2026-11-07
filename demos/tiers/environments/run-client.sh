#!/bin/bash
# =============================================================================
# Run R4 Client Environment
# =============================================================================
# Interactive shell with gateway access.
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
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8080}"

echo "=== R4 Client Environment ==="
echo "Gateway: ${GATEWAY_URL}"
echo ""

# Network options
if [[ "${CONTAINER_RUNTIME}" == "docker" ]]; then
    NETWORK_OPTS="--add-host=host.docker.internal:host-gateway"
else
    # Podman rootless - use slirp4netns with host loopback
    NETWORK_OPTS="--network=slirp4netns:allow_host_loopback=true"
fi

${CONTAINER_RUNTIME} run -it --rm \
    ${NETWORK_OPTS} \
    -e GATEWAY_URL="${GATEWAY_URL}" \
    -v "${REPO_ROOT}/demos:/workspace/demos:ro" \
    "${IMAGE_NAME}:${IMAGE_TAG}"
