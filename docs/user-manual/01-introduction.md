# 1. Introduction

## What is R4?

**R4** is pronounced _"are for"_ — as in "___ are for ___".

Both blanks are intentionally left open. **You** fill them:

- _Simulations_ **are for** _materials discovery_
- _RAG pipelines_ **are for** _protein structure analysis_
- _Agentic workflows_ **are for** _climate modeling_
- _Your tools_ **are for** _your science_

R4 connects researchers' diverse, complex scientific methodologies to their specific targets — efficiently and robustly — by bridging scientific HPC systems with unified file transfer, workflow management, and AI inference.

The platform provides **flexible, customizable functions and submodules** that researchers compose to achieve their own objectives. It does not prescribe a single way of doing science; it provides the connective tissue so that each scientist's "___ are for ___" becomes a reality.

## Platform Components

| Component | Role | User-Visible? |
|-----------|------|---------------|
| **r4** (CLI & SDK) | User-facing command-line tool and Python library | Yes |
| **r4-contracts** | Shared data schemas for clients and platform tooling | Developer-facing |
| **Gateway** | Auth, routing, rate limiting, and audit logging | Indirect |
| **UMRS** | Data plane, orchestration, jobs, workflows, RAG, model registry | Indirect |
| **RiVault** | Isolated inference runtime (vLLM + LiteLLM) | No |
| **MCP Servers** | Platform and custom tool providers | Optional |

**Users only need `r4`.** Everything else is infrastructure.

## Naming Conventions

| Name | Meaning |
|------|---------|
| **R4** | The platform itself — "___ are for ___" |
| **r4** | The CLI command and Python package |
| **r4-contracts** | Shared stdlib-only DTOs, schemas, and validators |
| **RiVault** | Inference infrastructure (not user-facing) |
| **Mini-RiVault** | A per-job isolated runtime containing vLLM + LiteLLM |
| **UMRS** | Data plane and orchestration control plane |
| **Gateway** | Platform API gateway (NGINX + auth sidecar) |
| **genai** | Deployment artifact name (same project as RiVault) |

## Architecture at a Glance

```
┌───────────────────────────────────────────────────────────────┐
│                        Client Layer                           │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐   │
│  │ Jupyter │  │   r4    │  │   r4    │  │   MCP Clients   │   │
│  │Notebooks│  │   CLI   │  │   SDK   │  │                 │   │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────────┬────────┘   │
│       └────────────┴────────────┴────────────────┘            │
└────────────────────────────┬──────────────────────────────────┘
                             │
               ┌─────────────▼─────────────┐
               │     Platform Gateway      │
               │  Auth · Routing · Audit   │
               └─────────────┬─────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌────────────────┐  ┌───────────────────────────────────────┐
│     UMRS       │  │      Mini-RiVault (per endpoint)      │
│ • Jobs & Files │  │  ┌──────────┐  ┌──────────┐           │
│ • RAG Pipeline │  │  │  LiteLLM │  │   vLLM   │           │
│ • Orchestrator │  │  │  :11434  │  │  :8000   │           │
│ • Model Reg.   │  │  └──────────┘  └──────────┘           │
│ • Workflows    │  │  Pre-staged models (read-only)        │
└────────────────┘  └───────────────────────────────────────┘
```

## Deployment Modes

R4 runs on **Slurm** (HPC), **Kubernetes**, or **Docker Compose**, using standard APIs (OpenAI-compatible, S3, PostgreSQL). Inference runs on your hardware — no cloud required.

| Environment | Orchestrator | Container Runtime | Use Case |
|-------------|-------------|-------------------|----------|
| HPC / RCCS | Slurm | Singularity/Apptainer | Production research |
| Cloud / Server | Kubernetes | Docker / containerd | Multi-tenant production |
| Development | Docker Compose | Docker / Podman | Local testing |

## Endpoint Tiers

The platform supports three deployment tiers:

| Tier | Owner | Lifecycle | Egress | Use Case |
|------|-------|-----------|--------|----------|
| **System** | Admin | Warm pool (always-on) | None (offline) | Shared LLM for quick inference |
| **Project** | Project lead | On-demand | EgressGuard controlled | Team research with project data |
| **User** | Individual | On-demand | EgressGuard controlled | Personal experimentation |

## What You Get in 5 Minutes

| Goal | Commands | GPU? |
|------|----------|------|
| Full demo (chat, RAG) | `cd demos && ./run_all.sh --auto` | Yes |
| Jupyter notebook | `pip install "r4[jupyter]"`, then load the r4 magic in any notebook | Yes |
| Production deployment | `r4 auth login --gateway-url ...` + use SDK | Depends |

## Next Steps

- **Install and authenticate:** [Chapter 2 — Quick Start](02-quickstart.md)
- **Deploy infrastructure:** [Chapter 16 — Deployment Overview](16-deployment.md)
- **Explore demos:** [Chapter 22 — Demo Suite](22-demos.md)
