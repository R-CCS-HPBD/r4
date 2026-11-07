# Demo 07: Knowledge-Enhanced Inference & Research Iteration

Ask questions grounded in the project knowledge store, generate a
graph-grounded research plan, start the autonomous research loop, and
exercise the `r4 agent`, `r4 search`, and `r4 team` surfaces.

## What It Shows

- `r4 project ask` — custom-knowledge enhanced inference with citations
- `r4 project plan` — graph-grounded research plan
- `r4 project findings / run --approve` — research iteration loop
- `r4 project export` — portable `.kb` knowledge bundle
- `r4 agent sessions / tools` — agent session and tool inspection
- `r4 team hardware` — hardware profile discovery
- `r4 search` — multi-step codebase search

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

- Run `00_onboarding` first to provision the endpoint.
- Run `03_projects_models` first to build the project knowledge store
  (`r4 project import` + `r4 project init`) — `ask`, `plan`, and `run`
  ground themselves in that knowledge.

## Detailed Step-by-Step (verified 2026-08-27)

1. Knowledge-enhanced inference
   ```bash
   r4 project ask "…"              # corpus-cited answer (uses your graph)
   ```
2. Plan
   ```bash
   r4 project plan "…"             # graph-grounded research plan
   r4 project findings
   ```
3. Research iteration loop
   ```bash
   r4 project loop start --approve --workflow r4-complex-dag-demo # UMRS loop on Demo 06's workflow; with several registered and none named the server asks you to choose
   ```
   Note: on a first run it answered `Steps: 1 | Sources: 0 | Confidence: 0%` —
   sufficient proof that the lane works even for an empty corpus.
4. Knowledge bundle export
   ```bash
   r4 project export               # .kb bundle path returned
   ```
5. Agent surfaces
   ```bash
   r4 agent sessions / tools --search rag
   r4 team hardware
   r4 search "…"                   # (code-oriented search)
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Sources: 0, Confidence: 0%` | corpus empty in this project namespace | ingest first (demo 03, demo 08); empty is NOT an error path |
| `UMRS knowledge package is required` | umrs client missing | `uv pip install -e ./umrs/src` |
