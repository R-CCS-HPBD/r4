"""Bridge RPC result contract, console family (plan H-4.2).

Verbs: initialize/shutdown, list_*/set_* of the session, sessions, models,
skills, MCP, changes and plan, jobs, tasks, files, explain, source.*,
shutdown, cancel_turn. Conventions: ``rpc_schema`` module docstring.
Derived from the handler code in ``bridge_server.py`` and what they relay
(``r4.companion.tools`` session changes, the job manager's sqlite rows,
``r4.mcp`` health rows, the model-option collectors in ``r4.tui``);
contract-tested by ``r4/tests/test_bridge_rpc_contract_console.py``.

Pure schema: nothing here is imported by the bridge at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

from .rpc_schema_base import (
    KnowledgeAcquisition,
    OutputLine,
    ResultSchemaRoot,
    SkillRow,
    StateSnapshot,
    absent,
)

# ── session lifecycle ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InitializeResult(StateSnapshot):
    """initialize: the session snapshot plus boot facts (``initialize``)."""

    workspace_root: str = ""
    bridge_protocol: int = 0
    source: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class LoadSessionResult(StateSnapshot):
    """load_session: the resumed snapshot; ``notice`` only on journal moves."""

    notice: Optional[str] = absent(None)


@dataclass(frozen=True)
class OkResult:
    """cancel_turn / shutdown: the bare acknowledgement."""

    ok: bool = False


@dataclass(frozen=True)
class ExportSessionResult:
    """export_session: the markdown export and the session's own identity."""

    markdown: str = ""
    model: Optional[str] = None
    project: Optional[str] = None


@dataclass(frozen=True)
class ReplaySessionResult:
    """replay_session: persisted event rows (their shape is the log's own)."""

    events: List[Dict[str, Any]] = field(default_factory=list)
    session_id: str = ""


@dataclass(frozen=True)
class SessionSummary:
    """One ``summarize`` rollup over the event log."""

    total_events: int = 0
    event_types: Dict[str, Any] = field(default_factory=dict)
    agents: List[Any] = field(default_factory=list)
    tools_used: List[Any] = field(default_factory=list)
    first_event: Optional[Any] = None
    last_event: Optional[Any] = None


@dataclass(frozen=True)
class SummarizeSessionResult:
    """summarize_session: the rollup and the id it was computed for."""

    summary: SessionSummary = field(default_factory=SessionSummary)
    session_id: str = ""


@dataclass(frozen=True)
class SessionListRow:
    """One row of the unified listing (local store or journal-merged)."""

    id: str = ""
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    model: Optional[str] = None
    message_count: int = 0
    project_id: str = ""
    endpoints: List[Any] = field(default_factory=list)
    endpoint: str = ""
    archived_at: str = ""
    pinned: bool = False
    pinned_at: str = ""
    venue: str = ""
    state: str = ""
    turn_count: Optional[int] = None
    sync: str = ""


@dataclass(frozen=True)
class ListSessionsResult:
    """list_sessions: local rows with journal merge badges."""

    sessions: List[SessionListRow] = field(default_factory=list)
    remote_available: bool = False
    remote_reason: str = ""
    sync_mode: str = ""


# ── session setters ──────────────────────────────────────────────────────────
#
# set_model / activate_skill answer the full snapshot, so they map onto
# StateSnapshot with no wrapper. (set_permission_mode retired 2026-10-31,
# ADR-104: the permission mode follows the session's one mode.)


@dataclass(frozen=True)
class PermissionRuleRow:
    """One G14 rule the session runs with (ADR-104 D-3)."""

    index: int = 0
    behavior: Literal["allow", "ask", "deny"] = "allow"
    rule: str = ""
    source: Literal["user", "user_project", "project"] = "user"
    removable: bool = False


@dataclass(frozen=True)
class PermissionRulesResult:
    """permission_rules.list / permission_rules.remove."""

    project_id: str = ""
    rules: List[PermissionRuleRow] = field(default_factory=list)


@dataclass(frozen=True)
class SetInteractionModeResult:
    """set_interaction_mode: the mode, the notice lines, the new snapshot."""

    mode: str = ""
    output: List[OutputLine] = field(default_factory=list)
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SetKnowledgeAcquisitionResult:
    """set_knowledge_acquisition: constraints, human summary, new snapshot."""

    acquisition: KnowledgeAcquisition = field(default_factory=KnowledgeAcquisition)
    summary: str = ""
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SetWorkflowArmedResult:
    """set_workflow_armed: the flag and the new snapshot."""

    workflow_armed: bool = False
    state: StateSnapshot = field(default_factory=StateSnapshot)


@dataclass(frozen=True)
class SetWriteRequiresPlanResult:
    """set_write_requires_plan: the bare flag."""

    write_requires_plan: bool = False


# ── models / skills / MCP ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelRow:
    """One model option (``collect_bridge_model_options``)."""

    name: str = ""
    source: str = ""
    status: str = ""
    label: str = ""
    is_profile_default: bool = absent(False)


@dataclass(frozen=True)
class ProbeProblemRow:
    """One registered source whose ``/models`` probe failed, named for pickers.

    Carrying the failure lets the console say "rikyu: API key rejected …"
    instead of a bare "no provisioned models" (2026-10-20 report).
    """

    source: str = ""
    error: str = ""


@dataclass(frozen=True)
class ListModelsResult:
    """list_models: provisionable options and the session's current model."""

    models: List[ModelRow] = field(default_factory=list)
    current: str = ""
    problems: List[ProbeProblemRow] = field(default_factory=list)


@dataclass(frozen=True)
class UpModelRow:
    """One registry model that `/endpoint up` can start."""

    name: str = ""
    source: str = ""
    status: str = ""
    label: str = ""
    running: bool = False


@dataclass(frozen=True)
class ListUpModelsResult:
    """list_up_models: registry options and the session's current model."""

    models: List[UpModelRow] = field(default_factory=list)
    session_model: str = ""


@dataclass(frozen=True)
class StoppableEndpointRow:
    """One running endpoint that may be stopped."""

    id: str = ""
    label: str = ""
    status: str = ""


@dataclass(frozen=True)
class ListStoppableEndpointsResult:
    """list_stoppable_endpoints: the stop candidates."""

    endpoints: List[StoppableEndpointRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListSkillsResult:
    """list_skills: registered skills with activation state."""

    skills: List[SkillRow] = field(default_factory=list)


@dataclass(frozen=True)
class McpServerRow:
    """One MCP inventory row (registry entry or declared user-config row;
    registry entries may carry arbitrary extra keys — tolerated by design)."""

    name: str = ""
    status: str = ""
    source: str = ""
    agent_visible: bool = False
    type: Optional[str] = absent(None)
    url: Optional[str] = absent(None)
    enabled: Optional[bool] = absent(None)
    description: Optional[str] = absent(None)
    hint: Optional[str] = absent(None)


@dataclass(frozen=True)
class ListMcpServersResult:
    """list_mcp_servers: registry + declared rows, annotated."""

    servers: List[McpServerRow] = field(default_factory=list)


@dataclass(frozen=True)
class SetMcpServerEnabledResult:
    """set_mcp_server_enabled: the re-read inventory plus the outcome;
    ``exposure`` only when enabling succeeded, ``reason`` only on a miss."""

    servers: List[McpServerRow] = field(default_factory=list)
    applied: bool = False
    exposure: Optional[Dict[str, Any]] = absent(None)
    reason: Optional[str] = absent(None)


@dataclass(frozen=True)
class McpToolRow:
    """One MCP tool row (``client.mcp.list_tools``)."""

    name: str = ""
    description: str = ""
    server: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ListMcpToolsResult:
    """list_mcp_tools: tools across servers (or one)."""

    tools: List[McpToolRow] = field(default_factory=list)


@dataclass(frozen=True)
class McpServerHealthResult:
    """mcp_server_health: registry row refreshed by a live client probe
    (``r4/mcp.py`` ``check_server_health``)."""

    server: str = ""
    server_id: str = ""
    url: str = ""
    healthy: bool = False
    status: str = ""
    registry_status: str = ""
    source: str = ""
    checked_at: str = ""


# ── changes / plan / jobs / tasks ────────────────────────────────────────────


@dataclass(frozen=True)
class SessionChangeRow:
    """One recorded before→after file change (``r4.companion.tools``);
    ``reverted`` appears only after a revert touched the entry."""

    path: str = ""
    before: str = ""
    after: str = ""
    before_existed: bool = True
    before_truncated: bool = False
    before_lossy: bool = False
    inserted: int = 0
    deleted: int = 0
    edits: int = 0
    diff_text: str = ""
    reverted: bool = absent(False)


@dataclass(frozen=True)
class GetSessionChangesResult:
    """get_session_changes: cumulative per-path session diff."""

    changes: List[SessionChangeRow] = field(default_factory=list)


@dataclass(frozen=True)
class RevertSessionChangeResult:
    """revert_session_change: the written-back path; ``deleted`` when the
    session created the file."""

    path: str = ""
    reverted: bool = False
    bytes: int = 0
    deleted: bool = absent(False)


@dataclass(frozen=True)
class TaskRow:
    """One task-graph row (``Task.to_dict``)."""

    id: str = ""
    parent_id: Optional[str] = None
    title: str = ""
    status: str = ""
    progress: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    notes: str = ""
    depends_on: List[str] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GetSessionPlanResult:
    """get_session_plan: the session's plan steps with dependency edges."""

    steps: List[TaskRow] = field(default_factory=list)


@dataclass(frozen=True)
class TaskResult:
    """add_task / get_task: the single row."""

    task: TaskRow = field(default_factory=TaskRow)


@dataclass(frozen=True)
class UpdateTaskResult:
    """update_task: the row after the update (null when the id is unknown)."""

    task: Optional[TaskRow] = None


@dataclass(frozen=True)
class ListTasksResult:
    """list_tasks: every task-graph row."""

    tasks: List[TaskRow] = field(default_factory=list)


@dataclass(frozen=True)
class LocalJobRow:
    """One detached-agent job (the job manager's sqlite row)."""

    id: str = ""
    session_id: str = ""
    pid: Optional[int] = None
    status: str = ""
    started_at: str = ""
    ended_at: Optional[str] = None
    log_path: Optional[str] = None
    task_description: Optional[str] = None


@dataclass(frozen=True)
class JobAttemptRow:
    """One durable attempt (``_attempt_row``)."""

    attempt_id: str = ""
    attempt_no: int = 0
    status: str = ""
    backend: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class DurableJobRow:
    """One UMRS durable job view (``_durable_job``)."""

    id: str = ""
    job_id: str = ""
    state: str = ""
    status: str = ""
    job_type: str = ""
    project_id: str = ""
    attempts: List[JobAttemptRow] = field(default_factory=list)


@dataclass(frozen=True)
class ListJobsResult:
    """list_jobs: this session's background jobs, newest first."""

    jobs: List[LocalJobRow] = field(default_factory=list)


@dataclass(frozen=True)
class GetJobResult:
    """get_job: the local row, or the durable view when only UMRS has it."""

    job: Optional[Union[LocalJobRow, DurableJobRow]] = None


@dataclass(frozen=True)
class GetJobLogResult:
    """get_job_log: the log tail."""

    log: str = ""
    job_id: str = ""


@dataclass(frozen=True)
class StopJobResult:
    """stop_job: whether a running job was stopped."""

    stopped: bool = False
    job_id: str = ""


# ── files / explain / source ─────────────────────────────────────────────────


@dataclass(frozen=True)
class FilesUploadResult:
    """files.upload: the stored object's identity (fields the bucket names)."""

    ok: bool = False
    key: Optional[str] = None
    bucket: Optional[str] = None
    uri: Optional[str] = None
    size: Optional[int] = None


@dataclass(frozen=True)
class FilesDownloadResult:
    """files.download: the written destination and the client's own result."""

    ok: bool = False
    key: str = ""
    dest: str = ""
    result: Any = None


@dataclass(frozen=True)
class ExplainResult:
    """explain: registry cause+remediation for an error id (or a miss hint)."""

    found: bool = False
    error_id: str = ""
    cause: str = absent("")
    remediation: str = absent("")
    exit_code: Optional[int] = absent(None)
    retryable: bool = absent(False)
    hint: str = absent("")


@dataclass(frozen=True)
class ExplainMatchResult:
    """explain.match: the first registry id found in free text (or none)."""

    found: bool = False
    error_id: str = absent("")
    cause: str = absent("")
    remediation: str = absent("")


@dataclass(frozen=True)
class SourceRow:
    """One local source with its sealed-remote badge."""

    name: str = ""
    kind: str = ""
    url: str = ""
    provider: Optional[str] = None
    has_api_key: bool = False
    remote: str = ""
    pushed: bool = False
    eligible: bool = False


@dataclass(frozen=True)
class SourceListRemoteResult:
    """source.list_remote: badge rows; journal reachability alongside."""

    rows: List[SourceRow] = field(default_factory=list)
    remote_available: bool = False


@dataclass(frozen=True)
class SourcePushResult:
    """source.push: the accepted push (fields the journal answers)."""

    ok: bool = False
    name: str = ""
    allow_host: Optional[str] = None
    key_fingerprint: Optional[str] = None
    replaced: bool = False


@dataclass(frozen=True)
class SourceUnpushResult:
    """source.unpush: the removed push and revoked lease count."""

    ok: bool = False
    name: str = ""
    leases_revoked: int = 0


# ── worktrees (ADR-108 D-2, W1b) ─────────────────────────────────────────────


@dataclass(frozen=True)
class WorktreeRow:
    """One registered worktree (``r4.worktrees.Worktree``) and its state."""

    id: str = ""
    path: str = ""
    branch: str = ""
    base: str = ""
    parent: str = ""
    owner: str = ""
    level: str = ""
    snapshot_ref: str = ""
    created_at: float = 0.0
    changed: bool = False


@dataclass(frozen=True)
class WorktreeListResult:
    """worktree.list: the repository's worktrees; ``current`` is the one this
    session is in ("" = none); ``error`` when the workspace has no repository."""

    worktrees: List[WorktreeRow] = field(default_factory=list)
    current: str = ""
    workspace_root: str = ""
    # W1c: off | auto, and where it came from (session, env, profile, default).
    mode: str = "off"
    mode_source: str = "default"
    error: str = absent("")


@dataclass(frozen=True)
class WorktreeEnterResult:
    """worktree.enter: the session now works in ``worktree``; ``ok`` false
    with ``message`` when it could not (a named refusal, never a raise)."""

    ok: bool = False
    message: str = ""
    workspace_root: str = ""
    created: bool = False
    warning: str = ""
    worktree: Optional[WorktreeRow] = None


@dataclass(frozen=True)
class WorktreeExitResult:
    """worktree.exit: back in the session's original workspace; the worktree
    was kept or ``removed`` (a changed one is kept unless discarded)."""

    ok: bool = False
    message: str = ""
    workspace_root: str = ""
    worktree_id: str = ""
    removed: bool = False
    changed: bool = False


@dataclass(frozen=True)
class WorktreeModeResult:
    """worktree.mode: the session's worktree mode (``off|auto``), after an
    optional set; ``ok`` false with ``message`` for a bad value."""

    ok: bool = False
    message: str = ""
    mode: str = "off"
    mode_source: str = "default"


CONSOLE_RESULT_TYPES: Dict[str, ResultSchemaRoot] = {
    "initialize": InitializeResult,
    "load_session": LoadSessionResult,
    "export_session": ExportSessionResult,
    "replay_session": ReplaySessionResult,
    "summarize_session": SummarizeSessionResult,
    "list_sessions": ListSessionsResult,
    "cancel_turn": OkResult,
    "shutdown": OkResult,
    "set_model": StateSnapshot,
    "permission_rules.list": PermissionRulesResult,
    "permission_rules.remove": PermissionRulesResult,
    "activate_skill": StateSnapshot,
    "set_interaction_mode": SetInteractionModeResult,
    "set_knowledge_acquisition": SetKnowledgeAcquisitionResult,
    "set_workflow_armed": SetWorkflowArmedResult,
    "set_write_requires_plan": SetWriteRequiresPlanResult,
    "list_models": ListModelsResult,
    "list_up_models": ListUpModelsResult,
    "list_stoppable_endpoints": ListStoppableEndpointsResult,
    "list_skills": ListSkillsResult,
    "list_mcp_servers": ListMcpServersResult,
    "set_mcp_server_enabled": SetMcpServerEnabledResult,
    "list_mcp_tools": ListMcpToolsResult,
    "mcp_server_health": McpServerHealthResult,
    "get_session_changes": GetSessionChangesResult,
    "revert_session_change": RevertSessionChangeResult,
    "get_session_plan": GetSessionPlanResult,
    "add_task": TaskResult,
    "get_task": TaskResult,
    "update_task": UpdateTaskResult,
    "list_tasks": ListTasksResult,
    "list_jobs": ListJobsResult,
    "get_job": GetJobResult,
    "get_job_log": GetJobLogResult,
    "stop_job": StopJobResult,
    "files.upload": FilesUploadResult,
    "files.download": FilesDownloadResult,
    "explain": ExplainResult,
    "explain.match": ExplainMatchResult,
    "source.list_remote": SourceListRemoteResult,
    "source.push": SourcePushResult,
    "source.unpush": SourceUnpushResult,
    "worktree.list": WorktreeListResult,
    "worktree.enter": WorktreeEnterResult,
    "worktree.exit": WorktreeExitResult,
    "worktree.mode": WorktreeModeResult,
}
