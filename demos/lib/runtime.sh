#!/usr/bin/env bash
# =============================================================================
# Container Runtime Detection Library
# =============================================================================
# Detects and configures podman (preferred) or docker runtime for demo scripts.
# Source this file in your scripts: source "$(dirname "$0")/../lib/runtime.sh"
#
# Exports:
#   CONTAINER_RUNTIME - "podman" or "docker"
#   COMPOSE_CMD       - "podman compose" or "docker compose"
#   GPU_FLAG          - GPU passthrough flag for the runtime
#
# Usage in scripts:
#   source "$SCRIPT_DIR/../lib/runtime.sh"
#   init_container_runtime || exit 1
#   $COMPOSE_CMD up -d
#   $CONTAINER_RUNTIME run $GPU_FLAG nvidia/cuda:12.4-base nvidia-smi
# =============================================================================

# Colors (if not already defined)
RED="${RED:-\033[0;31m}"
GREEN="${GREEN:-\033[0;32m}"
YELLOW="${YELLOW:-\033[1;33m}"
BLUE="${BLUE:-\033[0;34m}"
NC="${NC:-\033[0m}"

# -----------------------------------------------------------------------------
# Detect container runtime (prefer podman for HPC/DGX environments)
# -----------------------------------------------------------------------------
detect_container_runtime() {
    # Allow override via environment variable
    if [[ -n "${CONTAINER_RUNTIME:-}" ]]; then
        case "$CONTAINER_RUNTIME" in
            podman|docker)
                return 0
                ;;
            *)
                echo -e "${RED}[ERROR]${NC} Invalid CONTAINER_RUNTIME: $CONTAINER_RUNTIME (use 'podman' or 'docker')"
                return 1
                ;;
        esac
    fi

    # Prefer podman (common in HPC environments where docker daemon isn't allowed)
    if command -v podman &> /dev/null; then
        export CONTAINER_RUNTIME="podman"
    elif command -v docker &> /dev/null; then
        export CONTAINER_RUNTIME="docker"
    else
        echo -e "${RED}[ERROR]${NC} Neither podman nor docker found. Please install podman."
        echo -e "${RED}[ERROR]${NC} Install with: apt install podman podman-compose"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# Detect compose command
# -----------------------------------------------------------------------------
detect_compose_cmd() {
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        # Prefer podman-compose (standalone Python package) - works without Docker daemon
        if command -v podman-compose &> /dev/null; then
            export COMPOSE_CMD="podman-compose"
        # Try podman compose (built-in, podman 4.0+) but check it's not delegating to docker-compose
        elif podman compose version &> /dev/null 2>&1; then
            # Check if podman compose delegates to docker-compose (won't work without Docker daemon)
            if podman compose version 2>&1 | grep -qi "docker-compose\|external compose provider"; then
                echo -e "${YELLOW}[WARN]${NC} podman compose delegates to docker-compose which requires Docker daemon"
                echo -e "${YELLOW}[WARN]${NC} Install podman-compose: pip install podman-compose"
                return 1
            fi
            export COMPOSE_CMD="podman compose"
        else
            echo -e "${YELLOW}[WARN]${NC} podman found but no compose support."
            echo -e "${YELLOW}[WARN]${NC} Install with: pip install podman-compose"
            return 1
        fi
    else
        # Docker - try docker compose (v2) first, then docker-compose (v1)
        if docker compose version &> /dev/null 2>&1; then
            export COMPOSE_CMD="docker compose"
        elif command -v docker-compose &> /dev/null; then
            export COMPOSE_CMD="docker-compose"
        else
            echo -e "${RED}[ERROR]${NC} docker found but no compose support."
            echo -e "${RED}[ERROR]${NC} Install with: apt install docker-compose-plugin"
            return 1
        fi
    fi
}

# -----------------------------------------------------------------------------
# Configure GPU passthrough flags
# -----------------------------------------------------------------------------
configure_gpu_flags() {
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        # Podman GPU passthrough options:
        # Option 1: CDI (Container Device Interface) - recommended for newer systems
        # Option 2: --device flag for nvidia devices
        # Option 3: --hooks-dir for nvidia-container-toolkit

        # Check for CDI support (podman 4.1+)
        if podman info --format '{{.Host.CDISpecDirs}}' 2>/dev/null | grep -q nvidia; then
            export GPU_FLAG="--device nvidia.com/gpu=all"
            export GPU_MODE="cdi"
        # Check for nvidia-container-toolkit hooks
        elif [[ -d "/usr/share/containers/oci/hooks.d" ]] && \
             ls /usr/share/containers/oci/hooks.d/*nvidia* &>/dev/null 2>&1; then
            export GPU_FLAG="--hooks-dir=/usr/share/containers/oci/hooks.d"
            export GPU_MODE="hooks"
        # Fall back to direct device passthrough
        elif [[ -e "/dev/nvidia0" ]]; then
            export GPU_FLAG="--device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools"
            export GPU_MODE="device"
        else
            # No specific GPU flags - rely on system configuration
            export GPU_FLAG=""
            export GPU_MODE="auto"
        fi
    else
        # Docker uses --gpus
        export GPU_FLAG="--gpus all"
        export GPU_MODE="docker"
    fi
}

# -----------------------------------------------------------------------------
# Check if GPU is accessible in container
# -----------------------------------------------------------------------------
check_container_gpu() {
    local test_image="${1:-nvidia/cuda:12.4.0-base-ubuntu22.04}"

    echo -e "${BLUE}[INFO]${NC} Checking container GPU access with $CONTAINER_RUNTIME..."

    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        # For podman, try multiple methods
        if [[ -n "$GPU_FLAG" ]] && $CONTAINER_RUNTIME run --rm $GPU_FLAG "$test_image" nvidia-smi &>/dev/null 2>&1; then
            echo -e "${GREEN}[OK]${NC} Container GPU access working ($GPU_MODE)"
            return 0
        elif $CONTAINER_RUNTIME run --rm --device nvidia.com/gpu=all "$test_image" nvidia-smi &>/dev/null 2>&1; then
            echo -e "${GREEN}[OK]${NC} Container GPU access working (CDI fallback)"
            export GPU_FLAG="--device nvidia.com/gpu=all"
            return 0
        else
            echo -e "${YELLOW}[WARN]${NC} Container GPU access not verified via test"
            echo -e "${YELLOW}[WARN]${NC} GPU may still work - nvidia-smi on host is often sufficient for DGX Spark"
            # Don't fail - podman on DGX Spark may work differently
            return 0
        fi
    else
        if docker run --rm --gpus all "$test_image" nvidia-smi &>/dev/null; then
            echo -e "${GREEN}[OK]${NC} Container GPU access working"
            return 0
        else
            echo -e "${RED}[ERROR]${NC} NVIDIA Container Toolkit not working"
            return 1
        fi
    fi
}

# -----------------------------------------------------------------------------
# Initialize runtime (call this at the start of your script)
# -----------------------------------------------------------------------------
init_container_runtime() {
    detect_container_runtime || return 1
    detect_compose_cmd || return 1
    configure_gpu_flags

    echo -e "${GREEN}[OK]${NC} Container runtime: $CONTAINER_RUNTIME"
    echo -e "${GREEN}[OK]${NC} Compose command: $COMPOSE_CMD"
    if [[ -n "$GPU_FLAG" ]]; then
        echo -e "${BLUE}[INFO]${NC} GPU mode: $GPU_MODE"
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Print runtime info (for debugging)
# -----------------------------------------------------------------------------
print_runtime_info() {
    echo "Container Runtime Configuration:"
    echo "  CONTAINER_RUNTIME: ${CONTAINER_RUNTIME:-not set}"
    echo "  COMPOSE_CMD: ${COMPOSE_CMD:-not set}"
    echo "  GPU_FLAG: ${GPU_FLAG:-not set}"
    echo "  GPU_MODE: ${GPU_MODE:-not set}"
    echo ""
    if [[ -n "${CONTAINER_RUNTIME:-}" ]]; then
        $CONTAINER_RUNTIME --version
    fi
    if [[ -n "${COMPOSE_CMD:-}" ]]; then
        $COMPOSE_CMD version 2>/dev/null || $COMPOSE_CMD --version 2>/dev/null || true
    fi
}

# -----------------------------------------------------------------------------
# Helper: Run a container with appropriate GPU flags
# -----------------------------------------------------------------------------
run_container() {
    $CONTAINER_RUNTIME run "$@"
}

run_container_gpu() {
    $CONTAINER_RUNTIME run $GPU_FLAG "$@"
}

# -----------------------------------------------------------------------------
# Helper: Execute compose command
# -----------------------------------------------------------------------------
compose() {
    $COMPOSE_CMD "$@"
}

# Auto-initialize if sourced (can be disabled by setting SKIP_RUNTIME_INIT=1)
if [[ "${SKIP_RUNTIME_INIT:-0}" != "1" ]] && [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    detect_container_runtime || true
    detect_compose_cmd || true
    configure_gpu_flags || true
fi

# =============================================================================
# HuggingFace CLI Detection and Model Download
# =============================================================================

# -----------------------------------------------------------------------------
# Detect HuggingFace CLI (handles uv's 'hf' vs pip's 'huggingface-cli')
# Sets HF_CLI variable to the detected CLI path
# -----------------------------------------------------------------------------
detect_hf_cli() {
    export HF_CLI=""

    # Check standard locations
    if command -v hf &> /dev/null; then
        export HF_CLI="hf"
    elif command -v huggingface-cli &> /dev/null; then
        export HF_CLI="huggingface-cli"
    elif [[ -x "$HOME/.local/bin/hf" ]]; then
        export HF_CLI="$HOME/.local/bin/hf"
    elif [[ -x "$HOME/.local/bin/huggingface-cli" ]]; then
        export HF_CLI="$HOME/.local/bin/huggingface-cli"
    fi

    if [[ -z "$HF_CLI" ]]; then
        return 1
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Install HuggingFace CLI if not present
# Prefers uv, falls back to pip
# -----------------------------------------------------------------------------
install_hf_cli() {
    echo -e "${BLUE}[INFO]${NC} Installing HuggingFace CLI..."

    # Try uv first (installs 'hf' command)
    if command -v uv &> /dev/null; then
        echo -e "${BLUE}[INFO]${NC} Installing with uv..."
        if uv tool install huggingface_hub; then
            if command -v hf &> /dev/null; then
                export HF_CLI="hf"
                return 0
            elif [[ -x "$HOME/.local/bin/hf" ]]; then
                export HF_CLI="$HOME/.local/bin/hf"
                return 0
            fi
        fi
    fi

    # Try pip (installs 'huggingface-cli' command)
    if command -v pip &> /dev/null || command -v pip3 &> /dev/null; then
        local pip_cmd="${PIP_CMD:-pip3}"
        echo -e "${BLUE}[INFO]${NC} Installing with pip..."
        if $pip_cmd install --user huggingface_hub; then
            if command -v huggingface-cli &> /dev/null; then
                export HF_CLI="huggingface-cli"
                return 0
            elif [[ -x "$HOME/.local/bin/huggingface-cli" ]]; then
                export HF_CLI="$HOME/.local/bin/huggingface-cli"
                return 0
            fi
        fi
    fi

    echo -e "${RED}[ERROR]${NC} Failed to install HuggingFace CLI"
    echo -e "${RED}[ERROR]${NC} Please install manually: pip install huggingface_hub"
    return 1
}

# -----------------------------------------------------------------------------
# Check HuggingFace login status
# Returns 0 if logged in, 1 if not
# -----------------------------------------------------------------------------
check_hf_login() {
    if [[ -z "${HF_CLI:-}" ]]; then
        detect_hf_cli || return 1
    fi

    # 'hf' CLI uses 'hf auth whoami', 'huggingface-cli' uses 'huggingface-cli whoami'
    if [[ "$HF_CLI" == "hf" ]] || [[ "$HF_CLI" == *"/hf" ]]; then
        $HF_CLI auth whoami &> /dev/null
    else
        $HF_CLI whoami &> /dev/null
    fi
}

# -----------------------------------------------------------------------------
# Print HF login instructions
# -----------------------------------------------------------------------------
print_hf_login_instructions() {
    if [[ -z "${HF_CLI:-}" ]]; then
        echo -e "${YELLOW}[WARN]${NC} HuggingFace CLI not detected"
        return
    fi

    if [[ "$HF_CLI" == "hf" ]] || [[ "$HF_CLI" == *"/hf" ]]; then
        echo -e "${BLUE}[INFO]${NC} For gated models, run: $HF_CLI auth login"
    else
        echo -e "${BLUE}[INFO]${NC} For gated models, run: $HF_CLI login"
    fi
}

# -----------------------------------------------------------------------------
# Download model from HuggingFace
# Usage: download_hf_model <model_id> <local_dir>
# -----------------------------------------------------------------------------
download_hf_model() {
    local model_id="$1"
    local local_dir="$2"

    if [[ -z "${HF_CLI:-}" ]]; then
        if ! detect_hf_cli; then
            echo -e "${RED}[ERROR]${NC} HuggingFace CLI not found"
            return 1
        fi
    fi

    echo -e "${BLUE}[INFO]${NC} Downloading model: $model_id"
    echo -e "${BLUE}[INFO]${NC} Using: $HF_CLI"

    mkdir -p "$local_dir"

    # 'hf' CLI (from uv) has different options than 'huggingface-cli' (from pip)
    # hf: uses --no-force-download (default, resumes automatically)
    # huggingface-cli: uses --resume-download
    if [[ "$HF_CLI" == "hf" ]] || [[ "$HF_CLI" == *"/hf" ]]; then
        $HF_CLI download "$model_id" --local-dir "$local_dir"
    else
        $HF_CLI download "$model_id" --local-dir "$local_dir" --resume-download
    fi
}

# -----------------------------------------------------------------------------
# Ensure HF CLI is available (detect or install)
# Sets HF_CLI and optionally checks login
# -----------------------------------------------------------------------------
ensure_hf_cli() {
    local check_login="${1:-false}"

    if ! detect_hf_cli; then
        install_hf_cli || return 1
    fi

    echo -e "${GREEN}[OK]${NC} HuggingFace CLI: $HF_CLI"

    if [[ "$check_login" == "true" ]]; then
        if ! check_hf_login; then
            echo -e "${YELLOW}[WARN]${NC} Not logged in to Hugging Face"
            print_hf_login_instructions
        fi
    fi

    return 0
}

# =============================================================================
# Service Health Check Helpers
# =============================================================================

# -----------------------------------------------------------------------------
# Check UMRS health using the UMRS client ping command
# Usage: check_umrs_health [timeout_seconds]
# Returns 0 if healthy, 1 if not
# -----------------------------------------------------------------------------
check_umrs_health() {
    local timeout="${1:-5}"
    local container_name="${UMRS_CONTAINER_NAME:-umrs-server}"

    # Run the UMRS client ping command inside the container
    if $CONTAINER_RUNTIME exec "$container_name" \
        timeout "$timeout" python /app/src/client_umrs.py --output json ping 2>/dev/null | \
        grep -q '"server_ready": true'; then
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# Check LiteLLM/LLM health by querying models endpoint
# Usage: check_llm_health [url] [api_key] [timeout_seconds]
# Returns 0 if healthy, 1 if not
# -----------------------------------------------------------------------------
check_llm_health() {
    local url="${1:-http://localhost:11434/v1/models}"
    local api_key="${2:-}"
    local timeout="${3:-10}"

    local curl_args=(-sf --max-time "$timeout" "$url")
    if [[ -n "$api_key" ]]; then
        curl_args+=(-H "Authorization: Bearer $api_key")
    fi

    if curl "${curl_args[@]}" 2>/dev/null | grep -q '"data"'; then
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# Check Gateway health
# Usage: check_gateway_health [url] [timeout_seconds]
# Returns 0 if healthy, 1 if not
# -----------------------------------------------------------------------------
check_gateway_health() {
    local url="${1:-http://localhost:8080/health}"
    local timeout="${2:-10}"

    if curl -sf --max-time "$timeout" "$url" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# Wait for UMRS to become healthy
# Usage: wait_for_umrs [max_attempts] [interval_seconds]
# -----------------------------------------------------------------------------
wait_for_umrs() {
    local max_attempts="${1:-60}"
    local interval="${2:-5}"
    local attempt=0

    while [[ $attempt -lt $max_attempts ]]; do
        if check_umrs_health; then
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep "$interval"
    done
    return 1
}

# -----------------------------------------------------------------------------
# Wait for LLM to become healthy
# Usage: wait_for_llm [url] [api_key] [max_attempts] [interval_seconds]
# -----------------------------------------------------------------------------
wait_for_llm() {
    local url="${1:-http://localhost:11434/v1/models}"
    local api_key="${2:-}"
    local max_attempts="${3:-120}"
    local interval="${4:-5}"
    local attempt=0

    while [[ $attempt -lt $max_attempts ]]; do
        if check_llm_health "$url" "$api_key"; then
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep "$interval"
    done
    return 1
}

# -----------------------------------------------------------------------------
# Wait for Gateway to become healthy
# Usage: wait_for_gateway [url] [max_attempts] [interval_seconds]
# -----------------------------------------------------------------------------
wait_for_gateway() {
    local url="${1:-http://localhost:8080/health}"
    local max_attempts="${2:-30}"
    local interval="${3:-5}"
    local attempt=0

    while [[ $attempt -lt $max_attempts ]]; do
        if check_gateway_health "$url"; then
            return 0
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep "$interval"
    done
    return 1
}

# -----------------------------------------------------------------------------
# Check all services health (comprehensive check)
# Usage: check_all_services [options]
# Options:
#   --llm-url URL       LLM endpoint URL (default: http://localhost:11434/v1/models)
#   --api-key KEY       API key for LLM
#   --gateway-url URL   Gateway URL (default: http://localhost:8080/health)
#   --skip-umrs         Skip UMRS check
#   --skip-llm          Skip LLM check
#   --skip-gateway      Skip Gateway check (default: skipped in direct mode)
# -----------------------------------------------------------------------------
check_all_services() {
    local llm_url="http://localhost:11434/v1/models"
    local api_key=""
    local gateway_url="http://localhost:8080/health"
    local check_umrs=true
    local check_llm=true
    local check_gateway=false  # Default: skip gateway in direct mode

    while [[ $# -gt 0 ]]; do
        case $1 in
            --llm-url) llm_url="$2"; shift 2 ;;
            --api-key) api_key="$2"; shift 2 ;;
            --gateway-url) gateway_url="$2"; shift 2 ;;
            --skip-umrs) check_umrs=false; shift ;;
            --skip-llm) check_llm=false; shift ;;
            --skip-gateway) check_gateway=false; shift ;;
            --with-gateway) check_gateway=true; shift ;;
            *) shift ;;
        esac
    done

    local all_healthy=true

    if [[ "$check_umrs" == "true" ]]; then
        if check_umrs_health; then
            echo -e "${GREEN}[OK]${NC} UMRS is healthy"
        else
            echo -e "${RED}[FAIL]${NC} UMRS is not responding"
            all_healthy=false
        fi
    fi

    if [[ "$check_llm" == "true" ]]; then
        if check_llm_health "$llm_url" "$api_key"; then
            echo -e "${GREEN}[OK]${NC} LLM is healthy"
        else
            echo -e "${RED}[FAIL]${NC} LLM is not responding"
            all_healthy=false
        fi
    fi

    if [[ "$check_gateway" == "true" ]]; then
        if check_gateway_health "$gateway_url"; then
            echo -e "${GREEN}[OK]${NC} Gateway is healthy"
        else
            echo -e "${RED}[FAIL]${NC} Gateway is not responding"
            all_healthy=false
        fi
    fi

    [[ "$all_healthy" == "true" ]]
}