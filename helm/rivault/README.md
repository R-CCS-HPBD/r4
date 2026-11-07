# RiVault Helm Chart

Helm chart for deploying the R4 Platform (RiVault) on Kubernetes.

Current scope: this chart deploys the inference plane used by RiVault workloads, specifically vLLM, LiteLLM, and their supporting storage/network objects. The Platform Gateway and UMRS are still deployed separately via the manifests under `gateway/k8s` or the other deployment assets in this repository.

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- NVIDIA GPU Operator (for GPU support)
- Pre-staged model files on shared storage

## Installation

### Add Dependencies

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add minio https://charts.min.io/
helm repo update
```

### Install Chart

```bash
# Install with default values
helm install rivault ./helm/rivault -n rivault-system --create-namespace

# Install with custom values
helm install rivault ./helm/rivault -n rivault-system \
  --create-namespace \
  -f custom-values.yaml
```

## Configuration

### Important Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `vllm.enabled` | Enable vLLM inference server | `true` |
| `vllm.model.path` | Path to pre-staged model | `/models/rivault-gpt-oss-static` |
| `vllm.model.name` | Model name for API | `rivault-gpt-oss-static` |
| `vllm.maxModelLen` | Maximum context length | `4096` |
| `vllm.resources.limits.nvidia.com/gpu` | GPU count | `1` |
| `litellm.enabled` | Enable LiteLLM proxy | `true` |
| `litellm.masterKey` | Authentication key | `sk-ll-rivault-CHANGE-ME` |
| `litellm.port` | LiteLLM service port | `11434` |

### Model Configuration

**CRITICAL**: Models must be pre-staged. No downloads at runtime.

```yaml
vllm:
  model:
    path: /models/my-model
    name: my-model
```

### GPU Resources

```yaml
vllm:
  resources:
    limits:
      nvidia.com/gpu: 1
      memory: 48Gi
    requests:
      nvidia.com/gpu: 1
      memory: 32Gi
```

### Storage

```yaml
storage:
  models:
    enabled: true
    existingClaim: my-model-pvc
    # OR
    storageClass: nfs-client
    size: 100Gi
```

### Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "10g"
  hosts:
    - host: rivault.example.com
      paths:
        - path: /
          pathType: Prefix
          service: gateway
          port: 8000
  tls:
    - secretName: rivault-tls
      hosts:
        - rivault.example.com
```

### Autoscaling

```yaml
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
```

## Architecture

The chart deploys:

1. **vLLM** - Inference server (GPU pod)
2. **LiteLLM** - OpenAI-compatible proxy
3. **PostgreSQL** - Database (optional, use external for production)
4. **MinIO** - Object storage (optional, use external for production)

Gateway and UMRS connect to this inference plane externally. If you deploy the Gateway alongside this chart, set `GATEWAY_ENVIRONMENT`, `JWT_SECRET`, `UMRS_BASE_URL`, and `GATEWAY_LITELLM_SERVICE_KEY` on that separate Gateway deployment.

```
                      External Gateway / UMRS
                          │
                      ┌────────▼────────┐
                      │    LiteLLM      │
                      └────────┬────────┘
                          │
                      ┌────────▼────────┐
                      │      vLLM       │
                      │      (GPU)      │
                      └─────────────────┘

                      Supporting storage/network objects
                      ┌──────────────┬──────────────┐
                      │ PostgreSQL   │    MinIO     │
                      └──────────────┴──────────────┘
```

## HPC Deployment

For HPC environments, use Singularity/Apptainer instead:

```yaml
# hpc-values.yaml
vllm:
  enabled: false  # Use Slurm-managed containers

umrs:
  slurm:
    enabled: true
    partition: gpu
    time: "02:00:00"
  containerRuntime: singularity
```

## Upgrades

```bash
helm upgrade rivault ./helm/rivault -n rivault-system -f values.yaml
```

## Uninstall

```bash
helm uninstall rivault -n rivault-system
```

## Troubleshooting

### Check Pod Status

```bash
kubectl get pods -n rivault-system
kubectl describe pod <pod-name> -n rivault-system
```

### View Logs

```bash
kubectl logs -f deployment/rivault -n rivault-system -c vllm
kubectl logs -f deployment/rivault -n rivault-system -c litellm
```

### Test Endpoints

```bash
# Port-forward LiteLLM
kubectl port-forward svc/rivault-litellm 11434:11434 -n rivault-system

# Test
curl -H "Authorization: Bearer sk-ll-xxx" http://localhost:11434/v1/models
```

## Files

```
helm/rivault/
├── Chart.yaml           # Chart metadata
├── README.md            # This file
├── values.yaml          # Default configuration
└── templates/
    ├── _helpers.tpl     # Template helpers
    ├── configmap.yaml   # Configuration
    ├── deployment.yaml  # Workloads
    ├── hpa.yaml         # Autoscaling
    ├── ingress.yaml     # Ingress rules
    ├── networkpolicy.yaml # Network policies
    ├── pdb.yaml         # Pod disruption budget
    ├── pvc.yaml         # Persistent volumes
    ├── secrets.yaml     # Secrets
    └── service.yaml     # Services
```
