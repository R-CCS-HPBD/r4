# 16. Deployment Overview

This chapter covers the prerequisites, deployment modes, and site profiles for running the R4 platform.

## Prerequisites

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | ≥ 3.10 | UMRS, Gateway, r4 |
| PostgreSQL | ≥ 15 (with pgvector) | Metadata, RAG embeddings, durable jobs |
| MinIO or S3 | Any | File storage, model artifacts |
| Container runtime | Docker, Podman, or Singularity | Mini-RiVault containers |
| GPU driver | CUDA ≥ 12.0 | vLLM inference |
| uv | Latest | Python package management |

### Additional by Deployment Mode

| Mode | Additional Requirements |
|------|------------------------|
| HPC | Slurm, Munge, Singularity/Apptainer, CDI |
| Kubernetes | K8s cluster, Helm 3, GPU operator |
| Docker Compose | Docker Compose v2, NVIDIA container toolkit |

## Deployment Modes

| Mode | Orchestrator | Runtime | Use Case |
|------|-------------|---------|----------|
| **HPC** | Slurm | Singularity | Production research on HPC clusters |
| **Kubernetes** | K8s | containerd/Docker | Multi-tenant production |
| **Docker Compose** | Compose | Docker/Podman | Local development and testing |
| **Stand-alone** | Compose (`scheduler.kind: compose`) | Podman/Docker | The whole platform on one host for one scientist ([Chapter 29](29-standalone-deployment.md)) |

## Component Architecture

```
┌─────────────────────────────────────────────────────┐
│  Login / Frontend Node                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Gateway  │  │  UMRS    │  │  PostgreSQL      │  │
│  │ (NGINX + │  │  Server  │  │  + pgvector      │  │
│  │ Sidecar) │  │          │  │                  │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────┐                                       │
│  │  MinIO   │                                       │
│  └──────────┘                                       │
└─────────────────────────────────────────────────────┘
         │
         │ (Slurm / K8s / Compose)
         ▼
┌─────────────────────────────────────────────────────┐
│  Compute Node(s)                                    │
│  ┌──────────────────────────────────────────────┐   │
│  │         Mini-RiVault (per endpoint)          │   │
│  │  ┌────────┐  ┌─────────┐  ┌──────────────┐  │   │
│  │  │  vLLM  │  │ LiteLLM │  │ MCP Servers  │  │   │
│  │  │ :8000  │  │ :11434  │  │              │  │   │
│  │  └────────┘  └─────────┘  └──────────────┘  │   │
│  │  Pre-staged models (read-only mount)         │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## Site Profiles

| Profile | Cluster | Description |
|---------|---------|-------------|
| `rccs-cloud` | RCCS Cloud | Production HPC with Podman + Slurm |
| `umrs-front` | RCCS Frontend | Dual-cluster bridge (RCCS + AI4S) |
| `hpbd-atom` | HPBD ATOM | HPC cluster with Singularity + Slurm |
| `ai4s` | AI4S | AI4S infrastructure |
| `compose-dev` | Local | Docker Compose development |
| `k8s-cluster` | Any K8s | Kubernetes production |

## Package Layout

| Package | Install Location | Purpose |
|---------|-----------------|---------|
| `r4` | User machines | CLI and SDK |
| `r4-contracts` | User and server | Shared DTOs and validators |
| `umrs` | Server only | Orchestration and data plane |
| `gateway` | Server only | Auth, routing, policy |
| `mcp-servers` | Server only | Platform MCP tools |

### Server-Side Installation

```bash
python -m pip install -e ./r4-contracts -e ./r4 -e ./umrs/src
```

### Client-Side Installation

```bash
pip install r4
# or
./scripts/setup_r4_client.sh
```

## Quick Deployment Paths

- **Docker Compose (development):** [Chapter 17](17-docker-deployment.md)
- **HPC with Slurm:** [Chapter 18](18-hpc-deployment.md)
- **Kubernetes:** [Chapter 19](19-k8s-deployment.md)
- **Stand-alone (one host, one scientist):** [Chapter 29](29-standalone-deployment.md)

## Environment Configuration

All configurations are environment-driven. See [Appendix B — Environment Variables](appendix-b-env-vars.md) for the complete variable reference.

## Next Steps

- [Chapter 17 — Docker/Compose Deployment](17-docker-deployment.md)
- [Chapter 20 — User & Access Management](20-user-management.md)
