#!/bin/bash
# =============================================================================
# HPC E2E Test Runner
# =============================================================================
# Script to run real-HPC tests on RCCS/Slurm infrastructure.
#
# The canonical path exercises R4 -> Gateway -> UMRS on real Slurm,
# while the raw Slurm diagnostic harness remains available for lower-level
# cluster validation.
#
# Prerequisites:
# - Slurm access (sbatch, squeue, sinfo commands)
# - Singularity/Apptainer installed
# - Pre-staged model weights
# - Pre-built Singularity image
# - For Gateway-backed tests: R4_GATEWAY_URL, R4_API_KEY,
#   R4_PROJECT_ID, and SLURM_TEST_MODEL_NAME
#
# Usage:
#   ./run_hpc_tests.sh [options]
#
# Options:
#   --model-path PATH     Path to model weights (default: /models/rivault-gpt-oss)
#   --image PATH          Singularity image path for RIVAULT_ORCHESTRATOR=slurm
#   --partition NAME      Slurm partition (default: gpu)
#   --skip-prereq         Skip prerequisite checks
#   --dry-run             Show what would run without executing
#   --gateway-smoke       Validate Gateway→UMRS→Slurm connectivity without GPU allocation
#   --podman-smoke        Submit a short Slurm job that verifies Podman on an allocated node
#   --slurm-egress-smoke  Submit a short Slurm job that expects direct public egress to be blocked
#   --hybrid-slurm-smoke  Validate srun --jobid/--nodelist execution on an allocated node
#
# =============================================================================

set -e

# Default configuration
MODEL_PATH="${SLURM_TEST_MODEL_PATH:-/models/rivault-gpt-oss}"
SINGULARITY_IMAGE="${SLURM_TEST_IMAGE:-/opt/singularity/mini-rivault.sif}"
SLURM_PARTITION="${SLURM_TEST_PARTITION:-gpu}"
RIVAULT_ORCHESTRATOR="${RIVAULT_ORCHESTRATOR:-slurm-podman}"
SKIP_PREREQ=false
DRY_RUN=false
PODMAN_SMOKE=false
SLURM_EGRESS_SMOKE=false
HYBRID_SLURM_SMOKE=false
GATEWAY_SMOKE=false
HPC_TEST_WORK_DIR="${HPC_TEST_WORK_DIR:-${HOME}/.cache/rivault-hpc-tests}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --model-path)
            MODEL_PATH="$2"
            shift 2
            ;;
        --image)
            SINGULARITY_IMAGE="$2"
            shift 2
            ;;
        --partition)
            SLURM_PARTITION="$2"
            shift 2
            ;;
        --skip-prereq)
            SKIP_PREREQ=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --podman-smoke)
            PODMAN_SMOKE=true
            shift
            ;;
        --gateway-smoke)
            GATEWAY_SMOKE=true
            shift
            ;;
        --slurm-egress-smoke)
            SLURM_EGRESS_SMOKE=true
            shift
            ;;
        --hybrid-slurm-smoke)
            HYBRID_SLURM_SMOKE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "============================================================"
echo "HPC E2E Test Runner for R4 Platform"
echo "============================================================"
echo ""
echo "Configuration:"
echo "  Model Path:     $MODEL_PATH"
echo "  Orchestrator:   $RIVAULT_ORCHESTRATOR"
if [[ "$RIVAULT_ORCHESTRATOR" == "slurm" ]]; then
    echo "  Singularity:    $SINGULARITY_IMAGE"
fi
echo "  Partition:      $SLURM_PARTITION"
if [[ -n "${R4_GATEWAY_URL:-}" ]]; then
    echo "  Gateway URL:    ${R4_GATEWAY_URL}"
fi
if [[ -n "${R4_PROJECT_ID:-}" ]]; then
    echo "  Project ID:     ${R4_PROJECT_ID}"
fi
if [[ -n "${SLURM_TEST_MODEL_NAME:-}" ]]; then
    echo "  Model Name:     ${SLURM_TEST_MODEL_NAME}"
fi
echo ""

# =============================================================================
# Prerequisite Checks
# =============================================================================

check_prerequisite() {
    local name="$1"
    local check_cmd="$2"

    if eval "$check_cmd" > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} $name"
        return 0
    else
        echo -e "  ${RED}✗${NC} $name"
        return 1
    fi
}

run_gateway_smoke() {
        local gateway_url="${R4_GATEWAY_URL:-http://localhost:8080}"
        local umrs_url="${UMRS_HTTP_URL:-http://localhost:8082}"
        local rc=0

        echo "Gateway→UMRS connectivity smoke test"
        echo "  Gateway URL: ${gateway_url}"
        echo "  UMRS URL:    ${umrs_url}"
        echo ""

        # 1. Gateway /health
        echo -n "  Gateway /health ... "
        local gw_health
        if gw_health="$(curl -sf "${gateway_url}/health" 2>&1)"; then
            echo "OK  (${gw_health})"
        else
            echo "FAIL"
            rc=1
        fi

        # 2. Gateway /ready (checks UMRS backend connectivity)
        echo -n "  Gateway /ready  ... "
        local gw_ready
        if gw_ready="$(curl -sf "${gateway_url}/ready" 2>&1)"; then
            if echo "$gw_ready" | python3 -c "import json,sys; d=json.load(sys.stdin); umrs=d.get('umrs') or d.get('checks',{}).get('umrs',''); assert umrs == 'ok', umrs" 2>/dev/null; then
                echo "OK  (umrs=ok)"
            else
                echo "FAIL  (${gw_ready})"
                rc=1
            fi
        else
            echo "FAIL"
            rc=1
        fi

        # 3. UMRS /health
        echo -n "  UMRS /health    ... "
        local umrs_health
        if umrs_health="$(curl -sf "${umrs_url}/health" 2>&1)"; then
            echo "OK  (${umrs_health})"
        else
            echo "FAIL"
            rc=1
        fi

        # 4. UMRS ZMQ port reachable
        local zmq_port="${UMRS_ZMQ_PORT:-5555}"
        echo -n "  UMRS ZMQ :${zmq_port} ... "
        if python3 -c "
import socket, sys
try:
    s = socket.create_connection(('127.0.0.1', ${zmq_port}), timeout=3)
    s.close()
    print('OK')
except Exception as e:
    print('FAIL (' + str(e) + ')')
    sys.exit(1)
" 2>&1; then
            :
        else
            rc=1
        fi

        # 5. Slurm partition reachable
        echo -n "  Slurm partition ${SLURM_PARTITION} ... "
        if sinfo -p "${SLURM_PARTITION}" -h --format="%P %a %D" 2>/dev/null | head -1; then
            :
        else
            echo "FAIL"
            rc=1
        fi

        # 6. UMRS model catalog has entries
        echo -n "  UMRS model catalog ... "
        local catalog_count
        catalog_count="$(python3 -c "
import json, pathlib, os
p = pathlib.Path(os.path.expanduser('~/.config/umrs/model_catalog.json'))
if not p.exists():
    print(0)
else:
    d = json.loads(p.read_text())
    models = d if isinstance(d, list) else d.get('models', [])
    print(len(models))
" 2>/dev/null)"
        if [[ "${catalog_count:-0}" -gt 0 ]]; then
            echo "OK  (${catalog_count} models)"
        else
            echo "FAIL  (no models in catalog)"
            rc=1
        fi

        return "${rc}"
}

run_podman_smoke() {
        local work_dir
        local script
        local sbatch_rc

        work_dir="$(mktemp -d "${HPC_TEST_WORK_DIR}/slurm-podman.XXXXXX")"
        script="${work_dir}/slurm-podman-smoke.sh"
        cat > "${script}" <<EOF
#!/bin/bash
#SBATCH --job-name=rivault-podman-smoke
#SBATCH --partition=${SLURM_PARTITION}
#SBATCH --time=00:02:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --chdir=${work_dir}
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
set -euo pipefail
{
    echo "start=\$(date -Is)"
    echo "node=\$(hostname)"
    echo "pwd=\$(pwd)"
    echo "runtime=\$(command -v podman)"
    podman --version
    podman info --format 'host={{.Host.Hostname}} arch={{.Host.Arch}}'
    echo "end=\$(date -Is)"
} > command.log 2>&1
EOF
        chmod +x "${script}"

        echo "Submitting Slurm Podman smoke job..."
        set +e
        (cd "${work_dir}" && sbatch --wait "${script}")
        sbatch_rc=$?
        set -e

        echo ""
        echo "Slurm Podman smoke output:"
        cat "${work_dir}/command.log" 2>/dev/null || true
        cat "${work_dir}"/slurm-*.out 2>/dev/null || true
        cat "${work_dir}"/slurm-*.err 2>/dev/null || true
        rm -rf "${work_dir}"
        return "${sbatch_rc}"
}

run_slurm_egress_smoke() {
        local work_dir
        local script
        local sbatch_rc

        work_dir="$(mktemp -d "${HPC_TEST_WORK_DIR}/slurm-egress.XXXXXX")"
        script="${work_dir}/slurm-egress-smoke.sh"
        cat > "${script}" <<EOF
#!/bin/bash
#SBATCH --job-name=rivault-egress-smoke
#SBATCH --partition=${SLURM_PARTITION}
#SBATCH --time=00:02:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --chdir=${work_dir}
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
set -euo pipefail
export RIVAULT_HTTP_PROXY="\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}"
export RIVAULT_HTTPS_PROXY="\${RIVAULT_HTTPS_PROXY:-\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}}"
export RIVAULT_NO_PROXY="\${RIVAULT_NO_PROXY:-localhost,127.0.0.1,::1,litellm,vllm,ll-postgres,coredns,nginx,egress-guard}"
export HTTP_PROXY="\${HTTP_PROXY:-\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}}"
export HTTPS_PROXY="\${HTTPS_PROXY:-\${RIVAULT_HTTPS_PROXY:-\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}}}"
export NO_PROXY="\${NO_PROXY:-\${RIVAULT_NO_PROXY:-localhost,127.0.0.1,::1,litellm,vllm,ll-postgres,coredns,nginx,egress-guard}}"
export http_proxy="\${http_proxy:-\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}}"
export https_proxy="\${https_proxy:-\${RIVAULT_HTTPS_PROXY:-\${RIVAULT_HTTP_PROXY:-http://egress-guard:8888}}}"
export no_proxy="\${no_proxy:-\${RIVAULT_NO_PROXY:-localhost,127.0.0.1,::1,litellm,vllm,ll-postgres,coredns,nginx,egress-guard}}"
{
    echo "start=\$(date -Is)"
    echo "node=\$(hostname)"
    echo "HTTP_PROXY=\${HTTP_PROXY}"
    echo "NO_PROXY=\${NO_PROXY}"
    case "\${HTTP_PROXY}" in
        http://egress-guard:*) ;;
        *) echo "HTTP_PROXY does not default to EgressGuard"; exit 1 ;;
    esac
    case "\${NO_PROXY}" in
        *egress-guard*) ;;
        *) echo "NO_PROXY does not include egress-guard"; exit 1 ;;
    esac
    python3 - <<'PY'
import os
import socket
import sys

host = os.environ.get("SLURM_EGRESS_PROBE_HOST", "example.com")
port = int(os.environ.get("SLURM_EGRESS_PROBE_PORT", "80"))
timeout = float(os.environ.get("SLURM_EGRESS_PROBE_TIMEOUT_SEC", "5"))
expected = os.environ.get("SLURM_DIRECT_EGRESS_EXPECTED", "blocked")

try:
    with socket.create_connection((host, port), timeout=timeout):
        connected = True
except OSError as exc:
    connected = False
    print(f"direct_egress_error={type(exc).__name__}: {exc}")

print(f"direct_egress_target={host}:{port}")
print(f"direct_egress_connected={str(connected).lower()}")

if expected == "blocked" and connected:
    sys.exit("direct public egress succeeded; expected blocked")
if expected == "open" and not connected:
    sys.exit("direct public egress blocked; expected open")
PY
    echo "end=\$(date -Is)"
} > command.log 2>&1
EOF
        chmod +x "${script}"

        echo "Submitting Slurm egress smoke job..."
        set +e
        (cd "${work_dir}" && sbatch --wait "${script}")
        sbatch_rc=$?
        set -e

        echo ""
        echo "Slurm egress smoke output:"
        cat "${work_dir}/command.log" 2>/dev/null || true
        cat "${work_dir}"/slurm-*.out 2>/dev/null || true
        cat "${work_dir}"/slurm-*.err 2>/dev/null || true
        rm -rf "${work_dir}"
        return "${sbatch_rc}"
}

run_hybrid_slurm_smoke() {
        local work_dir
        local script
        local job_id
        local node
        local srun_rc
        local waited

        work_dir="$(mktemp -d "${HPC_TEST_WORK_DIR}/hybrid-slurm.XXXXXX")"
        script="${work_dir}/hybrid-slurm-hold.sh"
        cat > "${script}" <<EOF
#!/bin/bash
#SBATCH --job-name=rivault-hybrid-smoke
#SBATCH --partition=${SLURM_PARTITION}
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --chdir=${work_dir}
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
set -euo pipefail
node="\${SLURMD_NODENAME:-\$(hostname)}"
{
    echo "job_id=\${SLURM_JOB_ID}"
    echo "node=\${node}"
    echo "host=\$(hostname)"
    echo "ready=\$(date -Is)"
} > allocation.env.tmp
mv allocation.env.tmp allocation.env
while [[ ! -f stop ]]; do
    sleep 1
done
echo "stopped=\$(date -Is)" >> allocation.env
EOF
        chmod +x "${script}"

        echo "Submitting Slurm allocation for hybrid srun smoke..."
        job_id="$(cd "${work_dir}" && sbatch --parsable "${script}")"
        echo "  Job ID: ${job_id}"

        for waited in $(seq 1 60); do
            if [[ -s "${work_dir}/allocation.env" ]]; then
                break
            fi
            if ! squeue -h -j "${job_id}" >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done

        if [[ ! -s "${work_dir}/allocation.env" ]]; then
            echo "Allocation did not become ready. Slurm output:"
            cat "${work_dir}"/slurm-*.out 2>/dev/null || true
            cat "${work_dir}"/slurm-*.err 2>/dev/null || true
            scancel "${job_id}" >/dev/null 2>&1 || true
            rm -rf "${work_dir}"
            return 1
        fi

        # shellcheck disable=SC1091
        source "${work_dir}/allocation.env"
        echo "  Node: ${node}"

        set +e
        srun \
            --jobid="${job_id}" \
            --nodes=1 \
            --ntasks=1 \
            --overlap \
            --nodelist "${node}" \
            /bin/sh -c 'actual=$(hostname); expected=$1; echo "srun_node=${actual}"; test "${actual}" = "${expected}" || test "${actual%%.*}" = "${expected%%.*}"' \
            _ "${node}" > "${work_dir}/srun.log" 2>&1
        srun_rc=$?
        set -e

        touch "${work_dir}/stop"
        for waited in $(seq 1 30); do
            if ! squeue -h -j "${job_id}" | grep -q .; then
                break
            fi
            sleep 1
        done
        scancel "${job_id}" >/dev/null 2>&1 || true

        echo ""
        echo "Hybrid Slurm smoke output:"
        cat "${work_dir}/allocation.env" 2>/dev/null || true
        cat "${work_dir}/srun.log" 2>/dev/null || true
        cat "${work_dir}"/slurm-*.out 2>/dev/null || true
        cat "${work_dir}"/slurm-*.err 2>/dev/null || true
        rm -rf "${work_dir}"
        return "${srun_rc}"
}

if [[ "$SKIP_PREREQ" != "true" ]]; then
    echo "Checking prerequisites..."

    PREREQ_FAILED=false

    check_prerequisite "Slurm (sinfo)" "command -v sinfo" || PREREQ_FAILED=true
    check_prerequisite "Slurm (sbatch)" "command -v sbatch" || PREREQ_FAILED=true
    if [[ "$RIVAULT_ORCHESTRATOR" == "slurm" ]]; then
        check_prerequisite "Singularity" "command -v singularity || command -v apptainer" || PREREQ_FAILED=true
    else
        check_prerequisite "Podman" "command -v podman" || PREREQ_FAILED=true
    fi
    if [[ "$SLURM_EGRESS_SMOKE" == "true" ]]; then
        check_prerequisite "Python 3" "command -v python3" || PREREQ_FAILED=true
    fi
    if [[ "$PODMAN_SMOKE" != "true" && "$SLURM_EGRESS_SMOKE" != "true" && "$HYBRID_SLURM_SMOKE" != "true" && "$GATEWAY_SMOKE" != "true" ]]; then
        check_prerequisite "Python 3" "command -v python3" || PREREQ_FAILED=true
        check_prerequisite "pytest" "python3 -c 'import pytest'" || PREREQ_FAILED=true
        check_prerequisite "Model path exists" "test -d '$MODEL_PATH'" || PREREQ_FAILED=true
        if [[ "$RIVAULT_ORCHESTRATOR" == "slurm" ]]; then
            check_prerequisite "Singularity image exists" "test -f '$SINGULARITY_IMAGE'" || PREREQ_FAILED=true
        fi
    fi
    check_prerequisite "Partition available" "sinfo -p '$SLURM_PARTITION' -h" || PREREQ_FAILED=true

    echo ""

    if [[ "$PREREQ_FAILED" == "true" ]]; then
        echo -e "${RED}Prerequisite checks failed. Please fix the issues above.${NC}"
        echo ""
        echo "Common fixes:"
        echo "  - Load Slurm module: module load slurm"
        echo "  - Load Singularity: module load singularity"
        echo "  - Install pytest: pip install pytest httpx"
        echo "  - Set model path: export SLURM_TEST_MODEL_PATH=/path/to/model"
        echo "  - Set image path: export SLURM_TEST_IMAGE=/path/to/image.sif"
        exit 1
    fi

    echo -e "${GREEN}All prerequisites satisfied.${NC}"
    echo ""
fi

# =============================================================================
# Environment Setup
# =============================================================================

export SLURM_TEST_MODEL_PATH="$MODEL_PATH"
export SLURM_TEST_IMAGE="$SINGULARITY_IMAGE"
export SLURM_TEST_PARTITION="$SLURM_PARTITION"
export RIVAULT_ORCHESTRATOR

# =============================================================================
# Run Tests
# =============================================================================

if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY RUN - Would execute:"
    echo ""
    echo "  python3 -m pytest tests/ -v --tb=short -m hpc"
    echo ""
    echo "Environment:"
    echo "  SLURM_TEST_MODEL_PATH=$SLURM_TEST_MODEL_PATH"
    echo "  RIVAULT_ORCHESTRATOR=$RIVAULT_ORCHESTRATOR"
    if [[ "$RIVAULT_ORCHESTRATOR" == "slurm" ]]; then
        echo "  SLURM_TEST_IMAGE=$SLURM_TEST_IMAGE"
    fi
    echo "  SLURM_TEST_PARTITION=$SLURM_TEST_PARTITION"
    if [[ -n "${R4_GATEWAY_URL:-}" ]]; then
        echo "  R4_GATEWAY_URL=${R4_GATEWAY_URL}"
    fi
    if [[ -n "${R4_PROJECT_ID:-}" ]]; then
        echo "  R4_PROJECT_ID=${R4_PROJECT_ID}"
    fi
    if [[ -n "${SLURM_TEST_MODEL_NAME:-}" ]]; then
        echo "  SLURM_TEST_MODEL_NAME=${SLURM_TEST_MODEL_NAME}"
    fi
    exit 0
fi

if [[ "$GATEWAY_SMOKE" == "true" ]]; then
    run_gateway_smoke
    TEST_EXIT_CODE=$?
    echo ""
    echo "============================================================"
    if [[ $TEST_EXIT_CODE -eq 0 ]]; then
        echo -e "${GREEN}✓ Gateway→UMRS smoke passed!${NC}"
    else
        echo -e "${RED}✗ Gateway→UMRS smoke failed (exit code: $TEST_EXIT_CODE)${NC}"
    fi
    echo "============================================================"
    exit $TEST_EXIT_CODE
fi

if [[ "$PODMAN_SMOKE" == "true" ]]; then
    if [[ "$RIVAULT_ORCHESTRATOR" == "slurm" ]]; then
        echo -e "${YELLOW}Podman smoke is intended for slurm-podman validation; current orchestrator is slurm.${NC}"
    fi
    mkdir -p "${HPC_TEST_WORK_DIR}"
    run_podman_smoke
    TEST_EXIT_CODE=$?
    echo ""
    echo "============================================================"
    if [[ $TEST_EXIT_CODE -eq 0 ]]; then
        echo -e "${GREEN}✓ Slurm Podman smoke passed!${NC}"
    else
        echo -e "${RED}✗ Slurm Podman smoke failed (exit code: $TEST_EXIT_CODE)${NC}"
    fi
    echo "============================================================"
    exit $TEST_EXIT_CODE
fi

if [[ "$SLURM_EGRESS_SMOKE" == "true" ]]; then
    mkdir -p "${HPC_TEST_WORK_DIR}"
    run_slurm_egress_smoke
    TEST_EXIT_CODE=$?
    echo ""
    echo "============================================================"
    if [[ $TEST_EXIT_CODE -eq 0 ]]; then
        echo -e "${GREEN}✓ Slurm egress smoke passed!${NC}"
    else
        echo -e "${RED}✗ Slurm egress smoke failed (exit code: $TEST_EXIT_CODE)${NC}"
    fi
    echo "============================================================"
    exit $TEST_EXIT_CODE
fi

if [[ "$HYBRID_SLURM_SMOKE" == "true" ]]; then
    mkdir -p "${HPC_TEST_WORK_DIR}"
    run_hybrid_slurm_smoke
    TEST_EXIT_CODE=$?
    echo ""
    echo "============================================================"
    if [[ $TEST_EXIT_CODE -eq 0 ]]; then
        echo -e "${GREEN}✓ Hybrid Slurm smoke passed!${NC}"
    else
        echo -e "${RED}✗ Hybrid Slurm smoke failed (exit code: $TEST_EXIT_CODE)${NC}"
    fi
    echo "============================================================"
    exit $TEST_EXIT_CODE
fi

echo "Running HPC E2E tests..."
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Run the full HPC-marked suite
python3 -m pytest tests/ -v --tb=short -m hpc

TEST_EXIT_CODE=$?

echo ""
echo "============================================================"
if [[ $TEST_EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}✓ All HPC E2E tests passed!${NC}"
else
    echo -e "${RED}✗ Some tests failed (exit code: $TEST_EXIT_CODE)${NC}"
fi
echo "============================================================"

exit $TEST_EXIT_CODE
