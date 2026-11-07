# 10. Vision

R4 supports vision model inference through BentoML-backed endpoints. This enables tasks like image segmentation (SAM3), feature extraction (MAE), and other computer vision workflows.

## Quick Start

```bash
# Register a vision model
r4 model register sam3 \
  --path /shared/models/sam3 \
  --type vision \
  --backend bentoml \
  --service-kind bentoml_vision

# Provision a vision endpoint
r4 endpoint up --model sam3 --backend bentoml --service-kind bentoml_vision --wait

# Submit an inference task
r4 vision submit cell.png --model sam3 --adapter sam3

# Check status
r4 vision status <task-id> --model sam3

# Download results
r4 vision get <task-id> --model sam3 --output results/
```

## Vision Commands

### Submit

```bash
r4 vision submit <image> \
  --model <name> \
  --adapter <adapter-name> \
  --config KEY=VALUE \
  --config-json '{"param": "value"}' \
  --key <storage-key> \
  --output-bucket <bucket> \
  --endpoint-id <job-id>
```

### Status

```bash
r4 vision status <task-id> --model <name>
```

### Get Results

```bash
r4 vision get <task-id> --model <name> --output ./results/
```

### Cancel

```bash
r4 vision cancel <task-id> --model <name>
```

## Supported Adapters

| Adapter | Model | Description |
|---------|-------|-------------|
| `sam3` | SAM3 | Segment Anything Model 3 — image segmentation |
| `hmae` | H-MAE | Hierarchical Masked Autoencoder — feature extraction |
| Custom | User-defined | Any BentoML-compatible vision service |

## Vision Endpoint Configuration

Vision endpoints use the BentoML backend instead of vLLM:

```bash
r4 endpoint up --model sam3 \
  --backend bentoml \
  --service-kind bentoml_vision \
  --gpu-count 1
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `VISION_REQUIRE_PRESIGNED_URLS=true` | Enforce presigned URL access |
| `TRANSFORMERS_OFFLINE=1` | No internet access from inference containers |

## Data Flow

1. User submits image via `r4 vision submit`
2. Image is uploaded to UMRS S3 storage
3. UMRS generates a presigned URL
4. BentoML service accesses the image via presigned URL
5. Results are written back to S3
6. User downloads results via `r4 vision get`

> **Security:** Vision inference containers are offline. All file access uses presigned URLs. No direct internet access is permitted.

## Python SDK

```python
from r4 import Client

client = Client()

# Submit vision task
task = client.vision.submit(
    "cell.png",
    model="sam3",
    adapter="sam3",
    config={"threshold": 0.5},
)

# Check status
status = client.vision.status(task.task_id, model="sam3")

# Get results
results = client.vision.get(task.task_id, model="sam3")
```

## Interactive UI

`r4 vision ui` launches a local Gradio inference UI for a vision model —
submit tasks and inspect results from the browser:

```bash
r4 vision ui --model sam3
```

## Next Steps

- [Chapter 11 — Multi-Source Routing](11-sources.md)
- [Chapter 6 — Endpoints](06-endpoints.md)
