#!/usr/bin/env bash
# =============================================================================
# R4 Platform - DGX Spark System Model Seed
# =============================================================================
# Registers system-level models in UMRS for DGX Spark deployment
# using the `r4 model register` CLI command.
#
# Models are selected to fit on:
#   - 1x DGX Spark (128GB unified memory)
#   - 2x DGX Spark (256GB unified memory, tensor parallel)
#
# Usage:
#   ./seed_system_models.sh                # Register all models
#   ./seed_system_models.sh --dry-run      # Show what would be registered
#   ./seed_system_models.sh --download     # Also download models from HF
#
# Prerequisites:
#   - UMRS server running (start.sh already executed)
#   - r4 CLI installed (pip install -e ../../../r4 from this directory,
#     or pip install -e ./r4 from the repo root)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ROOT="$(cd "$DEMOS_DIR/.." && pwd)"

# Source environment
if [[ -f "$SCRIPT_DIR/env.local" ]]; then
    source "$SCRIPT_DIR/env.local"
fi

MODEL_STORAGE_ROOT="${MODEL_STORAGE_ROOT:-/shared/models}"
DRY_RUN=false
DOWNLOAD=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_spark() { echo -e "${CYAN}[DGX Spark]${NC} $*"; }

resolve_site_config_path() {
    if [[ -n "${UMRS_SITE_CONFIG:-}" && -f "${UMRS_SITE_CONFIG}" ]]; then
        printf '%s\n' "${UMRS_SITE_CONFIG}"
        return 0
    fi

    if [[ -f "./site.yaml" ]]; then
        printf '%s\n' "$(pwd)/site.yaml"
        return 0
    fi

    if [[ -f "$HOME/.config/umrs/site.yaml" ]]; then
        printf '%s\n' "$HOME/.config/umrs/site.yaml"
    fi
}

resolve_site_system_root() {
    local site_config_path=""
    local model_cache_dir=""

    site_config_path="$(resolve_site_config_path)"
    if [[ -z "$site_config_path" ]]; then
        return 0
    fi

    model_cache_dir="$(awk '
        /^[[:space:]]*model_cache_dir:[[:space:]]*/ {
            sub(/^[[:space:]]*model_cache_dir:[[:space:]]*/, "", $0)
            gsub(/["\x27]/, "", $0)
            print
            exit
        }
    ' "$site_config_path")"

    if [[ -n "$model_cache_dir" ]]; then
        dirname "$model_cache_dir"
    fi
}

normalize_storage_root() {
    local requested_root="$1"
    local site_system_root=""
    local nested_site_root=""

    site_system_root="$(resolve_site_system_root)"
    nested_site_root="${site_system_root}/system"

    if [[ -n "$site_system_root" && "$requested_root" == "$nested_site_root" ]]; then
        log_warn "Storage root ${requested_root} nests under the configured system model root. Using ${site_system_root} instead." >&2
        printf '%s\n' "$site_system_root"
        return 0
    fi

    printf '%s\n' "$requested_root"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --download)
            DOWNLOAD=true
            shift
            ;;
        --storage-root)
            MODEL_STORAGE_ROOT="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run        Show what would be registered (no changes)"
            echo "  --download       Download models from HuggingFace before registering"
            echo "  --storage-root P Model storage root (default: $MODEL_STORAGE_ROOT)"
            echo "  -h, --help       Show this help"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

MODEL_STORAGE_ROOT="$(normalize_storage_root "$MODEL_STORAGE_ROOT")"

# =============================================================================
# Banner
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║       DGX Spark - System Model Registration (r4 CLI)            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
log_info "Model storage root: ${MODEL_STORAGE_ROOT}"

# =============================================================================
# Check prerequisites
# =============================================================================
if ! command -v r4 &>/dev/null; then
    log_error "r4 CLI not found. Install from repo root with: pip install -e ./r4"
    exit 1
fi
log_ok "r4 CLI found: $(command -v r4)"

# =============================================================================
# Model Definitions
# =============================================================================
# DGX Spark Hardware:
#   1x DGX Spark: 128GB unified memory (Grace + Blackwell GB10)
#   2x DGX Spark: 256GB unified memory (tensor parallel across 2 nodes)
#
# Memory estimation (rough):
#   BF16: ~2 bytes/param  →  72B model ≈ 144GB
#   FP8:  ~1 byte/param   →  72B model ≈ 72GB
#   MoE:  all params loaded but only active subset used per token
# =============================================================================

register_model() {
    local name="$1"
    local storage_ref="$2"
    local hf_id="$3"
    local description="$4"
    local tp_size="$5"
    local max_model_len="$6"
    local gpu_mem_util="$7"
    local quantization="$8"    # "fp8", "awq", "gptq", or "" for none

    echo ""
    log_info "Model: ${name}"
    log_info "  HuggingFace: ${hf_id}"
    log_info "  Storage:     ${storage_ref}"
    log_info "  TP Size:     ${tp_size}"
    log_info "  Max Len:     ${max_model_len}"
    log_info "  GPU Mem:     ${gpu_mem_util}"
    log_info "  Quantization: ${quantization:-none}"
    log_info "  Description: ${description}"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_warn "  [DRY RUN] Would run:"
        local cmd="r4 model register \"${name}\" --path \"${storage_ref}\" --scope system --hf-id \"${hf_id}\" --description \"${description}\" --tp ${tp_size} --max-model-len ${max_model_len} --gpu-mem-util ${gpu_mem_util}"
        if [[ -n "$quantization" ]]; then
            cmd+=" --quantization ${quantization}"
        fi
        echo "    $cmd"
        return 0
    fi

    # Build r4 model register command
    local r4_args=(
        model register "$name"
        --path "$storage_ref"
        --scope system
        --hf-id "$hf_id"
        --description "$description"
        --tp "$tp_size"
        --max-model-len "$max_model_len"
        --gpu-mem-util "$gpu_mem_util"
    )
    if [[ -n "$quantization" ]]; then
        r4_args+=(--quantization "$quantization")
    fi

    local output=""
    if output="$(r4 "${r4_args[@]}" 2>&1)"; then
        if [[ -n "$output" ]]; then
            echo "$output"
        fi
        log_ok "  Registered successfully"
    else
        local exit_code=$?
        # Check if already registered (duplicate)
        if r4 model list --scope system 2>/dev/null | grep -q "$name"; then
            log_warn "  Already registered (skipping)"
        else
            log_error "  Registration failed (exit code ${exit_code})"
            if [[ -n "$output" ]]; then
                echo "$output"
            fi
        fi
    fi
}

download_model() {
    local hf_id="$1"
    local storage_ref="$2"

    if [[ "$DOWNLOAD" != "true" ]]; then
        return 0
    fi

    if [[ -d "$storage_ref" ]] && [[ -n "$(ls -A "$storage_ref" 2>/dev/null)" ]]; then
        log_info "  Model already downloaded: ${storage_ref}"
        # Still optimize striping for already-downloaded models
        optimize_lustre_striping "$storage_ref"
        return 0
    fi

    log_info "  Downloading ${hf_id} → ${storage_ref} ..."

    # Pre-set Lustre directory striping before downloading so new files
    # inherit multi-OST layout automatically.
    if command -v lfs &>/dev/null; then
        mkdir -p "$storage_ref"
        if lfs setstripe -c "${LUSTRE_STRIPE_COUNT:-4}" -S "${LUSTRE_STRIPE_SIZE:-4M}" "$storage_ref" 2>/dev/null; then
            log_info "  Lustre: directory stripe set (count=${LUSTRE_STRIPE_COUNT:-4}, size=${LUSTRE_STRIPE_SIZE:-4M})"
        fi
    else
        mkdir -p "$storage_ref"
    fi

    # Ensure HF CLI is available
    export PATH="$HOME/.local/bin:$PATH"
    if command -v hf &>/dev/null; then
        hf download "$hf_id" --local-dir "$storage_ref"
    elif command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$hf_id" --local-dir "$storage_ref" --resume-download
    else
        log_error "  HuggingFace CLI not found. Install with: pip install huggingface-hub[cli]"
        return 1
    fi

    # Optimize striping for any files that didn't inherit the directory default
    optimize_lustre_striping "$storage_ref"

    log_ok "  Download complete: ${storage_ref}"
}

# ---------------------------------------------------------------------------
# Lustre striping optimization
# ---------------------------------------------------------------------------
# Re-stripes large weight files across multiple OSTs for parallel I/O.
# No-op on non-Lustre filesystems.
# ---------------------------------------------------------------------------
optimize_lustre_striping() {
    local model_dir="$1"
    local stripe_count="${LUSTRE_STRIPE_COUNT:-4}"
    local stripe_size="${LUSTRE_STRIPE_SIZE:-4M}"
    local min_size_mb=256  # only re-stripe files > 256 MiB

    if ! command -v lfs &>/dev/null; then
        return 0
    fi

    # Check if directory is on Lustre
    if ! lfs getstripe "$model_dir" &>/dev/null; then
        return 0
    fi

    local migrated=0
    while IFS= read -r -d '' fpath; do
        local fsize_bytes
        fsize_bytes=$(stat --format='%s' "$fpath" 2>/dev/null || stat -f '%z' "$fpath" 2>/dev/null || echo 0)
        local min_size_bytes=$((min_size_mb * 1024 * 1024))

        if (( fsize_bytes < min_size_bytes )); then
            continue
        fi

        # Check current stripe count
        local current_count
        current_count=$(lfs getstripe --stripe-count "$fpath" 2>/dev/null | awk '/stripe_count:/{print $NF}')
        if [[ -n "$current_count" ]] && (( current_count >= stripe_count )); then
            continue
        fi

        local fsize_gib
        fsize_gib=$(awk "BEGIN{printf \"%.1f\", $fsize_bytes / 1073741824}")
        log_info "  Lustre: migrating ${fpath##*/} (${fsize_gib} GiB, stripe ${current_count:-1}→${stripe_count})"
        if lfs migrate -c "$stripe_count" -S "$stripe_size" "$fpath" 2>/dev/null; then
            migrated=$((migrated + 1))
        else
            log_warn "  Lustre: migrate failed for ${fpath##*/}"
        fi
    done < <(find "$model_dir" -type f \( -name '*.safetensors' -o -name '*.bin' -o -name '*.pt' -o -name '*.pth' -o -name '*.gguf' \) -print0)

    if (( migrated > 0 )); then
        log_ok "  Lustre: re-striped ${migrated} weight files (stripe_count=${stripe_count})"
    fi
}

# =============================================================================
# Model 1: Qwen3-235B-A22B (MoE flagship — 2x DGX Spark)
# =============================================================================
# 235B total params, 22B active per token (MoE with 128 experts, 8 active)
# BF16: ~470GB total weights → requires 2x DGX Spark (~235GB)
# Supports thinking/non-thinking mode, agentic tool use, 100+ languages
# Context: 32K native, 131K with YaRN
# =============================================================================
log_spark "=== Model 1: Qwen3-235B-A22B (2x DGX Spark, MoE flagship) ==="

MODEL1_NAME="qwen3-235b-a22b"
MODEL1_HF="Qwen/Qwen3-235B-A22B"
MODEL1_PATH="${MODEL_STORAGE_ROOT}/Qwen3-235B-A22B"
MODEL1_DESC="Qwen3 235B MoE (22B active) - flagship reasoning, thinking mode, agentic, 100+ languages. Requires 2x DGX Spark."

download_model "$MODEL1_HF" "$MODEL1_PATH"
register_model \
    "$MODEL1_NAME" \
    "$MODEL1_PATH" \
    "$MODEL1_HF" \
    "$MODEL1_DESC" \
    2       \
    32768   \
    0.85    \
    ""      # no quantization (BF16, vLLM handles MoE)

# =============================================================================
# Model 2: Qwen2.5-72B-Instruct (Dense workhorse — 1x DGX Spark with FP8)
# =============================================================================
# 72B dense model, ~144GB BF16 → ~72GB with FP8 quantization
# Excellent at general tasks, coding, reasoning
# Proven production model, widely deployed
# =============================================================================
log_spark "=== Model 2: Qwen2.5-72B-Instruct (1x DGX Spark, FP8) ==="

MODEL2_NAME="qwen25-72b-instruct"
MODEL2_HF="Qwen/Qwen2.5-72B-Instruct"
MODEL2_PATH="${MODEL_STORAGE_ROOT}/Qwen2.5-72B-Instruct"
MODEL2_DESC="Qwen2.5 72B Instruct - dense general-purpose, coding and reasoning. 1x DGX Spark with FP8."

download_model "$MODEL2_HF" "$MODEL2_PATH"
register_model \
    "$MODEL2_NAME" \
    "$MODEL2_PATH" \
    "$MODEL2_HF" \
    "$MODEL2_DESC" \
    1       \
    32768   \
    0.90    \
    "fp8"   # FP8 quantization to fit in 128GB

# =============================================================================
# Model 3: Qwen3-32B (Dense reasoning — 1x DGX Spark, BF16)
# =============================================================================
# 32B dense model, ~64GB BF16 — fits comfortably on 1x DGX Spark
# Supports thinking/non-thinking mode (/think, /no_think)
# Excellent reasoning, math, coding with thinking mode
# =============================================================================
log_spark "=== Model 3: Qwen3-32B (1x DGX Spark, BF16) ==="

MODEL3_NAME="qwen3-32b"
MODEL3_HF="Qwen/Qwen3-32B"
MODEL3_PATH="${MODEL_STORAGE_ROOT}/Qwen3-32B"
MODEL3_DESC="Qwen3 32B - thinking/non-thinking mode, strong reasoning and coding. 1x DGX Spark in BF16."

download_model "$MODEL3_HF" "$MODEL3_PATH"
register_model \
    "$MODEL3_NAME" \
    "$MODEL3_PATH" \
    "$MODEL3_HF" \
    "$MODEL3_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 4: Qwen2.5-Coder-32B-Instruct (Code specialist — 1x DGX Spark)
# =============================================================================
# 32B dense model fine-tuned for code generation and understanding
# ~64GB BF16 — fits on 1x DGX Spark
# Excels at code generation, completion, debugging, documentation
# =============================================================================
log_spark "=== Model 4: Qwen2.5-Coder-32B-Instruct (1x DGX Spark, BF16) ==="

MODEL4_NAME="qwen25-coder-32b-instruct"
MODEL4_HF="Qwen/Qwen2.5-Coder-32B-Instruct"
MODEL4_PATH="${MODEL_STORAGE_ROOT}/Qwen2.5-Coder-32B-Instruct"
MODEL4_DESC="Qwen2.5 Coder 32B - code generation, debugging, documentation. 1x DGX Spark."

download_model "$MODEL4_HF" "$MODEL4_PATH"
register_model \
    "$MODEL4_NAME" \
    "$MODEL4_PATH" \
    "$MODEL4_HF" \
    "$MODEL4_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 5: Bonsai-8B (Ultra-efficient — 1x DGX Spark)
# =============================================================================
# 8B model by PrismML, based on Qwen3-8B architecture
# ~16GB BF16 — trivially fits on any DGX Spark
# Competitive with full-precision 8B models
# Fast inference, low resource usage, good for lightweight tasks
# =============================================================================
log_spark "=== Model 5: Bonsai-8B (1x DGX Spark, BF16) ==="

MODEL5_NAME="bonsai-8b"
MODEL5_HF="prism-ml/Bonsai-8B-unpacked"
MODEL5_PATH="${MODEL_STORAGE_ROOT}/Bonsai-8B"
MODEL5_DESC="PrismML Bonsai 8B - ultra-efficient, competitive benchmarks at minimal resources. 1x DGX Spark."

download_model "$MODEL5_HF" "$MODEL5_PATH"
register_model \
    "$MODEL5_NAME" \
    "$MODEL5_PATH" \
    "$MODEL5_HF" \
    "$MODEL5_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 6: GLM-4.7-Flash (Latest GLM — 1x DGX Spark, BF16)
# =============================================================================
# 30B MoE (3B active) from Zhipu AI — strongest model in 30B class
# ~62GB BF16 — fits on 1x DGX Spark
# AIME 91.6, SWE-bench 59.2, excellent agentic reasoning
# vLLM native support, Blackwell GPU optimized, tool calling
# =============================================================================
log_spark "=== Model 6: GLM-4.7-Flash (1x DGX Spark, BF16) ==="

MODEL6_NAME="glm47-flash"
MODEL6_HF="zai-org/GLM-4.7-Flash"
MODEL6_PATH="${MODEL_STORAGE_ROOT}/GLM-4.7-Flash"
MODEL6_DESC="GLM-4.7 Flash 30B MoE (3B active) - strongest 30B class, agentic reasoning, tool calling. 1x DGX Spark."

download_model "$MODEL6_HF" "$MODEL6_PATH"
register_model \
    "$MODEL6_NAME" \
    "$MODEL6_PATH" \
    "$MODEL6_HF" \
    "$MODEL6_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 7: DeepSeek-R1-Distill-Qwen-32B (DeepSeek reasoning — 1x DGX Spark)
# =============================================================================
# 32B dense model distilled from DeepSeek-R1 (671B MoE)
# ~64GB BF16 — fits on 1x DGX Spark
# Outperforms OpenAI o1-mini on math/code/reasoning benchmarks
# Full DeepSeek-R1 (671B) is too large; this distilled version retains
# excellent reasoning with practical VRAM requirements
# =============================================================================
log_spark "=== Model 7: DeepSeek-R1-Distill-Qwen-32B (1x DGX Spark, BF16) ==="

MODEL7_NAME="deepseek-r1-distill-qwen-32b"
MODEL7_HF="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
MODEL7_PATH="${MODEL_STORAGE_ROOT}/DeepSeek-R1-Distill-Qwen-32B"
MODEL7_DESC="DeepSeek R1 Distill 32B - reasoning distilled from 671B R1, outperforms o1-mini. 1x DGX Spark."

download_model "$MODEL7_HF" "$MODEL7_PATH"
register_model \
    "$MODEL7_NAME" \
    "$MODEL7_PATH" \
    "$MODEL7_HF" \
    "$MODEL7_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 8: Qwen3-30B-A3B (Lightweight MoE — 1x DGX Spark, BF16)
# =============================================================================
# 30B total params, only 3B active per token (MoE with 128 experts, 8 active)
# ~60GB total weights in BF16 — fits on 1x DGX Spark
# Extremely efficient: full thinking/non-thinking mode with tiny active params
# Ideal for high-throughput serving with limited resources
# =============================================================================
log_spark "=== Model 8: Qwen3-30B-A3B (1x DGX Spark, BF16) ==="

MODEL8_NAME="qwen3-30b-a3b"
MODEL8_HF="Qwen/Qwen3-30B-A3B"
MODEL8_PATH="${MODEL_STORAGE_ROOT}/Qwen3-30B-A3B"
MODEL8_DESC="Qwen3 30B MoE (3B active) - lightweight thinking model, high throughput, agentic. 1x DGX Spark."

download_model "$MODEL8_HF" "$MODEL8_PATH"
register_model \
    "$MODEL8_NAME" \
    "$MODEL8_PATH" \
    "$MODEL8_HF" \
    "$MODEL8_DESC" \
    1       \
    32768   \
    0.85    \
    ""      # no quantization (BF16)

# =============================================================================
# Model 9: GLM-5.1 1-bit GGUF (Next-gen agentic — 2x DGX Spark)
# =============================================================================
# 754B MoE (glm_moe_dsa) from Zhipu AI — next-gen flagship for agentic engineering
# 1-bit quantized GGUF (UD-IQ1_M): ~206GB → requires 2x DGX Spark
# State-of-the-art on SWE-Bench Pro, NL2Repo, Terminal-Bench 2.0
# Excels at long-horizon agentic tasks: hundreds of rounds, thousands of tool calls
# vLLM v0.19.0+ native support, MIT license
# =============================================================================
log_spark "=== Model 9: GLM-5.1 1-bit GGUF (2x DGX Spark) ==="

MODEL9_NAME="glm51-1bit-gguf"
MODEL9_HF="unsloth/GLM-5.1-GGUF"
MODEL9_PATH="${MODEL_STORAGE_ROOT}/system/GLM-5.1-GGUF"
MODEL9_DESC="GLM-5.1 754B MoE 1-bit GGUF (UD-IQ1_M, 206GB) - next-gen agentic, SWE-Bench Pro SOTA, long-horizon tool use. 2x DGX Spark."

download_model "$MODEL9_HF" "$MODEL9_PATH"
register_model \
    "$MODEL9_NAME" \
    "$MODEL9_PATH" \
    "$MODEL9_HF" \
    "$MODEL9_DESC" \
    2       \
    32768   \
    0.85    \
    "gguf"  # 1-bit GGUF quantization (UD-IQ1_M)

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║              System Model Registration Complete                  ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "Registered models:"
echo ""
printf "  %-35s %-12s %-10s %s\n" "NAME" "PARAMS" "DGX SPARKS" "NOTES"
printf "  %-35s %-12s %-10s %s\n" "----" "------" "----------" "-----"
printf "  %-35s %-12s %-10s %s\n" "qwen3-235b-a22b"               "235B MoE"  "2x"  "Flagship reasoning, BF16"
printf "  %-35s %-12s %-10s %s\n" "qwen25-72b-instruct"           "72B"       "1x"  "General-purpose, FP8"
printf "  %-35s %-12s %-10s %s\n" "qwen3-32b"                     "32B"       "1x"  "Thinking mode, BF16"
printf "  %-35s %-12s %-10s %s\n" "qwen25-coder-32b-instruct"     "32B"       "1x"  "Code specialist, BF16"
printf "  %-35s %-12s %-10s %s\n" "bonsai-8b"                     "8B"        "1x"  "Ultra-efficient, BF16"
printf "  %-35s %-12s %-10s %s\n" "glm47-flash"                   "30B MoE"   "1x"  "Strongest 30B class, BF16"
printf "  %-35s %-12s %-10s %s\n" "deepseek-r1-distill-qwen-32b"  "32B"       "1x"  "Reasoning (R1 distill), BF16"
printf "  %-35s %-12s %-10s %s\n" "qwen3-30b-a3b"                 "30B MoE"   "1x"  "Lightweight thinking, BF16"
printf "  %-35s %-12s %-10s %s\n" "glm51-1bit-gguf"               "754B MoE"  "2x"  "Agentic SOTA, 1-bit GGUF"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY RUN] No changes were made."
    echo ""
fi

if [[ "$DOWNLOAD" != "true" ]]; then
    echo "To download models from HuggingFace, re-run with --download:"
    echo "  $0 --download --storage-root ${MODEL_STORAGE_ROOT}"
    echo ""
fi

echo "To verify registered models:"
echo "  r4 model list --scope system"
echo ""
