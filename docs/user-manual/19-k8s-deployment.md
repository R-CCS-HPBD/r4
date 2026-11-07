# 19. Kubernetes Deployment

Deploy R4 on Kubernetes for multi-tenant production environments.

## Prerequisites

- Kubernetes cluster (≥ 1.28)
- Helm 3
- NVIDIA GPU Operator (for GPU nodes)
- Persistent volume provisioner (for models, data)
- Ingress controller (NGINX recommended)

## Helm Charts

```bash
cd helm/

# Install the full platform
helm install r4 ./r4-platform \
  --namespace r4-system \
  --create-namespace \
  --values values-production.yaml
```

## Component Deployment

### Gateway

```bash
helm install r4-gateway ./gateway \
  --set gateway.auth.jwtSecret=<secret> \
  --set gateway.upstream.umrs=http://umrs:8000
```

### UMRS

```bash
helm install umrs ./umrs \
  --set database.url=postgresql://... \
  --set storage.endpoint=http://minio:9000 \
  --set orchestrator.backend=k8s
```

### PostgreSQL + pgvector

```bash
helm install postgres bitnami/postgresql \
  --set image.tag=15-pgvector \
  --set primary.persistence.size=50Gi
```

### MinIO

```bash
helm install minio bitnami/minio \
  --set persistence.size=100Gi
```

## Kubernetes Orchestrator

When UMRS uses the K8s backend, endpoint provisioning creates Kubernetes Jobs instead of Slurm jobs:

| Component | Slurm Equivalent | K8s Resource |
|-----------|-----------------|--------------|
| ExecutionTarget | SlurmTarget | K8sTarget |
| RuntimeWrapper | SingularityWrapper | K8sJobWrapper |
| Job tracking | sbatch/squeue | K8s Job API |

## GPU Scheduling

```yaml
# Pod spec for Mini-RiVault
resources:
  limits:
    nvidia.com/gpu: 1
  requests:
    nvidia.com/gpu: 1
nodeSelector:
  nvidia.com/gpu.present: "true"
```

## Model Storage

Models can be served via:

- **PVC** — Persistent Volume Claims for shared model storage
- **NFS** — Network filesystem mounts
- **Host path** — Node-local model directories

```yaml
volumes:
  - name: models
    persistentVolumeClaim:
      claimName: shared-models
```

## Air-Gap Setup

For disconnected environments:

```bash
# Prepare air-gap bundle on connected machine
python deploy/airgap_bundle.py --output ./bundle/

# Transfer bundle to air-gap cluster
scp -r ./bundle/ admin@cluster:/opt/r4/

# Install on air-gap cluster
./deploy/k8s-worker-airgap-setup.sh
```

## Testing

```bash
# Run K8s tests with Kind
./tests/run_k8s_tests.sh
```

## Next Steps

- [Chapter 20 — User & Access Management](20-user-management.md)
- [Chapter 21 — UMRS Operations](21-umrs-operations.md)
