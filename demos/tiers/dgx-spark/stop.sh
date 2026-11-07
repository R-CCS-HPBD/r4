#!/usr/bin/env bash
# =============================================================================
# R4 Platform - DGX Spark Stop
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
GATEWAY_DIR="$PROJECT_ROOT/gateway"

# Source container runtime detection (podman preferred)
source "$DEMOS_DIR/lib/runtime.sh"
init_container_runtime || exit 1

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Options
REMOVE_VOLUMES=false
REMOVE_ALL=false

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_spark() { echo -e "${CYAN}[DGX Spark]${NC} $*"; }

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
            echo "  --volumes, -v  Remove volumes (deletes data)"
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
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║         Stopping R4 Platform (DGX Spark)            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# Stop Gateway
# =============================================================================
if [[ -f "$GATEWAY_DIR/compose/gateway.yml" ]]; then
    log_info "Stopping Gateway..."
    cd "$GATEWAY_DIR"

    if [[ "$REMOVE_VOLUMES" == "true" ]]; then
        $COMPOSE_CMD -f compose/gateway.yml down -v 2>/dev/null || true
    else
        $COMPOSE_CMD -f compose/gateway.yml down 2>/dev/null || true
    fi

    log_ok "Gateway stopped"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Stop RiVault Stack
# =============================================================================
if [[ -f "$RIVAULT_DIR/env.mini-rivault" ]]; then
    log_info "Stopping RiVault stack..."
    cd "$RIVAULT_DIR"

    # podman-compose doesn't support --profile, so we stop containers explicitly
    if [[ "$COMPOSE_CMD" == "podman-compose" ]]; then
        # Stop and remove RiVault containers
        RIVAULT_CONTAINERS="rivault_nginx_1 rivault_litellm_1 rivault_ll-postgres_1 rivault_vllm_1 rivault_mcpo_1 rivault_mcp-time_1"
        for container in $RIVAULT_CONTAINERS; do
            $CONTAINER_RUNTIME stop "$container" 2>/dev/null || true
            $CONTAINER_RUNTIME rm "$container" 2>/dev/null || true
        done
    else
        if [[ "$REMOVE_VOLUMES" == "true" ]]; then
            $COMPOSE_CMD --env-file env.mini-rivault \
                -f serve-model.yaml \
                -f x_nvidia-override.yaml \
                --profile vllm --profile harness \
                down -v 2>/dev/null || true
        else
            $COMPOSE_CMD --env-file env.mini-rivault \
                -f serve-model.yaml \
                -f x_nvidia-override.yaml \
                --profile vllm --profile harness \
                down 2>/dev/null || true
        fi
    fi

    log_ok "RiVault stack stopped"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Stop UMRS Stack
# =============================================================================
if [[ -f "$UMRS_DIR/.env" ]]; then
    log_info "Stopping UMRS stack..."
    cd "$UMRS_DIR"

    # podman-compose doesn't handle down well, stop containers explicitly
    if [[ "$COMPOSE_CMD" == "podman-compose" ]]; then
        UMRS_CONTAINERS="umrs-server umrs-embed-worker postgres minio"
        for container in $UMRS_CONTAINERS; do
            $CONTAINER_RUNTIME stop "$container" 2>/dev/null || true
            $CONTAINER_RUNTIME rm "$container" 2>/dev/null || true
        done
    else
        if [[ "$REMOVE_VOLUMES" == "true" ]]; then
            $COMPOSE_CMD down -v 2>/dev/null || true
        else
            $COMPOSE_CMD down 2>/dev/null || true
        fi
    fi

    log_ok "UMRS stack stopped"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Cleanup (if requested)
# =============================================================================
if [[ "$REMOVE_ALL" == "true" ]]; then
    log_info "Pruning unused container images..."
    $CONTAINER_RUNTIME image prune -f
    log_ok "Cleanup complete"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
log_spark "All services stopped"
if [[ "$REMOVE_VOLUMES" == "true" ]]; then
    echo "  Volumes: removed"
fi
if [[ "$REMOVE_ALL" == "true" ]]; then
    echo "  Images: pruned"
fi
echo ""
