#!/usr/bin/env bash
# =============================================================================
# Local E2E Validation Runner for ADR-012 Assistant Service
# =============================================================================
# This script starts the demo-llm.yaml stack (Assistant Service + LiteLLM +
# vLLM) and runs the e2e validation tests against the live Assistant Service.
#
# Prerequisites:
# - Podman or Docker installed
# - The demo-llm.yaml stack available at umrs/src/umrs/mini-rivault-llm/
# - Python .venv at the repository root
#
# Usage:
#   bash tests/run_assistant_e2e.sh           # Full local validation
#   bash tests/run_assistant_e2e.sh --cleanup # Cleanup only
#   bash tests/run_assistant_e2e.sh --skip-cleanup # Don't cleanup after
#
# Environment variables:
#   ASSISTANT_E2E_MODEL  - Model name (default: rivault-gpt-oss-static)
#   ASSISTANT_E2E_TIMEOUT - Request timeout (default: 60)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEMO_DIR="${PROJECT_ROOT}/umrs/src/umrs/mini-rivault-llm"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Parse arguments
CLEANUP_ONLY=false
SKIP_CLEANUP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --cleanup-only)
            CLEANUP_ONLY=true
            shift
            ;;
        --skip-cleanup)
            SKIP_CLEANUP=true
            shift
            ;;
        *)
            error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Check prerequisites
if [[ ! -d "${DEMO_DIR}" ]]; then
    error "Demo directory not found: ${DEMO_DIR}"
    exit 1
fi

# Determine container runtime
if command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
    COMPOSE_CMD="podman-compose"
elif command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
    COMPOSE_CMD="docker-compose"
else
    error "Neither podman nor docker found. Please install one."
    exit 1
fi

info "Using container runtime: ${CONTAINER_CMD}"
info "Using compose command: ${COMPOSE_CMD}"

# =============================================================================
# Cleanup function
# =============================================================================
cleanup() {
    info "Cleaning up demo-llm stack..."
    cd "${DEMO_DIR}"
    ${COMPOSE_CMD} -f demo-llm.yaml down --volumes --remove-orphans 2>/dev/null || true
    info "Cleanup complete."
}

# =============================================================================
# Main
# =============================================================================

if [[ "${CLEANUP_ONLY}" == "true" ]]; then
    cleanup
    exit 0
fi

# Start the demo-llm stack
info "Starting demo-llm stack..."
cd "${DEMO_DIR}"

# Set default environment variables for the demo stack
export ASSISTANT_PORT="${ASSISTANT_PORT:-8090}"
export ASSISTANT_DOCKER_IMAGE="${ASSISTANT_DOCKER_IMAGE:-python:3.12-slim}"
export ASSISTANT_DEFAULT_MODEL="${ASSISTANT_E2E_MODEL:-rivault-gpt-oss-static}"
export ASSISTANT_UPSTREAM_BASE_URL="${ASSISTANT_UPSTREAM_BASE_URL:-http://litellm:4000/v1}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-demo-key}"

# Start the stack
info "Running: ${COMPOSE_CMD} -f demo-llm.yaml up -d"
${COMPOSE_CMD} -f demo-llm.yaml up -d 2>&1 || {
    error "Failed to start demo-llm stack."
    cleanup
    exit 1
}

# Wait for the Assistant Service to be ready
info "Waiting for Assistant Service to be ready..."
ASSISTANT_URL="http://localhost:${ASSISTANT_PORT}"
MAX_WAIT=120
WAITED=0

while [[ ${WAITED} -lt ${MAX_WAIT} ]]; do
    if curl -s "${ASSISTANT_URL}/health" | grep -q '"status":"ok"' 2>/dev/null; then
        info "Assistant Service is ready at ${ASSISTANT_URL}"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done
echo

if [[ ${WAITED} -ge ${MAX_WAIT} ]]; then
    error "Assistant Service did not become ready within ${MAX_WAIT}s"
    error "Check logs with: ${COMPOSE_CMD} -f demo-llm.yaml logs assistant"
    cleanup
    exit 1
fi

# Run the e2e tests
info "Running e2e validation tests..."
cd "${PROJECT_ROOT}"

export ASSISTANT_E2E_URL="${ASSISTANT_URL}"
export ASSISTANT_E2E_MODEL="${ASSISTANT_E2E_MODEL:-rivault-gpt-oss-static}"
export ASSISTANT_E2E_TIMEOUT="${ASSISTANT_E2E_TIMEOUT:-60}"

if .venv/bin/python -m pytest tests/test_assistant_e2e.py -v -s 2>&1; then
    info "E2E validation tests PASSED!"
    TEST_RESULT=0
else
    error "E2E validation tests FAILED!"
    TEST_RESULT=1
fi

# Cleanup (unless skipped)
if [[ "${SKIP_CLEANUP}" != "true" ]]; then
    cleanup
else
    warn "Skipping cleanup. Run 'bash tests/run_assistant_e2e.sh --cleanup-only' to clean up."
fi

exit ${TEST_RESULT}
