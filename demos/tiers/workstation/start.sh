#!/usr/bin/env bash
# =============================================================================
# R4 Platform - Workstation Start
# =============================================================================
# Starts all platform services on a single-GPU workstation.
#
# Usage:
#   ./start.sh              # Start all services
#   ./start.sh --rivault    # Start RiVault only (vLLM + LiteLLM)
#   ./start.sh --umrs       # Start UMRS only (UMRS + PostgreSQL + MinIO)
# =============================================================================

set -euo pipefail

# Script locations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ROOT="$(cd "$DEMOS_DIR/.." && pwd)"
RIVAULT_DIR="$PROJECT_ROOT/rivault"
UMRS_DIR="$PROJECT_ROOT/umrs/docker"
GATEWAY_DIR="$PROJECT_ROOT/gateway"

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
NC='\033[0m'

# Options
START_RIVAULT=true
START_UMRS=true
START_GATEWAY=true

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --rivault)
            START_UMRS=false
            START_GATEWAY=false
            shift
            ;;
        --umrs)
            START_RIVAULT=false
            START_GATEWAY=false
            shift
            ;;
        --gateway)
            START_RIVAULT=false
            START_UMRS=false
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --rivault    Start RiVault only (vLLM + LiteLLM)"
            echo "  --umrs       Start UMRS only (UMRS + PostgreSQL + MinIO)"
            echo "  --gateway    Start Gateway only"
            echo "  -h, --help   Show this help"
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
echo "  Starting R4 Platform"
echo "=============================================="
echo ""

# Check for environment files
if [[ "$START_RIVAULT" == "true" ]] && [[ ! -f "$RIVAULT_DIR/env.mini-rivault" ]]; then
    log_error "rivault/env.mini-rivault not found. Run ./setup.sh first."
    exit 1
fi

if [[ "$START_UMRS" == "true" ]] && [[ ! -f "$UMRS_DIR/.env" ]]; then
    log_error "umrs/docker/.env not found. Run ./setup.sh first."
    exit 1
fi

if [[ -f "$SCRIPT_DIR/env.local" ]]; then
    source "$SCRIPT_DIR/env.local"
fi

# =============================================================================
# Start UMRS Stack
# =============================================================================
if [[ "$START_UMRS" == "true" ]]; then
    log_info "Starting UMRS stack (PostgreSQL, MinIO, UMRS)..."

    # Ensure GLOBUS_DIR exists
    export GLOBUS_DIR="${GLOBUS_DIR:-/tmp/globus}"
    mkdir -p "$GLOBUS_DIR"

    cd "$UMRS_DIR"
    $COMPOSE_CMD up -d

    log_ok "UMRS stack started"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Start RiVault Stack
# =============================================================================
if [[ "$START_RIVAULT" == "true" ]]; then
    log_info "Starting RiVault stack (vLLM + LiteLLM)..."

    cd "$RIVAULT_DIR"

    # Determine GPU vendor and use appropriate compose file
    if nvidia-smi &> /dev/null; then
        log_info "Using NVIDIA GPU configuration"
        $COMPOSE_CMD --env-file env.mini-rivault \
            -f serve-model.yaml \
            -f x_nvidia-override.yaml \
            --profile vllm --profile harness \
            up -d
    elif command -v rocm-smi &> /dev/null; then
        log_info "Using AMD ROCm configuration"
        $COMPOSE_CMD --env-file env.mini-rivault \
            -f serve-model.yaml \
            -f x_rocm-override.yaml \
            --profile vllm --profile harness \
            up -d
    else
        log_error "No supported GPU found (NVIDIA or AMD)"
        exit 1
    fi

    log_ok "RiVault stack started"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Start Gateway
# =============================================================================
if [[ "$START_GATEWAY" == "true" ]]; then
    log_info "Starting Platform Gateway..."

    cd "$GATEWAY_DIR"

    # Start Gateway (uses Dockerfile in gateway/)
    $COMPOSE_CMD up -d 2>/dev/null || {
        # Fallback: run gateway directly if no compose file
        log_info "Starting Gateway via Python..."
        python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8080 &
    }

    log_ok "Gateway started"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Wait for Services
# =============================================================================
log_info "Waiting for services to be ready..."
echo ""

MAX_WAIT=300  # 5 minutes for vLLM model loading
INTERVAL=5
ELAPSED=0

while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    ALL_HEALTHY=true

    # Check vLLM
    if [[ "$START_RIVAULT" == "true" ]]; then
        if curl -sf http://localhost:8000/health &>/dev/null 2>&1; then
            echo -e "  vLLM:     ${GREEN}healthy${NC}"
        else
            echo -e "  vLLM:     ${YELLOW}starting (model loading)...${NC}"
            ALL_HEALTHY=false
        fi

        # Check LiteLLM
        if curl -sf http://localhost:11434/health/liveliness &>/dev/null 2>&1; then
            echo -e "  LiteLLM:  ${GREEN}healthy${NC}"
        else
            echo -e "  LiteLLM:  ${YELLOW}starting...${NC}"
            ALL_HEALTHY=false
        fi
    fi

    # Check UMRS services
    if [[ "$START_UMRS" == "true" ]]; then
        if curl -sf http://localhost:8001/health &>/dev/null 2>&1 || \
           curl -sf http://localhost:8001/ &>/dev/null 2>&1; then
            echo -e "  UMRS:     ${GREEN}healthy${NC}"
        else
            echo -e "  UMRS:     ${YELLOW}starting...${NC}"
            ALL_HEALTHY=false
        fi

        # Check PostgreSQL
        if $CONTAINER_RUNTIME exec -it postgres pg_isready -U postgres &>/dev/null 2>&1; then
            echo -e "  Postgres: ${GREEN}healthy${NC}"
        else
            echo -e "  Postgres: ${YELLOW}starting...${NC}"
            ALL_HEALTHY=false
        fi

        # Check MinIO
        if curl -sf http://localhost:9000/minio/health/live &>/dev/null 2>&1; then
            echo -e "  MinIO:    ${GREEN}healthy${NC}"
        else
            echo -e "  MinIO:    ${YELLOW}starting...${NC}"
            ALL_HEALTHY=false
        fi
    fi

    # Check Gateway
    if [[ "$START_GATEWAY" == "true" ]]; then
        if curl -sf http://localhost:8080/health &>/dev/null 2>&1; then
            echo -e "  Gateway:  ${GREEN}healthy${NC}"
        else
            echo -e "  Gateway:  ${YELLOW}starting...${NC}"
            ALL_HEALTHY=false
        fi
    fi

    if [[ "$ALL_HEALTHY" == "true" ]]; then
        echo ""
        log_ok "All services are healthy!"
        break
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
    echo ""
done

if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    log_warn "Timeout waiting for services. Some services may still be starting."
    log_info "Check logs with: $COMPOSE_CMD logs -f"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================="
echo "  Platform Started!"
echo "=============================================="
echo ""
echo "Service endpoints:"

echo ""
echo "  Platform Gateway (main entry point):"
echo "    Gateway:        http://localhost:8080"
echo "    LLM API:        http://localhost:8080/gw/llm/v1/*"
echo "    RAG API:        http://localhost:8080/gw/rag/*"
echo "    MCP API:        http://localhost:8080/gw/mcp/*"

if [[ "$START_RIVAULT" == "true" ]]; then
    echo ""
    echo "  Internal (use Gateway instead):"
    echo "    LiteLLM:      http://localhost:11434 (internal)"
fi

if [[ "$START_UMRS" == "true" ]]; then
    echo "    UMRS:         http://localhost:8001 (internal)"
    echo "    MinIO Console: http://localhost:9001 (minioadmin/minioadmin)"
    echo "    PostgreSQL:   localhost:5432"
fi

echo ""
echo "Next steps:"
echo "  - Run demo: ./demo.sh"
echo "  - Stop:     ./stop.sh"
echo ""
