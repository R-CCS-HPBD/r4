# Demo 06: Complex Workflow

Upload, trigger, and inspect a five-step UMRS DAG workflow that demonstrates
parallel analysis branches and fan-in synthesis.

## DAG Structure

```
collect_context
      ├── methods_review   (parallel)
      └── risk_review      (parallel)
              └── merge_findings
                      └── final_report
```

## What It Shows

- `r4 workflow upload` — bundle upload
- `r4 workflow list` — registry listing
- `r4 workflow trigger` — execution trigger
- `r4 workflow status` — execution status inspection

## Input Files

- `workflow.json` — Five-step DAG definition using `{{model}}` placeholder

`run_demo.sh` replaces the `{{model}}` placeholder with the active model
before it uploads the bundle; nothing replaces it at run time, so upload the
raw file only after setting `model` (or deleting it, to let the platform
route each step).

Every step declares `"model_type": "llm"`, so UMRS resolves its endpoint from
the model/endpoint registry, preferring the step's `model` when the registry
has it, and sends that endpoint's served model name. Without it a step fell
back to the server's env (`OPENAI_BASE_URL`), which a server does not set, and
failed with "LLMStep requires api_base" (2026-10-22, ops-watch #520).

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. Lint/inspect the bundle
   ```bash
   r4 workflow lint workflow.json   # client-side draft-spec check
   r4 workflow show r4-complex-dag-demo --graph
   ```
2. Register
   ```bash
   r4 workflow upload ./workflow.zip
   # re-upload purges the server's extracted-module cache (live-logged by the runner)
   ```
3. Trigger
   ```bash
   r4 workflow trigger r4-complex-dag-demo --param bucket=r4-data
   # returns execution_id wf-…  (verified: wf-6e7a1f2ddf89, status ran)
   ```
4. Status / control
   ```bash
   r4 workflow status wf-<id>   # progress + error text
   r4 workflow watch  wf-<id>   # live re-render until terminal
   r4 workflow cancel wf-<id>   # stop execution
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Invalid workflow.json: 'source'` | missing top-level source block | add `"source":{"type":"uploaded","path":"."}` |
`"X not found in module"` | module name shadowed by another bundle of yours | rename the step file uniquely (`<wf>_step.py`) |
| Exec `running`, nothing happens | engine wedge — inspect `r4 job list --status running` | today this class matched the zombie-reaper side; see STATUS doc P5 |
