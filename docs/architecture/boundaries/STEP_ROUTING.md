# Step-Level Routing Contract

This note documents the routing layer that sits between
`workflow_runner` and the per-step `Step.execute()` call. It is a
companion to [INTEGRATION_POLICY.md](INTEGRATION_POLICY.md) and follows
the UMRS-First boundary rule (routing is owned by UMRS; Gateway and
`r4` SDK are passive forwarders / clients).

## Why a router

Before this slice, every step in a workflow inherited a single
`api_base` / `model` from process-level environment (e.g.
`OPENAI_BASE_URL`, `R4_GATEWAY_URL`). That works for single-modal
workflows but breaks the moment a workflow mixes vision + text + tool
calling because no single endpoint satisfies all three.

The **StepRouter** (`umrs.server.step_router.StepRouter`) resolves each
step independently to a `(api_base, api_key, model, endpoint_id)`
tuple, using capability hints declared on the `StepSpec`.

## Step hints (workflow.json)

`StepSpec` (in `umrs.pipeline_sdk.workflow`) gained these optional
fields — all default to empty / `None` so existing workflows are
unchanged on the wire:

| Field | Type | Meaning |
|---|---|---|
| `requires` | `list[str]` | Capability tags the endpoint must satisfy. |
| `model` | `str` | Pin a specific model name (still must satisfy `requires`). |
| `lora` | `str` | Optional LoRA adapter name (passed through unchanged). |
| `model_type` | `"llm"` \| `"vision"` \| `"embedding"` | Coarse model class. |
| `routing_hints` | `dict` | Free-form bag (e.g. `{"owner": "system"}`). |

### Capability tag vocabulary

The router currently understands the tags listed in
`KNOWN_CAPABILITY_TAGS`:

| Tag | Maps to |
|---|---|
| `vision` | `ModelCapabilities.supports_vision` |
| `tool_calling` | `ModelCapabilities.supports_tool_calling` |
| `structured_output` | `ModelCapabilities.supports_structured_output` |
| `embedding` | `ModelType == "embedding"` |
| `gpu` | always satisfied (informational) |

Unknown tags do **not** raise — they emit a warning at validation
time so that workflow authors can introduce new tags incrementally
without coordinating a registry-side schema bump.

## Resolution order

For each step the router tries the following sources, in order, and
returns the first match:

1. **Mock mode** — if `UMRS_STEP_ROUTER_MOCK=1`, returns a static
   `RoutingDecision(resolved_via="mock")`. Used by E2E tests.
2. **Explicit `config`** — if the step's `config` already pins
   `api_base` (and optionally `api_key` / `model`), the router does
   *not* touch it (`resolved_via="explicit_config"`). This preserves
   backwards compatibility with existing workflows.
3. **Registry-backed match** — `build_registry_backed_router()` looks
   up `ModelCapabilities` in the model registry and the active
   endpoint registry. Candidates are ordered by tier
   (`system → project → user`); the first **warm** endpoint matching
   the step's `requires` + `model_type` wins
   (`resolved_via="registry_capability"`).
4. **Env fallback** — if no registry hit and the step did not declare
   any hints, fall back to `OPENAI_BASE_URL` / `R4_GATEWAY_URL`
   (`resolved_via="env_fallback"`). Used by single-modal workflows
   that never opted into routing.

If a step **did** declare hints but none of (1)–(3) match, the runner
fails the step with `StepRoutingError` rather than silently falling
through to env. This is the **strict-mode invariant**: declaring
`requires` is a contract.

## Runner behaviour

`run_workflow_record(...)` writes a per-step audit entry into
`record.results["_routing"][step_name]` with the full decision dict:

```json
{
  "api_base": "http://endpoint-vd:8000/v1",
  "api_key": null,
  "model": "Qwen2.5-VL-7B",
  "endpoint_id": "vlm-system",
  "resolved_via": "registry_capability",
  "routed_capabilities": ["vision"],
  "lora": null,
  "model_type": "vision"
}
```

The runner also injects the resolved `api_base`, `api_key`, `model`,
and merged `headers` into the step's `config` via `setdefault` —
existing config wins, so an explicitly-pinned step is never
clobbered.

A failure in step N with `StepRoutingError` is recorded the same way
any execution failure is: the per-step result is set to `failed`,
the workflow record transitions to `failed`, and downstream steps
that depend on it are skipped.

## Why no new endpoint-kind enum?

Reusing `ModelCapabilities` (already part of the model-registry
contract) means:

* No additional sync between registry data and routing logic.
* User-tier and project-tier endpoints participate in routing
  without admin involvement.
* Adding a new modality (e.g. audio) is a registry-side capability
  flag, not a router code change.

If we ever need first-class endpoint *kinds* (e.g. distinguishing
"vLLM serving" from "Triton serving"), that belongs on the
endpoint registry side, not as a parallel enum on `StepSpec`.

## Public API stability

* `WorkflowPipeline` and `Step` ABCs are unchanged.
* Gateway `/gw/*` HTTP routes are unchanged (the new
  `?capabilities=` and `?model_type=` query params on
  `GET /umrs/endpoints/active` are *additive* and ignored when
  absent).
* `r4` SDK / CLI surface is unchanged.
* Existing workflow.json files round-trip byte-stably (default
  values are omitted from `to_dict()`).

## Parallel execution

The runner schedules steps in waves: each wave is a maximal set of
steps whose `depends_on` parents have all completed. Steps in the
same wave run on a thread pool; steps across waves are serialised by
the dependency edges.

The wave index of a step is recorded in `_routing[<step>].wave_index`
so post-mortem tools can group steps by their concurrent batch.

### Pool size

`UMRS_RUNNER_MAX_PARALLEL_STEPS` (default `8`) caps the pool. Set to
`1` to force fully serial execution; this is useful for debugging
race-condition suspicions and for environments that cannot share GPUs
across requests.

### Failure semantics

When a step in a wave fails:

1. **Sibling steps are not cancelled.** The runner waits for the wave
   barrier before reacting to failures. This avoids partial-write
   artifacts: any blobs the step wrote before crashing remain
   consistent with what `record.results['blobs']` reports.
2. The workflow record is marked `failed` after the wave drains.
3. Steps in later waves are *not* executed. They are recorded in
   `record.steps[<name>]` with `status="skipped"` and a corresponding
   `_routing[<name>] = {"resolved_via": "skipped", "wave_index": N}`
   entry, so the routing log stays keyed identically to the step
   progress map.

`StepRoutingError` failures (declared `requires` cannot be matched in
the registry) are recorded the same way as runtime exceptions, with
`_routing[<name>] = {"resolved_via": "routing_error", "error": ...}`.

### Thread-safety contract for step authors

* `StepContext.config`, `parent_results`, and `metadata` are
  per-step. **Do not** mutate `metadata` expecting it to be visible
  to a sibling step in the same wave — siblings are independent by
  DAG construction; use explicit `depends_on` if you need ordering.
* The in-process S3 shim (`ctx.s3.put_object` / `get_object`) is
  thread-safe; concurrent writers do not lose data.
* Step classes are loaded under a process-wide import lock to avoid
  Python's import-system deadlock when two threads simultaneously
  import the same package tree. Step `setup()` and `run()` themselves
  run with no global lock and may execute on different threads
  across runs.

## Execution affinity

When a workflow has a multi-step DAG and a downstream step has more
than one capability-satisfying endpoint to choose from, the runner
biases selection toward the same endpoint that step's parents landed
on. This is **best-effort**: affinity is a *tiebreaker*, never a
constraint. If the parent's endpoint is no longer in the candidate
set (drained, drifted scope, expired in the registry) the step is
routed normally and falls back to declaration order.

### Why

Co-locating dependent steps on the same mini-RiVault instance keeps
KV-cache, RAG indices, and any in-process state warm across the
hand-off. It also simplifies operator reasoning when multiple
identical endpoints are running for capacity reasons — by default a
DAG sticks to one of them.

### Where it applies

Affinity is consulted **only** in the registry-capability resolution
path (`resolved_via == "registry_capability"`). The mock, explicit
`api_base`, and env-fallback paths are unaffected — those decisions
are already fully constrained by the workflow author or operator.

### State model

Affinity state is **per execution**, kept in the runner's local frame
and discarded when the workflow finishes. There is no cross-execution
or cross-workflow stickiness; two unrelated executions of the same
DAG may pick different endpoints.

For each successful step, the runner records
`step_name -> RoutingDecision.endpoint_id` in a dict guarded by
`state_lock`. Before resolving a dependent step, the runner reads
the entries for that step's `depends_on` parents and passes them to
`StepRouter.resolve(..., affinity_hints=...)`. The router scores
each viable candidate by the count of parents whose recorded
endpoint id equals the candidate's id, then picks the highest score
(ties broken by the existing scope/declaration order).

### Routing log

The `_routing[<step>]` entry now includes two additional fields
emitted on every routing decision (defaults are falsy so existing
consumers are unaffected):

* `affinity_used: bool` — `True` iff at least one parent's endpoint
  matched the chosen candidate.
* `affinity_matched_parents: list[str]` — the parent step names
  whose endpoint id equals the chosen one. Empty list when affinity
  did not influence the decision.

### Opt-out

Workflow authors can disable affinity for a single step via the
existing `routing_hints` map:

```json
{
  "name": "child",
  "depends_on": ["parent"],
  "requires": ["vision"],
  "routing_hints": { "affinity": "off" }
}
```

`"off"`, `"disabled"`, and `false` are all accepted; any other value
keeps affinity enabled (forward-compatible with future modes such as
`"strict"`).

### Backwards compatibility

* `RoutingDecision.to_dict()` adds `affinity_used` and
  `affinity_matched_parents`; both default to falsy so existing
  parsers that key off truthy values keep working.
* `StepRouter.resolve(..., affinity_hints=None)` is the default;
  callers that don't pass the new kwarg behave exactly as before.
* `workflow.json` is unchanged: `routing_hints.affinity` reuses the
  existing free-form hint dict.

## Load-aware scoring

Within a single scope tier, the router now scores capability-matching
candidates by combining affinity with reported endpoint load. The
endpoint with the highest score wins; ties fall back to declaration
order.

```text
total_score = (w_aff * affinity_matches) - (w_load * load_norm)
```

### Scope tier is still a hard gate

Load awareness operates **only within a tier**. A saturated `system`
endpoint still beats an idle `project` endpoint, because tier is
policy (cheapest-shared-infra preference) and not a tiebreaker. To
intentionally route around a hot system endpoint, take it out of the
registry rather than rely on load to spill into the project tier.

### Reading load from the registry

The router reads load signals out of the endpoint dict the registry
hands back, with safe defaults so endpoints without instrumentation
score 0. Lookup precedence:

1. `endpoint["load_norm"]` — pre-normalised float in `[0, ∞)`.
2. `endpoint["metadata"]["load_norm"]` — same, on the metadata dict.
3. `metadata.active_requests / metadata.max_concurrency`
   (`max_concurrency` defaults to 1).
4. `metadata.queue_depth / metadata.max_concurrency`.
5. Otherwise `0.0`.

Values may exceed `1.0` if a backend reports over-subscription; the
penalty grows proportionally instead of clamping, so a 2x-loaded
endpoint is correctly preferred-against versus a 1x-loaded one.

### Weights

Two env knobs tune the tradeoff. Defaults are intentionally
conservative so affinity dominates equal-load decisions and load only
swings the choice when one endpoint is materially busier.

| Env var | Default | Effect |
| --- | --- | --- |
| `UMRS_ROUTER_AFFINITY_WEIGHT` | `1.0` | Weight per matched parent. |
| `UMRS_ROUTER_LOAD_WEIGHT` | `0.5` | Penalty per unit `load_norm`. |

Negative values are clamped to 0. Setting `UMRS_ROUTER_LOAD_WEIGHT=0`
disables the load term (router falls back to affinity-only — i.e. the
Slice 3 behaviour).

### Routing log

The `_routing[<step>]` entry now carries two more fields:

* `load_score: float` — the chosen endpoint's `load_norm` at decision
  time.
* `total_score: float` — the combined `w_aff * affinity_matches -
  w_load * load_norm` value the router used to pick this endpoint.

Both default to `0.0` for paths that do not score (mock / explicit /
env_fallback), so existing log consumers ignore them safely.

### Backwards compatibility

* Endpoints without any load metadata score `0`, leaving the legacy
  declaration-order behaviour unchanged.
* No new public arguments to `StepRouter.resolve()`.
* No `workflow.json` schema change.

## Latency-aware scoring (Slice 5)

The score now folds in two more endpoint signals when they are
reported, plus a soft cap that kicks in near saturation. The full
formula inside a scope tier is:

```text
total_score =
    w_aff   * affinity_matches
  - w_load  * load_norm
  - w_lat   * latency_norm
  - w_q     * queue_norm
  - w_load  * softcap_penalty(load_norm)
```

### New signals

| Source | What it measures | Default reference |
| --- | --- | --- |
| `metadata.avg_latency_ms` | Recent average request latency. | `UMRS_ROUTER_LATENCY_REF_MS` (1000ms) |
| `metadata.queue_time_ms` | Time requests wait before being served. | `UMRS_ROUTER_QUEUE_REF_MS` (500ms) |

Both are normalised by their reference and then weighted. Missing /
non-numeric / zero values yield `0.0`, so endpoints without telemetry
pay no penalty (legacy behaviour preserved).

### Soft cap

The soft cap adds an extra penalty above an explicit utilisation
threshold so the router strongly prefers cooler endpoints once a
candidate crosses the saturation boundary:

```text
softcap_penalty(load_norm) =
    0                                      if load_norm <= softcap
    (load_norm - softcap) * factor         otherwise
```

Defaults: `softcap = 0.9`, `factor = 5.0`. The penalty is multiplied
by `w_load` together with the linear term so
`UMRS_ROUTER_LOAD_WEIGHT=0` truly disables the entire load family.

### Weights

| Env var | Default | Effect |
| --- | --- | --- |
| `UMRS_ROUTER_AFFINITY_WEIGHT` | `1.0` | Weight per matched parent. |
| `UMRS_ROUTER_LOAD_WEIGHT` | `0.5` | Penalty per unit `load_norm` (also scales the soft cap). |
| `UMRS_ROUTER_LATENCY_WEIGHT` | `0.3` | Penalty per unit `latency_norm`. |
| `UMRS_ROUTER_QUEUE_WEIGHT` | `0.3` | Penalty per unit `queue_norm`. |
| `UMRS_ROUTER_LATENCY_REF_MS` | `1000` | Latency normalisation reference. |
| `UMRS_ROUTER_QUEUE_REF_MS` | `500` | Queue-time normalisation reference. |
| `UMRS_ROUTER_LOAD_SOFTCAP` | `0.9` | Utilisation threshold for soft cap. |
| `UMRS_ROUTER_SOFTCAP_PENALTY` | `5.0` | Soft-cap slope above the threshold. |

Negative values are clamped to 0.

### Routing log additions

`_routing[<step>]` now also carries:

* `latency_score: float` — `avg_latency_ms / latency_ref_ms`.
* `queue_score: float` — `queue_time_ms / queue_ref_ms`.

All default to `0.0` so consumers that key off truthy values are
unaffected.

## Outcome telemetry

After every step finishes, the runner annotates the routing log entry
with realised execution data:

* `exec_time_ms: float` — wall-clock time the step's `run()` took.
* `outcome: "succeeded" | "failed"` — terminal status of the step.

This closes the loop between routing decisions and observed
performance: post-mortem tooling can now compare the predicted
`latency_score` against the realised `exec_time_ms` per endpoint, and
future slices can feed aggregated outcomes back into the router as a
learned latency signal.




## Feedback-aware scoring (Slice 6)

Slice 6 closes the loop the previous slice opened: the realised
`exec_time_ms` from each successful step is fed back into a
process-wide store and consumed by the router on its next decision.

### How it works

1. **Record** — after a step succeeds, the runner calls
   `get_feedback_store().record(decision.endpoint_id, exec_time_ms)`.
   Failures are intentionally NOT recorded — a crashing step says
   nothing useful about the endpoint's healthy throughput.
2. **Smooth** — the store maintains an exponentially-weighted moving
   average per `endpoint_id`. New samples blend with prior history at
   `UMRS_ROUTER_FEEDBACK_EWMA_ALPHA` (default `0.3`).
3. **Score** — at resolve time the router asks the store for a
   normalised value (`ewma / UMRS_ROUTER_FEEDBACK_REF_MS`, default
   `1000`ms reference) and subtracts
   `UMRS_ROUTER_FEEDBACK_WEIGHT * feedback_norm` (default weight
   `0.2`) from the candidate's total score.

The score formula now reads:

```
total = w_aff   * aff_score
      − w_load  * load_norm
      − w_lat   * latency_norm
      − w_q     * queue_norm
      − w_fb    * feedback_norm           (Slice 6)
      − w_load  * softcap_penalty(load_norm)
```

`feedback_score` is exposed on `RoutingDecision` and surfaces in
`_routing[<step>]` for diagnostics.

### Cold endpoints, failures, and resets

* **Cold endpoints** (no record yet) score `0.0` — they pay no
  penalty, so the router naturally tries them.
* **Failures** are not recorded; an endpoint that ran a buggy step
  isn't punished.
* `reset_feedback_store()` is exposed for tests; production code does
  not need it.

### Knobs

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `UMRS_ROUTER_FEEDBACK_WEIGHT`     | `0.2`   | Scale on the feedback term (0 disables). |
| `UMRS_ROUTER_FEEDBACK_EWMA_ALPHA` | `0.3`   | Smoothing factor; clamped to `(0, 1]`. |
| `UMRS_ROUTER_FEEDBACK_REF_MS`     | `1000`  | Reference latency the EWMA is divided by. |

### Scope

The store is **process-local**. A single UMRS server instance
benefits; in a horizontally-scaled deployment each instance learns
independently. Future work can swap the in-process dict + lock for a
shared backend (Redis / Postgres) without touching call sites — the
`EndpointFeedbackStore` interface is the abstraction boundary.

## Adaptive routing (Slice 7)

Slice 7 turns the multi-signal scorer into an **adaptive scheduler**:
the router now reacts to failures, treats the registry-reported
latency as a prior rather than a parallel signal, and explores so a
lucky-early endpoint cannot lock in dominance.

### Failure signal

`EndpointFeedbackStore` now tracks a second EWMA: a 0/1 failure
indicator. The runner calls `record_failure(endpoint_id)` whenever a
step's `run()` raises, and `record(endpoint_id, exec_time_ms)` mixes
in a 0-sample on success so failure rate decays naturally as
successes accumulate.

The router consults `_endpoint_failure_norm(ep)` (range `[0, 1]`) and
subtracts `UMRS_ROUTER_FAILURE_WEIGHT * failure_norm` (default `0.5`)
from the candidate score. Failures **do not** touch the latency EWMA
— a flaky endpoint shouldn't also be marked "slow".

`failure_score` is exposed on `RoutingDecision` and surfaces in
`_routing[<step>]`.

### Latency / feedback decorrelation

Once an endpoint has any successful runs (`feedback_norm > 0`) the
registry-reported `latency_norm` is multiplied by
`UMRS_ROUTER_LATENCY_DECORR_FACTOR` (default `0.5`) before the
penalty is applied. The intent:

* `latency_norm` is the **prior** (what the registry claims).
* `feedback_norm` is the **posterior** (what we measured).

Trusting both at full strength penalises slow endpoints twice. Cold
endpoints still get the full latency term — there's no posterior yet
to override the prior. Set the factor to `1.0` to disable
decorrelation; `0.0` to ignore registry latency entirely once an
endpoint is warm.

### UCB exploration (Slice 15)

The router uses Upper Confidence Bound (UCB) scoring to balance
exploitation of known-good endpoints with exploration of less-visited
ones. The UCB bonus is integrated directly into the scoring formula:

```
ucb_bonus = C * sqrt(log(total_visits + 1) / (visits + 1))
total += w_ucb * ucb_bonus
```

Cold endpoints (0 visits) receive the maximum bonus, guaranteeing
they are tried first. As an endpoint accumulates visits, its bonus
decays, and the router converges to the deterministic best. This
replaces the earlier ε-greedy mechanism with a principled approach
that requires no random seed management.

`exploration_used` on `RoutingDecision` is always `False` under UCB
(exploration is folded into the score, not a separate random branch).

### Affinity overrides UCB

When a step matches affinity to a parent endpoint, the affinity bonus
(default weight 1.0) must outweigh the UCB exploration bonus. With
the default `UCB_WEIGHT=0.3` and `UCB_C=1.0`, this holds naturally.
Operators increasing `UCB_C` significantly should verify that
affinity still dominates when needed.

### Knobs

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `UMRS_ROUTER_FAILURE_WEIGHT`         | `0.5`  | Scale on the failure-rate term (0 disables). |
| `UMRS_ROUTER_LATENCY_DECORR_FACTOR`  | `0.5`  | Multiplier applied to registry latency once feedback exists; 1.0 disables. |
| `UMRS_ROUTER_UCB_WEIGHT`             | `0.3`  | Scale on the UCB exploration bonus; 0 disables exploration. |
| `UMRS_ROUTER_UCB_C`                  | `1.0`  | Exploration coefficient — larger values explore more aggressively. |

### What this completes

After Slice 15 the router has:

* affinity (Slice 3)
* load + softcap (Slice 4)
* latency + queue (Slice 5)
* feedback (Slice 6)
* failure-aware decisions, decorrelated signals (Slice 7)
* UCB-principled exploration with cold-start guarantee (Slice 15)

## Pluggable step execution (Slice 8)

The runner used to hard-code "instantiate the Step class in this
process and call setup → run → teardown". Slice 8 abstracts that
behind a :class:`StepExecutor` so workflows can be run with process
isolation without changing any other layer.

### Backends

| Backend | Class | Trigger | What it does |
| ------- | ----- | ------- | ------------ |
| In-process | `InProcessExecutor` | default | Runs the step in the runner's own process. Identical to legacy behaviour. |
| Subprocess | `SubprocessExecutor` | `UMRS_STEP_EXECUTOR=subprocess` | Spawns `python -m umrs.server.step_worker` per step; pickles payload → stdin, reads StepResult ← stdout. GPU memory, CUDA contexts, and any process-global state are isolated per step. |

Future backends (Singularity exec, k8s Job, gRPC to a GPU worker
pool) plug in here without touching the runner. Cluster-side
isolation remains the responsibility of UMRS's existing hybrid
orchestration layer (`ExecutionTarget` + `RuntimeWrapper`); this
slice is the **process-level** boundary inside whichever container
the runner already lives in.

### Failure semantics

All executors raise :class:`StepExecutionError(phase, message)`.
``phase`` is one of:

* ``"setup"`` — step's `setup()` raised. No routing-log outcome,
  feedback store untouched (the endpoint never saw the step).
* ``"run"`` — step's `run()` raised. Routing log gets
  `outcome="failed"` + `exec_time_ms`; feedback store records a
  failure for the endpoint (Slice 7).
* ``"worker"`` — transport error specific to the subprocess
  executor (timeout, child crash before a payload was written, bad
  pickle). Treated like a run failure for routing purposes.

This unification means the runner has a single `except` clause
regardless of which backend is wired in.

### Knobs

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `UMRS_STEP_EXECUTOR`         | `inproc`     | `inproc` or `subprocess`. Unknown values fall back to inproc with a warning. |
| `UMRS_STEP_EXECUTOR_TIMEOUT` | unset (none) | Subprocess timeout in seconds. Hung workers raise a `worker`-phase error. |
| `UMRS_STEP_WORKER_LOG`       | `WARNING`    | Log level for the worker process (stderr only). |

### Why subprocess and not threads

Threads share GPU memory, CUDA context, and Python globals. A step
that allocates GPU tensors and forgets to free them in teardown will
poison every subsequent step in the same process. Subprocess
isolation makes that impossible: once the worker exits, the kernel
reclaims its address space and the GPU context drops. Cost is a few
hundred ms of fork+import overhead per step — negligible when a step
runs vLLM inference, painful when a step is a 10ms transform.
Default stays inproc; opt in per deployment.

### What this enables

* A "tighten one screw" path to safer execution without changing
  any user-facing API.
* A clean target for the next slice: replace the local subprocess
  with a Singularity exec / Slurm step / gRPC worker — all sit
  behind the same `StepExecutor` interface.
* Failure isolation: a buggy step crashing the runner's Python
  process (segfault, OOM kill, fatal CUDA error) no longer takes
  down the whole workflow.

## Podman executor (Slice 9)

Slice 9 adds a third backend to the `StepExecutor` Protocol from
Slice 8. It runs each step inside a fresh Podman container instead
of a bare subprocess, which is the natural fit for the
"podman on Slurm compute node" topology this platform standardises on.

### Topology

The runner already lives inside a podman container that the Slurm
wrapper (`serve-model.slurm`) launched on the compute node. The
Podman executor spawns **sibling** step containers in that same
allocation by talking to the host's podman socket — *not* nested
podman, *not* a new Slurm submission. Cluster-level isolation
(Slurm, k8s, Singularity) remains UMRS hybrid-orchestration's
responsibility; the Podman executor is a per-step worker launcher,
nothing more.

```text
Slurm allocation
  └─ runner container (mini-RiVault)
       └─ podman run --rm umrs-step-<uuid>   ← one per step
            └─ python -m umrs.server.step_worker
```

### Payload transport: file-based, not stdin

`SubprocessExecutor` pipes a pickle to the child's stdin. That works
fine for a bare subprocess, but stdin handling across podman
versions and rootless/rootful + host/none combinations has too many
edge cases to rely on. The Podman executor instead:

1. Creates a tmpdir on the host (`tempfile.mkdtemp(prefix="umrs-step-")`).
2. Writes the same pickle payload to `<tmpdir>/input.pkl`.
3. Mounts the tmpdir at `/umrs-step:Z` and sets
   `UMRS_STEP_INPUT=/umrs-step/input.pkl`,
   `UMRS_STEP_OUTPUT=/umrs-step/output.pkl` on the container.
4. The worker reads input from the env-var path and writes output
   next to it, leaving stdout/stderr free for diagnostic logs from
   the container runtime.
5. Parent reads `output.pkl`, then `shutil.rmtree`s the tmpdir
   (unless `UMRS_STEP_KEEP_WORKDIR=1`).

The pickle schema and `{status, phase, message, result}` reply
contract are identical to the subprocess executor, so adding more
backends later only needs to choose a transport.

### Failure semantics

Same three phases as Slice 8: `setup`, `run`, `worker`. A few
specific Podman cases that resolve to `worker`:

| Trigger | Phase | Detail |
|---|---|---|
| `subprocess.TimeoutExpired` | `worker` | Best-effort `podman rm -f <name>` then raise; "timed out" in message. |
| `podman` not on PATH (`FileNotFoundError`) | `worker` | "could not launch podman: …" |
| Container exits without writing `output.pkl` | `worker` | Captures truncated stderr in message. |
| Reply pickle is malformed | `worker` | "could not decode podman worker output: …" |

`run`-phase failures still feed the Slice-7 endpoint failure-rate
EWMA exactly as before — switching the executor backend does not
bypass the adaptive routing feedback loop.

### Knobs

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `UMRS_STEP_EXECUTOR=podman` | — | Selects this backend. |
| `UMRS_STEP_IMAGE`           | — (required) | Container image carrying the umrs Python package + the step's deps. `build_step_executor()` raises if unset. |
| `UMRS_STEP_PODMAN_BIN`      | `podman` | Override the binary path (e.g. rootless install). |
| `UMRS_STEP_PODMAN_NETWORK`  | `host`   | Network mode. `host` lets the worker reach MinIO/S3. Set to `none` for steps that operate purely on `parent_results`. |
| `UMRS_STEP_PODMAN_EXTRA_ARGS` | unset | Whitespace-split escape hatch injected before the image (e.g. `--pull=missing --label foo=bar`). |
| `UMRS_STEP_GPU`             | `0`      | `1` adds `--device nvidia.com/gpu=all`. CDI must be configured on the host. |
| `UMRS_STEP_KEEP_WORKDIR`    | `0`      | `1` preserves the host tmpdir on exit for post-mortem inspection. |
| `UMRS_STEP_EXECUTOR_TIMEOUT` | unset | Hard timeout in seconds (shared with `SubprocessExecutor`). |

### Image contract

A step image must contain:

* the `umrs` Python package (so `python -m umrs.server.step_worker`
  resolves);
* `boto3` (so the worker can rebuild an S3 client matching the
  parent's MinIO endpoint);
* whatever the user step's `setup`/`run` imports.

Recommended pattern: build one base image equal to the runner's
image + step-specific extras layered on top. Per-step image
overrides are deferred to a later slice.

### Why not reuse `DockerWrapper`?

`umrs/orchestrator/hybrid/wrappers/docker.py` already builds
`podman run` commands — but for a different layer. That wrapper is
the **inference-server lifecycle manager** (vLLM + LiteLLM inside
mini-rivault). The Podman executor is a **per-step worker launcher**
that spawns short-lived containers inside the inference container's
allocation. Conflating the two would inverse-violate the project's
"do not duplicate UMRS functionality" rule from the other side.
Same binary; different layer; deliberately separate code paths.

### What's deferred (and why)

* **Persistent worker pool / image warming** — Slice 13. The current
  cost is a podman cold-start per step (~hundreds of ms), which is
  fine for vLLM-bound steps and painful for fast transforms. Address
  when the workload demands it, not before.
* **Streaming step outputs** — needs a separate channel; not part of
  the executor contract.
* **GPU isolation enforcement / cgroup tuning** — UMRS hybrid-orch
  territory; we expose the `--device` flag and stop there.
* **Image registry / aliases** — workflows currently reference images
  by full reference (`example.com/foo:tag`); a curated alias table
  (`vlm` → `example.com/umrs-vlm:1.4`) is a Slice 12+ concern.

## Per-step images (Slice 10)

Slice 10 adds a single, narrow knob on top of Slice 9: each step in
a workflow can request a specific container image via
`routing_hints.image`. This is the contract:

| `routing_hints.image` value | Behaviour                                       |
| --------------------------- | ----------------------------------------------- |
| absent                      | Fall back to the executor's default image       |
| non-empty string            | Use that image for this step                    |
| empty string / whitespace   | **`setup`-phase error** (treat as misconfigured)|
| non-string (int, list, …)   | **`setup`-phase error**                         |

Empty strings are deliberately rejected: in practice they only
appear as the result of a templating bug (`"image": ""` from an
unset variable), and silently falling back masks the bug. We'd
rather fail loudly at setup than route a vision step to a generic
image.

### Workflow example

```json
{
  "name": "scientific-figure-understanding",
  "steps": [
    {
      "name": "vision",
      "entrypoint": "umrs.pipeline_sdk.templates.vision_step:VisionStep",
      "routing_hints": {"image": "example.com/umrs-vlm:cuda"}
    },
    {
      "name": "ocr",
      "entrypoint": "umrs.pipeline_sdk.templates.ocr_step:OCRStep",
      "routing_hints": {"image": "example.com/umrs-tesseract:latest"}
    },
    {
      "name": "reason",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": ["vision", "ocr"],
      "routing_hints": {"image": "example.com/umrs-vllm:gpu"}
    }
  ]
}
```

### Where validation lives

The runner is a **thin transport**: it copies
`routing_hints.image` into `StepContext.metadata` under the
executor-internal key `__executor_image__` (exposed as
`EXECUTOR_IMAGE_METADATA_KEY` in `umrs.server.step_executor`).
All validation happens inside the executor's `_resolve_image()`,
so you get the same error semantics whether the runner or some
future caller invokes the executor directly. The dunder key is
stripped from the payload sent to the worker — steps never see it.

### Executor opacity

* `PodmanExecutor` honours the override (the only one that has an
  image to override).
* `InProcessExecutor` and `SubprocessExecutor` ignore the key
  silently — they don't have an image concept. Setting
  `routing_hints.image` against them is a no-op, not an error,
  because the executor is a deployment-time choice and the workflow
  shouldn't have to know which one is configured.

### Routing log surfaces the resolved image

Every entry in `record.results["_routing"][step_name]` now carries
an `image` field — either the per-step override or the deployment
default `UMRS_STEP_IMAGE`. This makes "which image did this step
run in?" a one-line answer when debugging.

### What's deferred to later slices

* **Image alias registry** — full image refs in workflow.json are
  fine for now. Curated aliases (`vlm` → versioned image) belong
  with the model registry.
* **GPU/CPU/memory hints** beyond image — Slice 12.
* **Per-step network policy** (allowlist domains, etc.) — Slice 14.

## Object-store payloads (Slice 11)

Slice 11 adds an opt-in object-store transport for the pickle
payload that flows between the runner and the Podman worker. When
enabled and the payload exceeds a configurable threshold, the
executor uploads `input.pkl` to S3/MinIO instead of writing it to the
host bind-mount; the worker downloads it from S3, and uploads its
`output.pkl` reply back.

### Why

* `parent_results` can carry large `extra` dicts (dense
  descriptions, embeddings) — O(MBs). The bind-mount path is
  adequate for small payloads but doesn't scale to cross-node
  workers or very large intermediate results.
* The design preserves the existing local-file path so tests stay
  fast and dev machines don't need MinIO.

### Dual-mode transport

| Condition | Transport |
| --------- | --------- |
| `UMRS_STEP_PAYLOAD_S3` unset or `0` | Local file (existing) |
| S3 enabled, payload < threshold | Local file (still fast) |
| S3 enabled, payload ≥ threshold | S3 upload/download |
| Payload > hard cap | **setup-phase error** |

### Environment variables

```bash
UMRS_STEP_PAYLOAD_S3=1                   # opt-in
UMRS_STEP_PAYLOAD_THRESHOLD_BYTES=1048576  # 1 MB
UMRS_STEP_PAYLOAD_MAX_BYTES=52428800       # 50 MB hard cap
UMRS_S3_BUCKET=my-bucket                   # target bucket
UMRS_S3_PREFIX=umrs                        # key prefix
```

### S3 key layout

```text
<prefix>/<run_id>/<step_name>/<attempt>/input.pkl
<prefix>/<run_id>/<step_name>/<attempt>/output.pkl
```

`attempt` is always `0` today. The slot is reserved for a future
retry slice so the key schema won't need a migration.

### Where each concern lives

| Concern | Owner |
| ------- | ----- |
| Setting `UMRS_STEP_PAYLOAD_S3` | Operator (env) |
| Threading the hint into `ctx.metadata` | Runner |
| Threshold gating + upload/download | Executor |
| Reading/writing via S3 | Worker |
| Steps | **Never see it** |

### Metadata key

```python
EXECUTOR_PAYLOAD_S3_METADATA_KEY = "__payload_s3__"
# Shape: {"bucket": str, "prefix": str}
```

Same dunder pattern as Slice 10's `__executor_image__`. Stripped
from the payload before it reaches the worker.

### Routing log annotations

Every routing entry now includes:

```json
{
  "payload_mode": "s3" | "local",
  "payload_s3_bucket": "...",
  "payload_s3_prefix": "..."
}
```

`payload_s3_bucket` and `payload_s3_prefix` are only present when
`payload_mode` is `"s3"`.

### Failure semantics

| Failure | Phase | Affects EWMA? |
| ------- | ----- | ------------- |
| Payload exceeds hard cap | `setup` | No |
| S3 upload fails | `setup` | No |
| Worker S3 download fails | `worker` | Yes |
| Worker crashes | `run` | Yes |
| S3 reply download fails | `worker` | Yes |

### Cleanup policy

Objects are **not deleted** on success. This is deliberate:
debugging and post-mortem analysis benefit from being able to
inspect the exact payloads that flowed through a workflow. Use S3
lifecycle policies (bucket TTL) for automatic expiration.

### What's deferred

* **Streaming upload** — currently the payload is serialized to
  memory, then uploaded. Streaming would reduce peak memory but
  adds complexity without proven need.
* **SubprocessExecutor S3 mode** — payloads flow via stdin/stdout
  which works fine for co-located processes.
* **Automatic cleanup** — a later slice can add
  `UMRS_STEP_PAYLOAD_CLEANUP=1` to delete on success.
* **Persistent worker pool** — Slice 13.

---

## Slice 13 — WorkerPoolExecutor (Persistent Worker Pool)

### Motivation

`PodmanExecutor` starts a fresh container per step, paying
container startup and Python import costs every time. For
workflows with many small steps that share the same image, this
overhead dominates wall-clock time. `WorkerPoolExecutor` keeps
containers running across tasks, amortising both costs.

### Design

Workers are keyed by `(image, gpu)`. Steps with the same image
and GPU setting reuse the same container.

```
┌───────────────┐       ┌──────────────────────────────────┐
│  Executor     │──────▶│  _PooledWorker (podman run -i)   │
│  execute()    │       │  stdin  → [4B len][pickle]       │
│               │◀──────│  stdout ← [4B len][pickle]       │
└───────────────┘       └──────────────────────────────────┘
```

Communication uses a **length-prefixed pickle protocol**:

1. Executor serialises the task payload as pickle, prepends a
   4-byte big-endian length header, and writes to worker stdin.
2. Worker reads the framed message, calls `_process_task()`,
   serialises the reply, and writes it back framed to stdout.
3. Executor reads the framed reply and interprets it.

The framing helpers (`_read_framed`, `_write_framed`) live in
`step_worker.py` and are used by both `pool_main()` and
`_PooledWorker.run_task()`.

### Pool key semantics

The pool key is `(image: str, gpu: bool)`.

* Two steps with the same `(image, gpu)` reuse the same worker.
* Different image → different worker.
* Different GPU → different worker.
* Step name, model, and routing hints are **not** part of the key.

### Pool lifecycle

```
idle pool (deque per key)
         │
    ┌────▼─────┐     no idle worker
    │ acquire  │─────────────────────▶ _start_worker()
    │          │◀── idle & alive ────┐
    └────┬─────┘                     │
         │                           │
    run_task(payload)                 │
         │                           │
    ┌────▼─────┐                     │
    │ release  │─── alive ──────────►│
    │          │                     │
    │          │─── dead ──────────▶ discard
    └──────────┘
```

* **Acquire**: pop from idle deque; skip/discard dead workers;
  start a new one if none available.
* **Release**: return alive worker to idle deque; discard dead.
* **Discard**: stop the worker (close stdin, wait, rm container).
* **Shutdown**: stop all workers, clear pool and tracking list.

### Worker entry point

`step_worker.py --pool` runs `pool_main()`:

```python
def pool_main() -> int:
    while True:
        raw = _read_framed(sys.stdin.buffer)
        if not raw:
            break
        payload = pickle.loads(raw)
        reply = _process_task(payload)
        _write_framed(sys.stdout.buffer, pickle.dumps(reply))
    return 0
```

Step errors produce `{"status": "error", ...}` replies — the
worker stays alive for the next task. Only protocol-level
failures (broken pipe, crash) cause the worker to be discarded.

### Metadata stripping

Same as PodmanExecutor: `__executor_image__`,
`__executor_gpu__`, and `__payload_s3__` are stripped before
the payload is sent to the worker.

### GPU and image resolution

`WorkerPoolExecutor` has its own `_resolve_image()` and
`_resolve_gpu()` methods, following the same three-way
validation logic as PodmanExecutor:

* **Image**: metadata override → instance default
* **GPU**: metadata override (must be bool) → instance default

### Runner shutdown integration

`workflow_runner.py` calls `_try_shutdown_executor(executor)` at
all three exit points (cancel, failure, success). This is a
duck-typed call: if the executor has a `shutdown()` method, it's
called; otherwise nothing happens. This cleans up all pooled
workers when the workflow finishes.

### Configuration

```bash
UMRS_STEP_EXECUTOR=pool  # Select WorkerPoolExecutor
UMRS_STEP_IMAGE=myimg     # Default image for all steps
UMRS_STEP_GPU=1           # Default GPU setting
```

### Error handling

| Scenario | Phase | Worker reused? |
|----------|-------|----------------|
| Step class import fails | `setup` (in reply) | Yes |
| Step `run()` raises | `run` (in reply) | Yes |
| Non-bool GPU hint | `setup` (executor) | N/A |
| Pipe broken / worker crash | `worker` | No (discarded) |
| Reply unpickling fails | `worker` | No (discarded) |

### Test coverage

22 tests in `test_worker_pool.py`:

* `TestPoolProtocol` (6): single task, multi-task reuse, step
  error reply, error recovery, stop, dead-worker raises
* `TestWorkerPoolExecutor` (10): basic execute, reuse same key,
  different image/gpu → separate workers, parallel tasks,
  shutdown, dead worker replaced, step error doesn't poison,
  metadata stripped, non-bool GPU error
* `TestRunnerShutdown` (3): shutdown on success/failure, no
  shutdown without method
* `TestFramingHelpers` (3): roundtrip, empty stream, truncated

---

## Slice 12 — Per-step GPU override

### Motivation

Before Slice 12, `UMRS_STEP_GPU=1` applied to **every** Podman step
globally. A workflow mixing GPU-heavy steps (VLM inference, LLM
reasoning) with CPU-only steps (OCR, post-processing) had to run
everything on GPU or nothing.

Slice 12 adds `routing_hints.gpu` so each step can declare whether
it needs GPU access. The pattern is identical to Slices 10–11:

* Runner is a **thin transport** — lifts the hint into metadata.
* Executor **owns resolution and validation**.
* InProcess/Subprocess executors **ignore** the hint (debug-logged).

### Workflow example

```json
{
  "steps": [
    {"name": "vision", "routing_hints": {"image": "vlm:cuda", "gpu": true}},
    {"name": "ocr",    "routing_hints": {"image": "tesseract:latest", "gpu": false}},
    {"name": "reason", "routing_hints": {"image": "vllm:gpu", "gpu": true}}
  ]
}
```

### Tri-state resolution

| `routing_hints.gpu` | `UMRS_STEP_GPU` | Resolved |
| ------------------- | --------------- | -------- |
| `true`              | `0`             | **GPU on** (hint overrides) |
| `false`             | `1`             | **GPU off** (hint overrides) |
| absent              | `1`             | GPU on (env default) |
| absent              | `0` or unset    | GPU off (env default) |
| `"yes"` (non-bool)  | any             | **setup-phase error** |

The hint **overrides**, never merges with the global default. This
is enforced by `PodmanExecutor._resolve_gpu()`.

### Metadata key

```python
EXECUTOR_GPU_METADATA_KEY = "__executor_gpu__"
```

Same dunder pattern as `__executor_image__` and `__payload_s3__`.
Stripped from worker metadata before it reaches user steps.

### Routing log

Every routing entry now includes the **resolved** GPU value:

```json
{
  "gpu": true
}
```

When the hint is present, the logged value matches the hint. When
absent, it reflects the deployment-wide default (`UMRS_STEP_GPU`).

### Container device flag

When GPU is resolved to `true`, PodmanExecutor adds:

```
--device nvidia.com/gpu=all
```

This is isolated in `_build_command()` so it can be adapted to
different container runtimes (Docker `--gpus all`, Singularity
`--nv`, CDI devices) in a future slice.

### Failure semantics

| Failure | Phase | Affects EWMA? |
| ------- | ----- | ------------- |
| Non-bool hint value | `setup` | No |

### Where each concern lives

| Concern | Owner |
| ------- | ----- |
| Declaring `routing_hints.gpu` | Workflow author |
| Threading hint into `ctx.metadata` | Runner |
| Tri-state resolution + device flag | Executor |
| GPU scoring / capability filtering | Router (unchanged — `requires=["gpu"]`) |
| Steps | **Never see it** |

### What's deferred

* **GPU count** — `"gpu": 2` or `"gpu": "A100"`. Keep it binary
  for now; expand to structured GPU requests later.
* **Router GPU scoring** — GPU stays a pre-filter capability tag,
  not part of the scoring weights. Adding it to scoring too early
  creates interaction problems with load/latency.
* **Runtime-specific flags** — `--nv` (Singularity), `--gpus all`
  (Docker). Handled by extending `_build_command()` when those
  runtimes ship.
* **Persistent GPU worker pool** — Slice 13 benefits from this
  slice because pools can be keyed on `(image, gpu)` affinity.

---

## Slice 17 — Capability-aware routing

### Problem

Workflow authors need a way to *prefer* endpoints with specific
hardware or software capabilities (e.g. GPU, fast storage, NVLINK)
without hard-filtering those that lack them. The existing `requires`
list is a hard filter — the router rejects endpoints that don't
advertise the capability at all. We need a *soft* preference signal.

### Design

A new optional key `routing_hints.prefer` accepts a list of capability
tag strings or weighted capability objects:

```yaml
steps:
  embed:
    routing_hints:
      prefer: ["gpu", "fast_storage"]

  # Weighted form (backward-compatible):
  embed_v2:
    routing_hints:
      prefer:
        - {name: "gpu", weight: 1.0}
        - {name: "nvlink", weight: 0.5}
```

Both formats can be mixed in a single list.  String items are treated
as weight 1.0.

The router scores each candidate endpoint by computing the weighted
fraction of preferred tags satisfied by `endpoint.metadata`:

```
capability_score = sum(matched_weights) / sum(all_weights)   # ∈ [0, 1]
```

The total scoring formula gains a new positive term:

```
total = w_aff*aff - w_load*load - w_lat*lat - w_q*queue
      - w_fb*fb - w_fail*fail - w_load*softcap
      + w_ucb*ucb + w_cap*capability_score
```

| Env knob | Default | Meaning |
| -------- | ------- | ------- |
| `UMRS_ROUTER_CAPABILITY_WEIGHT` | `0.2` | Weight for capability preference term |

**Zero-weight disables**: setting `UMRS_ROUTER_CAPABILITY_WEIGHT=0`
reduces the capability term to zero, which makes the feature inert.

### Worker-side capability reporting

Workers respond to a ZMQ *ping* request with their capability set:

```
Client → Worker:  {"type": "ping"}
Worker → Client:  {"type": "pong", "capabilities": {"gpu": true, ...}}
```

`_RemoteWorker` caches the response with a configurable TTL:

| Env knob | Default | Meaning |
| -------- | ------- | ------- |
| `UMRS_WORKER_CAPABILITY_TTL` | `30` | Seconds to cache capability data |

### Files changed

| File | Change |
| ---- | ------ |
| `step_router.py` | `_capability_match()`, `capability_score` field on `RoutingDecision`, `w_cap` scoring term |
| `step_worker.py` | `_get_capabilities()`, ping handler in `zmq_pool_main()` |
| `step_executor.py` | `capabilities` / `_capability_ttl` fields on `_RemoteWorker`, `refresh_capabilities()` |
| `test_step_router.py` | 5 new tests |
| `test_remote_pool.py` | 2 new tests (ping, TTL caching) |

---

## Slice 18 — Multi-slot workers

### Problem

REQ/REP constrains each worker address to one in-flight request.
Workers with ample resources (CPU, GPU memory) are artificially
throttled. We need two phases:

1. **Phase 2a** — Logical slot expansion within REQ/REP.
2. **Phase 2b** — DEALER/ROUTER transport for true concurrent dispatch.

### Phase 2a — Logical slot expansion

`_acquire_worker()` now uses `count < capacity` instead of `count < 1`
where `capacity` comes from:

1. The worker's `refresh_capabilities()` response (via ping/pong).
2. Fallback to env `UMRS_REMOTE_WORKER_SLOTS` (default `1`).

| Env knob | Default | Meaning |
| -------- | ------- | ------- |
| `UMRS_REMOTE_WORKER_SLOTS` | `1` | Max concurrent tasks per worker address |

Each slot creates a new `_RemoteWorker` REQ socket to the same
address, so the per-task REQ/REP lockstep is preserved while the
pool allows N parallel requests.

### Phase 2b — DEALER/ROUTER transport

For true multiplexing a new `RouterPoolExecutor` uses a single ZMQ
ROUTER socket. Workers connect with DEALER sockets and echo a
`correlation_id` on every reply so the orchestrator can match
asynchronous replies to outstanding requests.

| Component | Socket type | Direction |
| --------- | ----------- | --------- |
| Orchestrator (`RouterPoolExecutor`) | ROUTER | bind |
| Worker (`zmq_dealer_main()`) | DEALER | connect |

Request format:
```python
{"entrypoint": "...", "config": {...}, "ctx": {...},
 "correlation_id": "uuid4"}
```

Reply format:
```python
{"status": "ok", "outputs": {...}, "correlation_id": "uuid4"}
```

A background `_recv_loop` thread in `RouterPoolExecutor` receives
all replies and resolves per-request `threading.Event` futures.

| Env knob | Default | Meaning |
| -------- | ------- | ------- |
| `UMRS_ROUTER_BIND` | `tcp://0.0.0.0:5560` | ROUTER socket bind address |
| `UMRS_WORKER_ID` | `""` (auto) | ZMQ IDENTITY for DEALER worker |

**CLI**: `step_worker.py --zmq-dealer <connect_address>`

**Factory**: `UMRS_STEP_EXECUTOR=router`

### Files changed

| File | Change |
| ---- | ------ |
| `step_executor.py` | `_get_capacity()`, multi-slot `_acquire_worker()`, `RouterPoolExecutor` |
| `step_worker.py` | `zmq_dealer_main()`, `--zmq-dealer` CLI flag |
| `test_remote_pool.py` | 2 new tests (multi-slot, single-slot blocking) |

---

## Slice 19 — GPU memory-aware slot sizing

### Problem

Static `UMRS_REMOTE_WORKER_SLOTS` wastes GPU memory (too few) or
causes OOM (too many). Workers should advertise dynamic capacity
based on available GPU memory.

### Design

Workers read GPU memory via pynvml (`_gpu_mem_info()`) and compute
slots:

```python
effective = free_mem * safety_factor
slots = floor(effective / mem_per_task)   # clamped [1, max_cap]
```

The slot count is included in the ping/pong capability payload and
refreshed by `_RemoteWorker.refresh_capabilities()`.

| Env knob | Default | Meaning |
| -------- | ------- | ------- |
| `UMRS_GPU_MEM_PER_TASK` | `4096` | MB per task estimate |
| `UMRS_GPU_MEM_SAFETY_FACTOR` | `0.9` | Fraction of free memory to use |
| `UMRS_MAX_SLOTS_CAP` | `16` | Maximum slots regardless of memory |

### Capacity smoothing

To avoid slot oscillation from memory noise:

- **Shrink**: immediate (avoids OOM).
- **Grow**: move halfway toward new capacity per refresh cycle.

### Files changed

| File | Change |
| ---- | ------ |
| `step_worker.py` | `_gpu_mem_info()`, `_compute_slots()`, included in `_get_capabilities()` |
| `step_executor.py` | `_update_capacity()` smoothing on `_RemoteWorker` |
| `test_remote_pool.py` | 2 new tests (`_compute_slots`, `_update_capacity` smoothing) |

