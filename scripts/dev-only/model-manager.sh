#!/usr/bin/env bash
#
# ============================================================================
# ❌ DO NOT USE IN PRODUCTION
# ============================================================================
# This script bypasses UMRS audit, quota, and policy enforcement.
# For production use: r4 model download <model-name>
# ============================================================================
#
# R4 Platform Model Manager (Dev/Admin Staging Only)
# ===============================================================
# Download and manage models for the AI platform.
#
# Usage:
#   ./scripts/dev-only/model-manager.sh [llm|embedding|list] [options]
#
# Commands:
#   llm       Download LLM model for vLLM
#   embedding Download embedding model for RAG
#   list      List downloaded models
#   help      Show this help
#
# Examples:
#   ./scripts/dev-only/model-manager.sh llm Qwen/Qwen2.5-1.5B-Instruct
#   ./scripts/dev-only/model-manager.sh embedding sentence-transformers/all-MiniLM-L6-v2
#   ./scripts/dev-only/model-manager.sh list
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Default paths
LLM_MODEL_DIR="${LLM_MODEL_DIR:-$ROOT_DIR/rivault/model}"
EMBEDDING_MODEL_DIR="${EMBEDDING_MODEL_DIR:-$ROOT_DIR/models/embeddings}"
HF_TOKEN="${HF_TOKEN:-}"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_header() { echo -e "\n${GREEN}=== $1 ===${NC}\n"; }

# =============================================================================
# Recommended Models
# =============================================================================

RECOMMENDED_LLM_MODELS=(
    "Qwen/Qwen2.5-1.5B-Instruct|1.5B params, fast, good for testing|~3GB"
    "Qwen/Qwen2.5-3B-Instruct|3B params, better quality|~6GB"
    "Qwen/Qwen2.5-7B-Instruct|7B params, high quality|~14GB"
    "microsoft/Phi-3-mini-4k-instruct|3.8B params, efficient|~7GB"
    "meta-llama/Llama-3.2-3B-Instruct|3B params, requires access|~6GB"
)

RECOMMENDED_EMBEDDING_MODELS=(
    "sentence-transformers/all-MiniLM-L6-v2|384 dims, fast, general purpose|~90MB"
    "sentence-transformers/all-mpnet-base-v2|768 dims, better quality|~420MB"
    "BAAI/bge-small-en-v1.5|384 dims, high quality|~130MB"
    "BAAI/bge-base-en-v1.5|768 dims, excellent quality|~440MB"
    "intfloat/multilingual-e5-small|384 dims, multilingual|~470MB"
    "intfloat/multilingual-e5-base|768 dims, multilingual|~1.1GB"
    "nomic-ai/nomic-embed-text-v1.5|768 dims, long context|~550MB"
)

# =============================================================================
# LLM Model Download
# =============================================================================

download_llm() {
    local model_id="$1"

    if [ -z "$model_id" ]; then
        log_header "LLM Model Download"
        echo "Usage: $0 llm <model_id> [--target <path>]"
        echo ""
        echo "Recommended models:"
        echo ""
        printf "  %-45s %-35s %s\n" "MODEL ID" "DESCRIPTION" "SIZE"
        printf "  %-45s %-35s %s\n" "--------" "-----------" "----"
        for model_info in "${RECOMMENDED_LLM_MODELS[@]}"; do
            IFS='|' read -r id desc size <<< "$model_info"
            printf "  %-45s %-35s %s\n" "$id" "$desc" "$size"
        done
        echo ""
        echo "Examples:"
        echo "  $0 llm Qwen/Qwen2.5-1.5B-Instruct"
        echo "  $0 llm Qwen/Qwen2.5-7B-Instruct --target /path/to/models"
        echo ""
        echo "Note: Some models (e.g., Llama) require HF_TOKEN for access."
        return 1
    fi

    # Parse additional arguments
    local target_dir="$LLM_MODEL_DIR"
    shift
    while [[ $# -gt 0 ]]; do
        case $1 in
            --target|-t)
                target_dir="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    log_header "Downloading LLM Model"
    log_info "Model: $model_id"
    log_info "Target: $target_dir"
    echo ""

    # Create target directory
    mkdir -p "$target_dir"

    # Check if using Docker or direct Python
    if command -v docker &> /dev/null && [ -f "$ROOT_DIR/rivault/compose.yaml" ]; then
        log_info "Using Docker-based downloader..."

        # Check if env file exists
        if [ ! -f "$ROOT_DIR/rivault/env.mini-rivault" ]; then
            if [ -f "$ROOT_DIR/rivault/env.mini-rivault.template" ]; then
                cp "$ROOT_DIR/rivault/env.mini-rivault.template" "$ROOT_DIR/rivault/env.mini-rivault"
                log_info "Created env.mini-rivault from template"
            fi
        fi

        cd "$ROOT_DIR/rivault"
        VLLM_MODEL_PATH="$target_dir" MODEL_ID="$model_id" HF_TOKEN="$HF_TOKEN" \
            docker compose --env-file env.mini-rivault --profile download run --rm vllm_dl
        cd "$ROOT_DIR"
    else
        log_info "Using direct Python downloader..."
        python3 -c "
from huggingface_hub import snapshot_download
import os
model_id = '$model_id'
target = '$target_dir'
token = os.environ.get('HF_TOKEN') or None
print(f'Downloading {model_id} to {target}...')
snapshot_download(
    repo_id=model_id,
    local_dir=target,
    local_dir_use_symlinks=False,
    ignore_patterns=['*.gguf', '*.ggml', '*.bin.index.json'],
    token=token
)
print('Download complete!')
"
    fi

    log_success "LLM model downloaded to: $target_dir"
    echo ""
    echo "To use this model:"
    echo "  1. Update VLLM_MODEL_PATH in rivault/env.mini-rivault"
    echo "  2. Start demo: cd demos && ./start-demo.sh"
}

# =============================================================================
# Embedding Model Download
# =============================================================================

download_embedding() {
    local model_id="$1"

    if [ -z "$model_id" ]; then
        log_header "Embedding Model Download"
        echo "Usage: $0 embedding <model_id> [--target <path>]"
        echo ""
        echo "Recommended models:"
        echo ""
        printf "  %-45s %-30s %s\n" "MODEL ID" "DESCRIPTION" "SIZE"
        printf "  %-45s %-30s %s\n" "--------" "-----------" "----"
        for model_info in "${RECOMMENDED_EMBEDDING_MODELS[@]}"; do
            IFS='|' read -r id desc size <<< "$model_info"
            printf "  %-45s %-30s %s\n" "$id" "$desc" "$size"
        done
        echo ""
        echo "Examples:"
        echo "  $0 embedding sentence-transformers/all-MiniLM-L6-v2"
        echo "  $0 embedding BAAI/bge-base-en-v1.5 --target /path/to/models"
        echo ""
        return 1
    fi

    # Parse additional arguments
    local target_dir="$EMBEDDING_MODEL_DIR"
    shift
    while [[ $# -gt 0 ]]; do
        case $1 in
            --target|-t)
                target_dir="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    # Create safe directory name from model ID
    local model_dir_name="${model_id//\//_}"
    local full_target="$target_dir/$model_dir_name"

    log_header "Downloading Embedding Model"
    log_info "Model: $model_id"
    log_info "Target: $full_target"
    echo ""

    # Create target directory
    mkdir -p "$target_dir"

    # Download using Python/sentence-transformers
    python3 << EOF
import os
import sys

# Set cache directory
os.environ['SENTENCE_TRANSFORMERS_HOME'] = '$target_dir'
os.environ['HF_HOME'] = '$target_dir/.hf_cache'

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("Installing sentence-transformers...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "sentence-transformers"])
    from sentence_transformers import SentenceTransformer

model_id = '$model_id'
print(f'Downloading {model_id}...')

# This will download and cache the model
model = SentenceTransformer(model_id)

# Get cache location
cache_path = model._model_card_vars.get('model_name', model_id)
print(f'Model cached successfully!')

# Test the model
test_embedding = model.encode("Hello, world!")
print(f'Embedding dimension: {len(test_embedding)}')
print(f'Model ready for use!')
EOF

    log_success "Embedding model downloaded!"
    echo ""
    echo "To use this model in UMRS, set:"
    echo "  SENTENCE_TRANSFORMERS_HOME=$target_dir"
    echo "  or configure the model name in your UMRS settings"
}

# =============================================================================
# List Downloaded Models
# =============================================================================

list_models() {
    log_header "Downloaded Models"

    # LLM Models
    echo -e "${CYAN}LLM Models (vLLM):${NC}"
    echo "  Location: $LLM_MODEL_DIR"
    if [ -d "$LLM_MODEL_DIR" ] && [ -n "$(ls -A "$LLM_MODEL_DIR" 2>/dev/null)" ]; then
        # Check for model config
        if [ -f "$LLM_MODEL_DIR/config.json" ]; then
            local model_type=$(python3 -c "import json; print(json.load(open('$LLM_MODEL_DIR/config.json')).get('model_type', 'unknown'))" 2>/dev/null || echo "unknown")
            local model_name=$(python3 -c "import json; print(json.load(open('$LLM_MODEL_DIR/config.json')).get('_name_or_path', 'unknown'))" 2>/dev/null || echo "unknown")
            echo "  ✓ $model_name (type: $model_type)"

            # Show size
            local size=$(du -sh "$LLM_MODEL_DIR" 2>/dev/null | cut -f1)
            echo "    Size: $size"
        else
            echo "  ⚠ Directory exists but no config.json found"
        fi
    else
        echo "  (none)"
    fi
    echo ""

    # Embedding Models
    echo -e "${CYAN}Embedding Models:${NC}"
    echo "  Location: $EMBEDDING_MODEL_DIR"
    if [ -d "$EMBEDDING_MODEL_DIR" ]; then
        local found=0
        for dir in "$EMBEDDING_MODEL_DIR"/*/; do
            if [ -d "$dir" ]; then
                local name=$(basename "$dir")
                local size=$(du -sh "$dir" 2>/dev/null | cut -f1)
                echo "  ✓ $name ($size)"
                found=1
            fi
        done
        if [ $found -eq 0 ]; then
            echo "  (none)"
        fi
    else
        echo "  (none)"
    fi

    # Also check default sentence-transformers cache
    local st_cache="$HOME/.cache/torch/sentence_transformers"
    if [ -d "$st_cache" ]; then
        echo ""
        echo -e "${CYAN}Cached Embedding Models (sentence-transformers):${NC}"
        echo "  Location: $st_cache"
        for dir in "$st_cache"/*/; do
            if [ -d "$dir" ]; then
                local name=$(basename "$dir")
                local size=$(du -sh "$dir" 2>/dev/null | cut -f1)
                echo "  ✓ $name ($size)"
            fi
        done
    fi
    echo ""
}

# =============================================================================
# Help
# =============================================================================

show_help() {
    cat << 'EOF'
R4 Platform Model Manager

Usage: ./scripts/dev-only/model-manager.sh [command] [options]

Commands:
  llm <model_id>       Download LLM model for vLLM inference
  embedding <model_id> Download embedding model for RAG
  list                 List downloaded models
  help                 Show this help

Options:
  --target, -t <path>  Custom download directory

Environment Variables:
  HF_TOKEN             Hugging Face token (required for some models)
  LLM_MODEL_DIR        LLM model directory (default: rivault/model)
  EMBEDDING_MODEL_DIR  Embedding model directory (default: models/embeddings)

Examples:
  # Download a small LLM for testing
  ./scripts/dev-only/model-manager.sh llm Qwen/Qwen2.5-1.5B-Instruct

  # Download embedding model
  ./scripts/dev-only/model-manager.sh embedding sentence-transformers/all-MiniLM-L6-v2

  # Download with custom path
  ./scripts/dev-only/model-manager.sh llm Qwen/Qwen2.5-7B-Instruct --target /data/models/llm

  # List all downloaded models
  ./scripts/dev-only/model-manager.sh list

  # Download gated model with token
  HF_TOKEN=hf_xxx ./scripts/dev-only/model-manager.sh llm meta-llama/Llama-3.2-3B-Instruct

Model Selection Guide:
  For LLM (vLLM):
    - Testing: Qwen/Qwen2.5-1.5B-Instruct (~3GB, fast)
    - Production: Qwen/Qwen2.5-7B-Instruct (~14GB, high quality)

  For Embeddings (RAG):
    - Fast/Small: sentence-transformers/all-MiniLM-L6-v2 (~90MB)
    - Quality: BAAI/bge-base-en-v1.5 (~440MB)
    - Multilingual: intfloat/multilingual-e5-base (~1.1GB)

EOF
}

# =============================================================================
# Main
# =============================================================================

COMMAND="${1:-help}"

case "$COMMAND" in
    llm)
        shift
        download_llm "$@"
        ;;
    embedding)
        shift
        download_embedding "$@"
        ;;
    list)
        list_models
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $COMMAND"
        show_help
        exit 1
        ;;
esac
