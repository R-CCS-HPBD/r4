# Gateway Kubernetes Manifests

Kubernetes deployment manifests for the RiVault Platform Gateway.

## Prerequisites

- Kubernetes 1.24+
- kubectl configured
- UMRS deployed and accessible
- TLS certificates (optional but recommended)

## Quick Start

```bash
# Create namespace
kubectl apply -f namespace.yaml

# Create secrets (edit first!)
kubectl apply -f secrets.yaml

# Create ConfigMap
kubectl apply -f configmap.yaml

# Deploy Gateway
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# (Optional) Create Ingress
kubectl apply -f ingress.yaml
```

## Using Kustomize

```bash
# Apply all manifests
kubectl apply -k .

# Or with a specific overlay
kubectl apply -k overlays/production
```

## Configuration

### Environment Variables

Configure via ConfigMap (`configmap.yaml`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GATEWAY_PORT` | Gateway listen port | `8080` |
| `GATEWAY_ENVIRONMENT` | Deployment mode for auth validation | `production` |
| `UMRS_BASE_URL` | UMRS service URL | `http://umrs:8000` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `RATE_LIMIT_RPM` | Requests per minute | `60` |

### Secrets

Configure via Secrets (`secrets.yaml`):

| Secret Key | Description |
|------------|-------------|
| `JWT_SECRET` | JWT signing secret |
| `GATEWAY_LITELLM_SERVICE_KEY` | Service key for LiteLLM auth |

Because the Kubernetes ConfigMap sets `GATEWAY_ENVIRONMENT=production`, you must supply a real `JWT_SECRET` in `secrets.yaml` before deploying.

## Health Checks

The Gateway exposes health endpoints used by Kubernetes probes:

- **Liveness**: `GET /health` - Returns 200 if service is running
- **Readiness**: `GET /ready` - Returns 200 if UMRS is reachable
- **Backend probe** (not a k8s probe): `GET /health/backend` - 200 only when
  UMRS answers its `/health`, else 503; for external monitors (2026-10-30,
  issue #634). Keep it out of `livenessProbe`: a backend outage must not
  restart the gateway.

## Networking

### Internal Access
```
http://rivault-gateway.rivault-system.svc.cluster.local:8080
```

### External Access (Ingress)
```
https://ai.example.com/gw/*
```

## Scaling

The Gateway is stateless and can be scaled horizontally:

```bash
kubectl scale deployment rivault-gateway -n rivault-system --replicas=3
```

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n rivault-system -l app=rivault-gateway

# Check logs
kubectl logs -n rivault-system -l app=rivault-gateway

# Check endpoint connectivity
kubectl exec -n rivault-system deploy/rivault-gateway -- curl -s http://umrs:8000/umrs/endpoints/health
```
