# Example: Workflow with a Human Approval Gate

A three-step workflow where step 2 waits for a human to approve before
the expensive downstream step runs. This mirrors production patterns
(review-before-download, approve-after-scan) — using a toy pipeline.

## Prerequisites

- A registered workspace with upload capacity: `r4 project current`
- Uploads enabled for a simple ZIP bundle (this example builds one below)

## The bundle

```bash
mkdir -p /tmp/gated-wf && cd /tmp/gated-wf

cat > workflow.json <<'JSON'
{
  "name": "gated-scan",
  "version": "0.1",
  "triggers": [],
  "steps": [
    {
      "name": "scan",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "config": {
        "system_prompt": "You scan documents for policy violations.",
        "prompt_template": "Flag what you find:\n{{ input_text }}",
        "output_schema": {"type": "object"}
      }
    },
    {
      "name": "human-review",
      "gate": true,
      "gate_timeout_seconds": 86400
    },
    {
      "name": "summarize",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": ["scan", "human-review"],
      "config": {
        "system_prompt": "Write the final sanitized summary."
      }
    }
  ]
}
JSON
```

```bash
zip -r gated-wf.zip workflow.json
r4 workflow upload gated-wf.zip --name gated-scan
```

## Run it

```bash
r4 workflow trigger gated-scan --param input_text="mention the internal host db-7b" --json
# {"status":"accepted","execution_id":"wf-a1b2c3d4e5f6"}
```

Watch progress until the gate parks:

```bash
r4 workflow status wf-a1b2c3d4e5f6
# running — steps:
#   scan:        ● completed   (attempts 1)
#   human-review:⧗ waiting_approval
```⧗

Or watch the DAG live:

```bash
r4 workflow watch wf-a1b2c3d4e5f6
```

## Decide

Approve (the downstream step executes):

```bash
r4 workflow approve wf-a1b2c3d4e5f6 human-review \
  --comment "content is fine after redaction"
# gate approved: human-review (execution wf-a1b2c3d4e5f6)
# execution status: succeeded
```

Or reject (the workflow fails with your reason):

```bash
r4 workflow reject wf-a1b2c3d4e5f6 human-review \
  --comment "contains internal hostname; rerun with sanitizer"
# gate rejected: human-review (execution wf-a1b2c3d4e5f6)
# execution status: failed: manual gate 'human-review' rejected by alice: contains internal hostname; rerun with sanitizer
```

## Check the audit trail

Every decision is written into the run's record:

```bash
r4 workflow status wf-a1b2c3d4e5f6 --json | jq '.execution.gates'
# {
#   "human-review": {
#     "step": "human-review",
#     "approved": true,
#     "approver": "alice",
#     "comment": "content is fine after redaction",
#     "decided_at": 1782220000.123
#   }
# }
```

## What *actually* happened behind the scenes

1. The runner set the step's status to `waiting_approval` and parked a
   worker thread on a per-gate event. Nothing executed during the wait.
2. Your `approve` POST released that event; the step's result carried the
   decision into the audit log and the dependents (`summarize`) ran on the
   same endpoint unless routing chose elsewhere.
3. A deadline (`gate_timeout_seconds`, default 24h) would have failed the
   step with "approval deadline of 86400s expired" if nobody decided.
4. `r4 workflow cancel` while waiting would have finalized the run as
   `cancelled` — it never wedges in the runner.

## Variations worth trying

- Add `"max_retries": 2, "retry_delay": 5` to the `scan` step and check
  `steps[].extra.attempts` in the status payload after a forced failure.
- Give only one section a trigger (`"on": "ingest"` in the workflow
  metadata). Upload a file to the catalog and observe the workflow
  starting by itself.
