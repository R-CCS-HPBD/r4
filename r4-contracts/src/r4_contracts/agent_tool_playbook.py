"""Agent-facing tool playbooks — injectable without reading the codebase.

Discovery contract (agents must not reverse-engineer usage from source):
(a) tool schemas that import *_TOOL_HELP constants,
(b) ``platform_guide(topic)`` (Companion / MCP always-on tool),
(c) ``r4 guide`` CLI.

Adapters import these strings / call ``platform_guide`` — they never rephrase.
"""

from __future__ import annotations

from r4_contracts.workflows import WORKFLOW_TOOL_HELP  # noqa: F401

# 2.3.0 (2026-11-01, ADR-105 skill audit): UMRS ops multi-source line —
# site=<name> routes a call now; source_use only sets the NEXT session's
# source; model@source routes LLM calls only (endpoint_provision never
# splits it).
# 2.2.0 (2026-10-02): authoring states the upload contract (description +
# every step's depends_on mandatory; r4_contracts.workflow_package),
# parameter_schema, and what templating really renders ("{{model}}" is not
# filled in; leave model out to let the platform route the step).
# 2.1.0 (2026-09-08): EXPERIENCE_AGENT_GUIDANCE added — experience.json
# schema + umrs experience CLI are now discoverable without UMRS source.
# 2.0.0 (2026-09-07, WP-07): the RC1 project_research_* family retired; one
# canonical research spine (research loops on ADR-036 job hosting, workflows
# as the compute layer beneath them). The "rc1" topic is gone.
AGENT_TOOL_PLAYBOOK_VERSION = "2.3.0"

PLATFORM_GUIDE_POINTER = "Call platform_guide for full how-to; do not invent tool usage."

WORKFLOW_AGENT_GUIDANCE = """\
UMRS Workflows (workflow_* tools) are registered server pipelines:
list → describe → run → wait/status → results → cancel.
They are the compute layer beneath research loops: a real-mode loop runs each
experiment as a registered workflow (research_loop_start workflow_name=...).
Resolve inputs with lab_catalog_search / lab_file_read when needed.
"""

WORKFLOW_AUTHORING_AGENT_GUIDANCE = """\
Workflow bundle authoring — building workflow file sets for UMRS:
- A bundle is a directory (or .zip) with workflow.json: {name, version,
  description, source: {"type": "local_path", "path": "."},
  parameter_schema, execution_mode, requirements, resources, steps[]}.
- An upload is REFUSED unless (r4_contracts.workflow_package):
  1. description says what the workflow does, what it needs (inputs,
     parameters) and what it produces — users and agents read it first;
  2. EVERY step lists depends_on: the steps it waits for, [] for a step
     that starts the run (known names, no cycles). Never rely on step
     order: a step that uses the one before it says ["<that step>"].
  `r4 workflow lint <bundle-dir>` prints each problem with its line before
  you upload; a refusal lists them all at once too.
- parameter_schema declares the run parameters: {"type": "object",
  "properties": {name: {type, description, default, enum}}, "required":
  [...]}; `required` is enforced at trigger time, and the console shows
  them as the line to trigger with. Values come at trigger time
  (`r4 workflow trigger <name> --param k=v`, --params-json '{...}' or a
  params file) and fill every step's config as defaults (a key the step's
  own config sets wins). project_id comes from the active project; files
  come with --input-file.
- execution_mode: in_process (default, runs in the UMRS server) |
  subprocess | slurm | k8s — slurm/k8s run one cluster job per step.
- steps[i]: {name, entrypoint: "pkg.module:Class", depends_on: [step
  names], config: {...}, max_retries, retry_delay, timeout_seconds,
  requires: [capability tags], model, model_type ("llm"|"vision"|
  "embedding"), image (per-step Singularity .sif path or registry ref),
  routing_hints}. "extends": "name@version" reuses a registered step.
  A step with "gate": true is a manual-approval gate — no class runs and
  it needs no entrypoint; a human approves it via workflow_approve_gate /
  `r4 workflow approve`.
- Ready-made step classes ship in umrs.pipeline_sdk.templates:
  llm_step:LLMStep (config: model, system_prompt, prompt_template,
  temperature, max_tokens, output_schema; emits {"response_text", ...}),
  vision_step, code_step, notebook_step, ocr_step, audio_step, video_step.
  Custom classes must be importable inside the UMRS runtime.
- Templating: only an LLMStep's prompt_template is rendered, and only
  {{ params.<key> }} and {{ steps.<step_name>.<output> }} (e.g.
  {{ steps.read.response_text }}). Leave config.model out to let the
  platform route the step (by model_type/requires), or name a served
  model — nothing fills in "{{model}}". Input files are S3 keys resolved
  via lab_catalog_search -> lab_file_read.
- resources: {gpu_count, cpu_limit, memory_limit, timeout_seconds}.
  Keep bundles offline-safe: HPC images are pre-staged, never pulled at
  runtime.
- Author loop: write workflow.json -> `r4 workflow lint <bundle-dir>` ->
  workflow_upload (privileged) -> workflow_describe to confirm
  registration and the step DAG -> workflow_run/trigger(params) ->
  workflow_wait/status -> workflow_results. Save a good invocation as a
  run definition (workflow_save_definition) so it replays exactly
  (workflow_replay).
- Knowledge-indexing drafts are a DIFFERENT schema:
  WorkflowDraft{workflow_id (UUID4), name, steps: [{step_id (UUID4), kind:
  rag_index|vector_index|graph_build|catalog|scan, config}]} — `r4
  workflow lint <file>` checks those too (it tells drafts and bundles
  apart).
"""

CATALOG_AGENT_GUIDANCE = """\
Lab catalog two-step flow (never skip step 1):
1. lab_catalog_search(query) — metadata only (summaries, tags, s3_key, suggested_read)
2. lab_file_read(s3_key, as=suggested_read|content|extracted_text|raw_bytes)
deterministic_summary + tags are authoritative for CSV/JSON; perception_summary is a hint.
lab_catalog_* tools are Companion-scoped today (not on the MCP platform server).
"""

SPINE_DISAMBIGUATION = """\
One research spine (ADR-036) — three layers, distinct verbs, no alternatives:
1. knowledge_* — the project knowledge graph (ingest / status / show / query);
   loops read it and write findings back into it with lineage.
2. research_loop_* — the server-hosted autonomous loop (start / list / status /
   stop; approvals / decide for ask-mode gates); durable jobs on UMRS.
   research_session_* groups loops into durable investigations.
3. workflow_* — registered UMRS pipelines, the compute layer BENEATH a loop:
   each real-mode experiment runs as one workflow (workflow_name at start).
The RC1 project_research_* family and local `r4 project run` are retired
(2026-09-07); `r4 project loop start` is the only way to run research.
"""

# Canonical operation sets for the research-spine tool families (mirrors the
# ``workflows`` contract shape).
RESEARCH_LOOP_OPERATIONS = ("start", "list", "status", "stop", "approvals", "decide")
RESEARCH_SESSION_OPERATIONS = ("create", "view")
KNOWLEDGE_OPERATIONS = (
    "status",
    "ingest",
    "show",
    "graph_list",
    "query",
    "graph_propose",
    "graph_decide",
)

RESEARCH_LOOP_TOOL_HELP = {
    "start": (
        "Start an autonomous research loop on a project — the engine step after "
        "knowledge build (knowledge_ingest). The resolved runtime (UMRS or local) "
        "runs iterations continuously; watch with research_loop_status / "
        "research_loop_list. workflow_name names the registered UMRS workflow each "
        "real-mode experiment runs (discover with workflow_list; with exactly one "
        "registered workflow UMRS uses it automatically). session='latest'|'sess-...' "
        "attaches it to a durable session; requires hints route cluster-class work "
        "to UMRS; agentic=true drives iterations with the site LLM agent executor."
    ),
    "list": (
        "List research loops on the current (or given) project with status and iteration counts."
    ),
    "status": "Fetch the current status + recent iterations of one research loop.",
    "stop": ("Stop a running research loop cleanly. Idempotent when the loop already ended."),
    "approvals": (
        "List pending tool-grain approvals of server-hosted loops (durable inbox; "
        "answerable from any client of the loop owner)."
    ),
    "decide": (
        "Record the human's decision (once | always | no) on one pending loop "
        "approval. Never decide on the human's behalf."
    ),
}

RESEARCH_SESSION_TOOL_HELP = {
    "create": (
        "Open a durable research investigation session (ADR-025) for a project. "
        "Use when a NEW line of inquiry starts; attach loops with "
        "session='sess-...'/'latest' in research_loop_start."
    ),
    "view": (
        "Aggregate a project's live research session: loops, knowledge digest, "
        "and where the authoritative state lives (read-only)."
    ),
}

KNOWLEDGE_TOOL_HELP = {
    "status": (
        "Show a compact snapshot of the project knowledge graph (node/edge "
        "counts by type + active research loops)."
    ),
    "ingest": (
        "Add data into the project knowledge graph — local file paths and/or "
        "free-text sources (web search subjects)."
    ),
    "show": "List knowledge graph nodes by label (optionally filtered by node type).",
    "graph_list": (
        "Discover all available knowledge graphs (id/name/kind/project/ "
        "description). Call first when a question may need knowledge beyond "
        "the current project."
    ),
    "query": (
        "Query knowledge across selected graphs (exclude > only > include > "
        "discovery > default). Results carry per-hit graph provenance; empty "
        "results flag knowledge_insufficient."
    ),
    "graph_propose": (
        "Stage a graph-extension proposal (graph, reason, missing info, "
        "sources). No mutation — asks the user for permission."
    ),
    "graph_decide": (
        "Record the user's explicit decision on a proposal. Never call on "
        "your own initiative; always shows an explicit approval prompt."
    ),
}

RESEARCH_LOOP_AGENT_GUIDANCE = """\
Research-loop engine (research_loop_* tools) iterates experiments AFTER knowledge build:
- research_loop_start(research_goal, project_id) — runtime resolved per venue
  (UMRS or local) runs iterations continuously; on UMRS the loop is a durable
  job (survives client disconnect and server restart)
- workflow_name — the registered UMRS workflow each real-mode experiment runs.
  Discover with workflow_list (`r4 workflow list`); with exactly one registered
  workflow UMRS picks it automatically; with none or several the start fails
  closed and names the candidates — never invent a workflow name
- session='latest'|'sess-...' attaches the loop to a durable session (create one
  with research_session_create when a new line of inquiry starts)
- requires={"gpu":1,"cores":128,"cluster":true} routes cluster-class work to
  UMRS in hybrid mode; agentic=true drives iterations with the site LLM agent executor
- Start loops only AFTER knowledge_ingest produced a non-empty knowledge graph;
  otherwise build knowledge first
- Watch with research_loop_status / research_loop_list; stop with
  research_loop_stop (idempotent). Ask-mode loops park at each workflow trigger:
  research_loop_approvals lists them, research_loop_decide records the HUMAN's
  answer (once | always | no) — it is never yours to make
- Findings of real iterations are minted into the knowledge graph with lineage
  (knowledge_show / knowledge_query see them)
"""

RESEARCH_SESSION_AGENT_GUIDANCE = """\
Research sessions (ADR-025 — research_session_* tools) are durable investigation threads:
- research_session_create when a NEW line of inquiry starts (goal; title/runtime
  hint optional)
- Pass session='sess-...' or 'latest' to research_loop_start so several loops stay
  resumable under one session across clients and restarts
- research_session_view — read-only aggregate of loops, knowledge digest, and
  authority location
- Needs a UMRS-backed gateway; on local-only configs creation fails closed with
  'sessions_unavailable'
"""

KNOWLEDGE_AGENT_GUIDANCE = """\
Project knowledge graph verbs (project_* + knowledge_* tools):
- HARD RULE: 'build / create / draw me a knowledge graph about X' is a KNOWLEDGE
  task, not a summarization task. Do NOT fetch pages ad hoc and hand-assemble a
  graph/summary in files or chat — that leaves the store empty and every UI
  (Graph view, Studio Knowledge & Loops) permanently blind. Always go through
  project_ensure + knowledge_ingest(urls/files/sources), then confirm counts
  with knowledge_status and point the user to the visual panels
- No current project and the user starts a knowledge/research task ->
  project_ensure FIRST (derive a slug from the subject, e.g. 'mermaids-seismic');
  project_list shows existing projects
- User says 'add these papers / files / topics / this page to knowledge' ->
  knowledge_ingest (files, sources=search terms, and/or urls=exact web pages;
  urls are fetched inside the egress-controlled durable ingest job)
- 'what is in the graph' / 'knowledge status' -> knowledge_status (counts) or
  knowledge_show (nodes by label, optional type_filter)
- Confirm what was added, then suggest starting the loop (research_loop_start)
- The graph is the input to the research-loop engine — ingest before loops

Multiple knowledge graphs (knowledge_graph_* tools):
- Discover before querying: knowledge_graph_list shows every registered graph
  with description/kind/owner — select the graphs RELEVANT to the task;
  never query every graph by default
- knowledge_query selects via precedence: explicit exclude > 'only' (hard
  constraint) > explicit include > discovery (relevance) > project default.
  Answers carry per-hit graph provenance — report which graphs you used
- Honor user constraints literally: 'use X only' -> only_graphs=[X];
  'don't use Y' -> exclude_graphs=[Y]; 'use A and B' -> graphs=[A,B]
- Insufficient knowledge is NOT permission to mutate: if knowledge_query
  returns knowledge_insufficient (or no useful hits), explain the gap

Extension requires explicit user permission (server-enforced):
1. knowledge_graph_propose(target graph, reason, missing info, sources)
2. Show the proposal to the user and STOP — never ingest/extend silently
3. On explicit user approval: knowledge_graph_decide(proposal_id, approved)
   (always prompts the user — autopilot cannot skip it)
4. Only then execute: knowledge_ingest(..., proposal_id=<id>)
- On decline: do NOT mutate; answer with available knowledge
"""

UMRS_OPS_AGENT_GUIDANCE = """\
UMRS core operations — how r4 drives the platform:
- Request path: r4 Client -> Platform Gateway (/gw/*) -> UMRS -> mini-RiVault.
  NEVER talk to vLLM / LiteLLM / S3 directly; the Gateway owns auth, audit,
  and file fetch. Multi-source: pass site=<name> (or an id@site handle;
  source= is an alias of site on endpoint_list/status/provision/stop) to
  act on another UMRS now;
  source_use only changes the stored source for the NEXT session;
  model@source routes LLM/chat calls only.
- Endpoints (mini-RiVault): one sandboxed container holds vLLM + LiteLLM +
  RAG + MCP side services. Tiers: user (default), project, system.
  Lifecycle: endpoint_list first (avoid duplicates) ->
  endpoint_provision(model, tier, mounts, egress_domains, gpu_count,
  node_count, partition, ttl_sec, wait) -> poll endpoint_status until ready
  -> endpoint_stop when done.
- EndpointMounts JSON: {"mcp_servers": [{"source": "/host/path", "target":
  "/mcp-servers/x"}], "project_data": {"source": "...", "target":
  "/project/..."}, "rag_index": {"source": "...", "target":
  "/rag-index/..."}}. Server validation: MCP targets must stay under
  /mcp-servers, project_data under /project, rag_index under /rag-index.
  Mounts are read-only except /scratch; never inject credentials into mounts.
- Models: registry scopes system (shared; read-only for non-admins),
  project, user. Inference is offline (TRANSFORMERS_OFFLINE=1): models must
  be pre-staged and only model-download jobs (and KNOWLEDGE_INGEST behind
  EgressGuard) have egress. Missing model -> model_stage submits a durable
  download job (POST /umrs/models/stage); probe with model_details then
  provision only after the model is available.
- Durable jobs: Job = immutable user intent; Attempt = one execution try; a
  commit pointer marks the current attempt. On UMRS restart, in-flight
  attempts are marked LOST and the job re-queues. Monitor with
  job_list / job_status (check attempts before assuming stuck); control with
  job_cancel / job_retry.
- Egress is default-deny: empty egress_domains = fully offline endpoint.
  Pass a comma-separated domain list to open specific APIs via EgressGuard
  (audited). System tier has no egress by default.
- Files: upload via file_upload_platform (Gateway HTTP). Presigned S3 URLs
  exist only so compute nodes can read S3 without credentials — never show
  them to end users.
- MCP: mcp_list_servers / mcp_list_tools before mcp_invoke; arguments are
  JSON. URL servers register via mcp_register_server /
  mcp_unregister_server. Custom mounted servers need config.json declaring
  capabilities + sandbox (filesystem=read_only, network=egress_guard, no
  shell entry points).
- Vision: BentoML-backed async queue — vision_submit (local files are
  auto-uploaded), then poll vision_status.
"""

EXPERIENCE_AGENT_GUIDANCE = """\
UMRS Experiences (experience.json) — per-user data/ingest definitions:
- An experience.json is a CLIENT-side fragment merged into the UMRS user
  config (~/.config/umrs/users/<user>.json). It declares xp_name,
  sql_username and modalities[] — each modality binds an S3 bucket alias +
  model + SQL table so `umrs data` uploads trigger background ingestion
  (embed, compute, index). It is a DIFFERENT schema than a workflow bundle
  (workflow.json) — never mix them; experience ops run via the `umrs` CLI,
  not r4/gateway tools.
- Shape (one object, or an array of objects):
  {"xp_name": str, "sql_username": str, "modalities": [{
    "modality_name": "text"|"text file"|"image"|"movie"|"segmentation"|...,
    "slurm": {"partition": str, "reservation": str, "nodelist": str} (optional),
    "s3": {"bucket_alias": str, "need_output_bucket": bool (default false),
           "data": {"type": "inline"|"file", "mime_type": str}},
    "model": {"model_name": str, "vector_size": str, "model_type": str,
              "return_type": null|"file"|"embedding"|"raw",
              "spacy_model": str (text only)},
    "sql": {"store_embeddings": "yes"|"no", "table_name": str,
            "meta_data": [{"name": str, "type": "TEXT"|...}]},
    "auto_compute": bool, "keep_endpoint": bool}]}
- model_type URL schemes: inference endpoint
  "rivault://openai://http://localhost:{LOCAL_PORT}/v1" (UMRS substitutes
  {LOCAL_PORT} per job); custom step "pipeline://<module_stem>:<ClassName>"
  (e.g. pipeline://custom_step:CustomWorkflowStep). Legacy plain model-type
  names still accepted (e.g. TransformerEncoder).
- HARD INVARIANT: s3.need_output_bucket=true REQUIRES model.return_type
  non-null — the server assert-fails reload otherwise. Non-vector / custom
  workflow modalities: sql.store_embeddings="no" and vector_size="0".
- Text embedding modality: vector_size must match the embedding model output
  dim (e5-base-v2 → "768") and spacy_model is needed for tokenization.
- Ops loop (shell `umrs` CLI, not r4/MCP tools):
  umrs experience validate <file>        # schema + model availability
  umrs experience add <file> --update    # merge into user config (--update
                                         # replaces same-xp_name; next reload
                                         # provisions buckets/DB tables)
  umrs reload                            # provision: MinIO buckets, Postgres
                                         # role/db, pgvector tables
  umrs bucket list                       # confirm alias provisioning
  umrs experience list                   # show config experiences
  Author loop: validate -> add --update -> reload -> bucket list -> one
  `umrs data dir-upload ./sample <bucket-alias>/smoke` -> `umrs status`
  shows the PROCESS_OBJECTS row. Fix ONE issue, re-validate, repeat.
- Removal is deliberately safe: `umrs experience remove <xp_name>` + reload
  drops the config stanza but RETAINS MinIO buckets, Postgres db/tables and
  embeddings (re-add later without re-upload/re-index). Rebuild a fragment
  from retained storage with:
  umrs experience recover <xp_name> --username <user>
    --s3cfg ~/.config/umrs/s3cfg_<user> --output-path recovered.json
  (--merge --update to re-add directly; --no-postgres / --no-object-count
  speed it up). Model identity cannot be proven from storage — pass
  --model-name / --model-type / --vector-size overrides.
- Deployment-specific slurm values (partition/reservation/nodelist) come from
  the site config or the user — NEVER invent reservation names.
"""

# Single prose source — AGENT_TOOL_PLAYBOOK and platform_guide compose from this map.
AGENT_TOOL_PLAYBOOK_SECTIONS: dict[str, str] = {
    "spines": SPINE_DISAMBIGUATION,
    "umrs": UMRS_OPS_AGENT_GUIDANCE,
    "workflow": WORKFLOW_AGENT_GUIDANCE,
    "authoring": WORKFLOW_AUTHORING_AGENT_GUIDANCE,
    "catalog": CATALOG_AGENT_GUIDANCE,
    "loops": RESEARCH_LOOP_AGENT_GUIDANCE,
    "sessions": RESEARCH_SESSION_AGENT_GUIDANCE,
    "knowledge": KNOWLEDGE_AGENT_GUIDANCE,
    "experiences": EXPERIENCE_AGENT_GUIDANCE,
}

_SECTION_ORDER = (
    "spines",
    "umrs",
    "workflow",
    "authoring",
    "catalog",
    "loops",
    "sessions",
    "knowledge",
    "experiences",
)

AGENT_TOOL_PLAYBOOK = "\n\n".join(AGENT_TOOL_PLAYBOOK_SECTIONS[k].strip() for k in _SECTION_ORDER)

PLATFORM_GUIDE_TOPICS = ("all",) + _SECTION_ORDER

PLATFORM_GUIDE_TOOL_DESCRIPTION = (
    "Return official R4 tool playbooks. Call this before using "
    "research_loop_*, research_session_*, knowledge_*, workflow_*, or "
    "lab_catalog_* if unsure. Topic 'umrs' covers platform ops; 'authoring' "
    "covers building workflow bundles; 'experiences' covers experience.json "
    "schema and the umrs experience CLI."
)


def platform_guide(topic: str = "all") -> str:
    """Return official playbook text for *topic*.

    Used by Companion, MCP, and ``r4 guide``. Pure lookup — no I/O.
    """
    key = (topic or "all").strip().lower()
    if key == "all":
        return AGENT_TOOL_PLAYBOOK
    if key not in AGENT_TOOL_PLAYBOOK_SECTIONS:
        raise ValueError(
            f"Unknown platform_guide topic {topic!r}; expected one of {PLATFORM_GUIDE_TOPICS}"
        )
    return AGENT_TOOL_PLAYBOOK_SECTIONS[key].strip()
