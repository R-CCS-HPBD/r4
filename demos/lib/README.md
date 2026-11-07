# Shared Libraries

This directory contains shared shell libraries used by demo and deployment scripts.

## runtime.sh

Container runtime detection and abstraction library that supports both Docker and Podman.

### Usage

Source the library in your bash script:

```bash
#!/bin/bash
source "$(dirname "$0")/lib/runtime.sh"

# Initialize all runtime detection (container, compose, GPU)
init_container_runtime

# Or call individual detection functions
detect_container_runtime
detect_compose_cmd
configure_gpu_flags
```

### Exported Variables

After sourcing and calling `init_container_runtime()` or individual detection functions:

| Variable | Description | Example Values |
|----------|-------------|----------------|
| `CONTAINER_RUNTIME` | Container runtime command | `podman`, `docker` |
| `COMPOSE_CMD` | Compose command | `podman compose`, `docker compose` |
| `GPU_FLAG` | GPU passthrough flag | `--device nvidia.com/gpu=all`, `--gpus all` |
| `GPU_MODE` | How GPU access works | `cdi`, `docker-gpus`, `hooks`, `direct` |

### Functions

| Function | Description |
|----------|-------------|
| `detect_container_runtime` | Detect podman or docker (prefers podman) |
| `detect_compose_cmd` | Detect compose command (podman compose, podman-compose, docker compose) |
| `configure_gpu_flags` | Configure GPU passthrough flags based on runtime and available methods |
| `check_container_gpu` | Verify GPU access works with detected runtime |
| `init_container_runtime` | Call all detection functions |

### Precedence

1. **Container Runtime**: Podman > Docker
2. **Compose Command**: `podman compose` > `podman-compose` > `docker compose` > `docker-compose`
3. **GPU Mode**: CDI > docker-gpus > hooks > direct

### Example

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source runtime library
source "$DEMOS_DIR/lib/runtime.sh"
init_container_runtime

echo "Using: $CONTAINER_RUNTIME"
echo "Compose: $COMPOSE_CMD"
echo "GPU flag: $GPU_FLAG"

# Use the detected runtime
$COMPOSE_CMD up -d
$CONTAINER_RUNTIME run $GPU_FLAG nvidia/cuda:12.1-base nvidia-smi
```

### Environment Variables

You can override automatic detection:

```bash
# Force Docker even if Podman is available
export CONTAINER_RUNTIME=docker
export COMPOSE_CMD="docker compose"

# Then source the library
source lib/runtime.sh
# Detection functions will preserve existing values
```
