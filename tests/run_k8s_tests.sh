#!/usr/bin/env bash
#
# T-003: Kubernetes End-to-End Test Runner
#
# Sets up a local Kind cluster and runs K8s E2E tests for UMRS + Mini-RiVault.
#
# Usage:
#   ./tests/run_k8s_tests.sh              # Full setup and test
#   ./tests/run_k8s_tests.sh --setup-only # Only create cluster
#   ./tests/run_k8s_tests.sh --test-only  # Run tests (cluster must exist)
#   ./tests/run_k8s_tests.sh --cleanup    # Delete cluster
#   ./tests/run_k8s_tests.sh --help       # Show help
#
# Environment Variables:
#   CLUSTER_NAME     - Kind cluster name (default: umrs-k8s)
#   REGISTRY_PORT    - Local registry port (default: 5001)
#   K8S_NAMESPACE    - Kubernetes namespace (default: umrs-system)
#   SKIP_IMAGE_BUILD - Skip Docker image builds (default: false)
#   USE_EXISTING_CLUSTER - Use current kubectl context instead of Kind (default: false)
#   PYTHON_BIN       - Python executable for tests (default: python3)
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEMOS_DIR="${PROJECT_ROOT}/demos"
CLUSTER_NAME="${CLUSTER_NAME:-umrs-k8s}"
REGISTRY_NAME="${REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5001}"
K8S_NAMESPACE="${K8S_NAMESPACE:-umrs-system}"
KIND_CONFIG="${PROJECT_ROOT}/umrs/docker/kind-config.yaml"
K8S_MANIFESTS="${PROJECT_ROOT}/umrs/docker/k8s"
SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-false}"
USE_EXISTING_CLUSTER="${USE_EXISTING_CLUSTER:-false}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Source runtime detection library
if [[ -f "$DEMOS_DIR/lib/runtime.sh" ]]; then
    source "$DEMOS_DIR/lib/runtime.sh"
    detect_container_runtime
else
    echo "ERROR: Runtime library not found at $DEMOS_DIR/lib/runtime.sh"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# ============================================================================
# Prerequisites Check
# ============================================================================

check_prerequisites() {
    info "Checking prerequisites..."

    local missing=()

    # Check container runtime
    if [[ -z "$CONTAINER_RUNTIME" ]]; then
        missing+=("docker or podman")
    elif ! $CONTAINER_RUNTIME info &>/dev/null; then
        error "Container runtime is not running"
        return 1
    else
        success "$CONTAINER_RUNTIME $($CONTAINER_RUNTIME version --format '{{.Server.Version}}' 2>/dev/null || $CONTAINER_RUNTIME --version)"
    fi

    # Check Kind only when this runner owns cluster creation.
    if [[ "${USE_EXISTING_CLUSTER}" == "true" ]]; then
        if kubectl cluster-info &>/dev/null; then
            success "existing Kubernetes cluster reachable"
        else
            missing+=("reachable Kubernetes cluster")
        fi
    elif ! command -v kind &>/dev/null; then
        missing+=("kind")
    else
        success "$(kind version)"
    fi

    # Check kubectl
    if ! command -v kubectl &>/dev/null; then
        missing+=("kubectl")
    else
        success "kubectl $(kubectl version --client -o json 2>/dev/null | jq -r '.clientVersion.gitVersion')"
    fi

    # Check Python (for running tests)
    if ! command -v "${PYTHON_BIN}" &>/dev/null; then
        missing+=("${PYTHON_BIN}")
    else
        success "Python $(${PYTHON_BIN} --version | cut -d' ' -f2)"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing required tools: ${missing[*]}"
        echo ""
        echo "Installation hints:"
        for tool in "${missing[@]}"; do
            case "$tool" in
                docker)
                    echo "  docker or podman: https://docs.docker.com/get-docker/ or https://podman.io/"
                    ;;
                kind)
                    echo "  kind: GO111MODULE=on go install sigs.k8s.io/kind@latest"
                    echo "    or: curl -Lo ./kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/"
                    ;;
                kubectl)
                    echo "  kubectl: https://kubernetes.io/docs/tasks/tools/"
                    ;;
                python|python3)
                    echo "  ${tool}: Should be installed with your system"
                    ;;
            esac
        done
        return 1
    fi

    success "All prerequisites met"
    return 0
}

# ============================================================================
# Registry Setup
# ============================================================================

setup_registry() {
    if [[ "${USE_EXISTING_CLUSTER}" == "true" && "${SKIP_IMAGE_BUILD}" == "true" ]]; then
        warn "Skipping local registry for existing-cluster validation (SKIP_IMAGE_BUILD=true)"
        return 0
    fi

    info "Setting up local container registry..."

    if $CONTAINER_RUNTIME inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" &>/dev/null; then
        success "Registry ${REGISTRY_NAME} already running"
        return 0
    fi

    info "Starting registry ${REGISTRY_NAME} on port ${REGISTRY_PORT}..."
    $CONTAINER_RUNTIME run -d \
        --restart=always \
        -p "127.0.0.1:${REGISTRY_PORT}:5000" \
        --name "${REGISTRY_NAME}" \
        registry:2

    success "Registry started at localhost:${REGISTRY_PORT}"
}

# ============================================================================
# Kind Cluster Setup
# ============================================================================

setup_cluster() {
    if [[ "${USE_EXISTING_CLUSTER}" == "true" ]]; then
        info "Using existing Kubernetes cluster from current kubectl context..."
        kubectl cluster-info
        kubectl get nodes -o wide
        return 0
    fi

    info "Setting up Kind cluster..."

    if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
        success "Cluster ${CLUSTER_NAME} already exists"
        kubectl cluster-info --context "kind-${CLUSTER_NAME}" || true
        return 0
    fi

    info "Creating Kind cluster ${CLUSTER_NAME}..."

    # Create cluster with config
    if [[ -f "${KIND_CONFIG}" ]]; then
        kind create cluster --name "${CLUSTER_NAME}" --config "${KIND_CONFIG}"
    else
        warn "Kind config not found at ${KIND_CONFIG}, using defaults"
        kind create cluster --name "${CLUSTER_NAME}"
    fi

    # Connect registry to Kind network
    if $CONTAINER_RUNTIME inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" &>/dev/null; then
        info "Connecting registry to Kind network..."
        $CONTAINER_RUNTIME network connect "kind" "${REGISTRY_NAME}" 2>/dev/null || true
    fi

    # Configure registry in cluster
    info "Configuring registry in cluster..."
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REGISTRY_PORT}"
    help: "Push images with '$CONTAINER_RUNTIME push localhost:${REGISTRY_PORT}/IMAGE_NAME'"
EOF

    success "Cluster ${CLUSTER_NAME} created"
    kubectl cluster-info --context "kind-${CLUSTER_NAME}"
}

# ============================================================================
# Image Building
# ============================================================================

build_and_push_images() {
    if [[ "${SKIP_IMAGE_BUILD}" == "true" ]]; then
        warn "Skipping image build (SKIP_IMAGE_BUILD=true)"
        return 0
    fi

    info "Building and pushing container images..."

    local registry="localhost:${REGISTRY_PORT}"

    # Build UMRS server image
    info "Building UMRS server image..."
    $CONTAINER_RUNTIME build \
        -t "${registry}/umrs:latest" \
        -f "${PROJECT_ROOT}/umrs/docker/Dockerfile" \
        "${PROJECT_ROOT}/umrs"

    $CONTAINER_RUNTIME push "${registry}/umrs:latest"
    success "UMRS image pushed to ${registry}/umrs:latest"

    # Load into Kind (alternative to push for local registries)
    info "Loading images into Kind cluster..."
    kind load docker-image "${registry}/umrs:latest" --name "${CLUSTER_NAME}" || true

    success "All images built and loaded"
}

# ============================================================================
# Deploy Stack
# ============================================================================

deploy_stack() {
    info "Deploying UMRS stack to namespace ${K8S_NAMESPACE}..."

    # Create namespace if not exists
    kubectl create namespace "${K8S_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

    # Apply manifests
    if [[ -d "${K8S_MANIFESTS}" ]]; then
        kubectl apply -k "${K8S_MANIFESTS}"
    else
        error "K8s manifests not found at ${K8S_MANIFESTS}"
        return 1
    fi

    success "Stack deployed"

    # Wait for pods
    info "Waiting for pods to be ready..."
    local deployments=("postgres" "minio")

    for deployment in "${deployments[@]}"; do
        info "  Waiting for ${deployment}..."
        kubectl rollout status "deployment/${deployment}" -n "${K8S_NAMESPACE}" --timeout=120s || {
            warn "Deployment ${deployment} not ready, continuing..."
        }
    done

    # Show pod status
    info "Current pod status:"
    kubectl get pods -n "${K8S_NAMESPACE}" -o wide
}

# ============================================================================
# Run Tests
# ============================================================================

run_tests() {
    info "Running K8s E2E tests..."

    cd "${PROJECT_ROOT}"

    # Set up Python path
    export PYTHONPATH="${PROJECT_ROOT}/umrs/src:${PYTHONPATH:-}"
    export K8S_NAMESPACE="${K8S_NAMESPACE}"
    export REGISTRY_HOST="localhost:${REGISTRY_PORT}"

    # Run the Python test
    if [[ -f "${PROJECT_ROOT}/tests/test_k8s_e2e.py" ]]; then
        "${PYTHON_BIN}" "${PROJECT_ROOT}/tests/test_k8s_e2e.py" --no-cleanup "$@"
    else
        error "Test file not found: ${PROJECT_ROOT}/tests/test_k8s_e2e.py"
        return 1
    fi
}

# ============================================================================
# Cleanup
# ============================================================================

cleanup_cluster() {
    info "Cleaning up Kind cluster..."

    if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
        info "Deleting cluster ${CLUSTER_NAME}..."
        kind delete cluster --name "${CLUSTER_NAME}"
        success "Cluster deleted"
    else
        warn "Cluster ${CLUSTER_NAME} not found"
    fi

    # Optionally remove registry
    if $CONTAINER_RUNTIME inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" &>/dev/null; then
        read -p "Delete registry ${REGISTRY_NAME}? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            $CONTAINER_RUNTIME rm -f "${REGISTRY_NAME}"
            success "Registry deleted"
        fi
    fi
}

# ============================================================================
# Help
# ============================================================================

show_help() {
    cat <<EOF
T-003: Kubernetes End-to-End Test Runner

Usage: $(basename "$0") [OPTIONS]

Options:
    --setup-only    Only set up cluster and registry (no tests)
    --test-only     Only run tests (assumes cluster exists)
    --cleanup       Delete Kind cluster
    --check         Check prerequisites only
    -h, --help      Show this help

Environment Variables:
    CLUSTER_NAME      Kind cluster name (default: umrs-k8s)
    REGISTRY_PORT     Local registry port (default: 5001)
    K8S_NAMESPACE     Kubernetes namespace (default: umrs-system)
    SKIP_IMAGE_BUILD  Skip container image builds (default: false)
    USE_EXISTING_CLUSTER Use current kubectl context instead of Kind (default: false)
    PYTHON_BIN        Python executable for tests (default: python3)

Examples:
    # Full setup and test
    ./tests/run_k8s_tests.sh

    # Just set up the cluster
    ./tests/run_k8s_tests.sh --setup-only

    # Run tests on existing cluster
    USE_EXISTING_CLUSTER=true SKIP_IMAGE_BUILD=true ./tests/run_k8s_tests.sh --test-only

    # Clean up everything
    ./tests/run_k8s_tests.sh --cleanup

    # Check prerequisites
    ./tests/run_k8s_tests.sh --check
EOF
}

# ============================================================================
# Main
# ============================================================================

main() {
    local mode="full"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --setup-only)
                mode="setup"
                shift
                ;;
            --test-only)
                mode="test"
                shift
                ;;
            --cleanup)
                mode="cleanup"
                shift
                ;;
            --check)
                mode="check"
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    echo "============================================================"
    echo "T-003: Kubernetes End-to-End Test"
    echo "============================================================"
    echo ""

    case "$mode" in
        check)
            check_prerequisites
            ;;
        setup)
            check_prerequisites
            setup_registry
            setup_cluster
            build_and_push_images
            deploy_stack
            ;;
        test)
            check_prerequisites
            run_tests
            ;;
        cleanup)
            cleanup_cluster
            ;;
        full)
            check_prerequisites
            setup_registry
            setup_cluster
            build_and_push_images
            deploy_stack
            run_tests
            ;;
    esac
}

main "$@"
