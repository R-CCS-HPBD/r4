# Workstation Deployment

Single GPU workstation deployment for the R4 Platform.

## Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| GPU | RTX 3090 (24GB VRAM) | RTX 4090 / A100 |
| System RAM | 32GB | 64GB+ |
| Disk Space | 50GB | 100GB+ |
| Container Runtime | Docker 24.0+ or Podman 4.0+ | Latest |
| NVIDIA Driver | 535+ | Latest |

> **Note:** Both Docker and Podman are supported. The scripts automatically detect which runtime is available, preferring Podman if both are installed.

## Quick Start

```bash
# 1. Setup (downloads model, creates config files)
./setup.sh

# 2. Start all services
./start.sh

# 3. Run interactive demo
./demo.sh

# 4. Stop when done
./stop.sh
```

## Scripts

| Script | Description |
|--------|-------------|
| `setup.sh` | Download model, create environment files |
| `start.sh` | Start all platform services |
| `demo.sh` | Run interactive demonstration |
| `stop.sh` | Stop all services |

## Environment Files

After running `setup.sh`, these files are created:

- `../../../helm/rivault/env.mini-rivault` - RiVault configuration
- `../../../umrs/docker/.env` - UMRS configuration  
- `./env.local` - Demo environment variables

## Service Endpoints

| Service | URL | Description |
|---------|-----|-------------|
| LiteLLM API | http://localhost:11434/v1 | OpenAI-compatible |
| UMRS API | http://localhost:8001 | Data plane |
| MinIO Console | http://localhost:9001 | S3 storage UI |
| MinIO S3 | http://localhost:9000 | S3 API |
| PostgreSQL | localhost:5432 | Database |

## Using r4 CLI

```bash
# Source environment
source env.local

# Install SDK (if not already installed)
pip install -e ../../../r4

# Chat
r4 chat "Hello, world!"

# Interactive chat
r4 chat -i

# List models
r4 model list
```

## Customization

### Use a Different Model

```bash
# During setup
./setup.sh --model meta-llama/Llama-3.1-8B-Instruct

# Or edit rivault/env.mini-rivault
VLLM_MODEL=microsoft/Phi-3-mini-4k-instruct
```

### Adjust GPU Memory

Edit `../../../helm/rivault/env.mini-rivault`:

```bash
VLLM_GPU_MEM_UTIL=0.85  # Reduce if OOM errors
```

### Change Ports

Edit the respective environment files:

```bash
# LiteLLM port
NGINX_LITELLM_DOCKER_PORT=11434

# UMRS port
UMRS_PORT=8001
```

## Troubleshooting

### GPU Not Found

```bash
# Check NVIDIA driver
nvidia-smi

# Check Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.1-base nvidia-smi
```

### Out of VRAM

```bash
# Reduce GPU memory usage
vim ../../../helm/rivault/env.mini-rivault
# Set: VLLM_GPU_MEM_UTIL=0.80

# Or use a smaller model
./setup.sh --model microsoft/Phi-3-mini-4k-instruct
```

### Services Won't Start

```bash
# Check logs
cd ../../../helm/rivault
docker compose --env-file env.mini-rivault logs -f vllm

cd ../../../umrs/docker
docker compose logs -f umrs-server
```

### Model Loading Slow

First model load downloads weights (~15GB for 7B model). 
Subsequent starts use cached weights and are faster.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Workstation                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────────────┐ │
│  │     RiVault Stack   │    │        UMRS Stack           │ │
│  ├─────────────────────┤    ├─────────────────────────────┤ │
│  │  ┌──────┐ ┌───────┐ │    │  ┌──────┐ ┌──────┐ ┌──────┐│ │
│  │  │ vLLM │→│LiteLLM│ │    │  │ UMRS │ │Postgres│ │MinIO ││ │
│  │  │(GPU) │ │:11434 │ │    │  │:8001 │ │:5432  │ │:9000 ││ │
│  │  └──────┘ └───────┘ │    │  └──────┘ └──────┘ └──────┘│ │
│  └─────────────────────┘    └─────────────────────────────┘ │
│                                                             │
│  Model: /rivault/model/ (pre-staged)                        │
└─────────────────────────────────────────────────────────────┘
```

## Next Steps

After verifying workstation deployment:

1. **Run the unified demo wrapper**: `./demo.sh --auto`
2. **Run the canonical demo suite**: `../../run_all.sh --auto`
3. **Scale to server**: `../server/` (multi-GPU)
4. **Deploy to HPC**: `../hpc/` (Slurm + Singularity)
