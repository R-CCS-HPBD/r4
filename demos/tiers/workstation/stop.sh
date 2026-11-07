#!/usr/bin/env bash
# =============================================================================
# R4 Platform - Workstation Stop
# =============================================================================
# Stops all platform services.
#
# Usage:
#   ./stop.sh               # Stop services, keep data
#   ./stop.sh --volumes     # Stop services and remove volumes
#   ./stop.sh --all         # Stop, remove volumes, and clean images
# =============================================================================

set -euo pipefail

# Script locations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ROOT="$(cd "$DEMOS_DIR/.." && pwd)"
RIVAULT_DIR="$PROJECT_ROOT/rivault"
UMRS_DIR="$PROJECT_ROOT/umrs/docker"

# Source runtime detection library
if [[ -f "$DEMOS_DIR/lib/runtime.sh" ]]; then
    source "$DEMOS_DIR/lib/runtime.sh"
    init_container_runtime
else
    echo "ERROR: Runtime library not found at $DEMOS_DIR/lib/runtime.sh"
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

# Options
REMOVE_VOLUMES=false
REMOVE_ALL=false

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --volumes|-v)
            REMOVE_VOLUMES=true
            shift
            ;;
        --all)
            REMOVE_VOLUMES=true
            REMOVE_ALL=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --volumes, -v  Remove container volumes (deletes data)"
            echo "  --all          Remove volumes and prune images"
            echo "  -h, --help     Show this help"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# =============================================================================
# Banner
# =============================================================================
echo ""
echo "=============================================="
echo "  Stopping R4 Platform"
echo "=============================================="
echo ""

# =============================================================================
# Stop RiVault Stack
# =============================================================================
if [[ -f "$RIVAULT_DIR/env.mini-rivault" ]]; then
    log_info "Stopping RiVault stack..."
    cd "$RIVAULT_DIR"

    if [[ "$REMOVE_VOLUMES" == "true" ]]; then
        $COMPOSE_CMD --env-file env.mini-rivault \
            -f serve-model.yaml \
            --profile vllm --profile harness \
            down -v 2>/dev/null || true
    else
        $COMPOSE_CMD --env-file env.mini-rivault \
            -f serve-model.yaml \
            --profile vllm --profile harness \
            down 2>/dev/null || true
    fi

    log_ok "RiVault stopped"
    cd "$SCRIPT_DIR"
else
    log_info "RiVault env not found, skipping"
fi

# =============================================================================
# Stop UMRS Stack
# =============================================================================
if [[ -f "$UMRS_DIR/.env" ]]; then
    log_info "Stopping UMRS stack..."
    cd "$UMRS_DIR"

    export GLOBUS_DIR="${GLOBUS_DIR:-/tmp/globus}"

    if [[ "$REMOVE_VOLUMES" == "true" ]]; then
        $COMPOSE_CMD down -v 2>/dev/null || true
    else
        $COMPOSE_CMD down 2>/dev/null || true
    fi

    log_ok "UMRS stopped"
    cd "$SCRIPT_DIR"
else
    log_info "UMRS env not found, skipping"
fi

# =============================================================================
# Clean up (optional)
# =============================================================================
if [[ "$REMOVE_ALL" == "true" ]]; then
    log_info "Cleaning up unused container resources..."
    $CONTAINER_RUNTIME system prune -f
    log_ok "Cleanup complete"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
log_ok "Platform stopped"

if [[ "$REMOVE_VOLUMES" == "true" ]]; then
    log_info "Data volumes removed"
fi

echo ""
