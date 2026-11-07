---
name: workflow_ops
description: "Registers, runs, monitors, and authors UMRS workflow bundles (registered server pipelines). Use for workflow, pipeline, automate, trigger, dag, orchestration tasks."
allowed-tools: "workflow_list workflow_describe workflow_upload workflow_plan job_commit workflow_run workflow_trigger workflow_status workflow_wait job_wait_any workflow_results workflow_cancel workflow_approve_gate workflow_save_definition workflow_replay lab_catalog_search search_agentic"
metadata:
  x-r4-keywords: "workflow, pipeline, automate, trigger, execution, n8n, dag, orchestrat"
  x-r4-dependencies: "platform_ops"
---

You manage UMRS Workflows (registered server pipelines).
{{contract:r4_contracts.agent_tool_playbook:WORKFLOW_AGENT_GUIDANCE}}
Lifecycle: workflow_list → workflow_describe → workflow_run →
workflow_wait/status → workflow_results → workflow_cancel.
Compute that costs (GPU steps, long runs, many runs): `workflow_plan`
first — it runs nothing and answers lane, partition, GPU steps and an
upper-bound wall time — show the user that plan, then `job_commit` it.
A project that requires plans refuses `workflow_run` with
`plan_required`; a stale or expired plan is refused: plan again.
Return structured JSON tool results; do not paraphrase errors as plain text.
Manual-approval gate steps pause a run; approve them with workflow_approve_gate.
Save a good invocation with workflow_save_definition so workflow_replay
re-executes it exactly without re-deriving parameters.

When the user asks you to BUILD a workflow file set:
{{contract:r4_contracts.agent_tool_playbook:WORKFLOW_AUTHORING_AGENT_GUIDANCE}}

Authoring aids in this skill directory:
- Complete, copyable bundle example (workflow.json + params):
  See [references/workflow-bundle.md](references/workflow-bundle.md)

## Authoring feedback loop (always run it)

1. Draft the bundle (workflow.json + params) from the reference template.
   It must have a `description`, and every step a `depends_on` list (`[]`
   for a step that starts the run). Without them the upload is refused.
   Declare run parameters in `parameter_schema`.
2. `r4 workflow lint <bundle-dir>`: fix every error it lists (warnings are
   advice). Then `workflow_upload`, then `workflow_describe` to confirm the
   registration and that the step DAG matches what you meant (names,
   depends_on).
3. Smoke-run with minimal params: `workflow_run`/`workflow_trigger`, then
   `workflow_wait` and inspect `workflow_results`. For a long run do other
   work: its end arrives as a `<job-notification>`; `job_wait_any`
   (handles=[<execution_id>, ...]) blocks on several runs at once.
4. On failure: read the structured error verbatim, fix ONE thing, re-upload
   under a bumped version, re-run. Do not scatter-fix.
5. Working invocation: `workflow_save_definition` so `workflow_replay`
   reproduces it exactly.
