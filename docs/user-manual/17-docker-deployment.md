# 17. Docker/Compose Deployment

Local development and testing deployment using Docker Compose.

## Prerequisites

- Docker Compose v2 or Podman Compose
- NVIDIA Container Toolkit (for GPU inference)
- At least 16GB RAM (32GB+ recommended for larger models)
- GPU with ≥8GB VRAM for inference

## Quick Start

```bash
# 1. Clone the repository
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4

# 2. Setup: download models and configure environment
cd demos/tiers/workstation
./setup.sh

# 3. Start the full stack
./start.sh

# 4. Run the demo suite
cd ../..
./run_all.sh --auto

# 5. Stop all services
cd tiers/workstation
./stop.sh
```

## What Gets Started

| Service | Port | Description |
|---------|------|-------------|
| Platform Gateway | 8080 | NGINX + Auth Sidecar |
| UMRS | 8001 | Orchestration + RAG |
| LiteLLM | 11434 | Internal OpenAI-compatible proxy |
| vLLM | 8000 | LLM inference backend (private) |
| MinIO | 9000/9001 | S3 storage |
| PostgreSQL | 5432 | Database + pgvector |

## Manual Setup

### 1. Environment File

Copy and customize the environment template:

```bash
cp .env.example .env
# Edit .env with your configuration
```

Key variables:

```bash
# Model path (must be pre-staged)
VLLM_MODEL_PATH=/models/Qwen/Qwen2.5-7B-Instruct
VLLM_MAX_MODEL_LEN=4096
VLLM_PORT=8000

# LiteLLM
LITELLM_MASTER_KEY=sk-demo-key
LITELLM_PORT=11434

# Gateway
GENAI_BASE_URL=http://litellm:11434
UMRS_BASE_URL=http://umrs:8000

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

# PostgreSQL
POSTGRES_PASSWORD=your-password
```

### 2. Start Services

```bash
docker compose -f docker-compose.e2e.yaml up -d
```

### 3. Configure Client

```bash
r4 auth login --gateway-url http://localhost:8080 --api-key sk-demo-key
r4 health
r4 model list
```

## Model Preparation

Models must be pre-staged on the filesystem:

```bash
# Development only — direct HuggingFace download
./scripts/dev-only/model-manager.sh llm Qwen/Qwen2.5-1.5B-Instruct

# Production — use UMRS-controlled staging
r4 model stage Qwen/Qwen2.5-7B-Instruct --name my-qwen
```

## Testing

```bash
# Run E2E test suite
python tests/run_e2e_tests.py

# Run specific tests
python -m pytest tests/ -v
```

## DGX Spark Deployment

For NVIDIA DGX Spark (ARM64 + GB10 GPU):

```bash
cd demos/tiers/dgx-spark
./setup.sh           # Install dependencies
./start.sh           # Start services
./seed_system_models.sh  # Register models
./demo.sh            # Run demo
./stop.sh            # Stop services
```

See [demos/tiers/dgx-spark/README.md](../../demos/tiers/dgx-spark/README.md) for large model support (GLM-4.7, Qwen 72B).

## Next Steps

- [Chapter 18 — HPC Deployment](18-hpc-deployment.md)
- [Chapter 20 — User & Access Management](20-user-management.md)
