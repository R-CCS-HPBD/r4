#!/usr/bin/env bash
# =============================================================================
# R4 Platform - Workstation Setup
# =============================================================================
# Prepares a single-GPU workstation for running the platform.
#
# Requirements:
#   - NVIDIA GPU with >= 16GB VRAM (RTX 3090, 4090, A100, etc.)
#   - Docker with NVIDIA Container Toolkit
#   - ~50GB disk space (model + containers)
#
# Usage:
#   ./setup.sh                    # Full setup with model download
#   ./setup.sh --no-model         # Setup without model download
#   ./setup.sh --check            # Check prerequisites only
#   ./setup.sh --model MODEL_ID   # Use specific model
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
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-7B-Instruct}"
MODEL_PATH="${VLLM_MODEL_PATH:-$RIVAULT_DIR/model}"
SKIP_MODEL=false
CHECK_ONLY=false

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

check_command() {
    if command -v "$1" &> /dev/null; then
        log_ok "$1 found"
        return 0
    else
        log_error "$1 not found"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-model)
            SKIP_MODEL=true
            shift
            ;;
        --check)
            CHECK_ONLY=true
            shift
            ;;
        --model)
            MODEL_ID="$2"
            shift 2
            ;;
        --model-path)
            MODEL_PATH="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-model        Skip model download"
            echo "  --check           Check prerequisites only"
            echo "  --model ID        Model to download (default: $MODEL_ID)"
            echo "  --model-path P    Path to store model (default: ./model)"
            echo "  -h, --help        Show this help"
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
echo "  R4 Platform - Workstation Setup"
echo "=============================================="
echo ""
echo "Configuration:"
echo "  Model: $MODEL_ID"
echo "  Model Path: $MODEL_PATH"
echo "  Skip Model Download: $SKIP_MODEL"
echo ""

# =============================================================================
# Check Prerequisites
# =============================================================================
log_info "Checking prerequisites..."
echo ""

PREREQ_OK=true

# Check container runtime (already detected by init_container_runtime)
if [[ -n "$CONTAINER_RUNTIME" ]]; then
    log_ok "$CONTAINER_RUNTIME found"
    RUNTIME_VERSION=$($CONTAINER_RUNTIME --version | grep -oP '[\d.]+' | head -1 || echo "unknown")
    log_info "  $CONTAINER_RUNTIME version: $RUNTIME_VERSION"
else
    log_error "No container runtime found (docker or podman)"
    PREREQ_OK=false
fi

# Check compose command (already detected by init_container_runtime)
if [[ -n "$COMPOSE_CMD" ]]; then
    log_ok "compose command found: $COMPOSE_CMD"
else
    log_error "No compose command found"
    log_info "  Install: podman-compose (pip) or docker-compose-plugin (apt)"
    PREREQ_OK=false
fi

# Check nvidia-smi
if check_command nvidia-smi; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    log_info "  GPU: $GPU_INFO"
else
    log_error "nvidia-smi not found. GPU required."
    PREREQ_OK=false
fi

# Check NVIDIA Container Toolkit
log_info "Checking NVIDIA Container Toolkit..."
if check_container_gpu; then
    log_ok "NVIDIA Container Toolkit working (mode: $GPU_MODE)"
else
    log_error "NVIDIA Container Toolkit not working"
    log_info "  Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"
    PREREQ_OK=false
fi

# Check disk space
DISK_AVAIL=$(df -BG "$PROJECT_ROOT" | tail -1 | awk '{print $4}' | tr -d 'G')
if [[ $DISK_AVAIL -ge 50 ]]; then
    log_ok "Disk space: ${DISK_AVAIL}GB available"
else
    log_warn "Low disk space: ${DISK_AVAIL}GB (50GB recommended)"
fi

echo ""

if [[ "$PREREQ_OK" != "true" ]]; then
    log_error "Prerequisites not met. Please install missing components."
    exit 1
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
    log_ok "All prerequisites satisfied!"
    exit 0
fi

# =============================================================================
# Create Environment Files
# =============================================================================
log_info "Creating environment files..."

# Generate secure keys
LITELLM_MASTER_KEY="sk-ll-$(openssl rand -hex 16)"
LITELLM_SALT_KEY="$(openssl rand -hex 32)"
JWT_SECRET="$(openssl rand -hex 32)"

# Create rivault env file
cat > "$RIVAULT_DIR/env.mini-rivault" << EOF
# Auto-generated by workstation/setup.sh on $(date -Iseconds)

# Model Configuration
VLLM_MODEL=$MODEL_ID
VLLM_MODEL_PATH=$MODEL_PATH
VLLM_TP=1
VLLM_PP=1
VLLM_GPU_MEM_UTIL=0.90

# LiteLLM
LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
LITELLM_SALT_KEY=$LITELLM_SALT_KEY
LITELLM_PORT=11434

# Networking
NGINX_LITELLM_DOCKER_PORT=11434

# Timezone
GENAI_TZ=Asia/Tokyo
EOF

log_ok "Created: rivault/env.mini-rivault"

# Create UMRS env file
cat > "$UMRS_DIR/.env" << EOF
# Auto-generated by workstation/setup.sh on $(date -Iseconds)

# UMRS Server
UMRS_PORT=8001
DB_HOST=postgres
DB_SUPERUSER_PASSWORD=postgres
DB_USER_PASSWORD=umrs_user

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

# Model Cache
SENTENCE_TRANSFORMERS_HOME=/shared/models/sentence_transformers
HF_HOME=/shared/models/hf

# GLOBUS (required, can be empty directory)
GLOBUS_DIR=${GLOBUS_DIR:-/tmp/globus}
EOF

log_ok "Created: umrs/docker/.env"

# Create local config file for demos
cat > "$SCRIPT_DIR/env.local" << EOF
# Demo environment - workstation tier
# Source this file: source env.local

export LITELLM_URL=http://localhost:11434
export UMRS_URL=http://localhost:8001
export GATEWAY_URL=http://localhost:8080
export MINIO_URL=http://localhost:9000

export LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
export JWT_SECRET=$JWT_SECRET
export GATEWAY_ENVIRONMENT=development
export GATEWAY_PROJECT_ID=workstation-demo
export GATEWAY_USER_ID=workstation-user
export MODEL_NAME=umrs  # Static model name used by vLLM

export PROJECT_ROOT=$PROJECT_ROOT
export RIVAULT_DIR=$RIVAULT_DIR
export UMRS_DIR=$UMRS_DIR
EOF

log_ok "Created: workstation/env.local"

# =============================================================================
# Download Model
# =============================================================================
if [[ "$SKIP_MODEL" != "true" ]]; then
    log_info "Downloading model: $MODEL_ID"
    log_info "This may take 10-30 minutes depending on network speed..."
    echo ""

    mkdir -p "$MODEL_PATH"

    # Use HuggingFace CLI if available (from runtime.sh), otherwise use container
    if ensure_hf_cli true; then
        log_info "Using $HF_CLI..."
        download_hf_model "$MODEL_ID" "$MODEL_PATH/hub/models--$(echo $MODEL_ID | tr '/' '--')"
    else
        log_info "Using $CONTAINER_RUNTIME to download model..."
        $CONTAINER_RUNTIME run --rm \
            -v "$MODEL_PATH:/models" \
            -e HF_HUB_CACHE=/models/hub \
            python:3.11-slim \
            bash -c "pip install -q huggingface_hub && huggingface-cli download $MODEL_ID"
    fi

    if [[ -d "$MODEL_PATH/hub/models--$(echo $MODEL_ID | tr '/' '--')" ]]; then
        log_ok "Model downloaded successfully"
    else
        log_warn "Model download may not have completed. Check $MODEL_PATH"
    fi
else
    log_info "Skipping model download (--no-model)"
fi

# =============================================================================
# Create Globus directory (required by UMRS)
# =============================================================================
mkdir -p "${GLOBUS_DIR:-/tmp/globus}"
log_ok "Created GLOBUS directory"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================="
echo "  Setup Complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Start the platform:"
echo "     ./start.sh"
echo ""
echo "  2. Run the demo:"
echo "     ./demo.sh"
echo ""
echo "  3. Or use the r4 CLI:"
echo "     source env.local"
echo "     pip install -e ../../../r4"
echo "     r4 chat 'Hello!'"
echo ""
echo "Configuration files created:"
echo "  - rivault/env.mini-rivault"
echo "  - umrs/docker/.env"
echo "  - workstation/env.local"
echo ""
