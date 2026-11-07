# Demo 03: Projects, Models & Knowledge Build-Up

Create, switch, and delete projects; inspect model metadata; then build up
the project knowledge store from sample data.

## What It Shows

- `r4 project current / list / create / switch / delete`
- `r4 model info` — model metadata inspection
- `r4 job list` — recent job listing
- `r4 project import` — ingest papers, code, notebooks, and data
- `r4 project init` — extract structured knowledge, build the knowledge graph
- `r4 project status / verify / readiness` — inspect and verify the knowledge

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. Project CRUD
   ```bash
   r4 project current / list
   r4 project create demo-project-scratch
   r4 project switch demo-project / delete demo-project-scratch
   ```
2. Model inspection
   ```bash
   r4 model info agents-a1
   r4 job list --limit 10
   ```
3. Knowledge build-up (needs `umrs[client]` python package in env):
   ```bash
   uv pip install -e ./umrs/src      # required: knowledge package
   r4 project import ../sample_data
   r4 project init
   r4 project status    # Artifacts ingested: N, Extraction, Graph: nodes/edges
   r4 project verify "What does the knowledge say about …"
   r4 project readiness
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `UMRS knowledge package is required` | knowledge primitives missing from the client env | `uv pip install -e ./umrs/src` |
| `Import … failed` | no sample files in path | point at `../sample_data/` from the repo root |
| `Graph: 0 nodes, 0 edges` after init | init calls the AI model — needs inference working | verify `r4 chat` first |
