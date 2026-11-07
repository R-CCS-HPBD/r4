# Kubernetes Deployment Guide

Complete guide for deploying the R4 Platform on Kubernetes.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start with Kind](#quick-start-with-kind)
4. [Production Deployment](#production-deployment)
5. [Helm Chart Deployment](#helm-chart-deployment)
6. [Kustomize Deployment](#kustomize-deployment)
7. [Configuration](#configuration)
8. [GPU Support](#gpu-support)
9. [Networking & Ingress](#networking--ingress)
10. [Monitoring](#monitoring)
11. [Troubleshooting](#troubleshooting)

---

## Overview

Kubernetes deployment is recommended for:
- Production cloud environments
- Multi-node GPU clusters
- Auto-scaling workloads
- Enterprise deployments

### Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                               │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     rivault-system namespace                       │  │
│  │                                                                    │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────────┐  │  │
│  │  │ rivault-gateway │  │      umrs       │  │   mini-rivault    │  │  │
│  │  │   Deployment    │  │   Deployment    │  │   Job (on-demand) │  │  │
│  │  │   replicas: 2   │  │   replicas: 1   │  │  ┌─────┐ ┌─────┐  │  │  │
│  │  │                 │  │                 │  │  │vLLM │ │LiteL│  │  │  │
│  │  │     :8080       │  │     :8000       │  │  │:8000│ │:1143│  │  │  │
│  │  └────────┬────────┘  └────────┬────────┘  │  └─────┘ └─────┘  │  │  │
│  │           │                    │           └───────────────────┘  │  │
│  │           └─────────┬──────────┘                                  │  │
│  │                     │ ClusterIP Services                          │  │
│  │  ┌──────────────────┴──────────────────────────────────────────┐  │  │
│  │  │  rivault-gateway:8080   umrs:8000   mini-rivault:11434      │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  │                                                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │                     Data Layer                               │  │  │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌───────────────────┐    │  │  │
│  │  │  │ PostgreSQL  │  │   MinIO     │  │  PVC: models      │    │  │  │
│  │  │  │ StatefulSet │  │ StatefulSet │  │  (ReadOnlyMany)   │    │  │  │
│  │  │  └─────────────┘  └─────────────┘  └───────────────────┘    │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌──────────────────────┐                                               │
│  │  Ingress Controller  │ ← External traffic                            │
│  └──────────────────────┘                                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### Deployment Methods

| Method | Use Case | Complexity |
|--------|----------|------------|
| **Kind + Kustomize** | Local testing, CI/CD | Simple |
| **Kustomize** | Direct manifest deployment | Medium |
| **Helm** | Production, customization | Advanced |

---

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Nodes** | 1 | 3+ |
| **CPU/Node** | 4 cores | 8+ cores |
| **RAM/Node** | 16GB | 32GB+ |
| **GPU/Node** | - | NVIDIA GPU |

### Required Tools

```bash
# kubectl (1.25+)
kubectl version --client

# Helm (3.10+)
helm version

# Kind (for local testing)
kind version

# Docker (for building images)
docker version
```

### Install Prerequisites

```bash
# kubectl (Linux)
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/

# Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x ./kind && sudo mv ./kind /usr/local/bin/

# Verify
kubectl version --client
helm version
kind version
```

---

## Quick Start with Kind

Use Kind (Kubernetes in Docker) for local development and testing.

### Step 1: Create Kind Cluster

```bash
cd /path/to/r4

# Use the provided setup script
./tests/setup_kind_cluster.sh

# Or create manually
kind create cluster --name rivault-e2e
```

### Step 2: Build and Load Images

```bash
# Build Gateway image
docker build -t rivault-gateway:latest ./gateway

# Load into Kind cluster
kind load docker-image rivault-gateway:latest --name rivault-e2e
```

### Step 3: Deploy Gateway (Working Example)

```bash
# Create namespace
kubectl create namespace rivault-system

# Apply Gateway manifests using Kustomize
kubectl apply -k gateway/k8s/

# Check deployment status
kubectl get pods -n rivault-system -w
```

### Step 4: Verify Deployment

```bash
# Check pods are running
kubectl get pods -n rivault-system

# Expected output:
# NAME                               READY   STATUS    RESTARTS   AGE
# rivault-gateway-xxxxxx-xxxxx       1/1     Running   0          1m

# Port forward to test locally
kubectl port-forward svc/rivault-gateway 8080:8080 -n rivault-system &

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/ready
```

### Step 5: Clean Up

```bash
# Delete cluster
kind delete cluster --name rivault-e2e

# Or just delete namespace
kubectl delete namespace rivault-system
```

---

## Quick Start Test Script

Use the provided test runner for automated K8s testing:

```bash
# Full setup and test
./tests/run_k8s_tests.sh

# Just set up cluster
./tests/run_k8s_tests.sh --setup-only

# Run tests on existing cluster
./tests/run_k8s_tests.sh --test-only

# Clean up
./tests/run_k8s_tests.sh --cleanup
```

---

## Production Deployment

### Step 1: Prepare Cluster

```bash
# Ensure you have admin access
kubectl auth can-i create deployments --all-namespaces

# Create namespace
kubectl create namespace rivault-system

# (If using GPU) Verify NVIDIA GPU Operator is installed
kubectl get pods -n gpu-operator
```

### Step 2: Configure Secrets

```bash
# Create secrets from template
cp gateway/k8s/secrets.yaml gateway/k8s/secrets-local.yaml

# Edit with your values
vim gateway/k8s/secrets-local.yaml
```

**Example secrets.yaml:**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: rivault-gateway-secrets
  namespace: rivault-system
type: Opaque
stringData:
  jwt-secret: "your-production-secret-min-32-chars"
  litellm-service-key: "sk-ll-your-production-key"
```

```bash
# Apply secrets
kubectl apply -f gateway/k8s/secrets-local.yaml
```

### Step 3: Configure Storage

**Create PersistentVolumeClaim for models:**

```yaml
# Example: k8s/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rivault-models
  namespace: rivault-system
spec:
  accessModes:
    - ReadOnlyMany  # For shared model access
  resources:
    requests:
      storage: 100Gi
  storageClassName: nfs  # Adjust for your storage class
```

```bash
# Apply PVC
kubectl apply -f k8s/pvc.yaml

# Pre-populate with model (example using a Job)
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: model-loader
  namespace: rivault-system
spec:
  template:
    spec:
      containers:
      - name: loader
        image: busybox
        command: ["sh", "-c", "echo 'Copy your model here'"]
        volumeMounts:
        - name: models
          mountPath: /models
      volumes:
      - name: models
        persistentVolumeClaim:
          claimName: rivault-models
      restartPolicy: Never
EOF
```

### Step 4: Deploy Services

```bash
# Deploy all components using Kustomize
kubectl apply -k gateway/k8s/

# Or use Helm (see Helm section below)
helm install rivault ./helm/rivault -n rivault-system
```

### Step 5: Configure Ingress (Optional)

```yaml
# gateway/k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: rivault-gateway
  namespace: rivault-system
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
spec:
  ingressClassName: nginx
  rules:
  - host: ai.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: rivault-gateway
            port:
              number: 8080
  tls:
  - hosts:
    - ai.example.com
    secretName: rivault-tls
```

```bash
kubectl apply -f gateway/k8s/ingress.yaml
```

---

## Helm Chart Deployment

### Install Chart

```bash
# Navigate to project root
cd r4

# Install with default values
helm install rivault ./helm/rivault \
  -n rivault-system \
  --create-namespace

# Install with custom values
helm install rivault ./helm/rivault \
  -n rivault-system \
  --create-namespace \
  -f my-values.yaml
```

### Custom Values Example

Create `my-values.yaml`:

```yaml
# my-values.yaml - Production configuration

global:
  namespace: rivault-system

# vLLM Configuration
vllm:
  enabled: true
  model:
    path: /models/Qwen2.5-7B-Instruct
    name: qwen2-7b
  maxModelLen: 8192
  gpuMemoryUtilization: 0.9
  resources:
    limits:
      nvidia.com/gpu: 1
      memory: 48Gi
    requests:
      nvidia.com/gpu: 1
      memory: 32Gi

# LiteLLM Configuration
litellm:
  enabled: true
  masterKey: "sk-ll-CHANGE-THIS-IN-PRODUCTION"
  port: 11434

# Gateway Configuration
gateway:
  enabled: true
  replicas: 2
  resources:
    limits:
      memory: 512Mi
      cpu: 1000m
    requests:
      memory: 256Mi
      cpu: 100m

# Storage
storage:
  models:
    enabled: true
    storageClass: "nfs"
    size: 100Gi

# Ingress
ingress:
  enabled: true
  hostname: ai.example.com
  tls:
    enabled: true
    secretName: rivault-tls
```

```bash
helm install rivault ./helm/rivault \
  -n rivault-system \
  --create-namespace \
  -f my-values.yaml
```

### Upgrade Chart

```bash
helm upgrade rivault ./helm/rivault \
  -n rivault-system \
  -f my-values.yaml
```

### Uninstall Chart

```bash
helm uninstall rivault -n rivault-system
```

---

## Kustomize Deployment

### Directory Structure

```
gateway/k8s/
├── kustomization.yaml     # Base kustomization
├── namespace.yaml         # Namespace definition
├── configmap.yaml         # Configuration
├── secrets.yaml           # Secrets (edit before applying)
├── deployment.yaml        # Gateway deployment
├── service.yaml           # ClusterIP services
├── networkpolicy.yaml     # Network policies
├── pdb.yaml              # Pod Disruption Budget
├── hpa.yaml              # Horizontal Pod Autoscaler
└── ingress.yaml          # Ingress (optional)
```

### Base Deployment

```bash
# Apply all manifests
kubectl apply -k gateway/k8s/

# Check status
kubectl get all -n rivault-system
```

### Create Overlays for Environments

```bash
mkdir -p gateway/k8s/overlays/{dev,staging,production}
```

**Development overlay** (`gateway/k8s/overlays/dev/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: rivault-dev

resources:
  - ../../

patches:
  - patch: |-
      - op: replace
        path: /spec/replicas
        value: 1
    target:
      kind: Deployment
      name: rivault-gateway

configMapGenerator:
  - name: rivault-gateway-config
    behavior: merge
    literals:
      - LOG_LEVEL=DEBUG
      - READINESS_REQUIRES=
```

**Production overlay** (`gateway/k8s/overlays/production/kustomization.yaml`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: rivault-system

resources:
  - ../../
  - ingress.yaml

patches:
  - patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
    target:
      kind: Deployment
      name: rivault-gateway

configMapGenerator:
  - name: rivault-gateway-config
    behavior: merge
    literals:
      - LOG_LEVEL=INFO
      - READINESS_REQUIRES=umrs
```

**Apply overlay:**

```bash
kubectl apply -k gateway/k8s/overlays/production/
```

---

## Configuration

### ConfigMap Reference

```yaml
# gateway/k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: rivault-gateway-config
  namespace: rivault-system
data:
  # Service
  GATEWAY_PORT: "8080"
  GATEWAY_HOST: "0.0.0.0"
  GATEWAY_ENVIRONMENT: "production"
  LOG_LEVEL: "INFO"

  # UMRS Connection
  UMRS_BASE_URL: "http://umrs.rivault-system.svc.cluster.local:8000"
  UMRS_TIMEOUT_SEC: "10"
  READINESS_REQUIRES: "umrs"  # Set empty if UMRS is not deployed

  # Rate Limiting
  RATE_LIMIT_ENABLED: "true"
  RATE_LIMIT_RPM: "60"
  RATE_LIMIT_RPS: "10"
  RATE_LIMIT_BURST: "20"

  # JWT
  JWT_ALGORITHM: "HS256"
```

### Secrets Reference

```yaml
# gateway/k8s/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: rivault-gateway-secrets
  namespace: rivault-system
type: Opaque
stringData:
  JWT_SECRET: "minimum-32-character-secret-for-jwt"
  GATEWAY_LITELLM_SERVICE_KEY: "sk-ll-your-service-key"
```

`GATEWAY_ENVIRONMENT=production` activates the Gateway JWT secret policy, so `JWT_SECRET` must be populated before applying the manifests.

---

## GPU Support

### NVIDIA GPU Operator

For production GPU clusters, install the NVIDIA GPU Operator:

```bash
# Add NVIDIA Helm repo
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

# Install GPU Operator
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator \
  --create-namespace \
  --set driver.enabled=true
```

### Verify GPU Resources

```bash
# Check node GPU resources
kubectl describe nodes | grep -A5 "Allocatable:" | grep nvidia

# Check GPU operator pods
kubectl get pods -n gpu-operator

# Test GPU access
kubectl run gpu-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.0-base \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi
```

### Mini-RiVault GPU Job

The mini-rivault job template requires GPU:

```yaml
# Example: k8s/job.yaml (excerpt)
spec:
  template:
    spec:
      containers:
        - name: vllm
          resources:
            requests:
              nvidia.com/gpu: "1"
            limits:
              nvidia.com/gpu: "1"
      nodeSelector:
        nvidia.com/gpu.present: "true"
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
```

---

## Networking & Ingress

### Service Types

| Service | Type | Port | Description |
|---------|------|------|-------------|
| rivault-gateway | ClusterIP | 8080 | Gateway API |
| rivault-gateway-headless | ClusterIP (None) | 8080 | For StatefulSet discovery |
| umrs | ClusterIP | 8000 | UMRS backend |
| mini-rivault | ClusterIP | 11434 | LiteLLM endpoint |

### Network Policies

Restrict traffic between components:

```yaml
# gateway/k8s/networkpolicy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: rivault-gateway
  namespace: rivault-system
spec:
  podSelector:
    matchLabels:
      app: rivault-gateway
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector: {}  # Allow from all namespaces
      ports:
        - protocol: TCP
          port: 8080
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: umrs
      ports:
        - protocol: TCP
          port: 8000
```

### Ingress Configuration

```bash
# Install NGINX Ingress Controller (if not installed)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.9.0/deploy/static/provider/cloud/deploy.yaml

# Verify
kubectl get pods -n ingress-nginx
```

---

## Monitoring

### Health Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness probe - service running |
| `/ready` | Readiness probe - dependencies available |
| `/metrics` | Prometheus metrics |

### Prometheus Integration

```yaml
# ServiceMonitor for Prometheus Operator
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: rivault-gateway
  namespace: rivault-system
spec:
  selector:
    matchLabels:
      app: rivault-gateway
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
```

### Grafana Dashboard

Import the provided Grafana dashboard:

```bash
# Dashboard JSON at observability/grafana/dashboards/gateway.json
```

---

## Troubleshooting

### Common Issues

#### 1. ImagePullBackOff

```bash
# Check image pull errors
kubectl describe pod <pod-name> -n rivault-system

# Solutions:
# - Build and load image for Kind
kind load docker-image rivault-gateway:latest --name rivault-e2e

# - Update imagePullPolicy
kubectl patch deployment rivault-gateway -n rivault-system \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"gateway","imagePullPolicy":"IfNotPresent"}]}}}}'
```

#### 2. Pods Pending (No GPU Nodes)

```bash
# Check pending reason
kubectl describe pod <pod-name> -n rivault-system | grep -A10 Events

# List nodes with GPU
kubectl get nodes -l nvidia.com/gpu.present=true

# Delete GPU jobs if no GPU available
kubectl delete job mini-rivault-template -n rivault-system
```

#### 3. Readiness Probe Failing

```bash
# Check logs
kubectl logs -l app=rivault-gateway -n rivault-system

# If UMRS not deployed, make readiness dependency list empty
kubectl patch configmap rivault-gateway-config -n rivault-system \
  -p '{"data":{"READINESS_REQUIRES":""}}'

# Restart pods
kubectl rollout restart deployment/rivault-gateway -n rivault-system
```

#### 4. Service Not Accessible

```bash
# Check service endpoints
kubectl get endpoints -n rivault-system

# Port forward for testing
kubectl port-forward svc/rivault-gateway 8080:8080 -n rivault-system

# Check network policies
kubectl get networkpolicy -n rivault-system
```

### Debug Commands

```bash
# Pod status
kubectl get pods -n rivault-system -o wide

# Pod logs
kubectl logs <pod-name> -n rivault-system -f

# Previous container logs
kubectl logs <pod-name> -n rivault-system --previous

# Exec into pod
kubectl exec -it <pod-name> -n rivault-system -- /bin/bash

# Describe resources
kubectl describe pod <pod-name> -n rivault-system
kubectl describe deployment rivault-gateway -n rivault-system

# Events
kubectl get events -n rivault-system --sort-by=.lastTimestamp

# Resource usage
kubectl top pods -n rivault-system
```

### Reset Deployment

```bash
# Delete and recreate
kubectl delete namespace rivault-system
kubectl create namespace rivault-system
kubectl apply -k gateway/k8s/

# Or just restart pods
kubectl rollout restart deployment -n rivault-system
```

---

## Related Documentation

- [Docker Deployment Guide](docker-deployment.md)
- [Administrator Guide](README.md)
- [Gateway K8s README](../../../gateway/k8s/README.md)
- [Helm Chart README](../../../helm/rivault/README.md)
- [Testing Guide](../../../TESTING.md)
