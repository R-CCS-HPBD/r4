# ADR-105: One UMRS capability layer for agents — generated tools, typed workflows, plan → commit, pushed job events

**Status:** Accepted (2026-11-02, maintainer: "accept. please proceed"; PR #665 merged 2026-11-01). First Proposed (2026-11-01; maintainer request: "make more dense the connection between R4 agents and UMRS … is built-in skills the best way? or more efficient way for agents to run workflow/hpc jobs/knowledge build/RAG preparation of umrs from r4? like tool calling?", then "sounds great. please proceed."; D-1…D-10 drafted by the agent under the 2026-09-24 delegation, reasons recorded; the maintainer accepts or rejects; `wave/umrs-agent-capability-layer`, wave ledger (126))
**Date:** 2026-11-01
**Relates to:** [ADR-030](030-companion-scoped-tool-pinning.md) (companion-only tools never become MCP names); [ADR-033](033-tui-first-agentic-umrs-setup-with-default-skills.md) (reviewed skills are the instruction source; amended by D-8); [ADR-035](035-enforcement-point-matrix.md) (server trust allowlist; UMRS authz is the enforcement point); [ADR-037](037-tool-selection-phase.md) (selection is never enforcement; no second LLM call); [ADR-039](039-untrusted-content-provenance-taint.md) (taint fence); [ADR-059](059-transport-and-edge-dual-transport-umrs-edge.md) (the gateway is UMRS's edge, `/gw/*` → `/umrs/*`); [ADR-076](076-decision-model-role.md) (decision gate); [ADR-092](092-team-plans-declarative-orchestration.md) (plan IR, D-6 `TEAM_AGENT_DENY`, MD-5, slice 7); [ADR-101](101-local-tool-sandbox.md) (local sandbox, used by D-6; its D-6 network rule amended by D-6 here); [ADR-104](104-one-mode-one-permission-decision.md) (one mode, one ladder; the console never acts by itself; rung 10 amended for `job_commit` by D-4); CONSTITUTION §1.3 ownership, §1.6 one loop / one team engine; gap rows **D-66** (new), D-27, D-37

## Context

Verified 2026-11-01 on the parent at `main` `d5926ed7a` and on the umrs pin
`9b5b8ea2` (checked out in this worktree; every `umrs/…` line below was
re-read there). Counts marked *measured* come from in-process runs with an
isolated `HOME`, no database and no network.

**R4 already uses tool calling; skills are the manual.** The agent reaches
UMRS through native tools (`get_builtin_tools` in `r4/src/r4/companion/tools.py`):
*measured* 113 tools with a client stub, 62,160 chars of OpenAI function
schemas ≈ 15.7k tokens (o200k). With the stable prefix on (the default)
every LLM call sends the whole catalog: `_prepare_turn_payload` calls
`select_active_tools(tools=self.tools, active_tool_names=set())`
(`r4/src/r4/companion/session.py:1106`); active skills only turn tools into
refusals (`SKILL_REFUSAL`, `turn_context.py:45`), they do not shrink the
array. The always-on `umrs` skill (`DEFAULT_ACTIVE_SKILLS`,
`skills.py:230`) adds ≈ 3.1k tokens of body plus ≈ 0.8k of skill catalog,
including a static "full function inventory" dated 2026-09-19. The
workflow/hpc/job tools themselves are small (19 tools, ≈ 1.7k tokens); the
cost is not the call mechanism but tool *shape*, *drift* and *polling*.

**Drift.** Tools, skills and the MCP platform server are written by hand
against UMRS and have diverged:

- A skill audit on 2026-11-01 (two refuters per finding) confirmed **31
  drift defects across 17 builtin skills**: `hpc/SKILL.md` and the D-37
  refusal string cite `r4 job submit`, which does not exist
  (`_HPC_OFF_CLUSTER_REFUSAL` in `tools.py` at `d5926ed7a`, fixed in S0; the
  only `add_parser("submit")` is `r4/src/r4/_cli/vision.py`);
  `hpc_submit` told the agent to track a durable `job-<hex>` id with
  `hpc_job_status`, which could not resolve it; `rag_upload` needs an
  approved proposal that `data_management`/`inference_env` never mention;
  vendored research skills cite Claude Code tools, `/loop` and `cron.add`.
  No test checked that a skill names only tools and CLI verbs that exist.
- `mcp-servers/src/r4_mcp/servers/platform.py` hand-duplicates 20 native
  tools over raw httpx (`PlatformServer`, `:36`; 20 `@Tool`). The copies
  drifted: `endpoint_status` takes `job_id` (`:154-165`) where native takes
  `instance_id` (`id@site`); `job_list` defaults to limit 100 with
  `job_type` (`:227-260`) versus native 20 with `site`; MCP `job_cancel`
  sends `reason`, native has none. Its `@Tool` schema format is flat and
  lossy (`mcp-servers/src/r4_mcp/base.py:49-81`: no `items`/`properties`),
  and its `workflow_run` bypasses the R4 permission ladder: only the local
  `R4_WORKFLOW_POLICY_FILE` guard (`platform.py:95-112`) stands between the
  model and `POST /gw/workflows/{name}/trigger`.
- `gap D-27` still says "Open: `tool_search` stays unwired"; it has been
  wired since G18 (`session.py:364-366` → `register_session_tool_search`).

**UMRS already describes itself.** `_create_app()`
(`umrs/src/umrs/server/http_sidecar.py:864`) builds offline: `_build_db_pool`
(`:65`) fails soft, and `umrs/tests/test_surface_characterization.py:65-76`
already builds the app with it patched to `None`. *Measured at the pin:*
`app.openapi()` gives OpenAPI 3.1.0, 346 paths, 417 operations, 269 component
schemas in ≈ 2 s. But: no route sets `operation_id` (`grep operation_id=
umrs/src/umrs/api` → 0), so ids are FastAPI's derived names and one collides
today (`rail_gateway_…` in `api/companion_venue.py`); 1102 gateway identity
headers (`x-user-*`, `x-verified-*`, `*-hmac`) appear as ordinary parameters
(e.g. `trigger_workflow`, `umrs/src/umrs/api/workflows.py:1358-1365`), which a
naive generator would hand to the model as forgeable inputs; only ≈ 150 of
417 operations have a typed success response — workflows 1 of 18 (upload,
`:1286`), and 9 of `api/knowledge.py`'s 10 `response_model`s are
`dict[str, Any]`. `openapi_url` is never disabled (`http_sidecar.py:968-973`
override only docs/redoc). The parent has the export pattern already:
`tools/rpc_schema_export.py` and `tools/control_schema_export.py`
(`--write/--check` into `contracts/`), and `tools/route_inventory.py`
(`build_inventory`, `:121`) walks `_create_app()` routes as a CI gate. The
gateway maps `/gw/<family>/` to `/umrs/<family>/` mostly one to one
(`gateway/nginx/conf.d/server.conf:599`, `:1380`); the exceptions are
`/gw/a2l/` (rewritten to `/umrs/a2l/ledger/` on a separate `a2l` upstream),
`/gw/knowledge/ingest/` (→ `/umrs/k2/`), `/gw/loopjob/` (→ `/umrs/loops/`,
with approvals and workflows sub-paths) and `/gw/v1/` (→ `/umrs/`). UMRS
paths are what the SDK and MCP call; the generator needs that path map.

**Workflows are untyped at the tool.** The per-workflow `parameter_schema`
is read from `workflow.json` (`workflows.py:167-184`) and returned by
describe (`:187`), but `workflow_run.parameters` is a JSON *string*
(`r4/src/r4/companion/workflow_tools.py:239`), so the schema never reaches
the model's tool input, and UMRS checks only that required top-level keys
are present (`_validate_params_against_schema`, `workflows.py:205-229`,
called at `:1393`): no types, enums or extra keys. `r4 workflow describe`
iterates the schema dict as if its keys were parameters and prints `type` /
`properties` (`r4/src/r4/_cli/workflow.py:587-591`).

**No preview, no push.** No dry-run, quote or estimate exists for jobs or
workflows (grep over `api/jobs.py`, `api/workflows.py`: none; the only
API dry-run is model capability detect, `api/models.py:1210`).
`workflow_wait` blocks the round in a 1 s client busy-poll up to 300 s
(`workflow_tools.py:16`, `r4/src/r4/workflows.py:762-790`); MCP's copy
sleeps 1 s per loop (`platform.py:443`). Nothing pushes a job event to an
agent. What exists to build on: `platform_notifications` (migration 0109;
`umrs/src/umrs/platform_notifications.py`, emitter wired at boot
`http_sidecar.py:682`; only producer the engine-loop budget stop, only reader
the web dashboard); a single post-completion callback pair on the JobStore
(`umrs/src/umrs/jobs/store.py:360-372`, fired at `:1360-1365` and `:1491-1494`,
occupied by auto-ingest, `jobs/adapters.py:1735-1752`), with no hook on
cancel (`cancel_job`, `:1630`) or lost; session lineage
`spec.origin.session_id` (`stamp_spec_origin` `:1575`,
`list_jobs_by_origin_session` `:1608`, no caller) stamped only by the
remote-session rail (`api/companion_venue.py:598-612`) — r4 never stamps it.
In r4, async sub-agents already deliver bounded
`<subagent-notification>` blocks marked "not a user message"
(`r4/src/r4/companion/async_agents.py:57-68`), drained once at the top of each
tool round (`agent.py:3166`, `_drain_async_notifications` `:3246`). The
knowledge SSE stream (`umrs/src/umrs/knowledge/events_sse.py:230`) is
in-memory with a 10-event replay buffer (`:294-296`) and carries ingest
events only; the ZMQ plane is request/reply.

**Results.** Auto-ingest is opt-in per job (`spec["ingest_results"]`,
`jobs/adapters.py:1766`), set unconditionally for durable workflow runs
(`server/workflow_registry_handler.py:751`); the per-step `StepOutput.ingest`
flag (`umrs/src/umrs/workflow/models.py:75`) is validated but never consumed.
One result has up to four provenance records (artifact registry 0068, a2l
0077, knowledge_artifacts metadata + KG `produced_by` edges, attempt metrics)
and no receipt joins them; `/umrs/assets` (`asset_index/fanout.py`) already
joins lineage at read time. (The project-wide artifact sweep the audit
flagged is already gone at the pin: `_collect_artifact_ids`,
`jobs/knowledge_adapters.py:4803-4811`, collects only the job's own outputs
since ops-watch #612–#620, 2026-10-27.)

**Ownership forces the shape.** UMRS owns durable jobs, scheduling, quotas
and the knowledge spine; R4 never keeps a second job store or reimplements
UMRS logic (CONSTITUTION §1.3, lines 68-78); no fifth loop/team mechanism
(§1.6). `r4-contracts` is stdlib-only (INV-10, `r4-contracts/pyproject.toml:27`)
and already holds the workflow capability contract
(`WORKFLOW_CAPABILITY_RISK` safe/elevated/privileged,
`r4_contracts/workflows.py:38`; `WORKFLOW_TOOL_HELP` `:53`;
`workflow_tool_descriptor` `:307`) and `TOOL_TAINT_CLASSES`
(`r4_contracts/taint.py:28`).

## Decision

UMRS capabilities reach agents through **one capability layer generated
from UMRS**, consumed by two thin adapters (native R4 tools, the MCP server).
Skills stay, as **procedures** (recipes), and are linted against the real
inventory. Compute submissions become **plan → commit**, owned by UMRS. Job
completion is **pushed** into the turn from a durable UMRS store. No second
job store, no client-side quote, no new loop engine.

### D-1 One capability manifest, generated from UMRS

- umrs exports `openapi.json` from `_create_app().openapi()` offline (DB pool
  patched to `None`, as `test_surface_characterization.py` does).
- The parent commits `contracts/umrs-http/v1/openapi.json` (identity headers
  `x-user-*`, `x-verified-*`, `*-hmac` stripped) and
  `contracts/umrs-http/v1/agent-tools.json`: an **allowlist** of
  agent-exposed operations. Each entry carries an explicit `operation_id`
  (set on the route, or `openapi_extra={"x-r4-tool": …}`), a risk class
  (generalising `WORKFLOW_CAPABILITY_RISK`: `safe | elevated | privileged`),
  a taint class (`TOOL_TAINT_CLASSES`), an ADR-030 scope
  (`companion-only | mcp-eligible`), and whether `site` applies.
- An exporter with `--write/--check` (the `tools/rpc_schema_export.py`
  pattern, extending `tools/route_inventory.py`'s walker) gates drift in CI.
- **Type before expose:** an operation enters the allowlist only with a
  pydantic request model *and* response model. Untyped routes stay out until
  typed.
- `r4-contracts` stays stdlib-only: JSON plus stdlib descriptors there; the
  SDK code lives in `r4`.

### D-2 Two adapters, one source

Native R4 tools and the MCP platform server are both generated from the
manifest. Native tools go through the r4 SDK, keeping site routing and the
`PermissionManager` ladder. The MCP base gains a raw `inputSchema` tool
definition (no lossy flattening). Hand-written `platform.py` is retired after
a parity test. ADR-030 exclusions are enforced by the manifest's scope field.
Generated tools **keep today's names** where they exist: permission rules,
trust rows and skills key on names.

### D-3 Typed workflows by schema, not by name

No per-workflow tool names (they would break the byte-stable tool array of
wave 80 and the name-keyed permission and trust model). Instead:

- `workflow_run.parameters` becomes a real JSON object (and `input_files` a
  real array);
- the client validates against the describe payload's `parameter_schema`
  before submit and returns the schema in the error;
- UMRS upgrades `_validate_params_against_schema` to full JSON Schema
  (types, enums, `additionalProperties`): the server stays authoritative;
- `tool_search` indexes registered workflows (name, description, parameter
  names), so "run my relaxation" finds `workflow_run` and the workflow;
- the `r4 workflow describe` parameter-printing bug is fixed.

### D-4 Plan → commit for every compute submission, owned by UMRS

- `POST /umrs/jobs/plan` and `POST /umrs/workflows/{name}/plan` validate,
  resolve site, partition, account and quota units, and return
  `{plan_id, digest, resolved_spec, site, partition, quota_units_left,
  estimate (when known), expires_at}`.
- **A plan is a pure computation, not a row.** Nothing is stored: `digest`
  is a server HMAC over `(project, user, resolved_spec, expires_at)`, and
  `plan_id` is derived from it. No job row exists before commit, so no
  client-side store, no new table and no migration.
  `JobStatus.PLANNED` is deliberately *not* reused as a plan marker: it is
  the status every job is created in (`JobStore.create_job`), fan-out
  children wait in it and are promoted when their siblings finish,
  `POST /umrs/jobs` with `enqueue_immediately=false` leaves a job in it, and
  `create_attempt` admits PLANNED → RUNNING directly (the client-attempt
  admission of ADR-092 slice 7). A plan stored as a PLANNED row could run
  without a commit.
- `POST /umrs/jobs/commit` and `POST /umrs/workflows/{name}/commit`, with
  `{resolved_spec, expires_at, digest}`, verify the HMAC, re-run the same
  resolution and quota check, then create and enqueue the job with
  `plan_id` as its idempotency key (a retried commit enqueues once). A bad
  digest, an expired plan, or a resolution that no longer matches is a
  typed refusal.
- R4 tools: `job_plan` / `workflow_plan` write nothing, so they are
  read-only (SAFE) and run in chat and plan mode; this stays inside ADR-037
  Rule 2b, which keeps only POST *mutations* out of `PLAN_READ_ONLY`
  (a plan POST mutates nothing). `job_commit` is RISKY.
- **Auto-commit is a deterministic, fail-closed UMRS check.** The quote is
  UMRS's, so the project's auto-commit threshold is checked by UMRS on
  commit: a commit flagged `auto` is refused unless the re-resolved quote is
  under the threshold, and refused when no threshold is set. In autopilot,
  R4 sends `job_commit` without a card only when ADR-104 rung 10 allows it
  **and** a server trust checker is wired: this amends rung 10 for
  `job_commit` only (the "no checker" leg asks instead of allowing). The
  ADR-076 `tool_gate/1` stays as extra caution: it can only add a question
  (ADR-076 D-3: a decision is evidence, never permission), so the threshold
  never depends on the decision model being on. *(2026-11-05, [ADR-104](104-one-mode-one-permission-decision.md) amendment 2:
  autopilot sends `job_commit` without a card whether or not a checker is
  wired; it is always an `auto` commit, so the UMRS threshold above decides.)* Commit arguments derived
  from RAG, MCP or lab results stay taint-fenced (ADR-104 rung 4).
- A per-project `require_plan` policy makes UMRS refuse a direct create or
  trigger, so MCP and CLI callers get the same safety.
- `hpc_submit` stays refused off-cluster after S3. Plan/commit does not move
  the script: the `hpc_script` spec carries only `script_path`
  (`umrs/src/umrs/jobs/hpc_script.py`, `validate_hpc_script_spec`) and UMRS
  runs `sbatch <script_path>` over SSH on the cluster, where a workstation
  path does not exist. The refusal can go only with a script-staging step
  (script content, or an uploaded object key, in the spec, staged by UMRS
  before `sbatch`); that step is not part of S3 and needs its own slice.

### D-5 Pushed job events, no second job store

UMRS:
- the JobStore post-completion callbacks become a **list** (auto-ingest and
  events coexist); cancel and lost get the hook too;
- a job-terminal emitter writes `platform_notifications` rows of kind
  `job.succeeded | job.failed | job.cancelled | job.lost` carrying `job_id`,
  `job_type`, `execution_id`, `origin.session_id` and a one-line summary;
- `GET /umrs/notifications?kind=job.*&origin_session=<id>&after=<cursor>`:
  a durable cursor read, exactly-once per consumer **by cursor, not by
  `read_at`**, so the TUI, VS Code and a venue can share one session.

R4:
- every submission stamps `spec.origin.session_id`;
- a `JobWatch` beside `AsyncAgentPool` is drained at the same round boundary
  (`agent.py` round-top drain) and emits bounded `<job-notification>` blocks
  with the same shape and "not a user message" line as sub-agent
  notifications; one cheap cursor read per round, with a 30 s floor while
  idle;
- `wait_any(handles, timeout)` blocks on the watcher; `workflow_wait`
  remains as a wrapper;
- a completion while idle becomes a bridge UI event (TUI / VS Code toast),
  **never an auto-turn** (ADR-104: the console never acts by itself).

ZMQ stays request/reply (no protocol bump); the knowledge SSE stream is not
reused (in-memory, lossy).

### D-6 Code mode for data-heavy reads only

A tool `umrs_script` runs agent-written Python in the ADR-101 sandbox
(required on for this tool; it refuses by name when the sandbox is
unavailable) with a generated `umrs` SDK limited to manifest operations of
risk class `safe`. **Credentials never enter the sandbox:** the SDK talks to
a companion loopback proxy that injects the session credential and refuses
non-safe operations; mutations go through tools so the ladder sees them.
**Amends ADR-101 D-6**, which allows exactly one way out of the sandbox (the
web forward proxy: ports 80/443 only, not started under web access `none`,
loopback and private addresses refused by D-7). `umrs_script` adds a second
per-session forward used only by this tool: a unix socket forwarded into the
namespace on Linux; on macOS, where Seatbelt denies unix-socket connects, an
ephemeral loopback TCP port with a per-session nonce. It runs under web
access `none` too, reaches only the companion proxy (never the network), and
refuses everything but manifest `safe` operations.
Output is size-bounded and carries a taint class registered in
`TOOL_TAINT_CLASSES`, so code mode cannot launder retrieved text around
rung 4.

### D-7 Skills are recipes, and they are linted

A hermetic test fails when a builtin skill names a tool or an `r4` CLI verb
that does not exist, or leaves a `{{contract:…}}` placeholder unresolved.
Lands in this wave with the 31 drift fixes. When D-1 lands, the tool
inventory the lint checks against comes from the manifest.

### D-8 A situational brief rendered by UMRS

`GET /umrs/agent/brief?project=` renders live facts: reachable sites and
partitions, quota left, workflows the caller may run, KGs and RAG corpora,
this session's running jobs. R4 injects it into the per-turn volatile
reminder (never the stable prefix), fenced as data. The `umrs` skill shrinks
to procedure; its static inventory goes. **Amends ADR-033:** reviewed skills
remain the instruction source; the brief is data, not instructions.

### D-9 Agent-authored compute plans reuse ADR-092

The plan IR gains a closed, deterministic `workflow` step kind: it calls a
registered workflow through D-4 plan/commit, with no LLM turn. Such a step
is not offered to an agent, so `TEAM_AGENT_DENY` (which governs the tools
offered to plan agents and team roles) does not apply to it; it runs under
the plan card's single approval plus the D-4 commit. `job_commit` is added
to `TEAM_AGENT_DENY` when it lands (S3; `hpc_submit` is already there), so
plan agents cannot commit compute themselves. `team_plan_run` stays never-auto (MD-5). No fifth
engine (CONSTITUTION §1.6).

### D-10 Receipts, and ingest that follows the step

- `GET /umrs/jobs/{id}/receipt`: a read-time join (the
  `asset_index/fanout.py` style, no new table) over the job, its commit
  attempt, `artifact_registry` rows, the a2l attempt artifact and the KG
  ingest job ids. Tools return receipt handles, not blobs.
- Auto-ingest honours the declared-but-unused `StepOutput.ingest` flag and
  the project's ADR-099 / knowledge allowlist.
- Agent-originated knowledge stays a proposal (CONSTITUTION §2:
  `knowledge_graph_decide` never auto-approves).

### Out of scope

A second job store, client-side quote or retry logic, per-workflow tool
names, an auto-started turn on job completion, MCP Tasks inside R4, a ZMQ
push verb, and selection by a second LLM call (ADR-037 Rule 1).

### Why

**Why not skills only.** Skills cannot carry the facts they describe: the
audit found 31 places where a reviewed skill named a verb, tool or id shape
that the code did not have, and the always-on `umrs` skill re-states an
inventory that UMRS can render live. A skill is the right home for
*procedure* (which tool, in which order, what to check) and the wrong home
for *capability truth* (names, schemas, sites, quotas). Skills also cannot
remove polling or give a preview of cost; only a server surface can.

**Why not MCP everywhere.** Inside R4 the native tool *is* tool calling
already, in process: wrapping it in MCP adds a transport and loses what the
native path has — site routing, the `PermissionManager` ladder (the MCP
`workflow_run` bypasses it today), the taint fence and the decision gate.
MCP Tasks (call now, fetch later) are still experimental in the 2025-11-25
spec. MCP stays the surface for *external* agents, generated from the same
manifest (D-2), so neither side drifts.

**Why generated, and why UMRS-owned.** UMRS owns the operations, their
types and their authorization; generating both adapters from its OpenAPI
removes the drift class at the source, and "type before expose" turns the
missing response models into a visible backlog instead of silent
`dict[str, Any]`. Plan/commit, quotes and job events live in UMRS because
quotas, scheduling and durable jobs do (§1.3); R4 keeps only adapters and
UX.

**Why push.** A busy-poll blocks the round for up to 300 s and sends an
HTTP status request every second; a durable cursor over `platform_notifications` reuses a
store that already exists and delivers into the same round seam as
sub-agent notifications.

## Consequences

- One manifest describes what an agent may call; native and MCP tools stop
  drifting, and the MCP `workflow_run` stops bypassing the ladder.
- The model sees a workflow's real parameter schema and gets a typed error
  with the schema when it is wrong; UMRS rejects bad types server-side.
- Every compute submission can be previewed with resolved site, partition
  and quota before anything runs; autopilot can commit cheap plans without a
  card and still asks for expensive ones.
- Waiting on jobs costs no tool rounds; completions arrive at the next round
  boundary or as a toast while idle.
- Fewer tokens per turn once the `umrs` skill loses its static inventory
  (D-8); data-heavy reads can run as one sandboxed script (D-6).
- Cost: UMRS work on several routers (operation ids, response models, plan
  and commit routes, a callback list, a notifications route, the brief, the
  receipt), plus gateway locations with `auth_request` (CONSTITUTION §2) for
  the new families: no `/gw/notifications/` or `/gw/agent/` location exists
  today (the existing notifications route is
  `/umrs/web/dashboard/notifications`), unless they are mounted under
  existing families (e.g. `/umrs/jobs/events`). Generated tools must keep today's names,
  which constrains `operation_id` choices. `platform.py` users see the
  native parameter names (`instance_id`) after S5.
- NOT in scope: see *Out of scope* above.

## Alternatives Considered

- **(a) Skills only, hand-written tools (status quo).** Drift measured at 31
  defects on 2026-11-01; polling and the missing preview stay.
- **(b) Everything over MCP inside R4 too.** No gain in process; loses the
  PermissionManager and site routing; MCP Tasks are experimental.
- **(c) Per-workflow tool names.** Breaks the byte-stable tool array (prompt
  cache) and the name-keyed permission and trust model.
- **(d) Code mode for everything, mutations included.** Bypasses the ladder,
  the taint fence and the decision gate.
- **(e) Knowledge SSE or a ZMQ push verb for job events.** SSE is in-memory
  and lossy (10-event replay); a ZMQ push verb is a protocol bump for what a
  durable cursor read gives.
- **(f) Client-side quote or estimate.** Quotas and scheduling are UMRS's
  (§1.3); a client estimate would be a second, drifting copy.

**External survey (2026-11-01).** Anthropic,
[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp):
presenting tools as code cut one workflow from ≈ 150k to ≈ 2k tokens (D-6).
Cloudflare, [Code Mode: give agents an entire API in 1,000 tokens](https://blog.cloudflare.com/code-mode-mcp/):
two tools, `search()` and `execute()`, over the OpenAPI spec, credentials
kept in the host (D-1, D-6). MCP 2025-11-25
[Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks):
call now, fetch later, experimental and requestor-polled (alternative (b)).
Argonne et al., [Experiences with MCP servers for science and HPC](https://arxiv.org/abs/2508.18489)
(arXiv 2508.18489): thin MCP wrappers over Globus Compute/Transfer/Search and
facility status APIs (D-2). JSC [jobstate](https://pypi.org/project/jobstate/):
Slurm submission as dry run plus a separate `confirm_submit`, server-side
limits and budget guards, `wait_for_jobs` (D-4, D-5).
[secure-cluster-mcp](https://pypi.org/project/secure-cluster-mcp/): read-only
cluster tools auto-allowed, submission behind confirmation (D-4, D-6).
Microsoft [Discovery](https://azure.microsoft.com/en-us/blog/transforming-rd-with-agentic-ai-introducing-microsoft-discovery/):
a copilot over a knowledge engine plus HPC (the overall shape).

## Rollout-status (2026-11-01)

Gap row **D-66** (new) is **Partial**. Slices, in order:

| Slice | Sub-decision | Status |
|---|---|---|
| S0 | ADR + D-7 lint + drift fixes + three tool fixes | **Landed in this wave** (`wave/umrs-agent-capability-layer`, not merged) |
| S1 | D-1 manifest + D-3 typed `workflow_run` (umrs + parent) | open |
| S2 | D-5 pushed job events | open |
| S3 | D-4 plan → commit | open |
| S4 | D-8 brief + `umrs` skill slimming | open |
| S5 | D-2 MCP regeneration + `platform.py` retirement | open |
| S6 | D-6 code mode | open |
| S7 | D-10 receipts + step-level ingest | open |
| S8 | D-9 `workflow` step kind in the ADR-092 IR | open |

S0 contents: the lint test `r4/tests/test_skill_lint.py` (D-7); 31 drift
fixes over 17 builtin skills; `hpc_job_status` accepts durable `job-<hex>`
ids; `mcp_list_tools` prints each tool's input schema; `model_details`
returns `storage_path`. Order rationale: S1 removes drift at the source; S2
is the largest turn and token win; S3 needs S1's manifest risk classes.

Corrections recorded with this ADR: gap D-27's "Open: `tool_search` stays
unwired" is stale (wired since G18, `session.py:364-366`); the audit's
"project-wide `_artifacts_from_store` sweep" is already fixed at the pin
(`_collect_artifact_ids`, ops-watch 2026-10-27), so S7 carries no work for it.

## Rollout-status (2026-11-02)

**Accepted** by the maintainer 2026-11-02 ("accept. please proceed"). **S1
landed** on `wave/umrs-agent-s1` (umrs `wave/umrs-agent-s1`, ledger (162)):

- **D-1.** `tools/umrs_agent_manifest.py --write|--check` builds the UMRS app
  offline and writes, from the hand-curated
  `contracts/umrs-http/v1/agent-tools.allowlist.json` (13 operations:
  workflows ×6, jobs ×4, rag ×3), `agent-tools.json` (tool, method, path,
  risk, taint, scope, site, request/response schema) and
  `agent-openapi.json`. Refinement of D-1: only the **agent subset** is
  committed (the allowlisted paths plus the component schemas they reference,
  63 kB), not the full specification, so a UMRS route change elsewhere never
  churns the contract; the sidecar still serves the full `/openapi.json`.
  Gateway identity headers (`x-user-*`, `x-verified-*`, `*-hmac`) are
  stripped. An operation is refused unless it has an explicit `operation_id`
  and typed request and response. Drift gate: `tests/test_umrs_agent_manifest.py`
  (repo-root lane); r4 parity: `r4/tests/test_umrs_agent_manifest_parity.py`
  (every row names a native tool; risk ⇔ `SAFE_TOOLS` / `RISKY_TOOLS`; taint ⇔
  `TOOL_TAINT_CLASSES`; site ⇔ the multi-site schema; workflow rows ⇔
  `WORKFLOW_CAPABILITY_RISK`; ADR-030 families never `mcp-eligible`).
- umrs: explicit `operation_id` on the 13 routes (named after the native
  tool); pydantic response models for the six workflow routes
  (`umrs/src/umrs/api/workflow_models.py`, `extra="allow"`,
  `response_model_exclude_unset`: payloads unchanged).
- The parity test found six misclassified tools, now fixed: `job_list`,
  `job_status`, `rag_query` and `rag_list_documents` are reads (chat mode
  asked before each); `job_retry` (re-runs compute) and `rag_upload` (writes
  the knowledge store) are `RISKY_TOOLS`. Other reads outside `SAFE_TOOLS`
  (`endpoint_*`, `model_*`, `mcp_list_*`) are outside S1's allowlist; the
  parity test classes them when their family joins.
- **D-3.** `workflow_run.parameters` is a JSON object and `input_files` an
  array (legacy JSON strings still accepted; an object used to raise
  `TypeError`); the client pre-checks against `parameter_schema`
  (`r4_contracts/workflow_params.py`, stdlib, top level, JSON Pointer paths)
  and answers every mismatch plus the schema in one
  `workflow_parameters_invalid`; UMRS validates the full JSON Schema on
  trigger (`jsonschema`, up to 20 `{path, message}` rows, `missing` kept; an
  invalid workflow schema falls back to the required-keys check). Keys UMRS
  injects (`input_files`, `project_id`) are checked only when the schema
  declares them. `tool_search` returns registered workflows as
  `workflow_run` hits (60 s per-session cache; failures cached, never a
  broken search). `r4 workflow describe` lists `properties` (it printed
  `type` / `properties` as parameter names) and the workflow name.
- Review fixes in the same wave (independent review of S1):
  - UMRS does not evaluate the regex keywords (`pattern`,
    `patternProperties`) of a workflow's schema: authors are ordinary users
    and Python `re` holds the GIL, so one backtracking pattern
    (`^(a+)+$`, 0.35 s at 24 characters, about 4× per 2 more) stalled the
    sidecar for everyone. A level with `patternProperties` also drops its
    `additionalProperties` check, so no valid run is refused for it.
  - Text sent for a declared integer/number/boolean is validated as that
    type (`r4 workflow run --param k=v` and the TUI trigger line send text);
    the run still receives the parameters unchanged.
  - A self-referential schema falls back instead of a 500; `mcp_tools`
    (`--mcp-tool`) is injected; a malformed `triggers`/`requirements` no
    longer breaks describe; `jsonschema>=4.18`.
  - The client pre-check accepts what UMRS accepts (`3.0`, text scalars, a
    late-supplied `project_id`) and is skipped when policy denies describe.
  - `tool_search` reads the workflow catalog only with a live platform (the
    cached TCP probe) and a query with terms; it indexes name and
    description, not scope tags.
  - The drift test records the fastapi/pydantic versions in
    `agent-openapi.json`; with other versions it compares the shape, not the
    text, so a library upgrade does not turn unrelated PRs red.
- Also: ADR-106's `POST /umrs/endpoints/{job_id}/roll` carried no auth marker
  and failed the route-inventory audit once the pointer moved; marked
  `# auth: verified` (admin-gated).

Gap row **D-66** stays **Partial** (S0 + S1). Next: S2 (pushed job events).

## Rollout-status (2026-11-02, S2)

**S2 landed** on `wave/umrs-agent-s2` (stacked on S1; umrs `wave/umrs-agent-s2`,
ledger (163)): D-5, pushed job events.

- UMRS: a process-wide job-terminal listener (one server process holds
  several `JobStore` instances, so a per-store listener would miss cancels
  served by the sidecar) fires once for succeeded, failed-not-requeued,
  cancelled and lost (from the reconciler, after it decides not to requeue);
  `jobs/job_events.py` writes one `platform_notifications` row
  `job.<state>` with `{job_id, job_type, status, execution_id, session_id,
  exit_reason, finished_at}`. `GET /umrs/jobs/events` (explicit
  `operation_id="job_events"`, typed) serves the verified caller's own rows
  after a cursor; `tail=true` answers the current cursor. Job create and
  workflow trigger stamp `spec.origin.session_id` from the existing D-52
  header `x-r4-session-id`. Job rows are left out of the dashboard's unread
  list (they are read by cursor, never marked read). No new table.
- r4: `companion/job_watch.py`. The session's `JobWatch` (top-level
  companion only: sub-agents share the client and must not take the
  parent's notices) takes its event cursor *before* a submitting tool runs
  (`workflow_run`, `workflow_trigger`, `workflow_replay`, `hpc_submit`,
  `job_retry`), so a job that ends within the call is never behind it; it
  keeps the returned job and run ids in an ephemeral watch set (no job
  state: CONSTITUTION §1.3) and, while that set is non-empty, reads the feed
  at the round boundary (`agent.py`, beside the sub-agent drain) at most
  every 5 s, up to four pages. A matching end becomes a bounded
  `<job-notification>` block, appended only when something ended (stable
  prefix intact); each end is delivered once. Matching is by watched id
  (`id@site` handles accepted), or by the session id UMRS stamped.
  `job_wait_any` (SAFE) blocks on the same feed for several jobs or runs;
  the turn's cancel wakes it; a handle that ended where the feed cannot show
  it (already ended, non-durable lane) is answered from a status read every
  few polls; ends it reads for other jobs go to the next drain. A feed that
  fails three times backs off for 60 s (an older UMRS without the route) and
  the tool says `events_unavailable` instead of a false timeout.
- The `hpc` and `workflow_ops` skills name the announcement and
  `job_wait_any` instead of polling; the allowlist gains `job_events` →
  `job_wait_any`.
- Independent review fixes in the same wave: the cursor-before-submit
  ordering, top-level-only draining and serialised reads, cancel, site
  handles, back-off, once-only delivery, paging (r4); the failed-path event
  fired after the pooled connection is released, the reconciler's `lost`
  only when its guarded update wins a race with a cancel (its requeue too:
  a cancelled job is never queued again), and an index on
  `platform_notifications (user_id, project_id, id)` for cursor reads (umrs).
- Not in S2 (known limits): the bridge UI event for a job that ends while no
  turn runs (it is delivered at the next turn's first round, like sub-agent
  results); fan-out parents emit no event of their own (their children do);
  a run on another site is not on the home feed (`workflow_wait` with
  `site=`); a trigger whose declared `project_id` differs from the session's
  project lands its event in that project; no retention policy for job rows
  yet; the MCP server keeps its own polling `workflow_wait` until S5.

## Rollout-status (2026-11-02, S3)

**S3 landed** on `wave/umrs-agent-s3` (stacked on S2; umrs `wave/umrs-agent-s3`,
ledger (164)): D-4, plan → commit, for workflow runs (the agent's compute
path; `hpc_submit` stays cluster-only and a generic job plan is a follow-up).

- UMRS (`umrs/src/umrs/workflow_plans.py`): `POST /umrs/workflows/{name}/plan`
  (`workflow_plan`) resolves without side effects — workflow version and
  steps, the validated parameters, lane, partition, account, GPU steps, an
  upper-bound wall time (per-step attempt bound × attempts, the Slurm
  `--time` on HPC lanes) and GPU-hours, the caller's process quota, the
  project's plan policy — and signs `signed` (project, user, workflow,
  version, parameters, input files, lane, partition, account, GPU steps,
  wall bound, GPU-hours, expiry) with HMAC-SHA256 keyed from the
  server-only `UMRS_SECRET_KEY` (label `umrs-workflow-plan-v1`; the
  gateway-shared handshake secret is deliberately not used; without the key
  a per-process random key, so plans do not survive a restart). TTL 900 s;
  nothing is stored. `POST .../commit` (`workflow_commit`) verifies the
  digest (`compare_digest`), the expiry and that user, project and workflow
  are the caller's; re-resolves and refuses a stale plan; runs only the
  signed parameters through the trigger path with `plan_id` as the
  idempotency key (a second commit returns the same run). Typed refusals
  `workflows.plan_invalid | plan_expired | plan_mismatch | plan_stale |
  auto_commit_refused | plan_required | policy_unavailable |
  lane_unavailable | commit_unavailable`.
- Project policy (migration 0131 on `project_budgets`, owner-or-admin
  `PUT /umrs/projects/{id}/budget`): `auto_commit_max_gpu_hours` — a commit
  flagged `auto` is refused unless the plan's signed GPU-hours fit it, and
  refused when unset; `require_plan` — trigger, run-definition replay, ZMQ
  `RUN_WORKFLOW` and the research loop's own trigger refuse with
  `plan_required`, so only a commit starts a run.
- r4 (`companion/plan_commit_tools.py`): `workflow_plan` (SAFE; usable in
  plan mode) and `job_commit` (RISKY, companion-only: MCP has no ADR-104
  ladder). Whether a human approved the commit is recorded by plan digest
  only where the approval callback said yes (an approval card, a fenced ask,
  an ADR-076 decision-gate ask) and popped by the commit tool; anything else
  (autopilot on the server allowlist, an "always" grant, a rule) is `auto`.
  The `job_commit` card shows only what UMRS signed. **ADR-104 rung 10 is
  amended for `job_commit` only**: in autopilot without a server trust
  checker it asks instead of the legacy process-global allow. `job_commit`
  is denied to team-plan agents (`TEAM_AGENT_DENY`) and watched by S2's
  `JobWatch`. The `workflow_ops` skill plans first for costly compute.
- **What the threshold binds** (stated so nobody over-reads it): `auto` is
  the client's claim. The threshold makes the R4 companion's unattended
  commits fail closed; it does not stop a holder of the user's credentials
  from calling the API with `auto: false`. `require_plan` is the server-side
  guarantee that every run in a project was planned first. A server-recorded
  approval intent per digest (ADR-057 rows) is the hardening path if needed.
- An independent security review found a FIFO approval queue that let a
  human yes to one (malformed) commit label a later unattended one as human,
  a crash for team-plan agents, unsigned GPU-hours on the card, and the
  research loop's direct trigger bypassing `require_plan`; all fixed here.
- Known limits: `require_plan` is not enforced where no database pool exists
  (ZMQ without the durable scheduler; S3 ingest auto-triggers); a plan signs
  the catalog version, not a hash of `workflow.json`; several sidecar
  workers need a shared `UMRS_SECRET_KEY`; the threshold counts GPU-hours
  only (a CPU-only run fits any threshold); migration 0131 must be applied
  before the new code (every trigger reads the policy and fails closed).

## Rollout-status (2026-11-05, S4)

**S4 landed** on `wave/umrs-agent-s4` (umrs `77b5734b`, ledger
(201)): D-8, the situational brief. Amendments to D-8 as written:

- **Where it lives.** `GET /umrs/projects/{project_id}/agent-brief`
  (`agent_brief`, typed), under the existing `/gw/projects/` family — not
  `/umrs/agent/brief`, so no new edge location. Authorisation is the
  project read rule (`_readable_project`), checked on a short-lived
  connection with a statement timeout before anything is aggregated.
- **What it replaces.** The per-turn `<project_state>` block (D-28) was
  built by the r4 client from about twelve HTTP reads on every send, sub-
  agents and team roles included, uncached. UMRS now renders it in one read:
  health, site (scheduler kind, staged lane, GPU, the partition rule and the
  site's allowed partitions — facts the client could not see), the service
  tier (ADR-106), quota, the project budget and plan policy (S3), ready
  endpoints, the workflows the caller may run, the caller's running and
  queued jobs (this session's first, marked from `spec.origin.session_id`),
  readable knowledge graphs, active research loops, MCP servers. RAG
  documents are left out (that list is too slow for every turn; the agent
  has `rag_list_documents`). Sections run concurrently on an 8-worker pool
  under a 2 s deadline; a failing one is named in `degraded`, never a 500.
  Cached 15 s per (user, verified project, admin, project, session).
- **Data, not instructions.** Rows are one-line names and states — no
  descriptions, goals or URLs; control and format characters and every
  angle-bracket lookalike are stripped so a row cannot close the client's
  fence. The text says it is data; it rides `<turn_context>`, never the
  stable prefix. When no section could be built the text is empty and r4
  adds no block (D-28: failure is absence).
- **Project-scoped sections only for the active project.** Endpoints,
  workflows, graphs, loops and MCP servers are built only when the path
  project is the caller's verified project (or for an admin); a circle
  affiliate reading another project gets the project-agnostic sections and
  an `omitted` list. (The review found the first cut listed another
  project's private endpoints to a circle affiliate.)
- **r4** (`companion/platform_brief.py`): one read per send at most, cached
  on the client for UMRS's `ttl_s` and shared by sub-agents; dropped after a
  submit or cancel tool so the next send sees the change. A 404 without a
  typed code is a UMRS without the route (the client builder is used for
  10 min); `projects.not_found` falls back for that project only.
- **S4b, same wave — the `umrs` skill loses its static inventory.** The
  always-on skill carried the console's whole bridge-verb list (TUI↔runtime
  RPC the agent cannot call) and said sub-agents could use it. The list moved
  to `builtin/umrs/references/bridge-verbs.md` (generated by
  `tools/sync_umrs_skill_inventory.py`, never injected, keeping the
  audit-coverage record); the skill keeps the slash catalog (the agent
  suggests slashes to the user) and points at the brief for live facts.
  SKILL.md 7,740 → 5,700 chars, about 500 tokens less on every turn.
  ADR-033 stays Proposed and is unaffected (skills remain the instruction
  source; the brief is data).

## Rollout-status (2026-11-05, S5)

**S5 landed** on `wave/umrs-agent-s5` (stacked on S4; ledger (202)): D-2 for
the MCP adapter. Amendments to D-2 as written:

- **The MCP platform server's UMRS tools are generated from the manifest.**
  `mcp-servers/src/r4_mcp/servers/generated_platform.py` builds one tool per
  `mcp-eligible` manifest row, named by its `operation_id`, with a raw JSON
  Schema input (`ToolDefinition.input_schema`, new in `base.py`) built from
  the path and query parameters and the inlined request body; it calls the
  gateway path (`/gw` + the UMRS path) with the server's Bearer token, checks
  arguments before any request, refuses `.`/`..` path values, and re-applies
  the local workflow policy guard (`workflow_plan` counts as describe;
  anything unmapped fails closed). Companion-only rows (`job_commit`) are
  never generated — the ADR-030 scope is enforced by construction. The
  manifest ships inside the package (`r4_mcp/contracts/`, kept byte-equal by
  `tools/umrs_agent_manifest.py --write|--check`) because the image's build
  context does not contain `contracts/`.
- The hand-written duplicates in `platform.py` (`job_list|status|cancel`,
  `workflow_list|describe|run|status|results|cancel`) are retired; every old
  tool name stays available, and five arrive (`job_events`, `job_retry`,
  `workflow_plan`, `rag_query`, `rag_list_documents`). Tools without a
  manifest row stay hand-written for now (`endpoint_*`, `model_*`,
  `platform_health`, `platform_guide`, `workflow_upload`, `workflow_wait`,
  the `workflow_trigger` alias). `rag_upload` (multipart) is not generated:
  a model-driven upload of a local file into the knowledge store needs its
  own decision.
- **S1 fix:** the agent OpenAPI subset still carried the legacy MVP identity
  *query* parameters (`caller_user_id`, `caller_project_id`, `is_admin`, …)
  and `x-r4-session-id`; any adapter generating tools from it would have let
  a model set them. The exporter now strips them (a test fails on the old
  file); real filters stay (`job_list.project_id` for admins,
  `rag_list_documents.project_id`, `job_events.session_id`).
- **Native r4 tools stay hand-written** (amendment): generating them would
  churn every tool for no behaviour change; the parity test
  (`r4/tests/test_umrs_agent_manifest_parity.py`) already pins them to the
  manifest's names, classes, taint and site support.

## Related

- [ADR-030](030-companion-scoped-tool-pinning.md), [ADR-033](033-tui-first-agentic-umrs-setup-with-default-skills.md), [ADR-035](035-enforcement-point-matrix.md), [ADR-037](037-tool-selection-phase.md), [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-059](059-transport-and-edge-dual-transport-umrs-edge.md), [ADR-076](076-decision-model-role.md), [ADR-092](092-team-plans-declarative-orchestration.md), [ADR-101](101-local-tool-sandbox.md) (D-6 amended by D-6 here), [ADR-104](104-one-mode-one-permission-decision.md) (rung 10 amended for `job_commit` by D-4)
- [CONSTITUTION](../../CONSTITUTION.md) §1.3 ownership, §1.6, §2
- [Gap analysis](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md) D-66, D-27, D-37
- `.github/instructions/companion.instructions.md`, `agent-safety.instructions.md`
