#!/usr/bin/env bash
# =============================================================================
# Server Deployment Setup Script
# =============================================================================
# One-time setup for multi-GPU server deployment.
#
# Requirements:
#   - 2-8 NVIDIA or AMD GPUs
#   - Docker with GPU support
#   - 256GB+ RAM recommended
#
# Usage:
#   ./setup.sh                    # Interactive setup
#   ./setup.sh --model <path>     # Specify model path
#   ./setup.sh --gpus 4           # Specify number of GPUs
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
MODEL_PATH=""
NUM_GPUS=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --model)
            MODEL_PATH="$2"
            shift 2
            ;;
        --gpus)
            NUM_GPUS="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

log_header "Server Deployment Setup"

# =============================================================================
# Step 1: Check Prerequisites
# =============================================================================
log_header "Step 1: Checking Prerequisites"

# Check container runtime (already detected by init_container_runtime)
if [[ -z "$CONTAINER_RUNTIME" ]]; then
    log_error "No container runtime found (docker or podman)"
    exit 1
fi
log_success "$CONTAINER_RUNTIME is installed"

# Check compose command (already detected by init_container_runtime)
if [[ -z "$COMPOSE_CMD" ]]; then
    log_error "No compose command found (docker compose or podman compose)"
    exit 1
fi
log_success "Compose command available: $COMPOSE_CMD"

# Detect GPU type and count
GPU_TYPE=""
GPU_COUNT=0

if command -v nvidia-smi &> /dev/null; then
    GPU_TYPE="nvidia"
    GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
    log_success "Detected $GPU_COUNT NVIDIA GPU(s)"
    nvidia-smi --query-gpu=name,memory.total --format=csv
elif command -v rocm-smi &> /dev/null; then
    GPU_TYPE="amd"
    GPU_COUNT=$(rocm-smi --showid | grep -c "GPU")
    log_success "Detected $GPU_COUNT AMD GPU(s)"
    rocm-smi --showmeminfo vram
else
    log_error "No NVIDIA or AMD GPUs detected"
    exit 1
fi

# Validate GPU count for server deployment
if [[ $GPU_COUNT -lt 2 ]]; then
    log_warn "Server deployment is designed for 2+ GPUs"
    log_warn "Consider using workstation deployment for single GPU"
    read -p "Continue anyway? [y/N] " -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Use specified or detected GPU count
if [[ -n "$NUM_GPUS" ]]; then
    if [[ $NUM_GPUS -gt $GPU_COUNT ]]; then
        log_error "Requested $NUM_GPUS GPUs but only $GPU_COUNT available"
        exit 1
    fi
    GPU_COUNT=$NUM_GPUS
fi

# Check NVIDIA Container Toolkit
if [[ "$GPU_TYPE" == "nvidia" ]]; then
    if ! check_container_gpu; then
        log_error "NVIDIA Container Toolkit not working properly"
        exit 1
    fi
    log_success "NVIDIA Container Toolkit verified (mode: $GPU_MODE)"
fi

# Check RAM
TOTAL_RAM_GB=$(free -g | awk '/^Mem:/{print $2}')
if [[ $TOTAL_RAM_GB -lt 64 ]]; then
    log_warn "Only ${TOTAL_RAM_GB}GB RAM detected. 256GB+ recommended for large models."
fi
log_info "Available RAM: ${TOTAL_RAM_GB}GB"

# =============================================================================
# Step 2: Calculate Parallelism Settings
# =============================================================================
log_header "Step 2: GPU Configuration"

# Calculate tensor parallel size (powers of 2)
TENSOR_PARALLEL=1
if [[ $GPU_COUNT -ge 8 ]]; then
    TENSOR_PARALLEL=8
elif [[ $GPU_COUNT -ge 4 ]]; then
    TENSOR_PARALLEL=4
elif [[ $GPU_COUNT -ge 2 ]]; then
    TENSOR_PARALLEL=2
fi

log_info "GPU Count: $GPU_COUNT"
log_info "Tensor Parallel Size: $TENSOR_PARALLEL"

# Calculate max concurrent sequences based on GPU count
MAX_NUM_SEQS=$((GPU_COUNT * 8))
MAX_MODEL_LEN=8192

# Estimate supported model size
VRAM_PER_GPU=0
if [[ "$GPU_TYPE" == "nvidia" ]]; then
    VRAM_PER_GPU=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
fi
TOTAL_VRAM=$((VRAM_PER_GPU * GPU_COUNT / 1024))  # In GB

log_info "Total GPU Memory: ~${TOTAL_VRAM}GB"

# Recommend model size
if [[ $TOTAL_VRAM -ge 160 ]]; then
    log_success "Can support 70B+ parameter models"
    RECOMMENDED_MODEL="Qwen/Qwen2.5-72B-Instruct"
elif [[ $TOTAL_VRAM -ge 80 ]]; then
    log_success "Can support 32B-70B parameter models"
    RECOMMENDED_MODEL="Qwen/Qwen2.5-32B-Instruct"
elif [[ $TOTAL_VRAM -ge 32 ]]; then
    log_success "Can support 7B-13B parameter models"
    RECOMMENDED_MODEL="Qwen/Qwen2.5-7B-Instruct"
else
    log_warn "Limited GPU memory. Consider smaller models."
    RECOMMENDED_MODEL="Qwen/Qwen2.5-7B-Instruct"
fi

# =============================================================================
# Step 3: Model Setup
# =============================================================================
log_header "Step 3: Model Setup"

if [[ -z "$MODEL_PATH" ]]; then
    echo "Recommended model for your configuration: $RECOMMENDED_MODEL"
    echo ""
    echo "Options:"
    echo "  1. Download recommended model ($RECOMMENDED_MODEL)"
    echo "  2. Enter path to pre-downloaded model"
    echo "  3. Enter HuggingFace model ID"
    echo ""
    read -p "Select option [1-3]: " -r CHOICE

    case $CHOICE in
        1)
            MODEL_PATH="$PROJECT_ROOT/models/$RECOMMENDED_MODEL"
            mkdir -p "$(dirname "$MODEL_PATH")"

            if ensure_hf_cli true; then
                log_info "Downloading $RECOMMENDED_MODEL..."
                download_hf_model "$RECOMMENDED_MODEL" "$MODEL_PATH"
            else
                log_warn "HuggingFace CLI not found. Install with: pip install huggingface_hub"
                read -p "Enter pre-downloaded model path: " MODEL_PATH
            fi
            ;;
        2)
            read -p "Enter model path: " MODEL_PATH
            ;;
        3)
            read -p "Enter HuggingFace model ID: " HF_MODEL_ID
            MODEL_PATH="$PROJECT_ROOT/models/$HF_MODEL_ID"
            mkdir -p "$(dirname "$MODEL_PATH")"

            if ensure_hf_cli true; then
                download_hf_model "$HF_MODEL_ID" "$MODEL_PATH"
            else
                log_error "HuggingFace CLI required for download"
                exit 1
            fi
            ;;
    esac
fi

# Validate model path
if [[ ! -d "$MODEL_PATH" ]]; then
    log_error "Model path does not exist: $MODEL_PATH"
    exit 1
fi

# Check for model files
if [[ ! -f "$MODEL_PATH/config.json" ]]; then
    log_error "No config.json found in $MODEL_PATH. Invalid model directory."
    exit 1
fi
log_success "Model validated: $MODEL_PATH"

# =============================================================================
# Step 4: Generate Environment Configuration
# =============================================================================
log_header "Step 4: Generating Configuration"

ENV_FILE="$SCRIPT_DIR/env.server"

# Generate secure keys
LITELLM_KEY="sk-ll-$(openssl rand -hex 12)"
JWT_SECRET="$(openssl rand -hex 32)"

cat > "$ENV_FILE" << EOF
# =============================================================================
# Server Deployment Configuration
# Generated: $(date -Iseconds)
# =============================================================================

# -----------------------------------------------------------------------------
# GPU Configuration
# -----------------------------------------------------------------------------
GPU_TYPE=${GPU_TYPE}
GPU_COUNT=${GPU_COUNT}

# vLLM Parallelism
VLLM_TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL}
VLLM_PIPELINE_PARALLEL_SIZE=1

# -----------------------------------------------------------------------------
# Model Configuration
# -----------------------------------------------------------------------------
VLLM_MODEL_PATH=${MODEL_PATH}
VLLM_MAX_MODEL_LEN=${MAX_MODEL_LEN}
VLLM_MAX_NUM_SEQS=${MAX_NUM_SEQS}
VLLM_ENABLE_TOOL_CALLING=true

# Model identifier (do not change after deployment)
VLLM_MODEL_NAME=rivault-gpt-oss-static

# -----------------------------------------------------------------------------
# vLLM Advanced Settings
# -----------------------------------------------------------------------------
VLLM_PORT=8000
VLLM_DTYPE=auto
# KV cache quantization for memory efficiency (options: auto, fp8, fp8_e5m2)
VLLM_KV_CACHE_DTYPE=auto
# Trust remote code for custom models
VLLM_TRUST_REMOTE_CODE=true

# -----------------------------------------------------------------------------
# LiteLLM Configuration
# -----------------------------------------------------------------------------
LITELLM_PORT=11434
LITELLM_MASTER_KEY=${LITELLM_KEY}
LITELLM_NUM_WORKERS=4

# -----------------------------------------------------------------------------
# UMRS Configuration
# -----------------------------------------------------------------------------
UMRS_PORT=8001
UMRS_WORKERS=4
UMRS_DB_URL=postgresql://umrs:umrs@postgres:5432/umrs
UMRS_S3_ENDPOINT=http://minio:9000
UMRS_S3_ACCESS_KEY=minioadmin
UMRS_S3_SECRET_KEY=minioadmin

# -----------------------------------------------------------------------------
# PostgreSQL Configuration
# -----------------------------------------------------------------------------
POSTGRES_USER=umrs
POSTGRES_PASSWORD=umrs
POSTGRES_DB=umrs
POSTGRES_MAX_CONNECTIONS=100

# -----------------------------------------------------------------------------
# MinIO Configuration
# -----------------------------------------------------------------------------
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
MINIO_PORT=9000
MINIO_CONSOLE_PORT=9001

# -----------------------------------------------------------------------------
# Gateway Configuration
# -----------------------------------------------------------------------------
GATEWAY_PORT=8000
GATEWAY_ENVIRONMENT=production
GATEWAY_LITELLM_SERVICE_KEY=${LITELLM_KEY}
JWT_SECRET=${JWT_SECRET}
GATEWAY_PROJECT_ID=server-demo
GATEWAY_USER_ID=server-user
LITELLM_BASE_URL=http://litellm:11434
UMRS_BASE_URL=http://umrs:8001
RATE_LIMIT_RPS=50

# -----------------------------------------------------------------------------
# Network Configuration
# -----------------------------------------------------------------------------
# NCCL settings for multi-GPU communication
NCCL_DEBUG=WARN
NCCL_IB_DISABLE=0
NCCL_P2P_DISABLE=0

# Transformers offline mode (no downloads during inference)
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1

# -----------------------------------------------------------------------------
# Monitoring
# -----------------------------------------------------------------------------
LOG_LEVEL=info
ENABLE_METRICS=true
PROMETHEUS_PORT=9090
EOF

log_success "Configuration saved to: $ENV_FILE"

# =============================================================================
# Step 5: Generate Docker Compose Override
# =============================================================================
log_header "Step 5: Generating Docker Compose Configuration"

COMPOSE_OVERRIDE="$SCRIPT_DIR/docker-compose.server.yaml"

cat > "$COMPOSE_OVERRIDE" << 'EOF'
# =============================================================================
# Server Deployment - Compose Override
# =============================================================================
# Multi-GPU server configuration
#
# Usage:
#   $COMPOSE_CMD -f ../../docker-compose.e2e.yaml -f docker-compose.server.yaml up
# =============================================================================

services:
  vllm:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    environment:
      - CUDA_VISIBLE_DEVICES=all
      - NCCL_DEBUG=${NCCL_DEBUG:-WARN}
      - NCCL_IB_DISABLE=${NCCL_IB_DISABLE:-0}
    command: >
      --model ${VLLM_MODEL_PATH}
      --tensor-parallel-size ${VLLM_TENSOR_PARALLEL_SIZE:-1}
      --pipeline-parallel-size ${VLLM_PIPELINE_PARALLEL_SIZE:-1}
      --max-model-len ${VLLM_MAX_MODEL_LEN:-8192}
      --max-num-seqs ${VLLM_MAX_NUM_SEQS:-32}
      --dtype ${VLLM_DTYPE:-auto}
      --kv-cache-dtype ${VLLM_KV_CACHE_DTYPE:-auto}
      --trust-remote-code
      --enable-auto-tool-choice
      --tool-call-parser hermes
    ulimits:
      memlock:
        soft: -1
        hard: -1
      stack:
        soft: 67108864
        hard: 67108864
    ipc: host
    shm_size: "16gb"

  litellm:
    environment:
      - LITELLM_NUM_WORKERS=${LITELLM_NUM_WORKERS:-4}
    deploy:
      resources:
        limits:
          cpus: "8"
          memory: 16G

  umrs:
    environment:
      - UMRS_WORKERS=${UMRS_WORKERS:-4}
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 8G

  postgres:
    environment:
      - POSTGRES_MAX_CONNECTIONS=${POSTGRES_MAX_CONNECTIONS:-100}
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 8G
    command: >
      postgres
      -c max_connections=100
      -c shared_buffers=2GB
      -c effective_cache_size=6GB
      -c maintenance_work_mem=512MB
      -c checkpoint_completion_target=0.9
      -c wal_buffers=16MB
      -c default_statistics_target=100
      -c random_page_cost=1.1
      -c effective_io_concurrency=200

  minio:
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 4G
EOF

log_success "Docker Compose override saved to: $COMPOSE_OVERRIDE"

# =============================================================================
# Step 6: Create Data Directories
# =============================================================================
log_header "Step 6: Creating Data Directories"

DATA_DIR="$SCRIPT_DIR/data"
mkdir -p "$DATA_DIR"/{postgres,minio,uploads,rag-index}
chmod -R 755 "$DATA_DIR"

log_success "Data directories created in: $DATA_DIR"

# =============================================================================
# Setup Complete
# =============================================================================
log_header "Setup Complete"

echo "Configuration Summary:"
echo "  GPU Type:           $GPU_TYPE"
echo "  GPU Count:          $GPU_COUNT"
echo "  Tensor Parallel:    $TENSOR_PARALLEL"
echo "  Model Path:         $MODEL_PATH"
echo "  Max Context:        $MAX_MODEL_LEN tokens"
echo "  Max Concurrent:     $MAX_NUM_SEQS requests"
echo ""
echo "Generated Files:"
echo "  - $ENV_FILE"
echo "  - $COMPOSE_OVERRIDE"
echo ""
echo "Next Steps:"
echo "  1. Review configuration: cat env.server"
echo "  2. Start services:       ./start.sh"
echo "  3. Run demo:             ./demo.sh"
echo ""
log_success "Server deployment is ready!"
