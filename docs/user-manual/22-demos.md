# 22. R4 Demo Suite

The canonical demo suite uses real `r4` CLI commands to walk through the full platform experience.

## Quick Start

```bash
cd demos
./run_all.sh --auto
```

## Individual Demos

Each demo is self-contained in `demos/XX_name/run_demo.sh`:

### Step 0: Onboarding

```bash
demos/00_onboarding/run_demo.sh
```

- Configure profile and authenticate
- Select or create a project
- Discover available models
- Provision an endpoint with mounts (MCP, RAG, data)
- Verify health and readiness

**Key commands:** `r4 config show`, `r4 auth status`, `r4 project`, `r4 model list`, `r4 health`, `r4 endpoint up`

### Step 1: Chat

```bash
demos/01_chat/run_demo.sh
```

- One-shot chat with streaming
- Interactive chat session
- Session management
- Launch companion TUI

**Key commands:** `r4 chat`, `r4 endpoint list`, `r4 session`

### Step 2: RAG

```bash
demos/02_rag/run_demo.sh
```

- Upload documents for RAG
- Query with semantic search
- Document management
- Retriever component discovery

**Key commands:** `r4 rag upload`, `r4 rag query`, `r4 rag docs`, `r4 rag retriever components`

### Step 3: Projects & Models

```bash
demos/03_projects_models/run_demo.sh
```

- Create and switch projects
- Model inspection
- Model lifecycle (stage, register, publish)
- Job tracking for downloads

**Key commands:** `r4 project create`, `r4 project switch`, `r4 model info`, `r4 job list`

### Step 4: Endpoints & Jobs

```bash
demos/04_endpoints_jobs/run_demo.sh
```

- Endpoint listing and status
- Endpoint logs
- Durable job inspection
- Mini-RiVault mount details

**Key commands:** `r4 endpoint list`, `r4 endpoint status`, `r4 endpoint logs`, `r4 job list`, `r4 job describe`

### Step 5: MCP & Workflows

```bash
demos/05_mcp_workflows/run_demo.sh
```

- MCP server discovery
- Hatchling package management
- Workflow listing

**Key commands:** `r4 mcp list`, `r4 mcp hatch`, `r4 workflow list`

### Step 6: Complex Workflow

```bash
demos/06_complex_workflow/run_demo.sh
```

- 5-step DAG workflow generation
- Workflow upload and trigger
- Execution status tracking

**Key commands:** `r4 workflow upload`, `r4 workflow trigger`, `r4 workflow status`

### Step 7: Ask

```bash
demos/07_ask/run_demo.sh
```

- Companion TUI sessions
- Teamup multi-agent collaboration
- Agent workflow builder
- Code search

**Key commands:** `r4 agent`, `r4 team`, `r4 search`

### Step 8: Scientific Data Management

```bash
demos/08_scientific_data_management/run_demo.sh
```

- Upload an artifact mix (or legacy CSV+PNG) into the catalog
- Wait for the DATA_INGESTION job, grep the catalog, raw-read objects
- Agentic search in `lab_only` scope

**Key commands:** `r4 data intake`, `r4 search --scope lab_only`, catalog utilities

### Step 9: Vision Inference (SAM3.1)

```bash
demos/09_vision_inference/run_demo.sh
```

- Text-prompted SAM3.1 segmentation through the Gateway vision path
- Interactive browser UI via `r4 vision ui`

**Key commands:** `r4 vision submit`, `r4 vision status`, `r4 vision get`, `r4 vision ui`

### Step 12: Dashboard & Workflow Visualization (2026-08 wave)

```bash
demos/12_dashboard_workflow/run_demo.sh
```

- Unified ops board: jobs, models, workflows, connectors in one view
- Layered ASCII + Mermaid rendering of the workflow step DAG
- Live `r4 workflow watch` of a running execution
- `r4 data intake` connector board with sync-state rows

**Key commands:** `r4 dashboard`, `r4 workflow show --graph`, `r4 workflow watch`, `r4 data intake`

### Step 99: Cleanup (Optional)

```bash
demos/99_cleanup/run_demo.sh
```

- Endpoint teardown
- Resource deprovisioning

**Key commands:** `r4 endpoint down`

## Deployment Tiers

The demo suite supports multiple deployment tiers:

| Tier | Directory | Description |
|------|-----------|-------------|
| Workstation | `demos/tiers/workstation/` | Desktop with local GPU |
| Server | `demos/tiers/server/` | Standalone server deployment |
| HPC | `demos/tiers/hpc/` | Slurm cluster deployment |
| DGX Spark | `demos/tiers/dgx-spark/` | NVIDIA DGX Spark (ARM64 + GB10) |

## Environment Overrides

For remote/HPC deployments, configure data source paths:

```bash
export R4_DEMO_MCP_SRC=/path/to/mcp-servers
export R4_DEMO_PROJECT_DATA_SRC=/path/to/project-data
export R4_DEMO_RAG_INDEX_SRC=/path/to/rag-index
```

## Demo Data

- Research papers: `demos/sample_data/papers/`
- Mount assets: `demos/.state/`

## Next Steps

- [Chapter 23 — UMRS Demos](23-umrs-demos.md)
