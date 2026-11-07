# User Environment Images

Consistent environments for running R4 Platform demos on local machines and HPC clusters.

## Quick Start

### Local Development (Docker/Podman)

```bash
# Build client image
./build-client.sh

# Run interactive client shell
./run-client.sh

# Inside container, run demos
cd /workspace/demos
./run_all.sh --auto
```

### HPC Cluster (Singularity/Apptainer)

```bash
# Build Singularity image (on login node with internet)
./build-singularity.sh

# Submit demo job
sbatch run-demo.sbatch

# Or run interactively on allocated node
srun --pty singularity shell r4-client.sif
```

### Full Stack (Docker Compose)

```bash
# Start without GPU (infrastructure only)
cd compose
docker compose -f demo-stack.yml up -d

# Start with GPU services
export MODEL_PATH=/path/to/model
docker compose -f demo-stack.yml --profile gpu up -d

# Interactive client
docker compose -f demo-stack.yml --profile interactive run client
```

## Images

| Image | Purpose | Build Command |
|-------|---------|---------------|
| `r4-client` | CLI/SDK user environment | `./build-client.sh` |
| `r4-client.sif` | Singularity for HPC | `./build-singularity.sh` |

## Environment Scenarios

### Scenario A: Local Workstation → Local Stack
```
┌─────────────────────────────────────────────────────┐
│  Local Workstation                                  │
│  ┌─────────────────────────────────────────────┐    │
│  │  Docker Compose Stack                        │    │
│  │  Gateway ─▶ UMRS ─▶ vLLM                    │    │
│  └─────────────────────────────────────────────┘    │
│                    ▲                                │
│                    │ http://localhost:8080          │
│  ┌─────────────────┴────────────────────────────┐   │
│  │  Client (native or container)               │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Scenario B: Local Workstation → Remote HPC
```
┌─────────────────────┐         ┌─────────────────────────┐
│  Local Workstation  │         │  HPC Cluster            │
│  ┌───────────────┐  │   SSH   │  ┌─────────────────┐    │
│  │ r4-client   │──┼────────▶│  │ mini-rivault    │    │
│  │ (native/Pod)  │  │ tunnel  │  │ (Singularity)   │    │
│  └───────────────┘  │         │  └─────────────────┘    │
└─────────────────────┘         └─────────────────────────┘
```

### Scenario C: Jupyter on HPC
```
┌─────────────────────────────────────────────────────────┐
│  HPC Cluster                                            │
│  ┌─────────────────┐        ┌─────────────────────────┐ │
│  │ JupyterHub      │        │ mini-rivault            │ │
│  │ (compute node)  │───────▶│ (GPU node)              │ │
│  └─────────────────┘        └─────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_URL` | `http://localhost:8080` | Platform Gateway URL |
| `MODEL_PATH` | - | Path to model directory (for GPU stack) |
| `LITELLM_MASTER_KEY` | `sk-demo-key` | API key for LiteLLM |
| `GATEWAY_PORT` | `8080` | Gateway listen port |

## File Structure

```
environments/
├── README.md                    # This file
├── build-client.sh              # Build Docker/Podman image
├── build-singularity.sh         # Build Singularity image
├── run-client.sh                # Run client container
├── run-demo.sbatch              # Slurm batch script
├── client/
│   ├── Containerfile            # Docker/Podman build
│   └── r4-client.def          # Singularity definition
└── compose/
    ├── demo-stack.yml           # Full compose stack
    └── litellm-config.yml       # LiteLLM configuration
```
