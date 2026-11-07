#!/usr/bin/env bash
# =============================================================================
# R4 Platform - DGX Spark Start
# =============================================================================
# Starts all platform services on NVIDIA DGX Spark.
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

# Source container runtime detection (podman preferred)
source "$DEMOS_DIR/lib/runtime.sh"
init_container_runtime || exit 1

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Options
START_RIVAULT=true
START_UMRS=true
# Gateway disabled by default - LLM already accessible via nginx at :11434
START_GATEWAY=true

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_spark() { echo -e "${CYAN}[DGX Spark]${NC} $*"; }

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
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║       Starting R4 Platform on DGX Spark             ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Check architecture
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]]; then
    log_spark "Running on ARM64 (Grace CPU)"
else
    log_warn "Expected ARM64, found: $ARCH"
fi

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
# Create Shared Network (r4-network) Early
# =============================================================================
# Gateway, LiteLLM, and UMRS all need to be on the same network so NGINX
# can resolve their hostnames. Create the network before any stack starts.
if ! $CONTAINER_RUNTIME network exists r4-network 2>/dev/null; then
    log_info "Creating r4-network..."
    $CONTAINER_RUNTIME network create r4-network
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

    # Connect umrs-server to r4-network so the gateway NGINX can resolve
    # it by hostname. The container may not be ready yet, but the network
    # connection can be added immediately after compose brings it up.
    if $CONTAINER_RUNTIME container exists umrs-server 2>/dev/null; then
        $CONTAINER_RUNTIME network connect --alias umrs r4-network umrs-server 2>/dev/null || true
        log_info "Connected umrs-server to r4-network (alias: umrs)"
    fi

    log_ok "UMRS stack started"
    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Start RiVault Stack
# =============================================================================
if [[ "$START_RIVAULT" == "true" ]]; then
    log_info "Starting RiVault stack (vLLM + LiteLLM)..."
    log_spark "Using DGX Spark optimized settings"

    cd "$RIVAULT_DIR"

    # Create the external network if it doesn't exist (required for RiVault)
    if ! $CONTAINER_RUNTIME network exists genai-private-net 2>/dev/null; then
        log_info "Creating genai-private-net network..."
        $CONTAINER_RUNTIME network create --driver bridge --subnet 10.0.0.0/24 genai-private-net
    fi

    # DGX Spark uses NVIDIA GPU
    if nvidia-smi &> /dev/null; then
        log_info "Using NVIDIA GPU configuration"

        # podman-compose doesn't support --profile, so we start services explicitly
        # Services for vllm+harness profiles: nginx, litellm, ll-postgres, vllm, mcpo, mcp-time
        if [[ "$COMPOSE_CMD" == "podman-compose" ]]; then
            log_info "Using podman-compose (starting services explicitly)"
            $COMPOSE_CMD --env-file env.mini-rivault \
                -f serve-model.yaml \
                -f x_nvidia-override.yaml \
                up -d nginx litellm ll-postgres vllm mcpo mcp-time
        else
            # docker compose supports --profile or COMPOSE_PROFILES
            export COMPOSE_PROFILES="vllm,harness"
            $COMPOSE_CMD --env-file env.mini-rivault \
                -f serve-model.yaml \
                -f x_nvidia-override.yaml \
                up -d
        fi
    else
        log_error "NVIDIA GPU not detected"
        exit 1
    fi

    # Connect rivault_litellm_1 to r4-network so the gateway NGINX can
    # resolve "litellm" by hostname.
    if $CONTAINER_RUNTIME container exists rivault_litellm_1 2>/dev/null; then
        $CONTAINER_RUNTIME network connect --alias litellm r4-network rivault_litellm_1 2>/dev/null || true
        log_info "Connected rivault_litellm_1 to r4-network (alias: litellm)"
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

    if [[ -f "compose/gateway.yml" ]]; then
        $COMPOSE_CMD -f compose/gateway.yml up -d
        log_ok "Gateway started"
    else
        log_warn "Gateway compose file not found, skipping"
    fi

    cd "$SCRIPT_DIR"
fi

# =============================================================================
# Wait for Services
# =============================================================================
log_info "Waiting for services to become healthy..."

echo ""

# Wait for UMRS using the UMRS client ping command
if [[ "$START_UMRS" == "true" ]]; then
    log_info "Waiting for UMRS (using client ping)..."
    if wait_for_umrs 60 5; then
        log_ok "UMRS is healthy (ping successful)"
    else
        log_warn "UMRS not responding to ping after 300 seconds"
    fi
fi

# Wait for LiteLLM (takes longer due to model loading)
if [[ "$START_RIVAULT" == "true" ]]; then
    log_spark "Waiting for vLLM model loading (this may take several minutes for large models)..."
    if wait_for_llm "http://localhost:11434/v1/models" "" 120 5; then
        log_ok "LiteLLM is healthy"
    else
        log_warn "LiteLLM not ready after 600 seconds"
    fi
fi

# Wait for Gateway
if [[ "$START_GATEWAY" == "true" ]]; then
    log_info "Waiting for Gateway..."
    if wait_for_gateway "http://localhost:8080/health" 30 5; then
        log_ok "Gateway is healthy"
    else
        log_warn "Gateway not responding after 150 seconds"
    fi
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                 DGX Spark Platform Running                       ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Services:"
if [[ "$START_GATEWAY" == "true" ]]; then
    echo "  Gateway:    http://localhost:8080"
fi
if [[ "$START_RIVAULT" == "true" ]]; then
    echo "  LiteLLM:    http://localhost:11434/v1"
fi
if [[ "$START_UMRS" == "true" ]]; then
    echo "  UMRS:       http://localhost:8001"
    echo "  MinIO:      http://localhost:9001 (console)"
fi
echo ""
echo "Run the demo: ./demo.sh"
echo ""
