# ADR-021: Workflow Model Unification

**Status:** Accepted
> **Date:** 2026-08-02
> **Relates to:** W2/W3 of `docs/development/the backbone proposals note (2026-07-31; archived 2026-10-19), ADR-016 (workflow CLI), W1 (durable workflow execution registry)

## Context

Four workflow model surfaces coexist in the platform, with drift between them:

1. **`umrs/workflow/models.py` — "schema v2"** (`WorkflowStep`, `WorkflowDefinition`,
   `StepType` incl. `MANUAL_GATE`, `WorkflowStatus`). Declared canonical in
   `umrs-backbone-proposals.md` (W2) but *unconsumed in production code*: no
   module under `umrs/src/umrs/` imports it outside its own package; only tests
   reference it. Functionally dead.
2. **`umrs/pipeline_sdk/workflow.py`** (`WorkflowDefinition`, `StepSpec`,
   `ResourceSpec`, `SourceSpec`, `OutputSpec`). The live `workflow.json`
   model — consumed by `WorkflowPipeline`, the step-upload path, the sandbox
   launcher, and the server runner. This is where execution actually happens.
3. **`umrs/server/workflow_runner.py`** — the production executor behind
   `POST /umrs/workflows/{name}/trigger`. Executes raw step dicts over
   `DAGScheduler.execution_levels()` with `StepExecutor`/`StepRouter`. It
   validated `StepSpec` per step for routing but, before this ADR, ignored the
   step policy fields (`max_retries`, `retry_delay`) that `StepSpec` declared.
4. **`r4/agent/models.py` + `r4-contracts`** — client-side copies
   (`WorkflowStep`, `WorkflowDefinition`, `WorkflowCatalogItem`) used for agent
   planning and wire DTOs. These are client-owned mirrors, not UMRS models.

Two executors honour different policy semantics today:

| Executor | Retry | Per-step timeout |
|----------|-------|------------------|
| `pipeline_sdk/dag.py` (`WorkflowPipeline` path) | enforced (`max_retries`, `retry_delay`) | none (field absent) |
| `server/workflow_runner.py` (production API path) | **not enforced** | none |

The original W2 framing ("canonical model = `workflow/` schema v2") predates the
discovery that schema v2 is dead code. Elevating it would require migrating the
step-upload path, the sandbox launcher, `WorkflowPipeline`, and the server
runner to a model no production path currently exercises — a high-risk,
behaviour-changing rewrite for zero user-visible gain.

## Decision

1. **Canonical execution model = `umrs/pipeline_sdk/workflow.py`.**
   `WorkflowDefinition`/`StepSpec`/`ResourceSpec` is the single model both
   executors consume. `workflow.json` remains the authoring format;
   `pipeline_sdk` is both its schema and its compiler (already true today).
2. **`umrs/workflow/models.py` is legacy, scheduled for removal.**
   It stays importable for backward compatibility but is no longer referenced
   by design documents as "canonical". Removal follows the additive-first
   mutation policy: mark deprecated, verify zero importers (including tests),
   delete in a separate change. `MANUAL_GATE` (its one unique feature) is
   re-specified as a StepSpec-driven gate when W5 lands.
3. **Two executors, one policy vocabulary.**
   Per-step policy fields are normalised on the `StepSpec` names:
   `max_retries` (int, 0 = no retries), `retry_delay` (seconds, float),
   `timeout_seconds` (float, soft deadline, `None` = no limit). The legacy
   schema-v2 names (`retry_count`, `retry_delay_seconds`) are not carried
   forward; they exist only in the dead model.
4. **Enforcement semantics (W3).**
   - *Retry*: both executors (`dag.execute` and `server/workflow_runner`)
     re-run a failed step up to `max_retries` times with `retry_delay`
     backoff. Backoff honours the execution `cancel_requested` flag.
   - *Deadline*: `timeout_seconds` is a **soft** deadline — the step is
     failed at the deadline but the underlying thread is not interrupted
     (CPython cannot kill threads safely); the late result is discarded.
     Hard interruption requires the subprocess executor and is out of scope.
   - *Terminal-only side effects*: routing-log failure annotation and the
     flaky-endpoint failure EWMA are fed only on the *final* attempt's
     failure; intermediate attempts are invisible to routing feedback
     (pre-W3 semantics preserved per attempt == per step previously).
   - Attempt counts are recorded in `steps[].extra` (`attempt`,
     `max_attempts`, `last_error` in flight; `attempts` on success) so
     `r4 workflow status`/detail surfaces retry history without a schema
     change to `ExecutionRecord`/`StepProgress`.
5. **Execution registry is orthogonal.** W1's durable
   `PgWorkflowExecutionRegistry` persists records regardless of which model
   the definition used; no coupling introduced.
6. **r4/client models are out of scope.** `r4/agent/models.py` and
   `r4-contracts` DTOs remain client-owned mirrors. No UMRS change propagates
   into them; guardrail §8 of the proposals doc (routes/DTOs additive-only)
   is preserved.

## Consequences

### Positive

- Production trigger path now honours the retry/timeout fields users could
  already declare in `workflow.json` — the schema stops lying.
- One documented policy vocabulary for both executors; future executors
  (Slurm durable path) adopt the same three fields.
- `StepSpec.timeout_seconds` is default-omitted in `to_dict()`, so existing
  workflow.json fixtures and registry payloads round-trip byte-stable.

### Negative / deferred

- The code-level facade from the original W2 framing ("one canonical class,
  two thin executors") is intentionally *not* done: model unification is
  semantic (names + enforcement), not a class refactor. A future increment may
  extract a shared `StepPolicy` value object if a third executor appears.
- Soft deadlines can leak a worker thread until the step function returns
  (documented in `call_with_deadline`). Acceptable for I/O-bound steps;
  CPU-bound runaway steps still need the subprocess executor.
- `umrs/workflow/models.py` remains in-tree until the removal change lands.

### Validation

- `umrs/tests/test_workflow_step_policy.py` — StepSpec field round-trip,
  `call_with_deadline` semantics, runner retry (success-after-retries,
  exhaustion, default-no-retry), runner deadline (fast-fail, retried
  timeout), plus existing runner/dag regression suites.

## Amendment 1 (2026-10-02): the upload contract — a description and an explicit flow graph

**Decision (maintainer request, 2026-10-02: "make the description of the
workflow and the flow graph mandatory fields when uploading a workflow").**
A `workflow.json` is accepted for upload only if:

1. `description` is a non-blank string saying what the workflow does, what
   it needs and what it produces;
2. **every** step declares `depends_on`: the steps it waits for, or `[]` for
   a step that starts the run. The names must be steps of this workflow,
   never the step itself, with no cycle.

There is one implementation, `r4_contracts.workflow_package.workflow_package_problems`,
with two callers:

- UMRS `server/file_handling._package_check` runs it before the
  `WorkflowDefinition` parse. An error refuses the upload with HTTP 400 and
  names every problem at once, each with how to fix it. Warnings go into the
  upload notice.
- r4 runs it before sending (`WorkflowClient._prepare_upload_archive`, for
  directories and zips) and in `r4 workflow lint <bundle>`, which prints
  compiler-style lines and still lints knowledge-indexing drafts as before.

The structural keys an upload always needed (`name`, `source`, `steps`, a
step's `entrypoint`) are reported in the same words, together with the
lowercase enum values for `source.type` and `execution_mode`. A UMRS test
pins those lists to the enums. Warnings never refuse an upload:

- `{{ params.x }}` used in a prompt that no `parameter_schema` declares;
- a placeholder nothing fills in at run time;
- the legacy `execution.parallel` shorthand.

**Why.** Before this rule, a workflow with no `depends_on` ran as a
declaration-order chain that nobody had written down. So the console's
definition card could only say "runs in declaration order", and agents
copying an example could not tell an independent step from a dependent one.
An explicit graph makes the graph that is drawn the graph that runs. The
description is the first thing users and their agents read (catalog rows,
the card, `workflow_describe`). Several examples were wrong: the agent
reference template, the user manual, and the `scientific-figure-understanding`
config example. They were refused at upload or misrouted at run time, so the
contract and the docs were fixed together.

**Compatibility.** The runner is unchanged. Registered workflows with no
`depends_on`, or with `execution.parallel`, keep running. Only new uploads
must spell the graph out. Repo workflow files were converted without
changing what runs:

- no edges at all → a chain in declaration order;
- some edges → the steps without one are roots;
- `execution.parallel` → the shorthand expanded.

`StepSpec.from_dict` now accepts a gate step without an `entrypoint`. The
docs and the runner already allowed it; only the parser refused the
documented example. `PipelineBuilder` always emits `depends_on`, requires a
description, and now writes the lowercase enum values the runner reads. It
used to write the enum names (`LOCAL_PATH`, `IN_PROCESS`), which the parser
refuses, so its output was never uploadable as it stood.

**Validation.**

- `r4-contracts/tests/test_workflow_package.py`
- `umrs/tests/test_upload_workflow_contract.py`, with the existing upload
  fixtures updated
- `umrs/tests/test_pipeline_builder.py::TestUploadContract`
- `r4/tests/test_workflow_upload_contract.py`, which also checks that the
  workflow_ops reference example meets the contract
