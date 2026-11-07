# ADR-048 — Wrapper attempt-budget sharing contract (G-P4-6, WP-20)

**Status:** Proposed (design wave; venue legs live-owned)
- **Implements:** gap G-P4-6 (`docs/verification/gap-register.md`), 6b-7 live finding
- **Related:** ADR-045 (venue portability contract), ADR-036 (durable retry),
  ADR-047 (executor classes)

## Context

The mini-RiVault wrapper keeps an **attempt-scoped** engine-respawn budget
(`RIVAULT_ENGINE_RESPAWN_MAX`, default 3) — sound when the wrapper is the job.
The durable-jobs retry chain breaks the premise: attempt N declared terminal
does not guarantee attempt N's podman stacks released the GPU before attempt
N+1 starts on the same node (6b-7: stranded legs held ~95 GiB; manual scancel
+ row cleanup was the single sane exit). Each fresh attempt arrives with a
fresh budget, so respawn capacity **multiplies** across attempts of one job
until node memory contention kills every engine boot.

Missing invariant the gap names: the budget's lifecycle must be the **JOB**,
not the attempt.

## Decision (normative)

### D-1 — One budget per job lineage, claimed on the venue

The wrapper keeps its respawn accounting in a **node-local claim file** keyed
to the durable job id (`<job_id>.budget`), never in attempt-process env. A
respawn is allowed only after an atomic claim (`flock`) increments
`budget_used` with `budget_used < budget_total`; exhaustion refuses the
respawn leg. `budget_total` = `RIVAULT_ENGINE_RESPAWN_MAX_PER_JOB` (default:
same 3 as today's per-attempt value; site-tunable via venue extends).

Attempt N+1's wrapper **inherits what attempt N spent**: retry chains can no
longer multiply GPU re-creation capacity. Node-local because podman state is
node-local; cross-node placement is D-4's separate concern.

Claim file shape (venue-local JSON, flock-guarded):

```json
{
  "job_id": "job-...",
  "budget_total": 3,
  "budget_used": 1,
  "claims": [{"attempt": "att-...", "slurm_job": "12345", "at": "..."}]
}
```

### D-2 — Same-lineage stale-stack reaper at wrapper startup

At startup the wrapper reaps left-over containers/networks whose recorded
owner lineage is the SAME `job_id` and whose durable state is terminal for the
owning attempt (per the state DB re-pin of ADR-045 D-lineage). Reaping foreign
job lineages remains forbidden (the 6b-3 invariant: never kill other jobs'
conmon/crun). This is the intra-job hand-off: attempt N+1 cleans before it
allocates, freeing the GPU attempt N could not release in time.

Cross-attempt reaping is job-lineage-scoped precisely so concurrent different
jobs on one node stay untouched.

### D-3 — Budget exhaustion is a terminal marker, not another retry

When the job-level budget is exhausted the wrapper exits 125 (the durable
retry code today) AND records `budget_exhausted` in the job-info file. The
endpoint_provision status checker maps the marker to **terminal-failed, no
further requeue** — an engine that burned the job's whole respawn budget
must not re-enter the retry chain with a fresh GPU claim. Without D-3 the
budget gate and the retry chain fight forever.

Repo-side leg (this wave's scope ends before it): status-checker mapping +
pins belong to the implementation wave together with the wrapper change.

### D-4 — Cross-node retry placement is explicitly out of scope

A retry landing on a different node cannot see attempt N's claim file; its
stale stacks get the existing venue dead-job GC (ADR-045 venue.sh class),
which already reaps by job-id lineage across the `~/.config/containers`
hygiene rules. Extending the claim across nodes needs a shared-FS budget
store + fencing token and is a follow-up design, not folded in here.

## Consequences

- Respawn capacity per job becomes **bounded** (K total across ALL attempts),
  closing the 6b-7 multiplicative class without touching different-job
  isolation.
- The wrapper startup gains a deterministic self-clean step for its own
  lineage's leftovers (noise in the log names the attempt it reaped on
  behalf of).
- Retry semantics get one more honest terminal: `budget_exhausted`.
- The 95 GiB-class host wedge becomes detectable at the FIRST refuse edge:
  claim file + log carries budget_used for the operator.

## Rollout status

- **2026-09-18 — wrapper-side live receipt (rccs, Bonsai-8B).** Natural
  provision fired with the single-file upstream advance (`serve-model.slurm`
  @`c229ca52` synced into the rccs cluster checkout, sha byte-exact,
  `.bak-20260917` breadcrumb): slurm job **385046** on idle `<compute-node>`
  came up in ~5 min at `VLLM_GPU_MEM_UTIL=0.75` (site override — the
  default 0.9 (=109.5 GiB) is currently unbootable on BOTH GB10 nodes; each
  shows only ~98.4/121.63 GiB free at vLLM init → engine-core ValueError →
  respawn ×3 → clean exit-125 teardown = the engine budget class behaving
  as designed). Receipts: (a) wrapper log line
  `budget: per-job ledger on 385046 (0/3 used)` — the per-job flock ledger
  is LIVE, plus per-job `3850*.budget{,.lock}` claim files on the cluster
  FS; (b) `rivault-job-385046.info` carries
  **`RIVAULT_FIRST_READY_AT=2026-09-18T00:12:28Z`** — the first live
  emission of the venue-stamped first-ready marker (WP-12 venue leg's
  wrapper half); (c) chat through `/rccs/gw/llm/v1/chat/completions`
  answered `WP20 FIRST READY LIVE` (7 completion tokens). Server-side
  halves (the `umrs.venue.first_ready` span emission + the
  `budget_exhausted` `state_reason` mapping + no-requeue guard) remain
  maintainer-gated: the deployed `umrs-server-rccs` container on data0
  carries a checkout that predates all WP-20 waves (no `attempt_budget.sh`,
  no first-ready span code) → needs a fresh image build + restart.

- **2026-09-13 — design wave (original draft).** No code changed.
- **2026-09-17 — WP-20 implementation leg landed (umrs `c9a027de`).**
  D-1: `mini-rivault-llm/attempt_budget.sh` — the per-job flock ledger
  (`<job_id>.budget` + `.lock`; `budget_claim_job` atomic; default total ==
  legacy per-attempt cap (`RIVAULT_ENGINE_RESPAWN_MAX_PER_JOB` or
  `RIVAULT_ENGINE_RESPAWN_MAX` = 3); claimed-from-`UMRS_DURABLE_JOB_ID` —
  provisioner now exports that on every endpoint provision (persistent durable
  identity); `budget_reap_lineage` intra-job startup sweep rides the
  `umrs_durable_job_id=<id>` label, never another lineage's conmon/crun).
  D-2: the wrapper sources it at engine-monitor; legacy env counter survives
  as the honest degenerate path when flock/python3 are absent (named warn).
  Where free `budget_claim_job` bougated `budget_mark_exhausted` → wrapper
  writes `RIVAULT_TERMINAL_REASON=budget_exhausted` into
  `rivault-job-<slurm_id>.info` and exits 125.
  D-3: remote slurm `JobInfo.state_reason="budget_exhausted"` surfaces the
  marker BEFORE the ghost sweep; durable store's `mark_attempt_failed` guard
  treats any exit_reason containing the literal as **terminal regardless of
  remaining retry budget** (the jobs row lands `failed` — pin caught + closed
  a stale-running-row variant where the marker would have straddled updates).
  Pins: 8 across lib+store+remote+wrapper+adapters (`test_attempt_budget.py`),
  neighbor suites 116 green.
- **D-4 (unchanged):** cross-node retry placement stays out of scope
  (venue.sh's ADR-045 dead-job GC covers it per lineage; shared-FS budget
  store + fencing token = follow-up design, not folded here).
- **Live venue smoke (wrapper-claim behavior + 125 flip through the registry
  chain)** rides the next live cluster attempt — the same convergence wave." 
