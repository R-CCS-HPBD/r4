#!/bin/bash
#SBATCH --job-name=r4-demo
#SBATCH --output=r4-demo-%j.out
#SBATCH --error=r4-demo-%j.err
#SBATCH --time=01:00:00
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G

# =============================================================================
# R4 Platform E2E Demo on HPC
# =============================================================================
# Runs complete demo workflow on Slurm cluster.
#
# Prerequisites:
#   - mini-rivault.sif in shared storage
#   - r4-client.sif in shared storage
#   - Model files in shared storage
#
# Usage:
#   sbatch run-demo.sbatch
#
# Environment Variables:
#   RIVAULT_SIF  - Path to mini-rivault container image
#   CLIENT_SIF   - Path to client container image
#   MODEL_PATH   - Path to model directory
# =============================================================================

set -euo pipefail

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║  R4 Platform E2E Demo                                                ║"
echo "╠════════════════════════════════════════════════════════════════════════╣"
echo "║  Job ID:    ${SLURM_JOB_ID}                                            ║"
echo "║  Node:      ${SLURMD_NODENAME}                                         ║"
echo "║  GPUs:      ${CUDA_VISIBLE_DEVICES:-none}                              ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# Configuration
# =============================================================================

RIVAULT_SIF="${RIVAULT_SIF:-/shared/containers/mini-rivault.sif}"
CLIENT_SIF="${CLIENT_SIF:-/shared/containers/r4-client.sif}"
MODEL_PATH="${MODEL_PATH:-/shared/models/Qwen2.5-7B-Instruct}"
WORKSPACE="${WORKSPACE:-${HOME}/r4-demo-${SLURM_JOB_ID}}"

# Ports
VLLM_PORT=8000
LITELLM_PORT=11434
GATEWAY_PORT=8080

# Singularity/Apptainer command
if command -v apptainer &> /dev/null; then
    SINGULARITY="apptainer"
else
    SINGULARITY="singularity"
fi

# =============================================================================
# Validation
# =============================================================================

echo "=== Validating Environment ==="

if [[ ! -f "${RIVAULT_SIF}" ]]; then
    echo "ERROR: mini-rivault.sif not found at ${RIVAULT_SIF}"
    exit 1
fi

if [[ ! -f "${CLIENT_SIF}" ]]; then
    echo "ERROR: r4-client.sif not found at ${CLIENT_SIF}"
    exit 1
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
    echo "ERROR: Model directory not found at ${MODEL_PATH}"
    exit 1
fi

echo "  Container runtime: ${SINGULARITY}"
echo "  RiVault image:     ${RIVAULT_SIF}"
echo "  Client image:      ${CLIENT_SIF}"
echo "  Model path:        ${MODEL_PATH}"
echo ""

# =============================================================================
# Workspace Setup
# =============================================================================

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# Cleanup handler
cleanup() {
    echo ""
    echo "=== Cleanup ==="
    ${SINGULARITY} instance stop mini-rivault 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT SIGTERM SIGINT

# =============================================================================
# Start Mini-RiVault
# =============================================================================

echo "=== Starting Mini-RiVault ==="
echo "Model: ${MODEL_PATH}"
echo "Ports: vLLM=${VLLM_PORT}, LiteLLM=${LITELLM_PORT}, Gateway=${GATEWAY_PORT}"
echo ""

# Environment for offline operation
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1

# Start mini-rivault instance
${SINGULARITY} instance start \
    --nv \
    --contain \
    --bind "${MODEL_PATH}:/models:ro" \
    --bind "${WORKSPACE}:/workspace" \
    --env VLLM_MODEL_PATH=/models \
    --env VLLM_PORT=${VLLM_PORT} \
    --env LITELLM_PORT=${LITELLM_PORT} \
    --env GATEWAY_PORT=${GATEWAY_PORT} \
    --env TRANSFORMERS_OFFLINE=1 \
    --env HF_HUB_OFFLINE=1 \
    --net --network host \
    "${RIVAULT_SIF}" \
    mini-rivault

# =============================================================================
# Wait for Services
# =============================================================================

echo "Waiting for services to be ready..."

MAX_WAIT=300
WAITED=0
while ! curl -sf "http://localhost:${GATEWAY_PORT}/health" &>/dev/null; do
    sleep 5
    WAITED=$((WAITED + 5))
    if [[ ${WAITED} -ge ${MAX_WAIT} ]]; then
        echo "ERROR: Services failed to start within ${MAX_WAIT}s"
        ${SINGULARITY} instance list
        exit 1
    fi
    echo "  Waiting... (${WAITED}s)"
done

echo ""
echo "=== Services Ready ==="
echo "Gateway: http://$(hostname):${GATEWAY_PORT}"
echo ""

# =============================================================================
# Run Demo Workflows
# =============================================================================

echo "=== Running Demo Workflows ==="
echo ""

# Run demos using client container
${SINGULARITY} exec \
    --bind "${WORKSPACE}:/workspace" \
    --env GATEWAY_URL="http://localhost:${GATEWAY_PORT}" \
    "${CLIENT_SIF}" \
    bash -c "
        cd /workspace/demos/workflows
        for script in 01-*.sh 02-*.sh 03-*.sh; do
            if [[ -f \"\$script\" ]]; then
                echo \"Running: \$script\"
                bash \"\$script\" --auto || echo \"WARN: \$script failed\"
                echo \"\"
            fi
        done
    "

DEMO_EXIT=$?

# =============================================================================
# Summary
# =============================================================================

echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
if [[ ${DEMO_EXIT} -eq 0 ]]; then
    echo "║  Demo Completed Successfully                                           ║"
else
    echo "║  Demo Completed with Warnings (exit: ${DEMO_EXIT})                     ║"
fi
echo "╚════════════════════════════════════════════════════════════════════════╝"

exit ${DEMO_EXIT}
