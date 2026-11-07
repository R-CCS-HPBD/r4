# ADR-036: Loop hosting — headless research loop as a DurableJob kind

**Status:** Proposed (round 1 implemented, WP-05, 2026-09-07; cluster-venue leg verified live 2026-09-10; was Draft (pre-code) until 2026-09-07)
- **Rollout status (2026-09-10, cluster venue + durable approvals — VERIFIED LIVE; recorded here 2026-09-13 from the ADR-046 rollout table):**
  D2 cluster venue: first real cluster-venue loop leg `cluster-v3-leg` via
  `job-cf065021fc94` — venue-normalized claim (only umrs-server-rccs claimed),
  `ClusterSlurmSubmitter` → remote sbatch 381169 → `deploy/rccs/loop-job.slurm`
  → `knowledge/_cluster_loop_job.sh` job-private venue → driver pod →
  `knowledge/cluster_loop_entry.py`; sim engine ran 1 iteration and wrote
  state back through the `/gw/loopjob/*` HMAC lane (`knowledge/http_loop_store.py`);
  loop `stopped|completed|1|1`, attempt `succeeded`, job `completed`.
  D3 durable gate intents: two-client approval probe `ask-probe-v1` via
  `job-14d4eed4c54c` — parked tool-grain gate visible to two client views over
  `/gw/loopjob/approvals/*`, non-owner 401 on decide, owner `once` → 200, engine
  resumed, second decision 409. Evidence and live-found fixes:
  [ADR-046 rollout table](046-remote-session-credential-egress.md) rows
  "Two-client approval probe" and "First real cluster-venue loop leg".
  **Remaining legs:** D5 autopilot start-condition (client refusal of
  `research_loop_start` until the server-side trust check returns), the
  ERROR auto-resume policy for hosted loops, and a per-job EgressGuard on the
  cluster leg (the venue today relies on the allowed-hosts-capped guard in
  `_cluster_loop_job.sh`, not a per-job EgressGuard instance). Console attach
  UX (item 7) stays out of scope.
- **Date:** 2026-09-02
- **Rollout status (2026-09-07, WP-05 round 1):** D1 `research_loop_job` kind + hosting seam
  landed; amendment (b) resume-at-last-iteration + per-iteration checkpoints landed;
  amendment (a) durable approval inbox landed (dedicated table in the companion_trust
  component — recorded deviation from D3's letter); D4 findings → knowledge artifacts +
  asset-index `loop → artifact` landed; D2 cluster venue and D5 autopilot start-condition
  remain. Details: [Amendment 2 (2026-09-07)](036-amendment-2026-09-07-loop-hosting.md);
  F2 precondition: [Amendment 1 (2026-09-06)](036-amendment-2026-09-06-session-snapshots.md).
- **Verified pre-code 2026-09-03:** zero `research_loop_job`/`loop_hosting`
  references in `umrs/src`; no implementing commits in either repo;
  loops still client-side (`r4/src/r4/companion/science_loop.py`,
  `research_loop_tools.py`). Sequencing: after the ADR-035 rollout tail
  (canary hard-fail promotion + autopilot gating), before Review item 7
  attach-to-loop UX.
- **Amendment 2026-09-04 (external review R2) — required scope before code:**
  (a) **Durable approval inbox** — pending permission decisions keyed by
  job/attempt, answerable from any client, timeout → pause (never fail);
  substrate = UMRS trust store extended PI-can-approve-student style.
  (b) **Per-iteration checkpoints** — LOST→requeue resumes the loop at the
  last iteration, never restarts the campaign.
  Sequencing correction: F2 (session authority: SQLite → read-model/cache
  over Postgres with offline write-ahead journal, `asset_index` rule)
  precedes this ADR's implementation.
- **Related:** Review F1 (headless companion job kind), T3 venue design,
  ADR-025 (research sessions), ADR-028 (turn-as-attempt), ADR-035
  (enforcement matrix + trust allowlist), companion_trust (T3c)

## Context

The Review (F1) places the platform mission — *autonomous research-loop
manager* — on a footing the tree already partially supports. Delta-checked
2026-09-02: `companion_worker` durable job kind, HybridRuntime venue routing,
server-durable research sessions, and turn-attempt semantics **landed**.
What remains client-side: **the loop itself.** A Companion-hosted research
loop dies when the laptop lid closes, escapes per-job EgressGuard and Slurm
accounting, and parks approvals in an in-process registry that a restart
loses (mitigated for turn attempts by ADR-028, but not for the loop's gate
intents or findings).

## Decision

1. **New durable job kind `research_loop_job`** (one job per loop, needs no
   new orchestrator): mirrors the `companion_worker` registration pattern;
   attempt = one loop *run* (iterations inside survive via the loop store's
   per-iteration checkpoints; attempt boundary = start/finish of a campaign).
2. **Execution venue**: mini-RiVault class container when cluster resources
   are requested (HybridRuntime `requires` grammar already encodes venue
   hints); frontend worker process otherwise — SAME Job/Attempt machinery
   either way (attempt-scope writes, commit-pointer, single authority).
3. **Gate intents become durable**: the loop's `awaiting_approval` records
   move from in-process `tool_approvals` to companion_trust rows with a
   ADR-028-style pending state (scope `session:<loop_id>`); decisions write
   to the same surface the venue worker's approval channel already uses —
   a user can answer from any client, the unattended loop waits, and
   restart does not lose state. `always` decisions additionally mint
   durable per-session-scope trust rows (shipped 2026-09-02, main 20582d64).
4. **Loop turn → asset lineage edge**: the asset index gains
   `loop → artifact` derivation once a loop job's iteration state records
   produced artifact ids (indexer reads the lineage table; no new model).
5. **Autopilot start-condition** (from ADR-035 decision 4): a client
   refuses `research_loop_start` in autopilot until the caller's trust
   allowlist check returns server-side for the loop-spawning tools.

## Consequences

- **Positive:** loops survive client disconnect and restart; approvals
  become durable server records; EgressGuard/Slurm accounting apply per
  job; lineage tables close the science-catalog gap flagged by the user.
- **Cost:** a new job kind (registration, restart semantics), one durable
  gate refactor, and one lineage edge derivation — all strictly additive
  to today's surface.
- **NOT in scope:** console attach UX (item 7, follows this), bundle
  signing (of .kb/workflow bundles), a2l-kind consolidation.

## Acceptance gates before item 7

1. `research_loop_job` DurableJob-kind + attempt semantics + durable gate
   intents green on a local harness.
2. Asset-index lineage test shows `loop_turn → artifact` derivation.
3. Autopilot refuses loop start when the server allowlist denies (visible
   in console status as well; badge honesty already shipped).
