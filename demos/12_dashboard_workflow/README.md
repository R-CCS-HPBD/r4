# 12 — Dashboard & Workflow Visualization (2026-08 wave)

This demo exercises the backbone-visibility features shipped in the 2026-08
backbone wave: the unified `r4 dashboard`, workflow DAG rendering, live
`workflow watch`, and the `r4 data intake` board.

Prerequisites: standard r4 profile with gateway access (see `demos/00_onboarding`).

## Flow

```bash
# 1. Unified ops board — jobs, models, workflows, connectors in one view
r4 dashboard

# Section-only views
r4 dashboard --jobs
r4 dashboard --workflows

# 2. Trigger a workflow, then visualize its step DAG
r4 workflow trigger <name> --param key=value
r4 workflow show <name> --graph          # layered ASCII with icons/glyphs
r4 workflow show <name> --graph --mermaid  # Mermaid for docs/web

# 3. Live watch the execution — step statuses re-render until terminal
r4 workflow watch <execution_id> --interval 2

# 4. Intake board — connector probe + per-connector sync state
r4 data intake
```

## What's being verified

| Feature | What you should see |
|---------|---------------------|
| Dashboard overview | `system ✓ healthy` header + jobs/models/workflows/connectors sections |
| Workflow graph | `L0 ○ [JOB] ingest` style layered DAG with dependency arrows `←` |
| Mermaid output | `flowchart TD` source starting the output |
| Watch loop | A re-rendering frame showing step glyphs changing state |
| Intake board | `✓ <name>` per loadable connector, `✗` per import-failed; sync rows when syncs have run |

## After shutdown

Run one more dashboard render — workflow executions and connector sync
state survive the UMRS restart because they're Postgres-backed (W1/P2):

```bash
r4 dashboard --workflows   # entries still visible after UMRS bounce
```

## Detailed Step-by-Step (verified 2026-08-27)

1. Unified ops board
   ```bash
   r4 dashboard
   # → "R4 platform — system ✓ healthy" + counters for jobs/models/workflows/connectors
   r4 dashboard --section jobs
   ```
2. Workflow DAG rendering
   ```bash
   r4 workflow show <name> --graph
   # → L0 ○ [STEP] <step-a> / L0 ○ [STEP] <step-b>
   r4 workflow watch wf-<id>
   ```
3. Data intake view
   ```bash
   r4 data intake
   ```
4. Persistence note — executions survive UMRS restarts (Postgres durable).

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard shows "n/a" per source | sync cursors/DLQ/health per source are P2 in ROADMAP | expected — not a demo bug |
| `jobs: 0 total` | shows project-visible jobs only | nothing to fix; your execs might live elsewhere (the guide §3.5 explains the mental model) |
