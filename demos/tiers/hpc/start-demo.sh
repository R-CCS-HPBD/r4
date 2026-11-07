#!/usr/bin/env bash
# =============================================================================
# UMRS + RiVault HPC Demo - Startup Script
# =============================================================================
# This script starts the UMRS services on the login node and submits a Slurm
# job to start RiVault (vLLM + LiteLLM) on a compute node with GPU.
#
# Architecture:
#   Login Node: postgres, minio, umrs-server (via podman-compose)
#   Compute Node: RiVault (vLLM, LiteLLM, nginx) (via Slurm sbatch)
#
# Requirements:
#   - Podman with podman-compose
#   - Slurm (sbatch, squeue, scancel)
#   - Shared filesystem between login and compute nodes
#   - Pre-staged model on shared filesystem
#
# Usage:
#   ./start-demo.sh [--skip-login] [--skip-compute]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RIVAULT_DIR="${SCRIPT_DIR}/../../../helm/rivault"
SLURM_SCRIPT="${RIVAULT_DIR}/serve-model.slurm"
JOB_ID_FILE="${SCRIPT_DIR}/.rivault_job_id"
ENV_FILE="${SCRIPT_DIR}/env.hpc"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Parse arguments
SKIP_LOGIN=false
SKIP_COMPUTE=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-login) SKIP_LOGIN=true; shift ;;
        --skip-compute) SKIP_COMPUTE=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--skip-login] [--skip-compute]"
            echo "  --skip-login   Skip starting login node services"
            echo "  --skip-compute Skip submitting compute node job"
            exit 0
            ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

# Load environment
if [[ -f "${ENV_FILE}" ]]; then
    log_info "Loading environment from ${ENV_FILE}"
    set -a
    source "${ENV_FILE}"
    set +a
else
    log_error "Environment file not found: ${ENV_FILE}"
    log_info "Copy env.template to env.hpc and configure it"
    exit 1
fi

# Validate required variables
: "${VLLM_PATH_TO_HUGGINGFACE:?VLLM_PATH_TO_HUGGINGFACE must be set in env.hpc}"
: "${VLLM_MODEL:?VLLM_MODEL must be set in env.hpc}"
: "${SLURM_PARTITION:?SLURM_PARTITION must be set in env.hpc}"

# -----------------------------------------------------------------------------
# Step 1: Start login node services
# -----------------------------------------------------------------------------
if [[ "${SKIP_LOGIN}" == "false" ]]; then
    log_info "Starting login node services..."
    cd "${SCRIPT_DIR}"

    # Check if podman is available
    if ! command -v podman &> /dev/null; then
        log_error "podman is required but not found"
        exit 1
    fi

    # Start services
    podman compose -f umrs-login.yaml up -d

    # Wait for services to be healthy
    log_info "Waiting for UMRS services to be ready..."
    MAX_WAIT=60
    WAIT_COUNT=0
    until curl -sf http://localhost:${UMRS_API_PORT:-8000}/health > /dev/null 2>&1; do
        WAIT_COUNT=$((WAIT_COUNT + 1))
        if [[ ${WAIT_COUNT} -ge ${MAX_WAIT} ]]; then
            log_error "UMRS services did not become ready within ${MAX_WAIT}s"
            exit 1
        fi
        sleep 1
    done
    log_info "Login node services ready"
fi

# -----------------------------------------------------------------------------
# Step 2: Submit compute node job (RiVault)
# -----------------------------------------------------------------------------
if [[ "${SKIP_COMPUTE}" == "false" ]]; then
    log_info "Submitting RiVault job to Slurm..."

    # Check if Slurm script exists
    if [[ ! -f "${SLURM_SCRIPT}" ]]; then
        log_error "Slurm script not found: ${SLURM_SCRIPT}"
        exit 1
    fi

    # Check if a job is already running
    if [[ -f "${JOB_ID_FILE}" ]]; then
        OLD_JOB_ID=$(cat "${JOB_ID_FILE}")
        if squeue -j "${OLD_JOB_ID}" &> /dev/null; then
            log_warn "RiVault job ${OLD_JOB_ID} is already running"
            log_warn "Use stop-demo.sh to stop it first, or remove ${JOB_ID_FILE}"
            exit 1
        fi
    fi

    # Get login node hostname for UMRS connection
    LOGIN_NODE=$(hostname -f)

    # Export variables for Slurm script (serve-model.slurm uses these)
    export VLLM_MODEL
    export VLLM_PATH_TO_HUGGINGFACE
    export VLLM_TP="${VLLM_TP:-1}"
    export VLLM_PP="${VLLM_PP:-1}"
    export UMRS_S3_BUCKET_HOST="${LOGIN_NODE}"
    export UMRS_S3_BUCKET_PORT="${MINIO_API_PORT:-9000}"

    # Submit job and capture job ID
    JOB_OUTPUT=$(sbatch \
        --partition="${SLURM_PARTITION}" \
        --gres="gpu:${SLURM_GPUS:-1}" \
        --time="${SLURM_TIME:-02:00:00}" \
        --cpus-per-task="${SLURM_CPUS:-8}" \
        --mem="${SLURM_MEM:-64G}" \
        --chdir="${RIVAULT_DIR}" \
        --export=ALL \
        "${SLURM_SCRIPT}" 2>&1)

    if [[ $? -ne 0 ]]; then
        log_error "Failed to submit Slurm job: ${JOB_OUTPUT}"
        exit 1
    fi

    # Extract job ID (format: "Submitted batch job 12345")
    JOB_ID=$(echo "${JOB_OUTPUT}" | grep -oP 'Submitted batch job \K\d+')
    if [[ -z "${JOB_ID}" ]]; then
        log_error "Could not parse job ID from: ${JOB_OUTPUT}"
        exit 1
    fi

    # Save job ID for later use
    echo "${JOB_ID}" > "${JOB_ID_FILE}"
    log_info "Submitted RiVault job: ${JOB_ID}"

    # Wait for job to start running
    log_info "Waiting for job to start..."
    MAX_WAIT=${JOB_START_TIMEOUT:-300}
    WAIT_COUNT=0
    while true; do
        JOB_STATE=$(squeue -j "${JOB_ID}" -h -o "%T" 2>/dev/null || echo "UNKNOWN")
        case "${JOB_STATE}" in
            RUNNING)
                log_info "Job ${JOB_ID} is now running"
                break
                ;;
            PENDING)
                WAIT_COUNT=$((WAIT_COUNT + 5))
                if [[ ${WAIT_COUNT} -ge ${MAX_WAIT} ]]; then
                    log_warn "Job still pending after ${MAX_WAIT}s"
                    log_warn "Check job status with: squeue -j ${JOB_ID}"
                    break
                fi
                echo -n "."
                sleep 5
                ;;
            COMPLETED|FAILED|CANCELLED|TIMEOUT|*)
                if [[ "${JOB_STATE}" == "UNKNOWN" ]]; then
                    # Job might have completed very quickly or been cancelled
                    log_error "Job ${JOB_ID} not found in queue (state: ${JOB_STATE})"
                else
                    log_error "Job ${JOB_ID} ended with state: ${JOB_STATE}"
                fi
                rm -f "${JOB_ID_FILE}"
                exit 1
                ;;
        esac
    done
    echo ""

    # Get compute node hostname
    COMPUTE_NODE=$(squeue -j "${JOB_ID}" -h -o "%N" 2>/dev/null)
    if [[ -n "${COMPUTE_NODE}" ]]; then
        log_info "RiVault running on: ${COMPUTE_NODE}"
    fi
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  UMRS + RiVault HPC Demo Started"
echo "=============================================="
echo ""
echo "Login Node Services:"
echo "  UMRS API:      http://localhost:${UMRS_API_PORT:-8000}"
echo "  MinIO Console: http://localhost:${MINIO_CONSOLE_PORT:-9001}"
echo ""
if [[ "${SKIP_COMPUTE}" == "false" && -f "${JOB_ID_FILE}" ]]; then
    JOB_ID=$(cat "${JOB_ID_FILE}")
    COMPUTE_NODE=$(squeue -j "${JOB_ID}" -h -o "%N" 2>/dev/null || echo "unknown")
    echo "Compute Node Services (Job ${JOB_ID} on ${COMPUTE_NODE}):"
    echo "  LiteLLM API:   http://${COMPUTE_NODE}:11434"
    echo "  vLLM API:      http://${COMPUTE_NODE}:8000"
    echo ""
    echo "Note: Wait for RiVault to be ready. Check status with:"
    echo "  squeue -j ${JOB_ID}"
    echo "  curl http://${COMPUTE_NODE}:11434/v1/models"
fi
echo ""
echo "To stop: ./stop-demo.sh"
echo "=============================================="
