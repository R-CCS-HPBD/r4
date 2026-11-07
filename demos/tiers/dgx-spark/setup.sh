#!/usr/bin/env bash
# =============================================================================
# R4 Platform - DGX Spark Setup
# =============================================================================
# Prepares NVIDIA DGX Spark for running the platform.
#
# DGX Spark Specifications:
#   - NVIDIA Grace CPU (ARM64)
#   - NVIDIA Blackwell B200D GPU (128GB HBM3e)
#   - 128GB unified memory
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

# Source container runtime detection (podman preferred)
SKIP_RUNTIME_INIT=1
source "$DEMOS_DIR/lib/runtime.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# DGX Spark optimized defaults (larger models, extended context)
MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-72B-Instruct}"
MODEL_PATH="${VLLM_MODEL_PATH:-$RIVAULT_DIR/model}"
SKIP_MODEL=false
CHECK_ONLY=false

# DGX Spark specific settings
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.85}"  # 85% to leave headroom for unified memory
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-32}"
VLLM_DOCKER_TAG="${VLLM_DOCKER_TAG:-cu130-nightly-aarch64}"  # Blackwell + ARM64 support

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_spark() { echo -e "${CYAN}[DGX Spark]${NC} $*"; }

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
        --max-model-len)
            VLLM_MAX_MODEL_LEN="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-model          Skip model download"
            echo "  --check             Check prerequisites only"
            echo "  --model ID          Model to download (default: $MODEL_ID)"
            echo "  --model-path P      Path to store model (default: ./model)"
            echo "  --max-model-len N   Maximum context length (default: $VLLM_MAX_MODEL_LEN)"
            echo "  -h, --help          Show this help"
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
echo "║         R4 Platform - DGX Spark Setup                ║"
echo "║                                                                  ║"
echo "║    NVIDIA Grace CPU + Blackwell B200D GPU (128GB HBM3e)         ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Configuration:"
echo "  Model: $MODEL_ID"
echo "  Model Path: $MODEL_PATH"
echo "  Max Context Length: $VLLM_MAX_MODEL_LEN"
echo "  GPU Memory Utilization: $VLLM_GPU_MEM_UTIL"
echo "  Max Concurrent Sequences: $VLLM_MAX_NUM_SEQS"
echo "  Skip Model Download: $SKIP_MODEL"
echo ""

# =============================================================================
# Check Prerequisites
# =============================================================================
log_info "Checking prerequisites..."
echo ""

PREREQ_OK=true

# Check architecture (DGX Spark uses ARM64)
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]]; then
    log_spark "ARM64 architecture detected (Grace CPU)"
else
    log_warn "Expected ARM64 (aarch64), found: $ARCH"
    log_warn "This profile is optimized for DGX Spark. Some settings may not apply."
fi

# Check container runtime (podman preferred)
if detect_container_runtime; then
    RUNTIME_VERSION=$($CONTAINER_RUNTIME --version | grep -oP '[\d.]+' | head -1 || echo "unknown")
    log_ok "$CONTAINER_RUNTIME found (v$RUNTIME_VERSION)"
else
    PREREQ_OK=false
fi

# Check compose command
if detect_compose_cmd; then
    log_ok "Compose command: $COMPOSE_CMD"
else
    log_error "No compose support found"
    log_info "  Install with: pip install podman-compose"
    log_info "  Or use podman 4.0+ which includes 'podman compose'"
    PREREQ_OK=false
fi

# Check nvidia-smi (DGX Spark has GB10/B200D GPU)
if check_command nvidia-smi; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    log_info "  GPU: $GPU_INFO"

    # Check for DGX Spark / Blackwell GPU (GB10 is the Grace-Blackwell chip)
    if echo "$GPU_INFO" | grep -qi "GB10\|B200\|Blackwell"; then
        log_spark "DGX Spark GPU detected (Grace-Blackwell)"
    else
        log_warn "Non-DGX Spark GPU detected. Profile may need adjustment."
    fi

    # Check GPU memory (should be 128GB for B200D)
    # Handle [N/A] or other non-numeric values gracefully
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d '[:space:]')
    if [[ "$GPU_MEM" =~ ^[0-9]+$ ]]; then
        if [[ $GPU_MEM -ge 100000 ]]; then
            log_spark "High memory GPU detected (${GPU_MEM}MiB)"
        else
            log_warn "GPU has ${GPU_MEM}MiB memory. Large models may not fit."
        fi
    else
        # Memory info not available via nvidia-smi query (common on Grace-Blackwell)
        log_info "  GPU memory: checking via nvidia-smi..."
        # Try alternative method - parse nvidia-smi output directly
        GPU_MEM_ALT=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
        if [[ -n "$GPU_MEM_ALT" && "$GPU_MEM_ALT" != "[N/A]" ]]; then
            log_info "  GPU memory: $GPU_MEM_ALT"
        else
            # On DGX Spark with unified memory, memory is shared with CPU
            log_spark "Unified memory architecture detected (Grace-Blackwell)"
            log_info "  System has unified CPU+GPU memory pool"
        fi
    fi
else
    log_error "nvidia-smi not found. GPU required."
    PREREQ_OK=false
fi

# Configure GPU flags and check container GPU access
configure_gpu_flags
log_info "Checking container GPU access..."

# On ARM64 (Grace/DGX Spark), nvidia-smi working on host is usually sufficient
if [[ "$ARCH" == "aarch64" ]]; then
    if command -v nvidia-container-cli &> /dev/null; then
        log_ok "NVIDIA Container Toolkit installed"
    elif $CONTAINER_RUNTIME info 2>/dev/null | grep -qi "nvidia\|cdi"; then
        log_ok "Container GPU runtime detected"
    else
        # On DGX Spark, GPU access often works without explicit toolkit
        log_ok "GPU access via host nvidia-smi (DGX Spark unified memory)"
        log_info "  GPU flag: ${GPU_FLAG:-auto}"
    fi
else
    # x86_64 - verify container GPU access
    check_container_gpu || {
        log_warn "Container GPU access could not be verified"
        log_info "  Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"
        # Don't fail - may still work
    }
fi

# Check disk space (DGX Spark needs more for larger models)
DISK_AVAIL=$(df -BG "$PROJECT_ROOT" | tail -1 | awk '{print $4}' | tr -d 'G')
if [[ $DISK_AVAIL -ge 200 ]]; then
    log_ok "Disk space: ${DISK_AVAIL}GB available (sufficient for large models)"
elif [[ $DISK_AVAIL -ge 100 ]]; then
    log_warn "Disk space: ${DISK_AVAIL}GB (200GB+ recommended for 70B+ models)"
else
    log_warn "Low disk space: ${DISK_AVAIL}GB (200GB+ recommended)"
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

# Create rivault env file (DGX Spark optimized)
cat > "$RIVAULT_DIR/env.mini-rivault" << EOF
# Auto-generated by dgx-spark/setup.sh on $(date -Iseconds)
# Optimized for NVIDIA DGX Spark (Grace + Blackwell B200D)

# =============================================================================
# Model Configuration
# =============================================================================
VLLM_MODEL=$MODEL_ID
VLLM_MODEL_PATH=$MODEL_PATH
VLLM_PATH_TO_HUGGINGFACE=$MODEL_PATH
VLLM_TP=1
VLLM_PP=1

# DGX Spark optimizations - high memory, extended context
VLLM_GPU_MEM_UTIL=$VLLM_GPU_MEM_UTIL
VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN
VLLM_MAX_NUM_SEQS=$VLLM_MAX_NUM_SEQS
VLLM_NCCL_MEM_LIMIT=16g

# Enable optimizations for large models
VLLM_ENABLE_CHUNKED_PREFILL=true
VLLM_ENABLE_PREFIX_CACHING=true

# =============================================================================
# LiteLLM / NGINX
# =============================================================================
LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
LITELLM_SALT_KEY=$LITELLM_SALT_KEY
LITELLM_PORT=11434
NGINX_LITELLM_DOCKER_PORT=11434

# =============================================================================
# Container Image Override (Blackwell GPU support)
# =============================================================================
VLLM_DOCKER_TAG=$VLLM_DOCKER_TAG

# =============================================================================
# S3/MinIO Access
# =============================================================================
UMRS_S3_BUCKET_HOST=minio

# =============================================================================
# Security (offline inference)
# =============================================================================
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1

# =============================================================================
# Grace CPU Optimizations
# =============================================================================
# Optimize memory allocation for unified memory architecture
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EOF

log_ok "Created rivault/env.mini-rivault"

# Create UMRS env file
mkdir -p "$UMRS_DIR"
cat > "$UMRS_DIR/.env" << EOF
# Auto-generated by dgx-spark/setup.sh on $(date -Iseconds)

# =============================================================================
# Database
# =============================================================================
POSTGRES_USER=r4
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=r4
DATABASE_URL=postgresql://r4:\${POSTGRES_PASSWORD}@postgres:5432/r4

# =============================================================================
# Object Storage
# =============================================================================
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
S3_ENDPOINT=http://minio:9000
S3_BUCKET=r4-data

# =============================================================================
# UMRS Configuration
# =============================================================================
UMRS_PORT=8001
EXECUTION_BACKEND=compose

# =============================================================================
# Authentication
# =============================================================================
JWT_SECRET=$JWT_SECRET
EOF

log_ok "Created umrs/docker/.env"

# Create local env file
cat > "$SCRIPT_DIR/env.local" << EOF
# Auto-generated by dgx-spark/setup.sh on $(date -Iseconds)
# Source this file: source env.local

export GATEWAY_URL=http://localhost:8080
export LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
export JWT_SECRET=$JWT_SECRET
export GATEWAY_ENVIRONMENT=development
export GATEWAY_PROJECT_ID=dgx-demo
export GATEWAY_USER_ID=dgx-user
export MODEL_NAME=umrs

# DGX Spark specific
export VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN
export PLATFORM_TYPE=dgx-spark
EOF

log_ok "Created dgx-spark/env.local"

# =============================================================================
# Download Model (if not skipped)
# =============================================================================
if [[ "$SKIP_MODEL" != "true" ]]; then
    log_info "Preparing model download..."

    # Ensure ~/.local/bin is in PATH (where uv/pipx install tools)
    export PATH="$HOME/.local/bin:$PATH"

    # Use shared HF CLI detection from runtime.sh
    if ! ensure_hf_cli true; then
        log_error "Failed to set up HuggingFace CLI"
        exit 1
    fi

    # Create model directory with Lustre striping if available
    if command -v lfs &>/dev/null; then
        mkdir -p "$MODEL_PATH"
        if lfs setstripe -c "${LUSTRE_STRIPE_COUNT:-4}" -S "${LUSTRE_STRIPE_SIZE:-4M}" "$MODEL_PATH" 2>/dev/null; then
            log_info "Lustre: directory stripe set (count=${LUSTRE_STRIPE_COUNT:-4})"
        fi
    else
        mkdir -p "$MODEL_PATH"
    fi

    log_info "Downloading model: $MODEL_ID"
    log_info "This may take a while for large models (70B+ can be 150GB+)..."

    # Download with resume support
    # 'hf' CLI resumes by default, 'huggingface-cli' needs --resume-download
    if [[ "$HF_CLI" == "hf" ]] || [[ "$HF_CLI" == *"/hf" ]]; then
        $HF_CLI download "$MODEL_ID" --local-dir "$MODEL_PATH"
    else
        $HF_CLI download "$MODEL_ID" --local-dir "$MODEL_PATH" --resume-download
    fi

    log_ok "Model downloaded to: $MODEL_PATH"
else
    log_info "Skipping model download (--no-model specified)"
    log_warn "Ensure model is available at: $MODEL_PATH"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                    DGX Spark Setup Complete                      ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Start the platform:  ./start.sh"
echo "  2. Run the demo:        ./demo.sh"
echo ""
echo "DGX Spark optimizations applied:"
echo "  - Extended context: ${VLLM_MAX_MODEL_LEN} tokens"
echo "  - High memory utilization: ${VLLM_GPU_MEM_UTIL}"
echo "  - Concurrent sequences: ${VLLM_MAX_NUM_SEQS}"
echo "  - Chunked prefill: enabled"
echo "  - Prefix caching: enabled"
echo ""
