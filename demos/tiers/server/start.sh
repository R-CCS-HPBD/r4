#!/usr/bin/env bash
# =============================================================================
# Server Deployment Start Script
# =============================================================================
# Starts all services for multi-GPU server deployment.
#
# Usage:
#   ./start.sh              # Start all services
#   ./start.sh --detach     # Start in background (default)
#   ./start.sh --foreground # Start in foreground with logs
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
DETACH="--detach"
for arg in "$@"; do
    case $arg in
        --foreground)
            DETACH=""
            ;;
        --detach)
            DETACH="--detach"
            ;;
    esac
done

log_header "Starting Server Deployment"

# =============================================================================
# Step 1: Validate Configuration
# =============================================================================
log_info "Checking configuration..."

ENV_FILE="$SCRIPT_DIR/env.server"
COMPOSE_OVERRIDE="$SCRIPT_DIR/docker-compose.server.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
    log_error "Configuration not found: $ENV_FILE"
    log_error "Run ./setup.sh first"
    exit 1
fi

if [[ ! -f "$COMPOSE_OVERRIDE" ]]; then
    log_error "Docker Compose override not found: $COMPOSE_OVERRIDE"
    log_error "Run ./setup.sh first"
    exit 1
fi

# Load configuration
source "$ENV_FILE"

log_success "Configuration loaded"
log_info "  Model: $VLLM_MODEL_PATH"
log_info "  GPUs: $GPU_COUNT (TP=$VLLM_TENSOR_PARALLEL_SIZE)"
log_info "  Max Sequences: $VLLM_MAX_NUM_SEQS"

# Validate model path
if [[ ! -d "$VLLM_MODEL_PATH" ]]; then
    log_error "Model not found: $VLLM_MODEL_PATH"
    exit 1
fi

# =============================================================================
# Step 2: Check GPU Availability
# =============================================================================
log_info "Checking GPU availability..."

if [[ "$GPU_TYPE" == "nvidia" ]]; then
    AVAILABLE_GPUS=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
    if [[ $AVAILABLE_GPUS -lt $GPU_COUNT ]]; then
        log_error "Configured for $GPU_COUNT GPUs but only $AVAILABLE_GPUS available"
        exit 1
    fi
    # Quick GPU health check
    if ! nvidia-smi > /dev/null 2>&1; then
        log_error "nvidia-smi failed. Check GPU drivers."
        exit 1
    fi
    log_success "All $GPU_COUNT GPUs available"
elif [[ "$GPU_TYPE" == "amd" ]]; then
    if ! rocm-smi > /dev/null 2>&1; then
        log_error "rocm-smi failed. Check GPU drivers."
        exit 1
    fi
    log_success "AMD GPUs available"
fi

# =============================================================================
# Step 3: Start Infrastructure Services
# =============================================================================
log_header "Starting Infrastructure Services"

cd "$PROJECT_ROOT"

# Start PostgreSQL and MinIO first
log_info "Starting PostgreSQL and MinIO..."
docker compose -f docker-compose.e2e.yaml \
    --env-file "$ENV_FILE" \
    up -d postgres minio

# Wait for PostgreSQL
log_info "Waiting for PostgreSQL..."
for i in {1..60}; do
    if docker compose -f docker-compose.e2e.yaml exec -T postgres pg_isready -U "${POSTGRES_USER:-umrs}" > /dev/null 2>&1; then
        log_success "PostgreSQL is ready"
        break
    fi
    if [[ $i -eq 60 ]]; then
        log_error "PostgreSQL failed to start"
        exit 1
    fi
    sleep 2
done

# Wait for MinIO
log_info "Waiting for MinIO..."
for i in {1..30}; do
    if curl -sf "http://localhost:${MINIO_PORT:-9000}/minio/health/live" > /dev/null 2>&1; then
        log_success "MinIO is ready"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log_error "MinIO failed to start"
        exit 1
    fi
    sleep 2
done

# =============================================================================
# Step 4: Start UMRS
# =============================================================================
log_header "Starting UMRS"

docker compose -f docker-compose.e2e.yaml \
    --env-file "$ENV_FILE" \
    up -d umrs

log_info "Waiting for UMRS..."
for i in {1..60}; do
    if curl -sf "http://localhost:${UMRS_PORT:-8001}/health" > /dev/null 2>&1; then
        log_success "UMRS is ready"
        break
    fi
    if [[ $i -eq 60 ]]; then
        log_error "UMRS failed to start"
        docker compose -f docker-compose.e2e.yaml logs umrs
        exit 1
    fi
    sleep 2
done

# =============================================================================
# Step 5: Start vLLM + LiteLLM (RiVault Stack)
# =============================================================================
log_header "Starting RiVault (vLLM + LiteLLM)"

log_info "Starting vLLM with $VLLM_TENSOR_PARALLEL_SIZE GPU(s)..."
log_warn "Large models may take several minutes to load"

docker compose -f docker-compose.e2e.yaml \
    -f "$COMPOSE_OVERRIDE" \
    --env-file "$ENV_FILE" \
    up -d vllm

# Wait for vLLM (longer timeout for large models)
log_info "Waiting for vLLM to load model..."
VLLM_TIMEOUT=600  # 10 minutes for large models
for i in $(seq 1 $VLLM_TIMEOUT); do
    if curl -sf "http://localhost:${VLLM_PORT:-8000}/health" > /dev/null 2>&1; then
        log_success "vLLM is ready"
        break
    fi
    if [[ $i -eq $VLLM_TIMEOUT ]]; then
        log_error "vLLM failed to start within ${VLLM_TIMEOUT}s"
        docker compose -f docker-compose.e2e.yaml logs vllm --tail=100
        exit 1
    fi
    # Progress indicator every 30 seconds
    if [[ $((i % 30)) -eq 0 ]]; then
        log_info "Still loading model... (${i}s elapsed)"
    fi
    sleep 1
done

# Start LiteLLM
log_info "Starting LiteLLM proxy..."
docker compose -f docker-compose.e2e.yaml \
    -f "$COMPOSE_OVERRIDE" \
    --env-file "$ENV_FILE" \
    up -d litellm

log_info "Waiting for LiteLLM..."
for i in {1..60}; do
    if curl -sf "http://localhost:${LITELLM_PORT:-11434}/health" > /dev/null 2>&1; then
        log_success "LiteLLM is ready"
        break
    fi
    if [[ $i -eq 60 ]]; then
        log_error "LiteLLM failed to start"
        docker compose -f docker-compose.e2e.yaml logs litellm
        exit 1
    fi
    sleep 2
done

# =============================================================================
# Step 6: Final Validation
# =============================================================================
log_header "Validating Deployment"

# Check model availability
log_info "Checking model availability..."
MODELS=$(curl -sf "http://localhost:${LITELLM_PORT:-11434}/v1/models" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(' '.join(m['id'] for m in d.get('data',[])))" 2>/dev/null || echo "")

if [[ -z "$MODELS" ]]; then
    log_warn "Could not retrieve model list"
else
    log_success "Available models: $MODELS"
fi

# Quick inference test
log_info "Testing inference..."
RESPONSE=$(curl -sf "http://localhost:${LITELLM_PORT:-11434}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -d '{
        "model": "'"${VLLM_MODEL_NAME:-rivault-gpt-oss-static}"'",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 10
    }' 2>/dev/null || echo "")

if [[ -n "$RESPONSE" ]] && echo "$RESPONSE" | grep -q "choices"; then
    log_success "Inference test passed"
else
    log_warn "Inference test inconclusive"
fi

# =============================================================================
# Startup Complete
# =============================================================================
log_header "Server Deployment Started"

echo "Services Running:"
echo "  vLLM:       http://localhost:${VLLM_PORT:-8000}"
echo "  LiteLLM:    http://localhost:${LITELLM_PORT:-11434}"
echo "  UMRS:       http://localhost:${UMRS_PORT:-8001}"
echo "  MinIO:      http://localhost:${MINIO_CONSOLE_PORT:-9001}"
echo ""
echo "GPU Configuration:"
echo "  Type:           $GPU_TYPE"
echo "  Count:          $GPU_COUNT"
echo "  Tensor Parallel: $VLLM_TENSOR_PARALLEL_SIZE"
echo ""
echo "View logs:     docker compose -f docker-compose.e2e.yaml logs -f"
echo "Stop services: ./stop.sh"
echo "Run demo:      ./demo.sh"
echo ""

# Show GPU utilization
if [[ "$GPU_TYPE" == "nvidia" ]]; then
    log_info "Current GPU utilization:"
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv
fi

log_success "Server deployment is ready!"
