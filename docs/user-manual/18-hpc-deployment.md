# 18. HPC Deployment

Deploy R4 on HPC clusters with Slurm and Singularity/Apptainer.

## Architecture

HPC deployment uses a **hybrid architecture**:

- **Login node:** Runs Gateway, UMRS server, PostgreSQL, MinIO (as Podman containers or native services)
- **Compute nodes:** Run Mini-RiVault instances via Slurm + Singularity

```
┌──────────────── Login Node ────────────────────┐
│  Gateway (Podman)  │  UMRS (native)            │
│  PostgreSQL (Podman) │ MinIO (Podman)           │
└────────────────────┼───────────────────────────┘
                     │ sbatch
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
┌─────────┐   ┌─────────┐   ┌─────────┐
│ Compute │   │ Compute │   │ Compute │
│  Node 1 │   │  Node 2 │   │  Node 3 │
│ (Mini-  │   │ (Mini-  │   │ (Mini-  │
│ RiVault)│   │ RiVault)│   │ RiVault)│
└─────────┘   └─────────┘   └─────────┘
```

## Prerequisites

- Slurm cluster with GPU partitions
- Munge authentication
- Singularity/Apptainer or Podman on compute nodes
- GPU drivers (CUDA ≥ 12.0) with CDI (Container Device Interface)
- Podman on login node (for infra containers)
- Shared filesystem between login and compute nodes

## Deployment tooling

The R-CCS sites are deployed with Ansible (`deploy/ansible`, with one
inventory per site). That deployment is kept in the development repository
and is not part of the public tree (2026-11-07). With the public repository:

- one host: [deploy/standalone](../../deploy/standalone/README.md);
- an HPC login node: the UMRS template `umrs/deploy/templates/login-node/`
  (walked through in the [HPC Quick Start](../r4/admin-guide/hpc-quickstart.md));
- one front-end serving several clusters: the UMRS template
  `umrs/deploy/templates/frontend-multi-hpc/`.

Wrap the steps below in your own configuration management for managed hosts.

## Manual Deployment

### 1. Infrastructure Setup

```bash
cd umrs/deploy/<site-profile>

# Initialize infrastructure (PostgreSQL, MinIO)
./init-setup.sh

# Start infrastructure containers
podman-compose up -d
```

### 2. UMRS Server

```bash
# Install UMRS in a virtual environment
python -m pip install -e ./r4-contracts -e ./umrs/src

# Configure site
cp site.yaml.example site.yaml
vim site.yaml

# Start UMRS server (native, for Slurm CLI access)
umrs-server --site-config site.yaml
```

### 3. Gateway

```bash
# Start Gateway containers
cd gateway/compose
docker-compose -f gateway.yml up -d
```

### 4. User Enrollment

```bash
# The token embeds this site's public gateway URL (2026-10-03), so the user
# needs only `r4 auth login --api-key TOKEN`:
export UMRS_PUBLIC_GATEWAY_URL="https://gateway.example.org/site"   # your URL

# Add users
umrs-server admin user add alice --project team-alpha --issue-token

# Set Slurm account for per-user accounting
umrs-server admin user set-slurm-account alice my-slurm-account
```

## Site Configuration (`site.yaml`)

```yaml
server:
  host: 0.0.0.0
  port: 8000

database:
  url: postgresql://user:pass@localhost:5432/umrs

storage:
  endpoint: http://localhost:9000
  access_key: minioadmin
  secret_key: minioadmin

orchestrator:
  backend: slurm
  slurm:
    partition: gpu
    time: "02:00:00"
    gpus: 1
    cpus: 8

network:
  litellm_port: 11434
  litellm_master_key: ""  # From .env

models:
  base_path: /shared/models
```

## Dual-Cluster Deployment (front-end)

For bridging two clusters (cluster A with a login-node stack, cluster B with
GPU compute only):

```
Cluster A (login)           front-end            Cluster B (compute)
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ Gateway         │   │ Gateway         │   │ Slurm + GPU     │
│ UMRS Server     │◄──│ SSH Tunnel      │──▶│ Mini-RiVault    │
│ PostgreSQL      │   │ Pool Config     │   │                 │
│ MinIO           │   │                 │   │                 │
└─────────────────┘   └─────────────────┘   └─────────────────┘
```

The UMRS template `umrs/deploy/templates/frontend-multi-hpc/` shows this topology.

## Validation

```bash
# Run the HPC validation harness
./tests/run_hpc_tests.sh

# Or use the site-specific harness
cd umrs/deploy/<site-profile>
./validate_harness.sh --case text
```

## Per-User Slurm Accounting

Every endpoint provisioning job uses the requesting user's Slurm account:

```bash
# Admin: Set user's Slurm account
umrs-server admin user set-slurm-account alice project-account

# User must have account configured before provisioning
r4 endpoint up --model my-qwen --wait
```

The Gateway forwards `X-User-ID` to UMRS, which passes `--account=` to sbatch.

## Singularity Container Build

```bash
cd deploy/singularity
singularity build genai-mini-rivault.sif genai-mini-rivault.def
```

## Next Steps

- [Chapter 19 — Kubernetes Deployment](19-k8s-deployment.md)
- [Chapter 20 — User & Access Management](20-user-management.md)
