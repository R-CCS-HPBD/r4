#!/bin/bash
# =============================================================================
# Kind Cluster Setup for K8s E2E Testing
# =============================================================================
#
# Creates a Kind cluster with local registry for testing UMRS + Mini-RiVault
# on Kubernetes locally.
#
# Prerequisites:
# - Docker installed and running
# - Kind installed (https://kind.sigs.k8s.io/)
# - kubectl installed
#
# Usage:
#   ./setup_kind_cluster.sh         # Create cluster
#   ./setup_kind_cluster.sh --clean # Delete cluster
#   ./setup_kind_cluster.sh --reset # Delete and recreate
#
# =============================================================================

set -e

# Configuration
CLUSTER_NAME="${KIND_CLUSTER_NAME:-rivault-e2e}"
REGISTRY_NAME="${KIND_REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${KIND_REGISTRY_PORT:-5001}"
K8S_VERSION="${KIND_K8S_VERSION:-v1.28.0}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../demos" && pwd)"

# Source runtime detection library
if [[ -f "$DEMOS_DIR/lib/runtime.sh" ]]; then
    source "$DEMOS_DIR/lib/runtime.sh"
    detect_container_runtime
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

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# =============================================================================
# Prerequisite Checks
# =============================================================================

check_prerequisites() {
    log_info "Checking prerequisites..."

    local failed=false

    # Container runtime
    if [[ -n "$CONTAINER_RUNTIME" ]] && $CONTAINER_RUNTIME info &> /dev/null; then
        echo -e "  ${GREEN}✓${NC} $CONTAINER_RUNTIME"
    else
        echo -e "  ${RED}✗${NC} Container runtime (not running)"
        failed=true
    fi

    # Kind
    if command -v kind &> /dev/null; then
        echo -e "  ${GREEN}✓${NC} Kind $(kind version | head -1)"
    else
        echo -e "  ${RED}✗${NC} Kind not installed"
        echo "    Install: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
        failed=true
    fi

    # kubectl
    if command -v kubectl &> /dev/null; then
        echo -e "  ${GREEN}✓${NC} kubectl"
    else
        echo -e "  ${RED}✗${NC} kubectl not installed"
        failed=true
    fi

    if [[ "$failed" == "true" ]]; then
        log_error "Prerequisites not met"
        exit 1
    fi

    log_success "All prerequisites met"
}

# =============================================================================
# Registry Management
# =============================================================================

create_registry() {
    log_info "Setting up local container registry..."

    # Check if registry already exists
    if $CONTAINER_RUNTIME inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null | grep -q true; then
        log_info "Registry ${REGISTRY_NAME} already running"
        return 0
    fi

    # Create registry container
    $CONTAINER_RUNTIME run -d --restart=always \
        -p "127.0.0.1:${REGISTRY_PORT}:5000" \
        --name "${REGISTRY_NAME}" \
        registry:2

    log_success "Local registry created at localhost:${REGISTRY_PORT}"
}

delete_registry() {
    log_info "Deleting local registry..."
    $CONTAINER_RUNTIME rm -f "${REGISTRY_NAME}" 2>/dev/null || true
    log_success "Registry deleted"
}

# =============================================================================
# Cluster Management
# =============================================================================

create_cluster() {
    log_info "Creating Kind cluster: ${CLUSTER_NAME}..."

    # Check if cluster already exists
    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        log_warn "Cluster ${CLUSTER_NAME} already exists"
        return 0
    fi

    # Create cluster config
    cat <<EOF | kind create cluster --name "${CLUSTER_NAME}" --image kindest/node:${K8S_VERSION} --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:${REGISTRY_PORT}"]
    endpoint = ["http://${REGISTRY_NAME}:5000"]
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  # UMRS API
  - containerPort: 30001
    hostPort: 8001
    protocol: TCP
  # Gateway
  - containerPort: 30080
    hostPort: 8080
    protocol: TCP
  # LiteLLM
  - containerPort: 30434
    hostPort: 11434
    protocol: TCP
  # PostgreSQL (for debugging)
  - containerPort: 30432
    hostPort: 5432
    protocol: TCP
  # MinIO (for debugging)
  - containerPort: 30900
    hostPort: 9000
    protocol: TCP
- role: worker
  labels:
    node-role.kubernetes.io/worker: ""
EOF

    log_success "Kind cluster created"

    # Connect registry to cluster network
    log_info "Connecting registry to cluster network..."
    $CONTAINER_RUNTIME network connect "kind" "${REGISTRY_NAME}" 2>/dev/null || true

    # Configure registry in cluster
    log_info "Configuring registry in cluster..."
    cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REGISTRY_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

    log_success "Cluster setup complete"
}

delete_cluster() {
    log_info "Deleting Kind cluster: ${CLUSTER_NAME}..."
    kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
    log_success "Cluster deleted"
}

# =============================================================================
# Namespace and RBAC Setup
# =============================================================================

setup_namespaces() {
    log_info "Setting up namespaces..."

    # Create UMRS namespace
    kubectl create namespace umrs-system --dry-run=client -o yaml | kubectl apply -f -

    # Create RiVault namespace
    kubectl create namespace rivault-system --dry-run=client -o yaml | kubectl apply -f -

    log_success "Namespaces created"
}

# =============================================================================
# Cluster Status
# =============================================================================

show_status() {
    echo ""
    echo "============================================================"
    echo "Kind Cluster Status"
    echo "============================================================"
    echo ""

    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        echo -e "Cluster:  ${GREEN}${CLUSTER_NAME}${NC} (running)"
    else
        echo -e "Cluster:  ${RED}Not created${NC}"
        return
    fi

    if $CONTAINER_RUNTIME inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null | grep -q true; then
        echo -e "Registry: ${GREEN}localhost:${REGISTRY_PORT}${NC} (running)"
    else
        echo -e "Registry: ${RED}Not running${NC}"
    fi

    echo ""
    echo "Endpoints (after deployment):"
    echo "  UMRS API:    http://localhost:8001"
    echo "  Gateway:     http://localhost:8080"
    echo "  LiteLLM:     http://localhost:11434"
    echo "  PostgreSQL:  localhost:5432"
    echo "  MinIO:       http://localhost:9000"
    echo ""
    echo "kubectl context: kind-${CLUSTER_NAME}"
    echo ""
    echo "Next steps:"
    echo "  1. Build and push images:"
    echo "     $CONTAINER_RUNTIME build -t localhost:${REGISTRY_PORT}/umrs:latest umrs/docker"
    echo "     $CONTAINER_RUNTIME push localhost:${REGISTRY_PORT}/umrs:latest"
    echo ""
    echo "  2. Deploy UMRS stack:"
    echo "     kubectl apply -k umrs/docker/k8s"
    echo ""
    echo "  3. Run E2E tests:"
    echo "     python tests/test_k8s_e2e.py"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

main() {
    echo "============================================================"
    echo "Kind Cluster Setup for R4 Platform"
    echo "============================================================"
    echo ""

    case "${1:-}" in
        --clean)
            delete_cluster
            delete_registry
            ;;
        --reset)
            delete_cluster
            delete_registry
            check_prerequisites
            create_registry
            create_cluster
            setup_namespaces
            show_status
            ;;
        --status)
            show_status
            ;;
        *)
            check_prerequisites
            create_registry
            create_cluster
            setup_namespaces
            show_status
            ;;
    esac
}

main "$@"
