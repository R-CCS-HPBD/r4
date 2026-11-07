#!/bin/bash
# =============================================================================
# Kind Cluster Setup with GPU Support (U-604, T-003)
# =============================================================================
# Creates a Kind cluster configured for testing UMRS Kubernetes backend.
#
# Features:
# - Local container registry
# - Ingress controller support
# - Optional NVIDIA operator for GPU nodes
# - Port forwarding for testing
#
# Usage:
#   ./setup_kind_gpu_cluster.sh           # Basic setup
#   ./setup_kind_gpu_cluster.sh --gpu     # With NVIDIA operator
#   ./setup_kind_gpu_cluster.sh --delete  # Delete cluster
# =============================================================================

set -e

# Configuration
CLUSTER_NAME="${KIND_CLUSTER_NAME:-rivault-test}"
REGISTRY_NAME="${KIND_REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${KIND_REGISTRY_PORT:-5001}"
INSTALL_GPU=false
DELETE_CLUSTER=false

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
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --gpu)
            INSTALL_GPU=true
            shift
            ;;
        --delete)
            DELETE_CLUSTER=true
            shift
            ;;
        --name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--gpu] [--delete] [--name CLUSTER_NAME]"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check dependencies
check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is required but not installed"
        exit 1
    fi
}

check_command kind
check_command kubectl

# Ensure container runtime is available
if [[ -z "$CONTAINER_RUNTIME" ]]; then
    log_error "No container runtime found (docker or podman)"
    exit 1
fi

# Delete cluster if requested
if $DELETE_CLUSTER; then
    log_info "Deleting Kind cluster: $CLUSTER_NAME"
    kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true

    # Stop registry
    $CONTAINER_RUNTIME stop "$REGISTRY_NAME" 2>/dev/null || true
    $CONTAINER_RUNTIME rm "$REGISTRY_NAME" 2>/dev/null || true

    log_info "Cluster deleted"
    exit 0
fi

# Check if cluster already exists
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log_warn "Cluster $CLUSTER_NAME already exists"
    log_info "Use --delete to remove it first"

    # Switch context
    kubectl cluster-info --context "kind-${CLUSTER_NAME}" &>/dev/null || true
    exit 0
fi

log_info "============================================="
log_info "Setting up Kind cluster: $CLUSTER_NAME"
log_info "============================================="

# Start local registry if not running
if ! $CONTAINER_RUNTIME ps --filter "name=${REGISTRY_NAME}" --format '{{.Names}}' | grep -q "^${REGISTRY_NAME}$"; then
    log_info "Starting local registry on port $REGISTRY_PORT"
    $CONTAINER_RUNTIME run -d \
        --restart=always \
        --name "${REGISTRY_NAME}" \
        -p "127.0.0.1:${REGISTRY_PORT}:5000" \
        registry:2
fi

# Create Kind config
KIND_CONFIG=$(mktemp)
cat > "$KIND_CONFIG" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: ${CLUSTER_NAME}
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      [plugins."io.containerd.grpc.v1.cri".registry.mirrors]
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
      - containerPort: 80
        hostPort: 8080
        protocol: TCP
      - containerPort: 443
        hostPort: 8443
        protocol: TCP
      - containerPort: 30000
        hostPort: 30000
        protocol: TCP
      - containerPort: 30001
        hostPort: 30001
        protocol: TCP
  - role: worker
EOF

# Add worker with GPU label if requested
if $INSTALL_GPU; then
    cat >> "$KIND_CONFIG" <<EOF
  - role: worker
    labels:
      nvidia.com/gpu.present: "true"
EOF
fi

log_info "Creating Kind cluster..."
kind create cluster --config "$KIND_CONFIG"
rm -f "$KIND_CONFIG"

# Connect registry to Kind network
log_info "Connecting registry to Kind network..."
$CONTAINER_RUNTIME network connect "kind" "${REGISTRY_NAME}" 2>/dev/null || true

# Create ConfigMap for registry
log_info "Configuring registry in cluster..."
kubectl apply -f - <<EOF
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

# Wait for cluster to be ready
log_info "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s

# Install NGINX Ingress Controller
log_info "Installing NGINX Ingress Controller..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

# Wait for ingress controller
log_info "Waiting for ingress controller..."
kubectl wait --namespace ingress-nginx \
    --for=condition=ready pod \
    --selector=app.kubernetes.io/component=controller \
    --timeout=120s || log_warn "Ingress controller may not be ready"

# Install NVIDIA operator if requested
if $INSTALL_GPU; then
    log_info "Installing NVIDIA GPU Operator..."

    # Add NVIDIA Helm repo
    helm repo add nvidia https://helm.ngc.nvidia.com/nvidia 2>/dev/null || true
    helm repo update

    # Install operator (in test mode - no actual GPU required)
    helm install --wait gpu-operator \
        nvidia/gpu-operator \
        --namespace gpu-operator \
        --create-namespace \
        --set operator.defaultRuntime=containerd \
        --set driver.enabled=false \
        --set toolkit.enabled=false \
        --set devicePlugin.enabled=false \
        --set migManager.enabled=false \
        --set mig.strategy=none \
        --set dcgmExporter.enabled=false \
        2>/dev/null || log_warn "GPU Operator installation failed (may need real GPU)"
fi

# Create test namespaces
log_info "Creating test namespaces..."
kubectl create namespace umrs-system 2>/dev/null || true
kubectl create namespace rivault-system 2>/dev/null || true

# Show cluster info
log_info "============================================="
log_info "Cluster ready!"
log_info "============================================="
echo ""
echo "Cluster: kind-${CLUSTER_NAME}"
echo "Registry: localhost:${REGISTRY_PORT}"
echo ""
echo "Next steps:"
echo "  1. Build and push images: docker build -t localhost:${REGISTRY_PORT}/gateway ."
echo "  2. Apply manifests: kubectl apply -k umrs/docker/k8s/"
echo "  3. Run tests: pytest tests/test_k8s_e2e.py -v"
echo ""
echo "Port mappings:"
echo "  - HTTP Ingress: http://localhost:8080"
echo "  - HTTPS Ingress: https://localhost:8443"
echo "  - NodePort 30000: localhost:30000"
echo "  - NodePort 30001: localhost:30001"
