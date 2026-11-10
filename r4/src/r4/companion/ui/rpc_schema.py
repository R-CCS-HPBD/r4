"""Typed result contract for the companion JSONL bridge (plan H-4.2).

The bridge (``bridge_server.py``) answers ~160 RPC verbs with plain dicts;
the v3 TUI read them through inline ``as {…}`` casts, and about twenty TUI
audit defects were the two sides drifting apart. This module names the
result shape of each covered verb once, as stdlib dataclasses (like
``r4.companion.control_schema``), derived from the handler code:

* ``tools/rpc_schema_export.py`` exports one JSON Schema per verb into
  ``contracts/bridge/v1/`` (``--check`` fails on drift);
* ``tools/rpc_types_gen.py`` turns those files into
  ``packages/r4-tui-v3/src/bridge/rpc-types.gen.ts``;
* ``r4/tests/test_bridge_rpc_contract.py`` runs each covered handler with
  fakes and validates its output against the schema.

Conventions (keep them; the exporter and the generator rely on them):

* A field is a key the handler always sends, unless it is declared with
  ``absent(...)``: then the handler may leave the key out (TS ``key?:``).
* ``Optional[X]`` means the key is present and may be ``null``.
* ``Dict[str, Any]`` is an object the bridge relays without shaping (an
  UMRS payload); ``Any`` is a value of no fixed type.
* A key that is not a Python identifier (``from``) names its wire key with
  ``wire("from")``.
* Results are objects; unknown keys are allowed (additive changes do not
  break older readers). A breaking change bumps ``BRIDGE_PROTOCOL_VERSION``.

Pure schema: nothing here is imported by the bridge at runtime.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Union, cast

from .rpc_schema_base import (  # noqa: F401 — re-exported for callers of rpc_schema
    _ABSENT,
    _WIRE,
    EndpointBudget,
    KnowledgeAcquisition,
    OutputLine,
    PermissionModeName,
    ProgressSnapshot,
    ResultSchemaRoot,
    SandboxState,
    SkillRow,
    SnapshotConfig,
    SnapshotMessage,
    SnapshotSession,
    StateSnapshot,
    UsageReport,
    UsageSummary,
    VenueRuntime,
    WebAccessChoice,
    absent,
    wire,
)
from .rpc_schema_console import CONSOLE_RESULT_TYPES
from .rpc_schema_research import RESEARCH_RESULT_TYPES
from .rpc_schema_teams import TEAMS_RESULT_TYPES

RPC_SCHEMA_VERSION = 1

# ── send_message / dispatch_slash_command ───────────────────────────────────


@dataclass(frozen=True)
class Citation:
    """One passage a turn's answer cited (``turn_citations``)."""

    sid: str = ""
    artifact_id: str = ""
    title: str = ""
    kind: str = "passage"
    preview: str = ""
    source_id: str = absent("")
    project_id: str = absent("")


@dataclass(frozen=True)
class SendMessageResult:
    """A finished chat turn (local), or a turn queued on the remote venue."""

    response: str = ""
    state: StateSnapshot = field(default_factory=StateSnapshot)
    turn_id: str = absent("")
    citations: List[Citation] = absent(factory=list)
    cancelled: bool = absent(False)
    suggested_next: str = absent("")
    remote: bool = absent(False)
    queued: bool = absent(False)
    queue: List[Any] = absent(factory=list)


@dataclass(frozen=True)
class ConfirmPlanResult:
    """confirm_plan: the turn that answered the pending plan (empty on keep_planning)."""

    response: str = ""
    state: StateSnapshot = field(default_factory=StateSnapshot)
    cancelled: bool = absent(False)


@dataclass(frozen=True)
class DispatchSlashResult:
    """A slash command's rendered output, follow-ups and the fresh snapshot."""

    handled: bool = False
    cleared: bool = False
    output: List[OutputLine] = field(default_factory=list)
    state: StateSnapshot = field(default_factory=StateSnapshot)
    dashboard: Optional[Dict[str, Any]] = None
    follow_up_messages: List[str] = field(default_factory=list)
    dropped_turn_ids: List[str] = field(default_factory=list)
    cancelled: bool = absent(False)


# ── session.* ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionPinResult:
    """session.pin / session.unpin: the row's new pin state."""

    session_id: str = ""
    pinned: bool = False


@dataclass(frozen=True)
class SessionForkResult:
    """session.fork: the new row (``CompanionSessionStore.fork`` summary)."""

    session_id: str = ""
    name: str = ""
    forked_from: str = ""
    messages: int = 0


@dataclass(frozen=True)
class SessionRenameResult:
    """session.rename: the stored name."""

    session_id: str = ""
    name: str = ""


@dataclass(frozen=True)
class SessionArchiveResult:
    """session.archive: the row is hidden from default listings."""

    session_id: str = ""
    archived: bool = True


@dataclass(frozen=True)
class SessionCompactResult:
    """session.compact (``compact_history``): from/to on a change, else a reason."""

    changed: bool = False
    keep_recent: int = 0
    reason: str = absent("")
    message_count: int = absent(0)
    from_count: int = wire("from", may_be_absent=True)
    to: int = absent(0)


@dataclass(frozen=True)
class SessionTruncateResult:
    """session.truncate: messages cut at a turn id, and the fresh snapshot."""

    ok: bool = True
    removed: int = 0
    messages: int = 0
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SessionSideQuestionResult:
    """session.side_question (/btw): an answer outside the recorded transcript."""

    question: str = ""
    answer: str = ""


@dataclass(frozen=True)
class SessionTurnsResult:
    """session.turns: stored turn payloads, relayed from the server index."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    count: int = 0
    session_id: str = ""
    turn_id: Optional[str] = None


@dataclass(frozen=True)
class SessionQueueAppendResult:
    """session.queue_append: the local queue after the append (ok=false on empty text)."""

    ok: bool = False
    queue: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionQueuePopResult:
    """session.queue_pop: the popped head (null on an empty queue)."""

    content: Optional[str] = None
    remaining: int = 0


@dataclass(frozen=True)
class SessionQueueClearResult:
    """session.queue_clear: how many rows were dropped."""

    ok: bool = True
    cleared: int = 0


@dataclass(frozen=True)
class SessionQueueRemoteResult:
    """session.queue_remote: the server queue (rows and ``front`` relayed as sent)."""

    applied: bool = False
    queue: List[Any] = field(default_factory=list)
    front: Any = None


@dataclass(frozen=True)
class SessionVenueStartResult:
    """session.venue_start: server venue row, projected runtime, fresh snapshot."""

    venue: Dict[str, Any] = field(default_factory=dict)
    runtime: VenueRuntime = field(default_factory=VenueRuntime)
    state: StateSnapshot = field(default_factory=StateSnapshot)
    adopted: bool = False
    # ADR-099 D-8: why the agent settings did not reach the worker ("" = sent).
    settings_error: str = ""


@dataclass(frozen=True)
class SessionVenueStatusResult:
    """session.venue_status: server venue row and seconds left on its lease."""

    venue: Dict[str, Any] = field(default_factory=dict)
    lease_remaining_sec: Optional[int] = None


@dataclass(frozen=True)
class SessionVenueStopResult:
    """session.venue_stop: server venue row and the fresh snapshot."""

    venue: Dict[str, Any] = field(default_factory=dict)
    state: StateSnapshot = field(default_factory=StateSnapshot)
    adopted: bool = False


@dataclass(frozen=True)
class VenueWorkerRuntime:
    """The worker's live report on the venue row (UMRS ``_runtime_report``); {} when absent."""

    phase: str = absent("")
    turn_seq: int = absent(0)
    partial: str = absent("")
    detail: str = absent("")
    tool_activity: List[str] = absent(factory=list)


@dataclass(frozen=True)
class SessionVenueSyncResult:
    """session.venue_sync: one answer to the TUI's remote-turn watcher."""

    adopted: bool = False
    runtime: VenueWorkerRuntime = field(default_factory=VenueWorkerRuntime)
    venue_state: str = ""
    lease_remaining_sec: Optional[int] = None
    turn_seq: int = 0
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SessionVenueApprovalsResult:
    """session.venue_approvals: pending remote approval rows, relayed."""

    approvals: List[Dict[str, Any]] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class SessionVenueDecideResult:
    """session.venue_decide: the decided approval row as the server answered it."""

    approval: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class SessionVenueQuestionsResult:
    """session.venue_questions: pending ask_user questions of the remote worker, relayed."""

    questions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SessionVenueAnswerResult:
    """session.venue_answer: the answered question as the server answered it."""

    question: Optional[Dict[str, Any]] = None


# ── session.web_access_* / session.venue_policy (ADR-099 D-2) ──────────────


@dataclass(frozen=True)
class SessionWebAccessGetResult:
    """session.web_access_get: the choice, the remembered last one, a launch preset."""

    web_access: WebAccessChoice = field(default_factory=WebAccessChoice)
    remembered: Optional[WebAccessChoice] = None
    preset: bool = False


@dataclass(frozen=True)
class SessionWebAccessSetResult:
    """session.web_access_set: the choice now stored on the session.

    ``notice`` (ADR-102): a line for the user, e.g. that public access has no
    search server for ``web_search`` yet; absent when there is nothing to say.
    ``search_server_offer`` (2026-11-01): with that notice, true when a
    container engine is installed, so the console can offer to start the
    local search server (``search_server.up``).
    """

    web_access: WebAccessChoice = field(default_factory=WebAccessChoice)
    notice: str = absent("")
    search_server_offer: bool = absent(False)


# ── search_server.* (ADR-102 amendment, 2026-11-01) ──────────────────────


@dataclass(frozen=True)
class SearchServerStatusResult:
    """search_server.status: the local SearXNG for ``web_search``.

    ``state``: running / down (recorded, not answering) / absent (never
    started); ``engine`` is the container engine ``up`` would use ("" =
    none installed); ``starting`` is true while an ``up`` runs;
    ``external_url`` is ``R4_WEB_SEARCH_URL`` when set ("" = unset), the
    instance ``web_search`` uses instead of the local one (2026-11-03).
    """

    state: str = ""
    url: str = ""
    engine: str = ""
    starting: bool = False
    external_url: str = ""


@dataclass(frozen=True)
class SearchServerUpResult:
    """search_server.up: accepted at once; the start runs on a worker thread
    and reports through ``search_server_event`` lines (``phase``: progress,
    done or error). ``accepted`` is false while another start runs."""

    accepted: bool = False
    state: str = ""


@dataclass(frozen=True)
class SearchServerDownResult:
    """search_server.down: ``stopped``, or ``failed`` with ``reason``."""

    state: str = ""
    reason: str = absent("")


# ── session.sandbox_* (ADR-101 D-12) ─────────────────────────────────────


@dataclass(frozen=True)
class SessionSandboxGetResult:
    """session.sandbox_get: the session's local tool sandbox."""

    sandbox: SandboxState = field(default_factory=SandboxState)


@dataclass(frozen=True)
class SessionSandboxSetResult:
    """session.sandbox_set: the new sandbox, the local MCP servers restarted
    under it, the processes started before it that still run unconfined,
    ``mcp_error`` ("" or why the local MCP servers did not follow the change:
    the setting itself is applied) and the new snapshot."""

    sandbox: SandboxState = field(default_factory=SandboxState)
    restarted_mcp: List[str] = field(default_factory=list)
    still_running: List[str] = field(default_factory=list)
    mcp_error: str = ""
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SessionVenuePolicyResult:
    """session.venue_policy: the server's preview of a remote start's caps.

    ``limits`` is the effective-limits document (``web.allow``,
    ``web.domains``, ``web.suggested_domains`` …), null when the server has
    no preview; ``error`` names a failed call.
    """

    session_id: str = ""
    project_id: str = ""
    choice_required: bool = True
    limits: Optional[Dict[str, Any]] = None
    current: Optional[Dict[str, Any]] = None
    error: str = absent("")


# ── workflow.* (and workflow_describe) ──────────────────────────────────────


@dataclass(frozen=True)
class WorkflowCatalogRow:
    """One registered workflow (``workflow_list``); shared: ``owner`` shared it with the caller.

    ``scope`` and ``access`` are the owner's; ``tags`` carries the display
    label ("user", "system" or "shared · <owner>")."""

    workflow_name: str = ""
    name: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    scope: str = ""
    owner: str = ""
    shared: bool = False
    access: List[str] = field(default_factory=list)
    # ADR-113 D-3: the default version, every registered one (SemVer-
    # descending) and the default's code digest; "" / [] from older UMRS,
    # absent from bridges before 2026-11-08.
    version: str = absent("")
    versions: List[str] = absent(factory=list)
    source_sha256: str = absent("")


@dataclass(frozen=True)
class WorkflowListResult:
    """workflow.list: the caller's workflow catalog."""

    workflows: List[WorkflowCatalogRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class WorkflowStatusResult:
    """workflow.status (``_serialize_workflow_status``)."""

    execution_id: str = ""
    workflow_name: Optional[str] = None
    status: str = ""
    progress: Optional[float] = None
    error: Optional[str] = None
    # ADR-113 D-4: the version and code digest the run pinned ("" from older
    # UMRS; absent from bridges before 2026-11-08).
    workflow_version: str = absent("")
    source_sha256: str = absent("")


@dataclass(frozen=True)
class WorkflowStatusAck(WorkflowStatusResult):
    """workflow.cancel / workflow.approve_gate: the new status, acknowledged."""

    ok: bool = True


@dataclass(frozen=True)
class WorkflowRunResult:
    """workflow.run / workflow.results (``_serialize_workflow_result``)."""

    execution_id: str = ""
    workflow_name: Optional[str] = None
    status: str = ""
    artifacts: Optional[List[Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # ADR-113 D-4: the version and code digest the run pinned ("" from older
    # UMRS; absent from bridges before 2026-11-08).
    workflow_version: str = absent("")
    source_sha256: str = absent("")


@dataclass(frozen=True)
class WorkflowStepProgress:
    """One step row of an execution record (UMRS ``StepProgress.to_dict``)."""

    name: str = ""
    status: str = ""
    started_at: Optional[float] = absent(None)
    finished_at: Optional[float] = absent(None)
    error: Optional[str] = absent(None)
    progress: float = absent(0.0)
    extra: Dict[str, Any] = absent(factory=dict)


@dataclass(frozen=True)
class WorkflowExecutionRecord:
    """The raw UMRS execution record (``ExecutionRecord.to_dict`` or its
    durable-job projection); relayed, so every key may be missing ({} when
    UMRS answers without one). ``progress`` is a 0..1 fraction."""

    execution_id: str = absent("")
    workflow_name: Optional[str] = absent(None)
    workflow_version: str = absent("")  # ADR-113 D-4
    source_sha256: str = absent("")
    user_id: str = absent("")
    status: str = absent("")
    submitted_at: Optional[float] = absent(None)
    started_at: Optional[float] = absent(None)
    finished_at: Optional[float] = absent(None)
    error: Optional[str] = absent(None)
    progress: Optional[float] = absent(None)
    params: Dict[str, Any] = absent(factory=dict)
    steps: List[WorkflowStepProgress] = absent(factory=list)
    artifacts: List[Any] = absent(factory=list)
    durable_job_id: Optional[str] = absent(None)
    durable_job: Dict[str, Any] = absent(factory=dict)
    cancel_requested: bool = absent(False)
    gates: Dict[str, Any] = absent(factory=dict)
    # Not in today's record; the TUI also reads the older ``results.steps`` map.
    results: Dict[str, Any] = absent(factory=dict)


@dataclass(frozen=True)
class WorkflowDetailResult:
    """workflow.detail: the raw UMRS execution record, wrapped."""

    execution: WorkflowExecutionRecord = field(default_factory=WorkflowExecutionRecord)


@dataclass(frozen=True)
class WorkflowParameterSchema:
    """A workflow's declared parameter JSON Schema (UMRS ``_workflow_parameter_schema``)."""

    type: str = absent("")
    properties: Dict[str, Any] = absent(factory=dict)
    required: List[str] = absent(factory=list)


@dataclass(frozen=True)
class WorkflowStepDefinition:
    """One declared step: a catalog row (name, entrypoint) or a workflow.json step."""

    name: str = ""
    entrypoint: str = absent("")
    config: Dict[str, Any] = absent(factory=dict)
    depends_on: Optional[List[str]] = absent(None)
    model_type: str = absent("")
    requires: List[str] = absent(factory=list)


@dataclass(frozen=True)
class WorkflowDescription:
    """workflow_describe: UMRS ``GET /workflows/{name}`` body, relayed."""

    status: str = absent("")
    contract_version: str = absent("")
    workflow_name: Optional[str] = absent(None)
    version: str = absent("")
    versions: List[str] = absent(factory=list)  # ADR-113 D-3
    source_sha256: str = absent("")
    code: str = absent("")  # ADR-113 D-8: "available" | "unavailable"
    description: str = absent("")
    scope: str = absent("")
    user_id: str = absent("")
    parameter_schema: WorkflowParameterSchema = absent(factory=WorkflowParameterSchema)
    steps: List[WorkflowStepDefinition] = absent(factory=list)
    requirements: List[Any] = absent(factory=list)
    triggers: List[Any] = absent(factory=list)


@dataclass(frozen=True)
class WorkflowPackageFile:
    """One registered code file of a workflow package (content capped by UMRS)."""

    path: str = ""
    bytes: int = 0
    lines: int = 0
    content: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class WorkflowPackage(WorkflowDescription):
    """workflow.package: UMRS ``GET /workflows/{name}/package`` body, relayed."""

    resources: Dict[str, Any] = absent(factory=dict)
    execution_mode: Optional[str] = absent(None)
    output: Optional[Dict[str, Any]] = absent(None)
    source_path: str = absent("")
    files: List[WorkflowPackageFile] = absent(factory=list)
    files_truncated: bool = absent(False)


@dataclass(frozen=True)
class WorkflowExportResult:
    """workflow.export: where the verified zip was saved, and its EXPORT.json counts.

    ``files`` / ``excluded`` are ``null`` when the manifest could not be read
    back (the SDK already verified it; the counts are display only).
    """

    ok: bool = False
    workflow_name: str = ""
    path: str = ""
    files: Optional[int] = None
    excluded: Optional[int] = None


# ── assets.* ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssetsSnapshotResult:
    """assets.snapshot: the caller's asset rows (relayed) and the snapshot hash."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    snapshot_hash: Optional[str] = None


@dataclass(frozen=True)
class AssetsPatchResult:
    """assets.patch: upserted rows and tombstoned shape keys since the base."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    tombstoned: List[str] = field(default_factory=list)
    snapshot_hash: Optional[str] = None


# ── admin.* ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdminRagStatsResult:
    """admin.rag_stats: UMRS rag/stats for the session project (relayed)."""

    stats: Dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None


@dataclass(frozen=True)
class AdminCorpusAttritionResult:
    """admin.corpus_attrition: the session project's attrition report (relayed)."""

    report: Dict[str, Any] = field(default_factory=dict)
    project_id: str = ""


# ── provenance.* (UMRS bodies relayed unchanged) ────────────────────────────


@dataclass(frozen=True)
class ProvenanceRecord:
    """provenance.job: UMRS ``ProvenanceResponse`` for one durable job."""

    job_id: str = ""
    job_type: str = ""
    project_id: str = ""
    user_id: str = ""
    status: str = ""
    spec: Dict[str, Any] = absent(factory=dict)
    created_at: Optional[str] = absent(None)
    completed_at: Optional[str] = absent(None)
    total_duration_seconds: Optional[float] = absent(None)
    model: Optional[Dict[str, Any]] = absent(None)
    attempts: List[Dict[str, Any]] = absent(factory=list)
    total_attempts: int = absent(0)
    artifacts: List[Dict[str, Any]] = absent(factory=list)
    workflow_name: Optional[str] = absent(None)
    execution_id: Optional[str] = absent(None)
    parent_job_id: Optional[str] = absent(None)


@dataclass(frozen=True)
class LineageNode:
    """One execution step of a lineage graph (UMRS ``LineageNode.to_dict``)."""

    trace_id: str = ""
    step_name: str = ""
    step_type: str = ""
    status: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    model_ref: Optional[Dict[str, Any]] = absent(None)
    artifact_refs: List[Dict[str, Any]] = absent(factory=list)
    error: Optional[str] = absent(None)


@dataclass(frozen=True)
class LineageEdge:
    """A parent → child edge between two lineage nodes."""

    source: str = ""
    target: str = ""


@dataclass(frozen=True)
class LineageGraph:
    """provenance.lineage: UMRS ``LineageGraph.to_dict`` for one job."""

    job_id: str = ""
    execution_id: Optional[str] = None
    nodes: List[LineageNode] = field(default_factory=list)
    edges: List[LineageEdge] = field(default_factory=list)
    root_ids: List[str] = field(default_factory=list)
    leaf_ids: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# ── sweep.* ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SweepTemplateRow:
    """One sweep template of the shipped pack."""

    name: str = ""
    description: str = ""
    required_params: List[str] = field(default_factory=list)
    params: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SweepTemplatesResult:
    """sweep.templates: the template pack inventory."""

    templates: List[SweepTemplateRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class SweepPreviewResult:
    """sweep.preview: one template rendered in memory (never written)."""

    name: str = ""
    description: str = ""
    required_params: List[str] = field(default_factory=list)
    job_name: str = ""
    script: str = ""


# ── trust_* ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrustRow:
    """One server-side trust allowlist row (UMRS ``TrustRow.to_dict``)."""

    user_id: str = ""
    scope: str = ""
    tool_name: str = ""
    expires_at: str = ""
    created_at: str = absent("")
    created_by: str = absent("")


@dataclass(frozen=True)
class TrustListResult:
    """trust_list: the caller's unexpired trust rows."""

    rows: List[TrustRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TrustGrantResult:
    """trust_grant: the stored row as UMRS answered it, and what was asked."""

    granted: TrustRow = field(default_factory=TrustRow)
    tool_name: str = ""
    scope: str = ""


# ── get/status leftovers (H-4 final batch: dashboard, events, agents, ────────
# science loop, resources, governance, welcome, slash catalog) ────────────────


@dataclass(frozen=True)
class GetStatusResult:
    """get_status: server readiness beside the session snapshot."""

    readiness: Dict[str, Any] = field(default_factory=dict)
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class BackendDashboard:
    """collect_backend_snapshot's best-effort snapshot (failed probes land
    in ``errors``, never as raised exceptions)."""

    model: str = ""
    mode: str = ""
    approval: str = ""
    secure: bool = False
    profile: str = ""
    project: str = ""
    gateway: str = ""
    readiness: Dict[str, Any] = field(default_factory=dict)
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    workflows: List[Any] = field(default_factory=list)
    rag_documents: List[Dict[str, Any]] = field(default_factory=list)
    mcp_summary: str = ""
    mcp_tools: List[Dict[str, Any]] = field(default_factory=list)
    local_tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    active_skills: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GetBackendDashboardResult:
    """get_backend_dashboard: the snapshot and the session state."""

    dashboard: BackendDashboard = field(default_factory=BackendDashboard)
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class CodeContext:
    """detect_code_context's compact root snapshot."""

    path: str = ""
    root: str = ""
    languages: Dict[str, Any] = field(default_factory=dict)
    primary_language: Optional[str] = None
    build_systems: List[str] = field(default_factory=list)
    make_targets: List[str] = field(default_factory=list)
    tools: Dict[str, Any] = field(default_factory=dict)
    language_servers: Dict[str, Any] = field(default_factory=dict)
    source_files: List[str] = field(default_factory=list)
    suggested_commands: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GetCodeContextResult:
    """get_code_context: the snapshot under one key."""

    code_context: CodeContext = field(default_factory=CodeContext)


@dataclass(frozen=True)
class GetWelcomeResult:
    """get_welcome: brand banner beside a fresh readiness read."""

    banner: str = ""
    readiness: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GetUsageResult:
    """get_usage: the session's usage report."""

    usage: UsageReport = field(default_factory=UsageReport)


@dataclass(frozen=True)
class EventLogRow:
    """One persisted event (the event_log row; ``data`` is the parsed JSON
    payload — ``_row_to_dict`` deserializes it on the way out)."""

    id: int = 0
    session_id: str = ""
    event_type: str = ""
    timestamp: str = ""
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    tool_name: Optional[str] = None
    tool_status: Optional[str] = None
    tool_duration_ms: Optional[int] = None
    agent_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    task_id: Optional[str] = None
    progress_pct: Optional[int] = None
    intent: Optional[str] = None
    rationale: Optional[str] = None


@dataclass(frozen=True)
class GetEventLogResult:
    """get_event_log: newest events, oldest-to-newest."""

    events: List[EventLogRow] = field(default_factory=list)
    session_id: str = ""


@dataclass(frozen=True)
class GetGovernanceOverviewResult:
    """get_governance_overview: per-section payloads, or ``{"error": ...}``
    for a section whose call failed (tolerant by design)."""

    ownership: Dict[str, Any] = field(default_factory=dict)
    quotas: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceSnapshot:
    """The resource monitor's live box read; section shapes vary by host."""

    gpu: List[Dict[str, Any]] = field(default_factory=list)
    cpu: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    slurm_jobs: List[Any] = field(default_factory=list)
    hostname: str = ""


@dataclass(frozen=True)
class GetResourcesResult:
    """get_resources: the snapshot under one key."""

    resources: ResourceSnapshot = field(default_factory=ResourceSnapshot)


@dataclass(frozen=True)
class GetArtifactsResult:
    """get_artifacts: key paths / job ids noted by the progress tracker."""

    artifacts: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentRecordRow:
    """One live agent (``AgentRecord.to_dict``; children are ids here)."""

    agent_id: str = ""
    parent_id: Optional[str] = None
    name: str = ""
    task: str = ""
    status: str = ""
    current_tool: Optional[str] = None
    last_tool: Optional[str] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    tool_count: int = 0
    children: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


@dataclass(frozen=True)
class ListActiveAgentsResult:
    """list_active_agents: flat active rows and their count."""

    agents: List[AgentRecordRow] = field(default_factory=list)
    active_count: int = 0


@dataclass(frozen=True)
class AgentTreeNode:
    """get_agent_tree node: a record whose children embed full subtrees."""

    agent_id: str = ""
    parent_id: Optional[str] = None
    name: str = ""
    task: str = ""
    status: str = ""
    current_tool: Optional[str] = None
    last_tool: Optional[str] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    tool_count: int = 0
    children: List["AgentTreeNode"] = field(default_factory=list)
    elapsed_sec: float = 0.0


@dataclass(frozen=True)
class GetAgentTreeResult:
    """get_agent_tree: rooted subtrees with counters."""

    agents: List[AgentTreeNode] = field(default_factory=list)
    active_count: int = 0
    total_count: int = 0


@dataclass(frozen=True)
class GetAgentStatusResult:
    """get_agent_status: the record, or null for an unknown id."""

    agent: Optional[AgentRecordRow] = None


@dataclass(frozen=True)
class ScienceTrajectoryRow:
    """One recorded science-loop iteration (science_loop sqlite row)."""

    id: str = ""
    session_id: str = ""
    hypothesis: str = ""
    experiment_id: Optional[str] = None
    reward_total: Optional[float] = None
    performance: Optional[float] = None
    novelty: Optional[float] = None
    information_gain: Optional[float] = None
    reproducibility: Optional[float] = None
    cost: Optional[float] = None
    belief_entropy: Optional[float] = None
    timestamp: str = ""


@dataclass(frozen=True)
class ScienceStats:
    """get_science_loop's aggregate counters."""

    total_iterations: int = 0
    cumulative_reward: float = 0.0
    avg_reward: float = 0.0
    total_performance: float = 0.0
    total_novelty: float = 0.0
    total_information_gain: float = 0.0
    avg_entropy: float = 0.0


@dataclass(frozen=True)
class GetScienceLoopResult:
    """get_science_loop: trajectory rows and counters."""

    trajectory: List[ScienceTrajectoryRow] = field(default_factory=list)
    stats: ScienceStats = field(default_factory=ScienceStats)
    session_id: str = ""


@dataclass(frozen=True)
class RecordScienceIterationResult:
    """record_science_iteration: the new iteration id."""

    iteration_id: str = ""


@dataclass(frozen=True)
class SlashCommandRow:
    """One companion slash catalog entry (``_catalog_entry``); subcommands
    and aliases ride only when the spec has them."""

    name: str = ""
    help: str = ""
    category: str = ""
    takesArgs: bool = False
    subcommands: List[str] = absent(field(default_factory=list))
    aliases: List[str] = absent(field(default_factory=list))


@dataclass(frozen=True)
class ListSlashCommandsResult:
    """list_slash_commands: shared ops only (surface-local chrome excluded)."""

    commands: List[SlashCommandRow] = field(default_factory=list)


# ── registry ────────────────────────────────────────────────────────────────

CORE_RESULT_TYPES: Dict[str, type] = {
    "get_state": StateSnapshot,
    "send_message": SendMessageResult,
    "confirm_plan": ConfirmPlanResult,
    "dispatch_slash_command": DispatchSlashResult,
    "session.pin": SessionPinResult,
    "session.unpin": SessionPinResult,
    "session.fork": SessionForkResult,
    "session.rename": SessionRenameResult,
    "session.archive": SessionArchiveResult,
    "session.compact": SessionCompactResult,
    "session.truncate": SessionTruncateResult,
    "session.side_question": SessionSideQuestionResult,
    "session.turns": SessionTurnsResult,
    "session.queue_append": SessionQueueAppendResult,
    "session.queue_pop": SessionQueuePopResult,
    "session.queue_clear": SessionQueueClearResult,
    "session.queue_remote": SessionQueueRemoteResult,
    "session.venue_start": SessionVenueStartResult,
    "session.venue_status": SessionVenueStatusResult,
    "session.venue_stop": SessionVenueStopResult,
    "session.venue_sync": SessionVenueSyncResult,
    "session.venue_approvals": SessionVenueApprovalsResult,
    "session.venue_decide": SessionVenueDecideResult,
    "session.venue_questions": SessionVenueQuestionsResult,
    "session.venue_answer": SessionVenueAnswerResult,
    "session.web_access_get": SessionWebAccessGetResult,
    "session.web_access_set": SessionWebAccessSetResult,
    "session.venue_policy": SessionVenuePolicyResult,
    "search_server.status": SearchServerStatusResult,
    "search_server.up": SearchServerUpResult,
    "search_server.down": SearchServerDownResult,
    "session.sandbox_get": SessionSandboxGetResult,
    "session.sandbox_set": SessionSandboxSetResult,
    "workflow.list": WorkflowListResult,
    "workflow.status": WorkflowStatusResult,
    "workflow.run": WorkflowRunResult,
    "workflow.cancel": WorkflowStatusAck,
    "workflow.results": WorkflowRunResult,
    "workflow.detail": WorkflowDetailResult,
    "workflow.approve_gate": WorkflowStatusAck,
    "workflow.package": WorkflowPackage,
    "workflow.export": WorkflowExportResult,
    "workflow_describe": WorkflowDescription,
    "assets.snapshot": AssetsSnapshotResult,
    "assets.patch": AssetsPatchResult,
    "admin.rag_stats": AdminRagStatsResult,
    "admin.corpus_attrition": AdminCorpusAttritionResult,
    "provenance.job": ProvenanceRecord,
    "provenance.lineage": LineageGraph,
    "sweep.templates": SweepTemplatesResult,
    "sweep.preview": SweepPreviewResult,
    "trust_list": TrustListResult,
    "trust_grant": TrustGrantResult,
    "get_status": GetStatusResult,
    "get_backend_dashboard": GetBackendDashboardResult,
    "get_code_context": GetCodeContextResult,
    "get_welcome": GetWelcomeResult,
    "get_usage": GetUsageResult,
    "get_event_log": GetEventLogResult,
    "get_governance_overview": GetGovernanceOverviewResult,
    "get_resources": GetResourcesResult,
    "get_artifacts": GetArtifactsResult,
    "list_active_agents": ListActiveAgentsResult,
    "get_agent_tree": GetAgentTreeResult,
    "get_agent_status": GetAgentStatusResult,
    "get_science_loop": GetScienceLoopResult,
    "record_science_iteration": RecordScienceIterationResult,
    "list_slash_commands": ListSlashCommandsResult,
}


def _merged_registry(*families: Dict[str, ResultSchemaRoot]) -> Dict[str, ResultSchemaRoot]:
    """One verb → result-type table; a verb typed by two families is an error."""
    merged: Dict[str, ResultSchemaRoot] = {}
    for family in families:
        clash = set(merged) & set(family)
        if clash:
            raise ValueError(f"bridge verbs typed twice: {sorted(clash)}")
        merged.update(family)
    return merged


# Every covered verb: the core families above plus the per-family modules.
RPC_RESULT_TYPES: Dict[str, ResultSchemaRoot] = _merged_registry(
    CORE_RESULT_TYPES, CONSOLE_RESULT_TYPES, RESEARCH_RESULT_TYPES, TEAMS_RESULT_TYPES
)


# ── JSON Schema derivation ──────────────────────────────────────────────────

_SCALARS: Dict[Any, Dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    type(None): {"type": "null"},
}


def _union_schema(args: tuple[Any, ...], defs: Dict[str, Any]) -> Dict[str, Any]:
    return {"anyOf": [annotation_schema(arg, defs) for arg in args]}


def _list_schema(args: tuple[Any, ...], defs: Dict[str, Any]) -> Dict[str, Any]:
    items = annotation_schema(args[0], defs) if args else {}
    return {"type": "array", "items": items} if items else {"type": "array"}


def _dict_schema(args: tuple[Any, ...], defs: Dict[str, Any]) -> Dict[str, Any]:
    values = annotation_schema(args[1], defs) if len(args) == 2 else {}
    return {"type": "object", "additionalProperties": values} if values else {"type": "object"}


def _literal_schema(args: tuple[Any, ...], defs: Dict[str, Any]) -> Dict[str, Any]:
    _ = defs
    return {"type": "string", "enum": list(args)}


_GENERIC_SCHEMAS: Dict[Any, Callable[[tuple[Any, ...], Dict[str, Any]], Dict[str, Any]]] = {
    Union: _union_schema,
    list: _list_schema,
    dict: _dict_schema,
    Literal: _literal_schema,
}


def annotation_schema(annotation: Any, defs: Dict[str, Any]) -> Dict[str, Any]:
    """JSON Schema fragment for one resolved annotation; dataclasses go to ``defs``."""
    if annotation is Any:
        return {}
    if dataclasses.is_dataclass(annotation):
        return {"$ref": f"#/$defs/{class_schema(cast(type, annotation), defs)}"}
    builder = _GENERIC_SCHEMAS.get(typing.get_origin(annotation))
    if builder is not None:
        return builder(typing.get_args(annotation), defs)
    if annotation in (list, dict):
        return {"type": "array" if annotation is list else "object"}
    return dict(_SCALARS.get(annotation, {}))


def wire_name(f: dataclasses.Field[Any]) -> str:
    """The key a field travels under on the wire."""
    return str(f.metadata.get(_WIRE) or f.name)


def class_schema(cls: type, defs: Dict[str, Any]) -> str:
    """Add ``cls`` (and every class it references) to ``defs``; return its name."""
    name = cls.__name__
    if name in defs:
        return name
    defs[name] = {}  # placeholder: breaks reference cycles
    hints = typing.get_type_hints(cls)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for f in dataclasses.fields(cls):
        key = wire_name(f)
        properties[key] = annotation_schema(hints[f.name], defs)
        if not f.metadata.get(_ABSENT):
            required.append(key)
    doc = (cls.__doc__ or "").strip().splitlines()
    defs[name] = {
        "type": "object",
        "description": doc[0] if doc else "",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }
    return name


def result_schema(method: str) -> Dict[str, Any]:
    """The JSON Schema document for one covered RPC verb's result.

    A dataclass root exports as ``$ref``; a non-dataclass root (a typing
    union, e.g. the two loop-control lanes) exports as the inline schema,
    since the honest relay shape is that union itself. Key order is part
    of the shipped bytes: dataclass-root documents must not churn on
    re-export.
    """
    cls = RPC_RESULT_TYPES[method]
    defs: Dict[str, Any] = {}
    if dataclasses.is_dataclass(cls):
        root = class_schema(cast(type, cls), defs)
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://r4.dev/contracts/bridge/v1/{method}.schema.json",
            "title": method,
            "description": defs[root]["description"],
            "x-bridge-protocol": RPC_SCHEMA_VERSION,
            "$ref": f"#/$defs/{root}",
            "$defs": dict(sorted(defs.items())),
        }
    inline = annotation_schema(cls, defs)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://r4.dev/contracts/bridge/v1/{method}.schema.json",
        "title": method,
        "description": f"Result union for {method} (see branches).",
        "x-bridge-protocol": RPC_SCHEMA_VERSION,
        **inline,
        "$defs": dict(sorted(defs.items())),
    }


# ── a small validator (the subset the exporter emits; no jsonschema dep) ────

_TYPE_CHECKS: Dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def _check_type(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    _ = root
    expected = schema["type"]
    return [] if _TYPE_CHECKS[expected](value) else [f"{path}: expected {expected}"]


def _check_enum(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    _ = root
    return [] if value in schema["enum"] else [f"{path}: {value!r} not in {schema['enum']}"]


def _check_ref(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    name = str(schema["$ref"]).rsplit("/", 1)[-1]
    return validate(value, root["$defs"][name], root, path)


def _check_any_of(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    branches = [validate(value, option, root, path) for option in schema["anyOf"]]
    if any(not errors for errors in branches):
        return []
    return [f"{path}: matches no anyOf branch ({'; '.join(e[0] for e in branches if e)})"]


def _check_items(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    if not isinstance(value, list):
        return []
    errors: List[str] = []
    for index, item in enumerate(value):
        errors.extend(validate(item, schema["items"], root, f"{path}[{index}]"))
    return errors


def _check_required(
    value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str
) -> List[str]:
    _ = root
    if not isinstance(value, dict):
        return []
    return [f"{path}: missing key {key!r}" for key in schema["required"] if key not in value]


def _check_properties(
    value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str
) -> List[str]:
    if not isinstance(value, dict):
        return []
    errors: List[str] = []
    for key, sub in schema["properties"].items():
        if key in value:
            errors.extend(validate(value[key], sub, root, f"{path}.{key}"))
    return errors


def _check_values(value: Any, schema: Dict[str, Any], root: Dict[str, Any], path: str) -> List[str]:
    extra = schema["additionalProperties"]
    if not isinstance(value, dict) or not isinstance(extra, dict):
        return []
    known = schema.get("properties", {})
    errors: List[str] = []
    for key, item in value.items():
        if key not in known:
            errors.extend(validate(item, extra, root, f"{path}.{key}"))
    return errors


_KEYWORDS: Dict[str, Callable[[Any, Dict[str, Any], Dict[str, Any], str], List[str]]] = {
    "$ref": _check_ref,
    "type": _check_type,
    "enum": _check_enum,
    "anyOf": _check_any_of,
    "items": _check_items,
    "required": _check_required,
    "properties": _check_properties,
    "additionalProperties": _check_values,
}


def validate(
    value: Any, schema: Dict[str, Any], root: Dict[str, Any] | None = None, path: str = "$"
) -> List[str]:
    """Errors of ``value`` against ``schema`` (empty when it conforms).

    Covers what ``result_schema`` emits: type, enum, anyOf, $ref, items,
    properties, required and additionalProperties.
    """
    document = schema if root is None else root
    errors: List[str] = []
    for keyword, check in _KEYWORDS.items():
        if keyword in schema:
            errors.extend(check(value, schema, document, path))
    return errors


def validate_result(method: str, value: Any) -> List[str]:
    """Errors of one RPC result against that verb's schema."""
    return validate(value, result_schema(method))
