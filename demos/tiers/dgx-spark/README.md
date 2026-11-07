# DGX Spark Deployment

Deployment profile for NVIDIA DGX Spark desktop AI workstation.

## System Specifications

| Component | Specification |
|-----------|---------------|
| CPU | NVIDIA Grace (ARM64) |
| GPU | NVIDIA GB10 (Blackwell architecture, 128GB unified memory) |
| System Memory | 128GB unified CPU+GPU |
| OS | Ubuntu 24.04 (ARM64) |
| Power | Desktop form factor (~500W) |

> **Note:** The DGX Spark uses a unified memory architecture where CPU and GPU share
> the same memory pool. `nvidia-smi` may show `[N/A]` for GPU memory - this is expected.

## Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| GPU Memory | 128GB HBM3e | (built-in) |
| System RAM | 128GB | (unified memory) |
| Disk Space | 100GB | 500GB+ (NVMe) |
| Container Runtime | Docker 24.0+ or Podman 4.0+ | Latest |
| NVIDIA Driver | 560+ | Latest |

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
| `seed_system_models.sh` | Register system-level models in UMRS |
| `demo.sh` | Run interactive demonstration |
| `stop.sh` | Stop all services |

## DGX Spark-Specific Features

### Large Model Support

With 128GB unified memory, DGX Spark can run models that require significant VRAM:

```bash
# GLM-4.7-Flash (latest GLM family model that fits cleanly)
./setup.sh --model zai-org/GLM-4.7-Flash

# Qwen 72B dense (default)
./setup.sh --model Qwen/Qwen2.5-72B-Instruct

# Qwen3 32B dense
./setup.sh --model Qwen/Qwen3-32B
```

### Optimized vLLM Settings

DGX Spark profile uses optimized settings:

| Setting | Value | Description |
|---------|-------|-------------|
| `VLLM_MAX_MODEL_LEN` | 32768 | Extended context length |
| `VLLM_GPU_MEM_UTIL` | 0.95 | High memory utilization |
| `VLLM_MAX_NUM_SEQS` | 32 | Higher concurrent sequences |
| `CUDA_VISIBLE_DEVICES` | 0 | Single B200D GPU |

### ARM64 Container Images

The DGX Spark uses ARM64 (aarch64) architecture. Container images are automatically selected:

- `vllm/vllm-openai:latest` → ARM64 variant
- `ghcr.io/berriai/litellm:main-latest` → Multi-arch
- `pgvector/pgvector:pg16` → Multi-arch

## Environment Files

After running `setup.sh`, these files are created:

- `../../../helm/rivault/env.mini-rivault` - RiVault configuration
- `../../../umrs/docker/.env` - UMRS configuration
- `./env.local` - Demo environment variables

## Service Endpoints

| Service | URL | Description |
|---------|-----|-------------|
| Gateway | http://localhost:8080 | Unified platform API |
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

# Chat with extended context
r4 chat "Summarize this long document..." --max-tokens 8192

# Interactive chat
r4 chat -i

# List models
r4 model list
```

## System Models (Pre-registered)

The following models are registered as system-level models via `seed_system_models.sh`.
All are freely downloadable (no authentication required):

| Model | Parameters | VRAM | DGX Sparks | Use Case |
|-------|------------|------|------------|----------|
| `Qwen/Qwen3-235B-A22B` | 235B MoE (22B active) | ~120GB FP8 | **2x** (TP=2) | Flagship reasoning, thinking mode, agentic, 100+ languages |
| `Qwen/Qwen2.5-72B-Instruct` | 72B dense | ~72GB FP8 | **1x** | General-purpose, coding, reasoning |
| `Qwen/Qwen3-32B` | 32B dense | ~64GB BF16 | **1x** | Thinking/non-thinking mode, balanced |
| `Qwen/Qwen2.5-Coder-32B-Instruct` | 32B dense | ~64GB BF16 | **1x** | Code generation specialist |
| `prism-ml/Bonsai-8B-unpacked` | 8B dense | ~16GB BF16 | **1x** | Ultra-efficient, fast inference |
| `zai-org/GLM-4.7-Flash` | 30B MoE (3B active) | ~62GB BF16 | **1x** | Strongest 30B class, agentic reasoning, tool calling |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | 32B dense | ~64GB BF16 | **1x** | Reasoning distilled from 671B R1, outperforms o1-mini |
| `Qwen/Qwen3-30B-A3B` | 30B MoE (3B active) | ~60GB BF16 | **1x** | Lightweight thinking, high throughput, agentic |
| `unsloth/GLM-5.1-GGUF` | 754B MoE (1-bit GGUF) | ~206GB | **2x** (TP=2) | Next-gen agentic, SWE-Bench Pro SOTA, long-horizon tool use |

### Register System Models

```bash
# Preview what will be registered
./seed_system_models.sh --dry-run

# Register all system models in UMRS
./seed_system_models.sh

# Register and download models from HuggingFace
./seed_system_models.sh --download --storage-root /shared/models

# RCCS cloud uses the site-configured model root directly
./seed_system_models.sh --download --storage-root <shared-fs>/umrs_models
```

## Additional Model Recommendations

Other models that run well on DGX Spark:

| Model | Parameters | Memory Usage | Use Case |
|-------|------------|--------------|----------|
| `Qwen/Qwen3-32B` | 32B dense | ~64GB BF16 | Strong reasoning and coding |
| `Qwen/Qwen3-30B-A3B` | 30B MoE | ~60GB BF16 | Lightweight thinking, high throughput |
| `zai-org/GLM-4.7-Flash` | 30B MoE | ~62GB BF16 | Latest GLM family model that fits DGX Spark |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | 32B dense | ~64GB BF16 | DeepSeek-style reasoning in a practical footprint |

## Customization

### Use a Different Model

```bash
# GLM-4.7-Flash
./setup.sh --model zai-org/GLM-4.7-Flash

# Qwen 72B
./setup.sh --model Qwen/Qwen2.5-72B-Instruct

# Or edit rivault/env.mini-rivault directly
VLLM_MODEL=zai-org/GLM-4.7-Flash
```

### Adjust Memory Utilization

Edit `../../../helm/rivault/env.mini-rivault`:

```bash
# DGX Spark can use higher utilization
VLLM_GPU_MEM_UTIL=0.95

# For multi-model serving, reduce per-model allocation
VLLM_GPU_MEM_UTIL=0.45
```

### Extended Context Length

```bash
# Leverage full context capability
VLLM_MAX_MODEL_LEN=131072  # For models supporting 128K context
```

### Change Ports

Edit the respective environment files or use environment variables:

```bash
export GATEWAY_PORT=9080
./start.sh
```

## Troubleshooting

### ARM64 Image Issues

If you encounter image pull errors:

```bash
# Verify architecture
uname -m  # Should show aarch64

# Pull with explicit platform
docker pull --platform linux/arm64 vllm/vllm-openai:latest
```

### GPU Memory Shows [N/A]

This is **expected** on DGX Spark due to unified memory architecture:

```bash
# nvidia-smi may show:
# NVIDIA GB10, [N/A]

# This is normal - Grace-Blackwell uses unified CPU+GPU memory
# The setup script handles this automatically
```

The DGX Spark shares its 128GB memory pool between CPU and GPU dynamically,
so traditional GPU memory reporting doesn't apply.

### Memory Errors with Large Models

```bash
# Reduce context length
VLLM_MAX_MODEL_LEN=16384 ./start.sh

# Or use quantized models
./setup.sh --model meta-llama/Llama-3.1-70B-Instruct-FP8
```

### Grace CPU Optimizations

DGX Spark uses NVIDIA Grace CPU with high memory bandwidth:

```bash
# Optimize for Grace's memory architecture
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

## Comparison with Workstation Profile

| Feature | Workstation | DGX Spark |
|---------|-------------|-----------|
| Architecture | x86_64 | ARM64 (Grace) |
| GPU Memory | 24-80GB | 128GB |
| Max Model | ~32B | ~70B+ |
| Context Length | 4096-8192 | 32768-131072 |
| Concurrent Requests | 4-8 | 16-32 |
| Memory Utilization | 0.90 | 0.95 |
