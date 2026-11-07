# Docker / Podman Compose Deployment Guide

Complete guide for deploying the R4 Platform using Docker Compose or Podman Compose.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Development Environment](#development-environment)
5. [E2E Test Environment](#e2e-test-environment)
6. [Production-like Environment](#production-like-environment)
7. [Configuration Reference](#configuration-reference)
8. [Networking](#networking)
9. [Troubleshooting](#troubleshooting)

---

## Overview

Docker Compose (or Podman Compose) provides the simplest deployment method for:
- Local development
- End-to-end testing
- CI/CD pipelines
- Small-scale production (with GPU support)

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Compose Network                       │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Gateway   │  │    UMRS     │  │   mock Mini-RiVault     │  │
│  │   :8080     │─→│   :8000     │─→│  ┌─────────┐ ┌────────┐ │  │
│  └─────────────┘  └──────┬──────┘  │  │  vLLM   │ │LiteLLM │ │  │
│                          │         │  │  :8000  │ │ :11434 │ │  │
│                          │         │  └─────────┘ └────────┘ │  │
│                          │         └─────────────────────────┘  │
│                          │                                      │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    Data Services                            ││
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────────────┐    ││
│  │  │ PostgreSQL  │  │   MinIO     │  │   slurm-mock      │    ││
│  │  │   :5432     │  │  :9000/9001 │  │     :6817         │    ││
│  │  └─────────────┘  └─────────────┘  └───────────────────┘    ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **CPU** | 4 cores | 8+ cores |
| **RAM** | 16GB | 32GB+ |
| **Storage** | 50GB | 100GB+ SSD |
| **GPU** | Optional for dev | NVIDIA GPU for inference |

### Required Software

```bash
# Podman (preferred, 4.0+) — or Docker (20.10+)
podman --version   # or: docker --version

# Podman Compose or Docker Compose v2
podman-compose version   # or: docker compose version

# Check container runtime is running
podman info   # or: docker info

# (Optional) Check NVIDIA Container Toolkit for GPU support
nvidia-container-cli info
```

### Install Prerequisites

```bash
# Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# Docker Compose v2 (usually included with Docker Desktop)
# For Linux standalone:
DOCKER_COMPOSE_VERSION=v2.24.0
sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# (Optional) NVIDIA Container Toolkit for GPU
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
    sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

---

## Quick Start

### Clone Repository

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
```

### Start E2E Test Environment

```bash
# Start all services
docker compose -f docker-compose.e2e.yaml up -d

# Check status
docker compose -f docker-compose.e2e.yaml ps

# View logs
docker compose -f docker-compose.e2e.yaml logs -f gateway

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/ready
  -H "Authorization: Bearer <gateway-jwt>"

```bash
# Stop but keep data
docker compose -f docker-compose.e2e.yaml down

# Stop and remove all data
docker compose -f docker-compose.e2e.yaml down -v
```

---

## Development Environment

### Setup for Development

```bash
cd r4
```

### Start Development Stack

```bash
# Start the E2E compose file (works with docker compose or podman-compose)
docker compose -f docker-compose.e2e.yaml up -d
```

### Build Local Images

```bash
# Build Gateway
podman build -t rivault-gateway:latest ./gateway   # or: docker build ...

# Build UMRS
podman build -t umrs:latest -f umrs/docker/Dockerfile ./umrs

# Build MCP Apps
podman build -t mcp-apps:latest ./mcp-apps
```

---

## E2E Test Environment

The E2E test environment provides a complete mock stack for testing without GPU requirements.

### Components

| Service | Port | Description |
|---------|------|-------------|
| `gateway` | 8080 | Platform Gateway (API entry) |
| `umrs` | 8001 | UMRS backend service |
| `compute-mock` | 11434 | Mock LiteLLM-compatible service; debug only |
| `postgres` | 5432 | PostgreSQL with pgvector |
| `minio` | 9000/9001 | S3-compatible storage |
| `slurm-mock` | 6817 | Mock Slurm controller |
| `mcp-apps` | 5173 | Interactive UI applications |

### Start Test Environment

```bash
# Start full E2E environment
docker compose -f docker-compose.e2e.yaml up -d

# Wait for all services to be healthy
docker compose -f docker-compose.e2e.yaml ps

# Expected output (all should show "healthy"):
# NAME           STATUS
# e2e-gateway    running (healthy)
# e2e-umrs       running (healthy)
# e2e-postgres   running (healthy)
# e2e-minio      running (healthy)
# compute-mock   running (healthy)
# slurm-mock     running (healthy)
```

### Run E2E Tests

```bash
# Run tests using the Python test runner
python tests/run_e2e_tests.py

# Or run with pytest directly
pytest tests/test_e2e_smoke.py -v

# Or use the test-runner container
docker compose -f docker-compose.e2e.yaml --profile test run test-runner
```

### Test Endpoints Manually

```bash
# Health checks
curl http://localhost:8080/health
curl http://localhost:8080/ready

# Chat completion (mock LLM)
curl -X POST http://localhost:8080/gw/llm/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test-e2e" \
  -d '{
    "model": "rivault-gpt-oss-static",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# UMRS endpoints
curl http://localhost:8001/health
curl http://localhost:8080/gw/endpoints \
  -H "Authorization: Bearer sk-test-e2e" \
  -H "X-Project-ID: test-project" \
  -H "X-User-ID: test-user"
```

### Access Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Gateway API | http://localhost:8080 | Bearer token: `sk-test-e2e` |
| UMRS API | http://localhost:8001 | - |
| MinIO Console | http://localhost:9001 | `minioadmin`/`minioadmin` |
| MCP Apps | http://localhost:5173 | - |

---

## Production-like Environment

`docker-compose.e2e.yaml` is the development and E2E harness. It intentionally
uses `compute-mock` to emulate a Mini-RiVault endpoint without requiring a GPU.

For real GPU inference, do not expose vLLM or LiteLLM directly from a Compose
override. Provision Mini-RiVault through UMRS so endpoint state, model staging,
offline inference settings, and cleanup remain under the durable job system:

```bash
# User-facing path through Gateway and UMRS
r4 endpoint up --model <model-name> --wait
r4 chat "hello"

# Operator validation path for the Compose harness
python tests/run_e2e_tests.py
```

If an operator builds a local GPU-only harness for debugging, keep vLLM and
LiteLLM private on the Compose network and call them through Gateway
`/gw/llm/*`. Host port mappings for vLLM/LiteLLM are debug-only and should not
be documented as the production path.

---

## Configuration Reference

### Environment Variables

#### Gateway (`gateway` service)

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_PORT` | `8080` | HTTP listen port |
| `GATEWAY_ENVIRONMENT` | `development` | Deployment mode for JWT secret validation |
| `UMRS_BASE_URL` | `http://umrs:8000` | UMRS backend URL |
| `JWT_SECRET` | Required | JWT signing secret |
| `LOG_LEVEL` | `INFO` | Log level |

For staging or production-style Docker deployments, set `GATEWAY_ENVIRONMENT` accordingly and provide a real `JWT_SECRET` instead of relying on the development default.

The Gateway no longer supports direct LiteLLM mode. LLM traffic flows through UMRS (`/umrs/llm/*`), and UMRS handles endpoint discovery plus the upstream LiteLLM proxy.

#### UMRS (`umrs` service)

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `postgres` | PostgreSQL host |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | Required | Database password |
| `S3_ENDPOINT` | `http://minio:9000` | S3 endpoint |
| `UMRS_BACKEND` | `slurm` | Orchestration backend |
| `TRANSFORMERS_OFFLINE` | `1` | Prevent in-container downloads; staging/download jobs run outside the inference container |

#### Mini-RiVault (vLLM)

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_MODEL_PATH` | `/models` | Model directory |
| `VLLM_MAX_MODEL_LEN` | `4096` | Max context length |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.9` | GPU memory fraction |
| `VLLM_TENSOR_PARALLEL_SIZE` | `1` | GPU count for parallelism |

### Volume Mounts

| Mount | Purpose |
|-------|---------|
| `./models:/models:ro` | Pre-staged model files |
| `pg_data:/var/lib/postgresql/data` | PostgreSQL data |
| `minio_data:/data` | MinIO object storage |

---

## Networking

### Default Port Mappings

| Host Port | Container | Service |
|-----------|-----------|---------|
| 8080 | gateway:8080 | Platform Gateway |
| 8001 | umrs:8000 | UMRS API |
| 11434 | compute-mock:11434 | Mock LiteLLM-compatible service; debug only |
| 5432 | postgres:5432 | PostgreSQL |
| 9000 | minio:9000 | MinIO API |
| 9001 | minio:9001 | MinIO Console |
| 5173 | mcp-apps:5173 | MCP Apps UI |

### Internal Service Discovery

Services communicate using Docker DNS:

```bash
# From gateway container
curl http://umrs:8000/health
curl -H "Authorization: Bearer sk-test-e2e" http://gateway:8080/gw/llm/v1/models
```

---

## Troubleshooting

### Common Issues

#### 1. Services Not Starting

```bash
# Check container status
docker compose -f docker-compose.e2e.yaml ps -a

# View logs for specific service
docker compose -f docker-compose.e2e.yaml logs -f umrs

# Check resource usage
docker stats
```

#### 2. Database Connection Failed

```bash
# Check postgres is running
docker compose -f docker-compose.e2e.yaml logs postgres

# Connect manually
docker exec -it e2e-postgres psql -U postgres -d e2e_test
```

#### 3. GPU Not Available

```bash
# Check NVIDIA container toolkit
nvidia-container-cli info

# Test GPU access in container
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

#### 4. Port Conflicts

```bash
# Check what's using port 8080
lsof -i :8080

# Use different ports
GATEWAY_PORT=9080 docker compose -f docker-compose.e2e.yaml up -d
```

### Reset Everything

```bash
# Stop and remove all containers, networks, volumes
docker compose -f docker-compose.e2e.yaml down -v --remove-orphans

# Remove all related images
docker images | grep -E 'e2e|rivault|umrs' | awk '{print $3}' | xargs docker rmi

# Start fresh
docker compose -f docker-compose.e2e.yaml up -d --build
```

### Logs and Debugging

```bash
# All logs
docker compose -f docker-compose.e2e.yaml logs -f

# Specific service with timestamps
docker compose -f docker-compose.e2e.yaml logs -f -t gateway

# Enter container shell
docker exec -it e2e-gateway /bin/bash

# Inspect container
docker inspect e2e-gateway
```

---

## Related Documentation

- [Kubernetes Deployment Guide](kubernetes-deployment.md)
- [Administrator Guide](README.md)
- [E2E Testing Guide](../../../tests/E2E_TESTING.md)
- [Main README](../../../README.md)
