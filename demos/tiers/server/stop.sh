#!/usr/bin/env bash
# =============================================================================
# Server Deployment Stop Script
# =============================================================================
# Stops all services and optionally cleans up data.
#
# Usage:
#   ./stop.sh             # Stop services (keep data)
#   ./stop.sh --volumes   # Stop and remove volumes
#   ./stop.sh --all       # Full cleanup (remove everything)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ${NC} $*"; }
log_success() { echo -e "${GREEN}✓${NC} $*"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $*"; }
log_error() { echo -e "${RED}✗${NC} $*"; }
log_header() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  $*${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Parse arguments
REMOVE_VOLUMES=false
FULL_CLEANUP=false

for arg in "$@"; do
    case $arg in
        --volumes)
            REMOVE_VOLUMES=true
            ;;
        --all)
            FULL_CLEANUP=true
            REMOVE_VOLUMES=true
            ;;
    esac
done

log_header "Stopping Server Deployment"

# Load env file if exists (for compose project name)
ENV_FILE="$SCRIPT_DIR/env.server"
COMPOSE_OVERRIDE="$SCRIPT_DIR/docker-compose.server.yaml"

cd "$PROJECT_ROOT"

# =============================================================================
# Step 1: Stop Services
# =============================================================================
log_info "Stopping all services..."

COMPOSE_FILES="-f docker-compose.e2e.yaml"
if [[ -f "$COMPOSE_OVERRIDE" ]]; then
    COMPOSE_FILES="$COMPOSE_FILES -f $COMPOSE_OVERRIDE"
fi

ENV_FILE_ARG=""
if [[ -f "$ENV_FILE" ]]; then
    ENV_FILE_ARG="--env-file $ENV_FILE"
fi

$COMPOSE_CMD $COMPOSE_FILES $ENV_FILE_ARG down --timeout 30

log_success "Services stopped"

# =============================================================================
# Step 2: Remove Volumes (Optional)
# =============================================================================
if [[ "$REMOVE_VOLUMES" == true ]]; then
    log_header "Removing Volumes"

    log_warn "This will delete all data (PostgreSQL, MinIO, uploads)"

    if [[ "$FULL_CLEANUP" != true ]]; then
        read -p "Are you sure? [y/N] " -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Volume removal cancelled"
            exit 0
        fi
    fi

    # Remove container volumes
    $COMPOSE_CMD $COMPOSE_FILES $ENV_FILE_ARG down --volumes

    # Remove local data directory
    DATA_DIR="$SCRIPT_DIR/data"
    if [[ -d "$DATA_DIR" ]]; then
        log_info "Removing local data: $DATA_DIR"
        rm -rf "$DATA_DIR"
    fi

    log_success "Volumes removed"
fi

# =============================================================================
# Step 3: Full Cleanup (Optional)
# =============================================================================
if [[ "$FULL_CLEANUP" == true ]]; then
    log_header "Full Cleanup"

    # Remove generated configuration
    if [[ -f "$ENV_FILE" ]]; then
        log_info "Removing: $ENV_FILE"
        rm -f "$ENV_FILE"
    fi

    if [[ -f "$COMPOSE_OVERRIDE" ]]; then
        log_info "Removing: $COMPOSE_OVERRIDE"
        rm -f "$COMPOSE_OVERRIDE"
    fi

    # Prune unused container resources
    log_info "Pruning unused container resources..."
    $CONTAINER_RUNTIME system prune -f --volumes > /dev/null 2>&1 || true

    log_success "Full cleanup complete"
fi

# =============================================================================
# Status
# =============================================================================
log_header "Shutdown Complete"

# Check for any remaining containers
REMAINING=$($COMPOSE_CMD $COMPOSE_FILES ps -q 2>/dev/null | wc -l || echo "0")
if [[ "$REMAINING" -gt 0 ]]; then
    log_warn "$REMAINING container(s) still running"
    $COMPOSE_CMD $COMPOSE_FILES ps
else
    log_success "All services stopped"
fi

echo ""
echo "To restart: ./start.sh"
if [[ "$REMOVE_VOLUMES" == true ]]; then
    echo "To setup fresh: ./setup.sh"
fi
echo ""
