# ADR-051 — Self-service projects + remote-agent resource poles (CPU processes + disk bytes)

**Status:** Proposed (wave 1 executing same day)
- **Related:** ADR-046 (remote session credentials), ADR-050 (visibility tiers),
  session-venue plan r1-r6 (picker landed), WP-05 round 1 (companion worker jobs).
  User directive 2026-09-13/14.

## Context

Verified from code today:

1. The user's issue confirmed today: there is NO project registry server-side —
   every `project_id` in calls is a spelled string; the HMAC tuple
   `(user_id, project_id, is_admin)` accepts whatever the bearer wrote,
   which is precisely the account/project mismatch the owner raised twice.
2. Venue selection (local/remote one-step switch at session start) IS
   landed end-to-end (session-venue plan P1–P6): registry venues routes,
   picker overlays, pull-resume — user need #2 is already real; don't
   redo it, bind to it.
3. CPU-only remote host stands in: polled by a worker spawn logical
   process model (`POST /umrs/companion/workers` = one spawned process
   per durable job). There is no measured per-user cap: concurrent
   workers are counted by convention only, not bounded.
4. Nothing meters per-USER disk against a budget across project-owned
   content on the server; project catalog rows own `owner_user_id`
   attribution already in `catalog_objects.owner` field — usage can be
   answered honestly from the attribution plane without fake bookkeeping.
5. The LLM-access model is dual-path-first-class (public external sources
   +
   UMRS-deployed) — nothing in this wave gates the LLM itself, only the
   agent's CPU processes and bytes.

## Decision (normative)

### D-1 — Free casual projects, durable + owner-attributed, registration-required
New `projects` table (migration 0095): project_id, name, owner_id, status
(active/retired), created/updated. Self-service: any verified user creates
one freely (`POST /umrs/projects`, idempotent ensure-shape, owner_=
caller); issuers never pre-assign — the user's initial project header
remains a *default*, never a ceiling. Admin can create on another user's
behalf (project row gets attributed).

### D-2 — Remote agent budget in CPU-process units, spawn-time
`UMRS_FRONTEND_AGENT_MAX_CPU_PROCESSES_PER_USER` (default 4, site-tunable).
Cap counts*tracked handles* spawned on the frontend that host an agent
process (companion-worker durable jobs AND frontend loop drivers — the
claim/decrement pair happens at spawn/exit). Enforcement: typed
`quota.cpu_process_exceeded` 503 with current/limit names. Reaping:
heartbeats mark stale handles dead automatically (process wedge can
NOT silently pin a user's budget).

### D-3 — Disk budget per user, attributed-summed, no fragmentation incentive
`UMRS_FRONTEND_USER_DISK_BYTES_MAX` (default 100 GiB, site-tunable). Usage
= SUM(bytes) across catalog rows attributed to the SAME uploader user_id
(evaluated at upload time; hash-dedup counts each unique object hash once
against the uploader). Ambient-marked rows still count (hidden ≠ freed).
Refusals: typed `quota.disk_bytes_exceeded` with current/limit plus the
measurement source named.

### D-4 — Venue semantics untouched; these are caps, not venues
Nothing in this wave changes the session venue semantics or write path
(bps flows), authorization, visibility tiers, or either LLM path. The
agent budget and disk budget are pure *caps at spawn/upload time* — and
NOT accounting semantics riders (A2L stays the attribution line).

## Consequences

- Accounts-as-given only matters for capability entry truly (HMAC claim
  default project), and anything else becomes self-service "casually".
- The frontend host's CPU/process table is safe against one user's
  runaway spawning, without GPU consequentialism.
- Foundation table (projects) enables membership semantics whenever the
  owner wants one — it's the natural Namespace measurement later rather
  than a look-up-later table.
- Nothing fragment-incentivizes projects:
  the accounting is per user, never per project.

## Rollout-status (2026-09-14)

Wave 1 executing now per direct user consent.

## Rollout-status (2026-09-14, Wave 1 landed)

- **D-1**: 0095 projects table + `ProjectStore` (ensure-shape); routes
  POST/GET/list/PATCH-retire (owner-or-admin, tombstone); for_user minting
  is admin-only; taxonomy `projects.*` in r4_contracts.errors.
- **D-2**: `umrs/api/agent_quota.py` — spawn unit count from the jobs
  table only (`companion_worker_job | research_loop_job` × live states);
  typed `quota.cpu_process_exceeded` (503) fired once at every spawn edge
  (companion worker register + research-loop frontend creation). Terminal
  rows close the claim by construction; nothing reads process liveness.
- **D-3**: 0096 ledger + `umrs/api/disk_ledger.py`; charge per unique
  content hash per uploader at the central durable upload leg
  (`/umrs/knowledge/upload`); quota.disk_bytes_exceeded on breach.
  ~~Named follow-up: staged-object cleanup on refusal~~ CLOSED 2026-09-15
  (umrs `9c560a6c`): refusal triggers an immediate
  `delete_object` on the staged key (cleanup failure never masks the
  typed 503; 3 pins); byte metering on remaining presign lanes + the
  /units summarizes route stay open.
- Credentials contracts: `user_id` attribution now flows explicitly into
  durable job submissions (was ambient-row-only).
- Lane family `projects` recorded BETA and the suite passes 4936/0;
  live-PG rehearsal (umrs_test): projects lifecycle create→no-stomp→
  mine-list→retire-kept all receipted.
- Out of scope (honestly named): casual project's model registration,
  /umrs/projects state indexes beyond owner+status, cluster venue work,
  any LLM/dispatch-side enforcement semantics (dual-path unchanged).

## Rollout-status (2026-09-16, remaining named follow-ups)

- **/units summary route** (D-2/D-3 read model): `GET /umrs/quota/units` —
  the verified caller's two poles (CPU-process units from the jobs table,
  catalog-attributed disk bytes), limits read from the same env knobs the
  enforcement edges read (gauge ≡ gate). `umrs/api/quota_status.py`, mounted
  in http_sidecar, lane BETA with `quota` family pin, matrix row + canary +
  `/gw/quota/` NGINX family. Anonymous refused in both modes (shared scope
  layer strict; mvp defensive branch, same canonical code).
- **Presign-lane byte metering**: remaining catalog-write edges carry the
  D-3 ledger charge — `POST /umrs/files/upload` (md5 of staged body,
  refusal unstages) and the multipart intake `POST /umrs/uploads/manifests/{id}/complete`
  (charge before status flip + ingest trigger; refusal deletes the assembled
  object but keeps parts + manifest re-completable — no re-upload after
  quota frees). `/umrs/knowledge/upload` was covered in the 09-15 leg.
  Named residual: a presigned PUT whose bytes never cross ANY metered
  server edge stays unmetered until a catalog janitor sweep exists (owner
  decision whether that lane needs the sweep).
- **Pool-conn leak closed**: the knowledge lane's `getconn()` never
  `putconn`-ed on either path (2026-09-09 exhaustion class) — balanced now,
  pooled providers everywhere in the new wiring, harness pins count returns.
- **Found en route**: stale `policy_router` alias double-mounted every
  `/umrs/models` route in http_sidecar (route_inventory 19 dupes; removed;
  policy endpoints ride models_router as designed).
- Added `quota.unavailable` (503) to the quota taxonomy; contracts 179 green.
- Route inventory: 44 mutating-surface routes across 10 api files annotated
  `# auth: verified` (audit X2 zero-unknown floor restored: 339 rows; check
  OK); lane registries + surface pins + matrix rows kept step-for-step.
- Suites: umrs 4965/163-skip, belt 1303, contracts 179, umrs-changed gate
  clean (complexity ratchet 1/424 pre-existing as of this wave).
