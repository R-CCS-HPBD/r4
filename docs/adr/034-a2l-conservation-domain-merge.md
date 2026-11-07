# ADR-034: A2L conservation domain — UMRS merge with join-not-copy keying

**Status:** Accepted
- **Date:** 2026-08-28
- **Related:** ADR-002 (job/attempt separation), ADR-005 (r4-contracts
  stdlib-only), ADR-017 (a2l knowledge-graph bridge), ADR-024 (knowledge
  module boundary), ADR-031 (knowledge plane ownership),
  `.github/instructions/mutation-safety.instructions.md` (internal layer rules).

## Context

A2L (Artifact & Agent Layer) exists today as a standalone FastAPI service
(`a2l/`, port 8002) with its own Postgres store (`a2l_artifacts`,
`a2l_artifact_edges`, `a2l_agent_executions`, `a2l_execution_artifacts`),
an FSM-enforced artifact registry, an agent-execution ledger, and
`a2l/poller.py`, which polls UMRS over HTTP to reconstruct execution history
UMRS already wrote.

Three forces argue for merging it into UMRS rather than keeping the separate
service:

1. **Authority inversion.** UMRS is the execution authority — it owns
   Job/Attempt/CommitPointer (ADR-002). A2L, downstream, re-derives that same
   history. A projection that must discover its truth by polling its own
   source of truth is architecturally backwards.
2. **Duplicated infrastructure.** Flyway-style migrations, asyncpg pools,
   FastAPI wiring, presign, health probes, an nginx block, a deploy flag
   (`umrs_front_a2l_enabled`), secrets — all a second copy of things UMRS
   already owns. Operationally this is a service that must be deployed,
   monitored, and recovered for a ledger.
3. **Roadmap collision.** A2L's own README plans a lineage DAG, vector
   search, and an autonomous agent runtime — things UMRS already has
   (`pg_graph_sink`, pgvector 0075, research loops). Continuing apart means
   building the same layers twice.

The one legitimate counterweight is **audit-plane independence**: if the
ledger must remain trustworthy and recoverable when UMRS itself is
compromised or corrupt, A2L must stay an independent service *with
independent storage and an independent failure boundary* — process
separation sharing UMRS's Postgres is theatre. Today's deployment does not
meet that bar (same Postgres, fail-silent bridge), so independence is not
the governing constraint.

## Decision

1. **Ownership split is by write authority, not by file location.**

   | Domain | Owns | Never owns |
   |--------|------|------------|
   | UMRS execution spine | Jobs, Attempts, CommitPointer, lifecycle events, authoritative timestamps/status | a2l interpretation fields |
   | A2L conservation domain | Conserved *additions* to execution history — artifact identities (deterministic content+context hash), lineage edges, agent-execution annotations (traces, outputs, errors, HITL decisions, operator notes), all keyed by `job_id`/`attempt_id` | execution state, status, timestamps — it joins, it never copies |
   | Knowledge/KG | *Semantic* provenance — derived knowledge nodes and edges (0041, ADR-031) | operational history |
   | r4 | Rendering, SDK/CLI, DTOs (via r4-contracts) | any durable history |

2. **Join-not-copy.** A2L rows are addressable solely by keys owned by the
   authoritative spine: `job_id`, `attempt_id`, artifact content hash.
   Read models (history, trace views, agent dashboards) are **compositional
   queries** — `LEFT JOIN` at query time over the job/attempt tables and a2l
   tables — never a materialized copy of execution state. If a row exists in
   a2l with no job counterpart, that is a reconcile finding, not a second
   truth.

3. **Lineage is split before it re-diverges.**
   - a2l lineage = *operational* lineage: "job J produced artifact A from
     artifacts [B, C] with command C".
   - `umrs.knowledge` lineage = *semantic* provenance: "chunk C supports
     claim D about entity E".
   They cross-reference by `content_hash`; they do not merge stores.

4. **Bounded module, not subsystem merger.** The merged code lives as
   `umrs/src/umrs/a2l/` with the same internal-layer rule as `knowledge/` and
   `model_registry/`: it must not import from `umrs.api` or `umrs.server`.
   The execution spine publishes events through a narrow, stable interface
   (an `A2lRecorder` protocol implemented by the durable side); a2l consumes
   that interface, never database innards.

5. **A2L remains a separately deployable service only if** an operator
   declares the audit-survivability requirement *and* provides independent
   storage. Process separation without storage separation is not a valid
   reason to keep it. Absent that declaration, the standalone deployment is
   retired after cutover.

## Migration plan

Parallel-run, additive-first, cutover last. Never move + remove in the same
step (mutation-safety).

### Phase 0 — Ownership contract + event keys (this ADR)

- The authoritative keys for conservation are defined: `job_id`,
  `attempt_id`, `content_hash`, `(execution_id, agent_name)` on the a2l
  ledger side.
- The a2l-ledger write surface is typed in `r4-contracts` as stdlib-only
  dataclasses so r4 and UMRS can both shape reads without importing a2l
  internal models.

### Phase 1 — Bounded module + new durable store

- Create `umrs/src/umrs/a2l/` with `store.py`, `models.py`, `fsm.py`,
  `recorder.py` (event keying), `routes.py`.
- New migration `NNNN_a2l_conservation.sql` creates the a2l tables in the
  UMRS Postgres with **additive** columns the standalone schema lacked:
  `job_id TEXT`, `attempt_id TEXT` on `a2l_agent_executions`;
  `job_id TEXT`, `attempt_id TEXT` indexes for join-referential integrity.
  No destructive changes to existing a2l tables yet.
- `umrs/src/umrs/api/a2l_ledger.py` mounts under `/umrs/a2l/ledger/*` —
  a new prefix, so `/gw/a2l/*` (standalone, nginx-proxied) keeps working
  during parallel-run.
- The execution spine gains an `A2lRecorder` port; job lifecycle handlers
  (completion, artifact commit attempts) emit recorder events *in addition
  to* existing behavior. The standalone poller is not yet removed.

### Phase 2 — Reconciliation proof

- Run both paths (UMRS-native recorder + standalone poller) in parallel.
- A reconcile job compares histories: every UMRS-side event should appear in
  a2l-ledger rows with matching keys; divergences are explainable by poller
  latency only.
- Only after proof does the standalone service become read-only, then stop.

### Phase 3 — Cutover

- Gateway nginx `/gw/a2l/*` re-proxies to UMRS's a2l-ledger router.
- `umrs_front_a2l_enabled` flips to `false`-by-retirement: the standalone
  service is removed from the umrs-front deployment playbooks.
- Standalone poller is deleted. `A2L` package root directory is removed only
  after deploy proof (records in `memories/repo/adr034-a2l-cutover-*.md`).

## Internal boundaries under the merge

- `umrs/src/umrs/a2l/**` may import from `umrs.knowledge`,
  `umrs.data_catalog`, `umrs.jobs` (event payloads only — never the store),
  and `r4_contracts`. **Forbidden**: `umrs.api.*`, `umrs.server.*`,
  `umrs.shared.config` (receives config via constructor, per ADR-008).
- The execution spine emits recorder events; it does not read a2l tables.
  Read joins from a2l to the spine use the spine's own read views, not raw
  tables.

## Non-goals

- No move of knowledge-graph, vector, or catalog responsibilities into a2l.
  a2l is conservation + presentation of *execution history*; derived semantic
  state stays in the knowledge plane (ADR-027/031).
- No change to r4's persistence surface. r4 never stores execution history;
  it reads via gateway proxies and renders.

## Rollback

- Phase 1 is additive. Rollback = drop the `/umrs/a2l/ledger/*` router mount
  and delete the migration SQL; the standalone service is untouched until
  cutover.
- After cutover: re-enable `umrs_front_a2l_enabled: true` and re-deploy the
  standalone service. a2l tables are never dropped by migration tooling.

## Rollout status (2026-09-07, WP-14 round 1)

Live probe (read-only, umrs-front, this date) corrects the WP-14 gap premise
(G-P4-3 "merged module shipped but not deployed on umrs-front"):

- Merged A2L **is deployed**: `/umrs/a2l/artifacts` answers **422** (mounted,
  validating) on both cluster servers (rccs :8000, ai4s :8002); gateway
  exposure wired — `/gw/a2l/artifacts` answers **302** (auth-request redir)
  on both gateway ports (50080/50082). No standalone `a2l` container or
  process exists. NGINX /gw/a2l blocks were rebuilt live in the earlier
  rebuild session.
- `umrs_front_a2l_enabled` semantics re-scoped (comment in group_vars): the
  flag governs ONLY the legacy standalone role — it stays `false` forever
  and the role + `a2l/` tree are removed at Phase-3 cutover, never flipped
  to `true` again (which would re-collide port 8002 with UMRS-AI4S).

**Jobs-authority schema placement (G-P4-4, S2) — DECIDED:**

- Durable-job authority is the SHARED `umrs_db` for every umrs-front cluster
  (rccs/ai4s/bdrt/octopus). Per-cluster DBs (`umrs_rccs`, `umrs_ai4s`,
  `front_bdrt`, `front_octopus`) are registry-only (`endpoint_registry`,
  audit log, usage partitions) and must never grow `jobs`/`job_attempts`.
- Rationale: one reconciler fabric; the A2L conservation join
  (`a2l_artifacts.job_id/attempt_id`) has a single join target; cluster is a
  job attribute (`authority`/`backend`), not a DB boundary.
- Made load-bearing in code, not convention: every cluster server in
  `deploy/umrs-front/docker-compose.yaml` now sets
  `UMRS_JOBS_DB: ${UMRS_JOBS_DB:-umrs_db}` explicitly (previously the chain
  fell through `POSTGRES_DB` defaults — a quiet re-target hazard); pinned by
  `tests/test_jobs_schema_placement.py::TestPlacementDecisionIsDocumented`.
- Drift detector: `tools/check_jobs_schema_placement.py` (read-only;
  jobs-present/jobs-unexpected rules; runbook inside the docstring),
  unit-pinned in `tests/test_jobs_schema_placement.py` (7).
- Resolution default pinned: `umrs_db` absent `UMRS_JOBS_DB`+`POSTGRES_DB`
  (`umrs/tests/test_job_tracking_handler_durable.py::test_durable_jobs_default_db_is_umrs_db`).
- The 8-table `umrs_ai4s` inventory matches the decision as-is (registry
  surface) — no migration needed into it; this is why the remedy's
  "migration for AI4S cluster jobs tables" resolves to **none required**.

**Phase-3 cutover gates remaining (NOT done in round 1):**

1. 7 consecutive green reconcile days with real traffic (first run
   2026-08-30 was green-but-trivial: 0 terminal attempts in the window —
   meaningful dwell starts when terminal attempts exist).
2. Removal of the standalone surface: the `a2l/` package tree, the
   `deploy/ansible/roles/a2l/` role, the standalone poller, and the
   `umrs_front_a2l_enabled` knob (delete, do not keep false).

## Rollout status (2026-09-11, Phase-3 cutover — user-decided dwell waiver)

**The 7-day dwell is WAIVED by maintainer decision** ("pre-real-use platform,
no important data yet" 2026-09-11): the dwell existed to protect live
artefacts; with no production data the merged-module check is provable only
code-side, and it passes (both servers answer `/umrs/a2l/artifacts` with
422 + gateway exposure on both gateway ports, live-probed 2026-09-07).

**Removed (this wave):**
- `a2l/` package tree (standalone service + `api/` + `main.py` + poller +
  `Dockerfile` + standalone pyproject + its own test suite).
- `deploy/ansible/roles/a2l/` role.
- `umrs_front_a2l_enabled` flag (group_vars + baseline inventory + the
  conditional task in `playbooks/umrs-front.yaml`).
- Science-domain test files whose module died with the package
  (`r4/tests/test_e2e_autonomous_loop.py`, `test_integration_week3.py`,
  `test_working_research_state.py`) — the science types/lineage layer died
  with the standalone package; their boundary constraint prevents a clean
  merge into `umrs.a2l` (r4↔umrs import rules hold for r4/tests too —
  zero umrs imports exist in r4/tests today).

**Kept untouched:** `umrs/src/umrs/a2l/` conservation module (models,
recorder, join-store) — the merged home per this ADR.

Gate evidence: full-scoped local gate green incl. `adr_index_uniqueness`
on this ADR set + r4 suite 3105 pass (`test_probe_cwd` env-host
pre-existing only).

## Rollout status (2026-10-09, Phase-3 gateway leg — landed in the tree)

Found on the stand-alone stack:

- The repo's nginx still rewrote `/gw/a2l/(.*)` to bare `/$1` (the retired service's paths), so every `r4 artifact` call 404'd.
- The ledger router took no caller identity at all. STATE `SEC-20261009-002`.

Landed:

- `gateway/nginx/conf.d/server.conf` rewrites to `/umrs/a2l/ledger/$1` (Phase 3's first bullet).
- Every ledger route requires the verified identity:
  - reads are scoped by `knowledge.access_scope` predicates;
  - writes are stamped with the caller;
  - invisible rows answer 404;
  - job-scoped reads need job access;
  - new error code `a2l.forbidden` (403).
- New routes for the CLI: `POST /artifacts` (admin-only; deterministic id from the content hash, else the URI's hash), `GET /artifacts/{id}/lineage` (breadth-first over `a2l_artifact_edges`, unreadable nodes omitted), `GET /health`; list filters `project_id`/`type`/`status`. Derive defaults `operation`/`user_id`/`input_data_hash`, so the CLI's body is enough.
- Store fixes, all of which failed every write against real Postgres (the tests used fakes): ISO-string timestamps sent to `timestamptz` parameters, and `$2` deduced as both text and enum in the status updates.
- Live: create → derive (stamped `scientist`/`default`) → list → lineage tree → status pending→running→completed; an illegal rewind is 409; anonymous direct access is 401.

Remote wiring is the maintainer's: the rccs gateway's `A2L_HOST:A2L_PORT` default (`127.0.0.1:8002`) must name the rccs UMRS server. `knowledge/a2l_bridge.py` still calls `/gw/a2l/resolve`, which is not a ledger route, and calls it without a token. It is best-effort and was already dead before this change; it should call the in-process store instead.
