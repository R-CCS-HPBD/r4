# R4 Demos

User-facing demo suite for the R4 platform. Every demo uses the real `r4` CLI.

## Directory Layout

```
demos/
├── 00_onboarding/       Deploy & onboard (profile, project, endpoint up)
├── 01_chat/             Chat & session commands
├── 02_rag/              RAG upload, query, retriever inspection
├── 03_projects_models/  Project & model management, knowledge build-up
├── 04_endpoints_jobs/   Endpoint lifecycle & durable jobs
├── 05_mcp_workflows/    MCP servers, Hatch packages (default registry), workflows
├── 06_complex_workflow/ Five-step DAG upload, trigger, status
├── 07_ask/              Knowledge-enhanced inference, research iteration, agent/search
├── 08_scientific_data_management/  Lab upload → catalog ingest → search
├── 09_vision_inference/    Vision adapters (submit/status/get + Gradio UI)
├── 99_cleanup/          Optional endpoint teardown
├── lib/                 Shared bash helpers (r4_demo.sh, runtime.sh)
├── sample_data/         Shared sample data (papers, code, datasets)
├── tiers/               Deployment tier scripts (workstation, server, hpc, dgx-spark)
├── run_all.sh           Run the full demo suite
└── README.md
```

Each demo directory contains:
- `run_demo.sh` — self-contained, idempotent demo script
- `README.md` — what it shows, prerequisites, quick start
- Input files (where needed) — co-located in the same directory

## Quick Start

```bash
# Run the full suite
bash run_all.sh --auto

# Run a single demo
bash 02_rag/run_demo.sh --auto

# Full suite + endpoint teardown
bash run_all.sh --auto --destroy
```

## Demo Index

| # | Directory | What It Shows |
|---|-----------|---------------|
| 00 | `00_onboarding/` | `r4 config`, `r4 auth`, model discovery, endpoint up with MCP/RAG/egress mounts |
| 01 | `01_chat/` | `r4 chat`, streaming, `r4 session`, companion sessions |
| 02 | `02_rag/` | `r4 rag upload`, `docs list`, `query`, retriever components |
| 03 | `03_projects_models/` | `r4 project` CRUD, `r4 model info`, knowledge build-up (`import`/`init`/`verify`/`readiness`) |
| 04 | `04_endpoints_jobs/` | `r4 endpoint` status/logs, mounted profile, `r4 job` commands |
| 05 | `05_mcp_workflows/` | `r4 mcp` servers/tools, Hatch packages + default platform registry, `r4 workflow list` |
| 06 | `06_complex_workflow/` | Five-step DAG upload, trigger, and status |
| 07 | `07_ask/` | Knowledge-enhanced `r4 project ask`/`plan`, research loop (`run --approve`), `r4 agent`/`search`/`team` |
| 08 | `08_scientific_data_management/` | Blank Project upload (15 papers + notebooks + code + CSV) → catalog → `r4 search --scope lab_only` |
| 09 | `09_vision_inference/` | `/gw/vision/*` adapters: `r4 model list --type vision`, `r4 vision submit/status/get`, interactive `r4 vision ui` (Gradio) |
| 12 | `12_dashboard_workflow/` | `r4 dashboard`+`r4 dashboard --section`, `r4 workflow show --graph/--mermaid`, `r4 workflow watch`, `r4 data intake` (2026-08 backbone visibility wave) |
| 99 | `99_cleanup/` | Optional endpoint teardown |

## Prerequisites

Start one deployment tier first (see `tiers/`):

```bash
# Workstation
cd tiers/workstation && ./start.sh

# Server
cd tiers/server && ./start.sh

# DGX Spark
cd tiers/dgx-spark && ./start.sh

# HPC
cd tiers/hpc && ./start-demo.sh
```

Then ensure `r4` is configured:

```bash
r4 config show
r4 auth status
```

## Environment Overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `R4_DEMO_PROJECT` | `demo-project` | Project ID for all demos |
| `R4_DEMO_MODEL` | (auto-detected) | Model name |
| `R4_DEMO_ENDPOINT_MOUNTS` | `1` | Set `0` to skip mount demo |
| `R4_DEMO_MCP_SRC` | `.state/mounts/mcp/demo-tools` | MCP mount source path |
| `R4_DEMO_EGRESS_DOMAINS` | `api.example.com` | Egress domain allowlist |

For HPC or remote deployments, point `R4_DEMO_MCP_SRC`,
`R4_DEMO_PROJECT_DATA_SRC`, and `R4_DEMO_RAG_INDEX_SRC` at shared paths
visible to the compute node.

## Design Rules

- All demos use real `r4` CLI commands — no curl, no mocks.
- Scripts print real failures and continue so the full command surface remains visible.
- Each demo is idempotent and safe to re-run.
- Input files needed by a demo are co-located in its directory.

## Related Docs

- `../docs/r4/quickstart.md` — end-user onboarding
- `../docs/r4/index.md` — R4 doc index