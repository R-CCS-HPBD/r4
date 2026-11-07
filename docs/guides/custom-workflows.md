# Custom Workflow Guide

> **Stale in parts (dated note 2026-11-04).**
> - **Container sections.** This guide still describes a `Dockerfile` that is auto-built on upload and registry images the server pulls. Both were removed for uploaded code by [ADR-074](../adr/074-uploaded-step-code-runs-sandboxed.md) D-7 (2026-09-25). A step image must be the platform step image or an admin allow-listed one, and steps run with `--network=none` on the site's HPC lane.
> - **Your own image.** Self-service images are decided in [ADR-110](../adr/110-self-service-step-images.md) (not landed yet), and so are model calls from steps ([ADR-109](../adr/109-vision-service-and-routed-endpoints-for-steps.md) D-5).
> - **Until the rewrite (ADR-110 slice I5).** Treat the container sections below as history.

This guide explains how to create, containerize, and run custom workflows
on the R4 Platform.

## Overview

A **workflow** is a JSON file (`workflow.json`) that defines one or more
**steps**. Each step is a Python class that inherits from `Step` and
implements a `run()` method. Workflows are uploaded to the UMRS server
and executed either in-process or inside a container.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Step** | A Python class that processes data. Each step has an `entrypoint` (e.g., `my_step:MyStep`) pointing to the module and class. |
| **Workflow** | A `workflow.json` file that defines steps, their order, and configuration. |
| **routing_hints** | Per-step metadata that tells the executor which container image and GPU to use. |
| **Dockerfile** | An optional file in the workflow directory. When present, the server auto-builds a container image. |
| **auto_trigger** | When `true`, the workflow starts automatically when data is uploaded to the bucket. |

## Creating a Custom Workflow

### Step 1: Create the Workflow Directory

```bash
mkdir my_workflow
cd my_workflow
```

### Step 2: Write the Step Code

Create `my_step.py`:

```python
from umrs.pipeline_sdk import Step, StepContext, StepResult

class MyStep(Step):
    name = "my_step"

    def setup(self, config: dict) -> None:
        self.threshold = config.get("threshold", 0.5)

    def run(self, ctx: StepContext) -> StepResult:
        output_keys = []
        for key in ctx.input_keys:
            data = ctx.download_bytes(key)
            processed = self._process(data)
            out_key = f"output/{key}"
            ctx.upload_bytes(out_key, processed)
            output_keys.append(out_key)
        return StepResult(output_keys=output_keys)

    def _process(self, data: bytes) -> bytes:
        # Your processing logic here
        return data.upper()
```

### Step 3: Write the Workflow Definition

Create `workflow.json`:

```json
{
    "name": "my-workflow",
    "version": "1.0",
    "description": "My custom workflow",
    "source": {
        "type": "uploaded",
        "path": "."
    },
    "requirements": ["numpy"],
    "auto_trigger": true,
    "steps": [
        {
            "name": "my_step",
            "entrypoint": "my_step:MyStep",
            "depends_on": [],
            "config": {
                "threshold": 0.5
            }
        }
    ],
    "output": {
        "return_type": "file",
        "embedding_vector_size": 0
    }
}
```

### Step 4: (Optional) Add a Dockerfile

If your workflow needs specific dependencies (e.g., CUDA-enabled OpenCV,
PyTorch, custom libraries), add a `Dockerfile` to the workflow directory:

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir opencv-python-headless numpy

# Copy workflow files
COPY . /workspace
WORKDIR /workspace
```

When `umrs upload-workflow` detects a `Dockerfile`, it:

1. Extracts the workflow zip as before.
2. Runs `podman build -t <workflow-name>:latest` in the extracted directory.
3. Injects `routing_hints.image` into `workflow.json` for all steps that
   do not already declare an image override.

### Step 5: Upload the Workflow

```bash
umrs upload-workflow . --name my-workflow
```

### Step 6: Run the Workflow

```bash
# Upload data to the bucket
umrs data dir-upload ./data my-bucket

# Run the workflow
umrs run-workflow my-workflow --bucket my-bucket

# Download results
umrs data download my-bucket results/ --force-overwrite
```

## Container Image Strategies

### Option 1: Dockerfile-in-Workflow (Recommended)

Include a `Dockerfile` in your workflow directory. The server
auto-builds the image on upload.

**Best for:** Most workflows. Simple, self-contained, reproducible.

```
my_workflow/
  workflow.json
  my_step.py
  Dockerfile
```

### Option 2: Pre-built Image Reference

Build the image locally, push to a registry, and reference it in
`workflow.json`:

```json
{
    "steps": [
        {
            "name": "my_step",
            "entrypoint": "my_step:MyStep",
            "depends_on": [],
            "routing_hints": {
                "image": "registry.example.com/my-workflow:latest",
                "gpu": true
            }
        }
    ]
}
```

**Best for:** Advanced users with private registries. Avoids server-side
build time.

### Option 3: Mixed Mode

Both mechanisms can coexist in the same workflow. Steps with
`routing_hints.image` use that specific image. Steps without any image
hint run in-process (the default `InProcessExecutor`).

```json
{
    "steps": [
        {
            "name": "preprocess",
            "entrypoint": "preprocess:PreprocessStep",
            "depends_on": [],
            "routing_hints": {
                "image": "registry.example.com/preprocess:latest"
            }
        },
        {
            "name": "gpu_step",
            "entrypoint": "gpu_step:GpuStep",
            "depends_on": ["preprocess"],
            "routing_hints": {
                "image": "gpu-image:latest",
                "gpu": true
            }
        },
        {
            "name": "postprocess",
            "entrypoint": "postprocess:PostprocessStep",
            "depends_on": ["gpu_step"]
        }
    ]
}
```

## GPU Support

To use GPU in your workflow step:

1. **Add `routing_hints.gpu: true`** to the step definition:

```json
{
    "name": "gpu_step",
    "entrypoint": "gpu_step:GpuStep",
    "routing_hints": {
        "gpu": true
    }
}
```

2. **Use a CUDA-enabled base image** in your Dockerfile:

```dockerfile
FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3-pip ffmpeg
RUN pip3 install opencv-python-headless numpy torch

COPY . /workspace
WORKDIR /workspace
```

3. **Check for CUDA in your step code:**

```python
def _have_cuda() -> bool:
    try:
        import cv2
        return cv2.cuda.getCudaEnabledDeviceCount() > 0
    except Exception:
        return False
```

## Auto-Trigger

When `"auto_trigger": true` is set in `workflow.json`, the workflow
starts automatically when data is uploaded to the bucket. This
eliminates the need for a manual `run-workflow` step.

```json
{
    "name": "my-workflow",
    "auto_trigger": true,
    "steps": [...]
}
```

## Workflow JSON Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique workflow name |
| `version` | string | Version string (e.g., "1.0") |
| `description` | string | Human-readable description |
| `source.type` | string | Always "uploaded" |
| `source.path` | string | Path to step code (usually ".") |
| `requirements` | array | Python packages to install |
| `auto_trigger` | boolean | Auto-run on data upload |
| `steps` | array | List of step definitions |
| `output.return_type` | string | "file" or "embedding" |
| `output.embedding_vector_size` | number | Vector size for embeddings |

### Step Definition

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique step name |
| `entrypoint` | string | Module:Class (e.g., "my_step:MyStep") |
| `config` | object | Step-specific configuration |
| `routing_hints.image` | string | Container image to use |
| `routing_hints.gpu` | boolean | Whether to attach GPU |
| `depends_on` | array | Steps that must complete first |

## Examples

### Example 1: Simple Text Processing

```
text_workflow/
  workflow.json
  text_step.py
```

### Example 2: GPU Video Analysis

```
video_workflow/
  workflow.json
  video_step.py
  Dockerfile
```

### Example 3: Multi-Step DAG

```
dag_workflow/
  workflow.json
  step1.py
  step2.py
  step3.py
  Dockerfile
```

## Troubleshooting

### "No module named 'my_step'"

The workflow source directory is not on `sys.path`. Make sure
`source.path` in `workflow.json` points to the directory containing your
step code (usually `.`).

### "podman build failed"

Check the Dockerfile syntax. Common issues:
- Missing `FROM` line
- Package not available in base image
- `COPY` path doesn't match workflow directory structure

### "Container image not found"

The image specified in `routing_hints.image` doesn't exist on the
server. Either:
- Add a `Dockerfile` to auto-build the image
- Push the image to a registry the server can access

### "GPU not available"

The step has `routing_hints.gpu: true` but no GPU endpoint is
provisioned. Either:
- Set `routing_hints.gpu: false` to run on CPU
- Provision a GPU endpoint: `umrs endpoint up --model <model> --gpu-count 1`
