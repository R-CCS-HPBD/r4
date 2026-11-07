# UMRS + RiVault HPC Demo

This demo runs UMRS and RiVault on an HPC cluster using Podman and Slurm.
Docker is **not required**.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Login Node                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐       │
│  │ Postgres │  │  MinIO   │  │     UMRS Server      │       │
│  │ pgvector │  │   (S3)   │  │  API :8000, ZMQ :5555│       │
│  └──────────┘  └──────────┘  └──────────────────────┘       │
│       ↑              ↑                    ↑                  │
│       └──────────────┴────────────────────┘                  │
│                  podman compose                              │
└─────────────────────────────────────────────────────────────┘
                              ↓ HTTP (internal network)
┌─────────────────────────────────────────────────────────────┐
│                      Compute Node (GPU)                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    Slurm Job                          │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────────────┐ │   │
│  │  │   vLLM    │  │  LiteLLM  │  │      NGINX        │ │   │
│  │  │  (GPU)    │  │  (proxy)  │  │  (optional)       │ │   │
│  │  │   :8000   │  │   :11434  │  │                   │ │   │
│  │  └───────────┘  └───────────┘  └───────────────────┘ │   │
│  │                  podman compose                       │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Podman** with podman-compose plugin
2. **Slurm** access (sbatch, squeue, scancel)
3. **Shared filesystem** between login and compute nodes
4. **Pre-staged model** on shared filesystem

## Quick Start

### 1. Configure Environment

```bash
cp env.template env.hpc
vim env.hpc  # Edit MODEL_PATH, SLURM_PARTITION, etc.
```

### 2. Start Demo

```bash
./start-demo.sh
```

This will:
1. Start login node services (Postgres, MinIO, UMRS) via podman-compose
2. Submit a Slurm job to start RiVault on a GPU node
3. Wait for services to be ready

### 3. Access Services

| Service | URL |
|---------|-----|
| UMRS API | http://localhost:8000 |
| MinIO Console | http://localhost:9001 |
| LiteLLM API | http://<compute-node>:11434 |

### 4. Stop Demo

```bash
./stop-demo.sh           # Stop and remove volumes
./stop-demo.sh --keep-data  # Stop but keep data
```

## Configuration

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `VLLM_PATH_TO_HUGGINGFACE` | Path to HuggingFace cache | `/shared/models/vllm/huggingface` |
| `VLLM_MODEL` | HuggingFace model ID | `Qwen/Qwen2.5-7B-Instruct` |
| `SLURM_PARTITION` | Slurm partition name | `gpu` |

### Optional Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_TP` | `1` | Tensor parallelism |
| `VLLM_PP` | `1` | Pipeline parallelism |
| `SLURM_GPUS` | `1` | Number of GPUs |
| `SLURM_TIME` | `02:00:00` | Job time limit |
| `SLURM_CPUS` | `8` | CPUs per task |
| `SLURM_MEM` | `64G` | Memory allocation |
| `UMRS_API_PORT` | `8000` | UMRS API port |
| `MINIO_API_PORT` | `9000` | MinIO API port |

## Files

| File | Description |
|------|-------------|
| `umrs-login.yaml` | Podman compose for login node services |
| `start-demo.sh` | Startup script |
| `stop-demo.sh` | Shutdown script |
| `env.template` | Environment template |
| `env.hpc` | Your configuration (gitignored) |
| `init_pgvector.sql` | PostgreSQL init script |

## Troubleshooting

### Services won't start

Check podman-compose is installed:
```bash
podman compose version
```

Check service logs:
```bash
podman compose -f umrs-login.yaml logs
```

### Slurm job pending

Check job status:
```bash
squeue -j <job_id>
```

Check available resources:
```bash
sinfo -p <partition>
```

### Cannot connect to compute node

Ensure network access is allowed between login and compute nodes:
```bash
ping <compute-node>
curl http://<compute-node>:11434/v1/models
```

### Model loading fails

Verify model path is accessible from compute node:
```bash
ls -la ${VLLM_PATH_TO_HUGGINGFACE}/hub/models--$(echo ${VLLM_MODEL} | tr '/' '--')
```

## Integration with the Platform Gateway

Once services are running, point R4 and other clients at the Platform Gateway. Direct compute-node LiteLLM access is useful for troubleshooting only.

```bash
# Get compute node from job
COMPUTE_NODE=$(squeue -j $JOB_ID -h -o "%N")

# Normal client path: go through Gateway
export R4_GATEWAY_URL="http://<gateway-host>:8080"
export R4_PROJECT_ID="your-project-id"
export R4_API_KEY="<gateway-bearer-token>"

# Direct LiteLLM reachability check (debug only)
curl "http://${COMPUTE_NODE}:11434/v1/models"
```

## See Also

- [Main demos README](../README.md)
- [RiVault documentation](../../../helm/rivault/README.md)
- [UMRS documentation](../../umrs/Readme.md)
