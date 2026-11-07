# ADR-058 — Deprovision-before-launch cancel contract (G-P4-7, WP-22)

**Status:** Proposed (legs 1–4 landed 2026-09-18; leg 5 (live rehearsal at next natural queued provision) rides venue + image)
- **Implements:** gap G-P4-7 (`docs/verification/gap-register.md`),
  live finding 2026-09-18 (`instance 5434` ↛ `slurm 384897`)
- **Related:** ADR-048 (budget-sharing contract; same venue-lifecycle
  family), ADR-002 (job/attempt separation), ADR-036 (durable retry)

## Context

The registry deprovision route (`DELETE /umrs/endpoints/{id}/deprovision`)
hands the **registry instance id** to `backend.cleanup()`. For a running
endpoint the registry id *is* the backend job id (slurm number arrives at
registration time), so the cancel works. For a **still-provisioning**
endpoint the registry row exists *before* the backend allocates its job id:
the registry id is then a surrogate with no meaning to the backend — and
every cancel implemented so far forwarded that surrogate straight to
`scancel`/equivalents.

Live evidence (2026-09-18, rccs): `r4 endpoint down 5434` against a queued
but unclaimed provision returned `deprovisioned`. The never-referenced
slurm job `384897` (submitted at request time, queued ~3.5 h on GB10 pool
contention) started later, engine-booted three times against the OOM class
(GB10 headroom finding, same episode), terminated by its own engine budget
(exit 125 — ADR-048 class behaving). Registry said "deprovisioned" for a
job that was still real. The masking was structural: `SlurmBackend.cleanup`
never checked scancel's returncode (`140aa2e6` closed that hole; the remote
backend already warned). But even with the log the cancel **cannot** hit
the right identity — nothing in the registry row carries it.

Missing invariant: *a deprovision must name the backend job it terminates,
and the surface must say when termination was not achieved*.

## Decision (normative)

### D-1 — Registry rows carry `backend_job_id` once assigned

New nullable column `backend_job_id` on the endpoint-registry store
(additive migration; no backfill heuristic — rows that cannot be resolved
keep the NULL honestly). The registry's other ids never change meaning;
`backend_job_id` is **set exactly once**, by the provisioner, in the same
transaction family that records the durable attempt/backend assignment.

### D-2 — Cancel targets the backend identity first

`service.deprovision(job_id)`:

1. If `backend_job_id` is present → cancel THAT id (the identity the
   backend knows), never the surrogate.
2. If `backend_job_id` is absent → the job is pre-claim; cancel via the
   job-lineage view (the durable jobs row + its attempts) so the backend
   job is terminated *on assignment*: the provisioner must check a
   cancellation marker before writing `backend_job_id`, and if set, abort
   the claim and mark the attempt cancelled (never start the wrapper).
3. In both cases, run the existing post-cancel verify loop; count any
   backend-side not-found as successful-gone (never an error — but also
   never silently).

### D-3 — The surface never lies about what happened

The route responds with the *effective* outcome, one of:

- `deprovisioned` — backend confirmed gone at answer time (the verify
  loop's terminal condition, already present).
- `stopping` (202) — cancel signal issued, backend still settling (already
  present).
- `cancellation-unachieved` (409) — the lineage could not be cancelled at
  answer time (e.g. scancel rc≠0 after D‑2 fell through; backend id never
  assigned and the race window closed wrongly). Body names the surrogate id
  AND any backend id that IS known; clients must NOT show "deprovisioned"
  for this class.

### D-4 — Reaper invariant for orphaned provisioning rows

The janitor already reaps stale claim-less registry rows; extend the same
rule to rows in `provisioning` whose lineage shows no attempt activity
past a bounded window, and emit the reaper's evidence line (surrogate id,
age, lineage state) so an operator can correlate without guesswork.

## Legs

- **2026-09-18 — Legs 1–3 landed** (umrs wave): `RegisteredEndpoint.
  backend_job_id` (additive PG column via the same `_migrate_schema`
  inheritance channel variant uses; positional `_select_columns` contract
  pinned so rows keep identical field order) + `registry.set_backend_job_id`
  set-once guard (refuses different overwrite, idempotent same replay,
  empty value fail-fast) + service.provision claim writes the direct-path
  identity; `service.deprovision` returns `DeprovisionOutcome`
  (gone/stopping/cancellation-unachieved/not_found) with the identity-first
  target dispatch and settling-with-surrogate coerced to UNACHIEVED;
  route surfaces 409 `endpoints.cancellation_unachieved` (detail names
  surrogate ± known backend id) / 202 stopping with `cleanup_status` /
  GONE continues the existing audit path. addendum — the lineage abort-
  on-claim marker from D-2 item 2 is its own leg (next wave) so legs 1–3
  ship without touching durable-jobs internals mid-flight. Pins:
  `test_wp22_backend_job_id` (8), `test_wp22_deprovision` (9); premise
  refreshes: readiness-budget 2 fixtures now carry claim-written identities
  (byte-equivalence of surface preserved, no coverage weakened), security-api
  stubs return Outcome enums, attempt-budget legacy pin loop-safe
  (`asyncio.run`).

- **2026-09-18 — Leg 2 addendum landed** (lineage cancel-through /
  abort-on-claim): the durable bridge already existed as
  `metadata["durable_job_id"]` at adapter register-time; UNACHIEVED route
  path now calls the lineage cancel best-effort (`jobs.cancel_job` holds
  the advisory lock against attempt creation; claim SQL never pulls
  CANCELLED — the abort-on-claim pieced from existing primitives, no new
  column). Leg 4 (D-4) landed same wave: `_cancel_durable_lineage` in the
  janitor's stuck-reaper (evidence line `janitor reaper lineage cancel
  FAILED: endpoint=<surrogate> durable_job=<id>` pinned). Pins: +3
  lineage route pins, +3 reaper pins (15 total in
  `test_wp22_deprovision`).

1. Migration + registry store: `backend_job_id` column, set-once write at
   attempt-assignment (the provisioner's claim transaction), pins.
2. `service.deprovision` D-2 dispatch (identity-first, lineage second,
   abort-on-claim marker), pins at the backend seam for both backends
   (local slurm; remote slurm).
3. Route surface D-3 (`cancellation-unachieved` 409 shape + taxonomy codes
   `endpoints.cancellation_unachieved`), client rendering: the CLI must
   print the effective class, not presume success.
4. Janitor reaper extension D-4 + pinned eviction log shape.
5. Live rehearsal on rccs (queued-only provision → cancel → `squeue`
   empty + registry says `deprovisioned` only when the backend is empty).
   Rides next venue access; numbered receipt row lands in
   `docs/verification/packages/wp01.md`-style receipts for this lane.

## Non-goals

- No change to proven-running deprovisions (D-2 keeps the identity it
  already had — strictly a superset of today's surface).
- No registry id evolution for existing rows beyond the additive column.
- No cross-backend abstraction cleanup (k8s/zmq keep their own cancel
  paths; the contract is applied at their seams with the same honest
  surface, not folded away).

## Consequences

- Cancelling a queued-but-unclaimed provision becomes *possible by
  construction* (previously impossible and silently masked).
- The 409 surface forces the janitor/ops loops to see real cancellation
  failures — the class that left 384897 alive is now observable.
- `backend_job_id` duplicates information the venue may report later;
  that is deliberate: the registry keeps its own truth for the cancel path
  rather than trusting probe-time best-effort lookups (the whole class
  started from probe-lossiness).
