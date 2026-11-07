#!/usr/bin/env bash
# =============================================================================
# UMRS + RiVault HPC Demo - Shutdown Script
# =============================================================================
# This script stops all demo services:
#   - Cancels the RiVault Slurm job on compute node
#   - Stops login node services (postgres, minio, umrs-server)
#
# Usage:
#   ./stop-demo.sh [--keep-data]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_ID_FILE="${SCRIPT_DIR}/.rivault_job_id"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Parse arguments
KEEP_DATA=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data) KEEP_DATA=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--keep-data]"
            echo "  --keep-data  Keep database and MinIO volumes"
            exit 0
            ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

# -----------------------------------------------------------------------------
# Step 1: Cancel Slurm job
# -----------------------------------------------------------------------------
if [[ -f "${JOB_ID_FILE}" ]]; then
    JOB_ID=$(cat "${JOB_ID_FILE}")
    log_info "Cancelling RiVault job: ${JOB_ID}"

    # Check if job is still running
    if squeue -j "${JOB_ID}" &> /dev/null 2>&1; then
        scancel "${JOB_ID}" 2>/dev/null || true
        log_info "Job ${JOB_ID} cancelled"
    else
        log_warn "Job ${JOB_ID} is not running"
    fi

    rm -f "${JOB_ID_FILE}"
else
    log_info "No RiVault job ID file found, skipping Slurm cancellation"
fi

# -----------------------------------------------------------------------------
# Step 2: Stop login node services
# -----------------------------------------------------------------------------
log_info "Stopping login node services..."
cd "${SCRIPT_DIR}"

if command -v podman &> /dev/null; then
    if [[ "${KEEP_DATA}" == "true" ]]; then
        podman compose -f umrs-login.yaml down
        log_info "Login services stopped (volumes preserved)"
    else
        podman compose -f umrs-login.yaml down -v
        log_info "Login services stopped (volumes removed)"
    fi
else
    log_warn "podman not found, cannot stop services"
fi

# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------
log_info "Cleanup complete"
echo ""
echo "=============================================="
echo "  UMRS + RiVault HPC Demo Stopped"
echo "=============================================="
if [[ "${KEEP_DATA}" == "true" ]]; then
    echo "Database and MinIO data preserved."
    echo "Use: podman volume ls"
fi
echo "=============================================="
