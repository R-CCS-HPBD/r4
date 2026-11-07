# ADR-036 Amendment 2 (2026-09-07, WP-05 round 1) — Loop hosting: job kind, durable inbox, findings lineage

**Status:** Proposed (with implementation, same PR)
- **Base document:** [036-loop-hosting-companion-job-kind.md](036-loop-hosting-companion-job-kind.md) (D1–D5 + amendment (a)/(b) of 2026-09-04); precondition [Amendment 1 — session snapshots](036-amendment-2026-09-06-session-snapshots.md) (WP-04 leg 1, landed)
- **Gap anchors:** G-P4-1 (no durable loop job kind), G-P8-1 (approval decisions are process state), G-P1-1 (findings never minted as KG assets), G-P6-2 pending-approval leg
- **Package report:** ../verification/packages/wp05.md

## Decisions (round 1)

1. **`research_loop_job` durable kind (D1).** `JobType.RESEARCH_LOOP = "research_loop_job"`,
   one job per loop, attempt = one hosted run. Registered like `companion_worker`:
   enum + backoff (`[5, 30, 60]`, 4 attempts), backend `ResearchLoopBackend`,
   `ResearchLoopStatusChecker`, submit-time spec validator (`loop_id`, `driver`),
   dispatchable. The backend executes nothing itself: `submit_job` hands the loop
   to `umrs.knowledge.loop_hosting.host(loop_id, job_id, attempt_id)`, which
   spawns the engine/agent driver against the loop store and returns a typed
   `research_loop_job|loop_id=…|driver=…` ref. The REST layer (`umrs.api.loop`)
   *configures* the hosting seam with late-bound getters (store, engine
   factory, agent executor, checkpoint store, finding sink) so `jobs/` never
   imports `api/`.
2. **Hosting is decided at loop start and written on the record.** With a
   durable runtime the route submits the job (`metadata.hosting == "job"`,
   `durable_job_id`, later `attempt_id`); without one the process drives the
   loop on a thread (`hosting == "thread"`, `driver`); a failed submission
   falls back to a thread and records `hosting_note`. Test-injectable via
   `app.state.loop_job_submitter` (mirrors the companion venue seam).
3. **Resume, never restart (amendment (b)).** `host` never resets a loop: a
   LOST attempt (UMRS restart) is requeued by the reconciler, the scheduler
   submits a new attempt, and the driver continues from the stored
   `current_iteration`. Each completed iteration of a job-hosted loop writes
   one `checkpoints` row (`CheckpointStore`, step `iteration`, progress
   n/max, inline state with the iteration's artifact and finding ids).
   Terminal loops (STOPPED/ERROR) are reported, not re-driven. Boot order in
   the sidecar is now store upgrade → durable runtime → driver rehydration;
   `reregister_loop_drivers` leaves job-hosted loops to the reconciler when
   the runtime is up and thread-hosts them (recorded `hosting_note`) when it
   is not, so a loop never parks forever and is never driven twice.
4. **Durable approval inbox (amendment (a)).** New table
   `companion_approval_inbox` (migration 0081; `companion_trust` schema
   component v3) behind `ApprovalInboxStore` (psycopg conn or sqlite
   fallback, one lock, `durability ∈ {postgres, sqlite, process}`).
   `umrs.knowledge.tool_approvals` is now the configured-store facade — the
   sidecar installs the Postgres store on the trust connection; an
   unconfigured process runs process-local *and says so* on the route
   (`durability`). Rows carry the loop owner (`user_id`), hosting `job_id`
   and `attempt_id`; both the intent and its terminal decision are rows, so a
   restarted driver re-enters its gate under the `awaiting_approval` marker
   and reads the recorded decision instead of re-asking (pinned by test).
   *Deviation from D3's letter, recorded:* D3 said "companion_trust rows";
   the substrate is kept (same component, same connection) but an approval is
   a per-action intent with a payload and a terminal decision, not a
   (user, scope, tool) allowlist row — it gets its own table. D3's
   `always → session-scope trust row` is kept: the route mints
   `session:<loop_id>` for the action's workflow name and flips the loop to
   `mode=auto`.
5. **Owner-pinned, answerable from any client.** Loop start reads the
   gateway-verified identity when present (401 on a forged signature in
   strict mode) and pins `metadata.owner_user_id`; `GET /approvals/pending`
   returns the caller's rows plus ownerless rows (loops started without an
   identity — direct sidecar callers, mvp mode), never another owner's;
   `POST /approvals/{id}/decision` is 401 for non-owners, 404 unknown, 409
   already decided. r4 gains `Client.list_loop_approvals` /
   `decide_loop_approval`, `ResearchRuntime.list_approvals` /
   `decide_approval` (UMRS; local runtime has no inbox and says so),
   `r4 project loop approvals [LOOP_ID]` / `r4 project loop decide ID
   --decision {once|always|no}`, and companion tools `research_loop_approvals`
   (SAFE) and `research_loop_decide` (RISKY **and** `NEVER_AUTO_APPROVE` — the
   decision is the human's; an agent auto-approving loop gates would hollow
   out ask mode).
6. **Findings become knowledge assets with lineage (D4).** `LoopIteration`
   (and the `LoopIterationRecord` contract) gain `artifact_ids` (migration
   0082, additive). `umrs.knowledge.finding_ingest.mint_iteration` mints each
   *real* finding of a completed iteration as a knowledge artifact (id =
   finding id; kind `document`, mime `application/vnd.r4.finding+json`;
   metadata `asset_kind=finding`, `source_loop`, iteration, hypothesis,
   workflow run, hosting job, evidence artifact ids, the finding payload) and
   the graph nodes/edges `finding --produced_by--> loop:<id>` and
   `finding --derived_from--> <evidence artifact>` (node ids follow the
   ingest chain: an artifact's node id is its artifact id; `node_type` stays in
   the DB CHECK vocabulary). Workflow-produced artifact ids from
   `results.artifacts` are recorded too. The asset index derives
   `loop --produced--> artifact` from `artifact_ids` (fanout, no new model).
   Honesty rules: simulated findings are never minted; minting is idempotent;
   the sink kind and counts are folded into `loop.metadata.finding_ingest`
   (`sink: none` when the process has no knowledge DSN — reported, not silent).
7. **RC1 spine retirement, recorded (spine policy).** The client-only
   `project_research_*` spine (9 tools, `r4/src/r4/companion/project_research_tools.py`,
   `project_research_policy.py`, the `project_research` skill, the
   `r4_contracts.project_research` module, `project_bridge._start_project_research_job`,
   guide rows, permission-tier entries; zero UMRS routes) **retires — delete,
   no fold**. Its verbs already map onto the canonical spine
   (`research_loop_*` + `knowledge_*` on ADR-036 job hosting). Execution is
   WP-07 (G-P1-2); this amendment is the recorded decision.
   *Executed 2026-09-07 (WP-07 round 1):* the family is deleted on every agent
   surface, `r4 project run` retired, the Bun `/project run` folded onto
   `r4 project loop start`; the same round added loop-start workflow
   resolution (G-P1-3) and, from its live walkthrough, three fixes on this
   spine — the in-process workflow port now enqueues the jobs it creates, a
   stop while parked at a gate is a cancellation, and a LOST→requeue resume
   redoes the interrupted iteration (`metadata.resume_rewind`). Report:
   ../verification/packages/wp07.md.
8. **Taint coverage of UMRS-hosted loops (ADR-039 addendum follow-through).**
   The only gated action of a server-hosted engine loop is `workflow_trigger`,
   whose arguments derive from the plan's `experiment.parameters` and the
   loop's own project id — never from retrieved content or tool output; the
   agentic runner has no tool-grain actions at all. There is therefore no
   taint path to fence in round 1. Residual, named: any future loop action
   that takes arguments from retrieval or tool output must pass a taint check
   before the approval gate (WP-19 follow-on).

## Fixes folded in (pre-existing, on the exercised path)

- The psycopg loop store could not serialize engine `Finding` dataclasses
  (`Json([Finding])` → TypeError inside the iteration hook); findings are now
  written via `to_dict()` and reloaded rows (dicts) render their statement on
  the iterations route instead of a dict repr.

## Acceptance gates (base ADR) — status

| Gate | Status |
|---|---|
| 1. job kind + attempt semantics + durable gate intents green on a local harness | **Met** — `test_research_loop_job.py` (resume at last iteration, checkpoints, checker), `test_approval_inbox.py` + restart test in `test_tool_grain_approvals.py` |
| 2. asset-index lineage shows loop → artifact derivation | **Met** — `test_asset_index.py::TestLoopArtifactLineage`, `test_finding_ingest.py` |
| 3. autopilot refuses loop start when the server allowlist denies | **Not this round** (D5; ADR-035 autopilot gating wire-up) |

## Deferred (named)

- **Cluster venue for loop jobs (D2, second half):** mini-RiVault/Slurm
  hosting when `requires` asks for cluster resources, and per-job EgressGuard
  application — needs cluster access (same blocker as WP-01 round 3). Round 1
  hosts in the frontend process only; the loop record says `hosting: job`
  with the frontend venue implied.
- **ERROR loops are terminal.** A retry of a failed attempt does not re-drive
  an ERROR loop (the checker reports the loop's error; the job fails after
  its bounded retries). Auto-resume of transient workflow failures is a
  policy decision for a later round.
- **Approval timeout → PAUSED** (amendment (a) letter): parked loops stay
  RUNNING with the `awaiting_approval` marker; no timeout, nothing fails.
- **Live two-client probe on umrs-front** (ratified allowance) — not run here.
- **Agent-runner findings:** the agentic driver records no `Finding` objects,
  so it mints nothing (WP-07 ergonomics).
- **ADR-034 conservation-ledger edges for findings** — round 1 writes KG
  nodes/edges + asset-index derivation only.
- **Mandatory identity on loop start in strict mode** — today absent headers
  keep the historical ownerless loop (visible approvals for any authenticated
  caller, as before); making identity mandatory is a surface decision (WP-08).
- **D5 autopilot start-condition** (gate 3 above).
