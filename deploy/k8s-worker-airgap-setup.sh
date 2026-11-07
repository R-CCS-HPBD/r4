#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Air-gapped Worker Node Image Import
# ─────────────────────────────────────────────────────────────────────
# Run this script FROM the control-plane node (<control-plane-node>).
# It requires sudo access on the worker node (<worker-node>).
#
# Prerequisites (already done by the smoke-test session):
#   - Image tars exported on <control-plane-node> and SCP'd to <worker-node>:/tmp/
#
# Usage:
#   ssh <control-plane-node>
#   bash /path/to/k8s-worker-airgap-setup.sh <worker-node>
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

WORKER="${1:?usage: $0 <worker-node>}"
CTR="ctr -a /run/k3s/containerd/containerd.sock -n k8s.io"   # for the remote ssh commands
CTR_LOCAL=(ctr -a /run/k3s/containerd/containerd.sock -n k8s.io)
IMAGES=(
    "docker.io/rancher/mirrored-pause:3.6"
    "nvcr.io/nvidia/k8s-device-plugin:v0.19.1"
)
TARS=(
    "/tmp/pause.tar"
    "/tmp/nvidia-dp.tar"
)

echo "=== Air-gapped worker image import ==="
echo "Worker: ${WORKER}"
echo ""

# Step 1: Export images on control plane (if not already done)
for i in "${!IMAGES[@]}"; do
    tar="${TARS[$i]}"
    img="${IMAGES[$i]}"
    if [[ ! -f "${tar}" ]]; then
        echo "[control-plane] Exporting ${img} → ${tar}"
        sudo "${CTR_LOCAL[@]}" images export "${tar}" "${img}"
    else
        echo "[control-plane] ${tar} already exists, skipping export"
    fi
done

# Step 2: SCP to worker
echo ""
echo "[control-plane → ${WORKER}] Transferring image tars..."
scp "${TARS[@]}" "${WORKER}:/tmp/"

# Step 3: Import on worker (requires sudo password)
echo ""
echo "[${WORKER}] Importing images (sudo required)..."
for tar in "${TARS[@]}"; do
    echo "  Importing ${tar}..."
    ssh -t "${WORKER}" "sudo ${CTR} images import ${tar}"
done

# Step 4: Verify
echo ""
echo "[${WORKER}] Verifying imported images..."
ssh -t "${WORKER}" "sudo ${CTR} images ls -q | grep -E 'pause|nvidia'"

echo ""
echo "=== Done. The device plugin pod on ${WORKER} should start within ~30s. ==="
echo "Verify with: KUBECONFIG=${KUBECONFIG:-~/.kube/config} kubectl get pods -n kube-system -o wide | grep nvidia"
