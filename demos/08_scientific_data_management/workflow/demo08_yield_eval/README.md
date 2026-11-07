# demo08_yield_eval

Minimal durable experiment for Companion / CLI research loops
(`research_loop_start` / `r4 project loop start`).

When `demo08_yield_eval` is the only registered workflow, loop start picks it
automatically; otherwise pass `workflow_name="demo08_yield_eval"`
(`--workflow demo08_yield_eval`). Each experiment requires a `durable_job_id`
from the job backend.

## Upload (workstation tier running)

```bash
r4 workflow upload demos/08_scientific_data_management/workflow/demo08_yield_eval \
  --name demo08_yield_eval
r4 workflow list | grep demo08_yield_eval
```

## Smoke trigger

```bash
r4 workflow trigger demo08_yield_eval --param project_id=<your-project>
# wait / results should include durable_job_id
```

## Companion sequence

After knowledge_ingest → plan → readiness(approve):

```text
Call research_loop_start with workflow_name="demo08_yield_eval" and max_iterations=1,
then research_loop_status for the returned loop_id (findings are minted into the
knowledge graph — knowledge_show lists them).
```

Findings should cite batch-3 / 85% / 4.21 (from workflow metrics and/or ingested CSV).

## Slurm / HPC note

On sites where `step_executor.kind: slurm` and `step_executor.image` is empty
(see `umrs/deploy/*/site.yaml`), each step needs a container image or the
durable job fails with:

`SlurmStepExecutor requires a container image. Set UMRS_STEP_IMAGE or routing_hints.image.`

Set `UMRS_STEP_IMAGE` on the deploy host, or add per-step
`"routing_hints": {"image": "<registry>/umrs-step:<tag>"}` before re-upload.

## Offline unit proof (no Gateway)

```bash
pytest umrs/tests/knowledge/test_demo08_operational.py -q
```
