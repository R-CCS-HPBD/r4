# Server Deployment

Multi-GPU server deployment for teams and production workloads.

## Requirements

- **Hardware**: 2-8 GPUs (NVIDIA A100/H100 or AMD MI250X/MI300X recommended)
- **Memory**: 256GB+ RAM
- **Storage**: 500GB+ fast SSD (NVMe preferred)
- **OS**: Ubuntu 22.04 LTS

## Features vs Workstation

| Feature | Workstation | Server |
|---------|-------------|--------|
| GPUs | 1 | 2-8 |
| Tensor Parallel | No | Yes |
| Pipeline Parallel | No | Yes |
| Model Size | 7B-13B | 13B-70B+ |
| Concurrent Requests | 4 | 16-64 |
| High Availability | No | Optional |

## Quick Start

```bash
# Setup (one-time)
./setup.sh

# Start services
./start.sh

# Run demo
./demo.sh

# Stop services
./stop.sh
```

## GPU Configuration

Edit `env.server` after setup:

```bash
# For 4x A100 40GB with 70B model
VLLM_TENSOR_PARALLEL_SIZE=4
VLLM_PIPELINE_PARALLEL_SIZE=1
VLLM_MAX_MODEL_LEN=8192
VLLM_MAX_NUM_SEQS=32
```

### Parallelism Strategies

- **Tensor Parallelism (TP)**: Splits model layers across GPUs. Use for large models.
- **Pipeline Parallelism (PP)**: Splits model stages across GPUs. Use for very large models.

| Model Size | GPUs | Recommended |
|------------|------|-------------|
| 7B-13B | 1-2 | TP=1 or TP=2 |
| 30B-34B | 2-4 | TP=2-4 |
| 70B | 4-8 | TP=4-8 |
| 70B+ | 8+ | TP=4, PP=2 |

## Model Recommendations

| Use Case | Model | GPU Memory (Total) |
|----------|-------|-------------------|
| General purpose | Qwen2.5-7B-Instruct | 16GB |
| Better quality | Qwen2.5-32B-Instruct | 80GB |
| Best quality | Qwen2.5-72B-Instruct | 160GB |
| Code-focused | DeepSeek-Coder-V2-Instruct | 80GB |

## Environment Variables

Server-specific settings (in `env.server`):

```bash
# Multi-GPU settings
VLLM_TENSOR_PARALLEL_SIZE=4
VLLM_PIPELINE_PARALLEL_SIZE=1

# Higher concurrency for server
VLLM_MAX_NUM_SEQS=32
VLLM_MAX_MODEL_LEN=8192

# Multiple workers
UMRS_WORKERS=4

# Gateway auth / routing
GATEWAY_ENVIRONMENT=production
JWT_SECRET=<generated-by-setup>
GATEWAY_LITELLM_SERVICE_KEY=<matches-LiteLLM-key>

# Production database
POSTGRES_MAX_CONNECTIONS=100
```

## Monitoring

Server deployment includes enhanced monitoring:

```bash
# Check GPU utilization across all GPUs
nvidia-smi dmon -s um

# View logs
docker compose -f docker-compose.server.yaml logs -f

# Prometheus metrics
curl http://localhost:8001/metrics
```

## Troubleshooting

### NCCL Errors

```bash
# Ensure NCCL can use all GPUs
export NCCL_DEBUG=INFO
export NCCL_IB_DISABLE=0  # If InfiniBand available
```

### Out of Memory

```bash
# Reduce batch size and context
VLLM_MAX_NUM_SEQS=16
VLLM_MAX_MODEL_LEN=4096

# Enable KV cache quantization
VLLM_KV_CACHE_DTYPE=fp8
```

### GPU Assignment

```bash
# Use specific GPUs only
CUDA_VISIBLE_DEVICES=0,1,2,3
```
