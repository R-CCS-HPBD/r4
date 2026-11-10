"""Shared pieces of the bridge RPC result contract (plan H-4.2).

The field helpers (``absent``, ``wire``) and the ``get_state`` snapshot
dataclasses, which many verbs embed (``state``). ``rpc_schema`` and the
family modules (``rpc_schema_console``, ``rpc_schema_research``,
``rpc_schema_teams``) import them from here, so a family module never
imports ``rpc_schema`` (which imports every family for its registry).

Pure schema: nothing here is imported by the bridge at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

_ABSENT = "absent"
_WIRE = "wire"


def absent(default: Any = None, *, factory: Callable[[], Any] | None = None) -> Any:
    """A field whose key the handler may leave out of the result."""
    if factory is not None:
        return field(default_factory=factory, metadata={_ABSENT: True})
    return field(default=default, metadata={_ABSENT: True})


def wire(name: str, *, may_be_absent: bool = False) -> Any:
    """A field whose wire key is not a Python identifier (``from``)."""
    return field(default=None, metadata={_WIRE: name, _ABSENT: may_be_absent})


PermissionModeName = Literal["ask_always", "auto_safe", "deny", "trust"]


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


# ── get_state: the session snapshot (CompanionBridgeServer.snapshot) ────────


@dataclass(frozen=True)
class KnowledgeAcquisition:
    """Session knowledge-acquisition mode (``sanitize_acquisition``)."""

    mode: Literal["open", "pinned", "disabled"] = "open"
    graphs: List[str] = field(default_factory=list)


WebAccessModeName = Literal["", "none", "allowlist", "public"]


@dataclass(frozen=True)
class WebAccessChoice:
    """The session's web-access choice (ADR-099 D-2; ``WebAccess.to_metadata``).

    ``mode`` "" = not chosen yet; ``source`` is prompt | flag | env |
    remembered | default_off ("" when not chosen).
    """

    mode: WebAccessModeName = ""
    domains: List[str] = field(default_factory=list)
    chosen_at: str = ""
    source: str = ""


SandboxModeName = Literal["off", "on", "remote"]


@dataclass(frozen=True)
class SandboxState:
    """The session's local tool sandbox (ADR-101 D-12; ``sandbox_state``).

    ``mode`` "remote" for a frontend session (its server sandbox, ADR-046).
    ``source`` is flag | env | session | profile | slash | default_off | venue
    | inherited. ``notice`` is the one line a local session shows while the
    sandbox is off or without a usable backend; "" otherwise. ``mcp_error``
    says why its local MCP servers did not follow an on sandbox ("" when they
    did). ``detail`` lists what turning it on confines; the console renders
    these as sent (2026-10-30, review Q-5).
    """

    mode: SandboxModeName = "off"
    source: str = ""
    backend: str = ""
    available: bool = False
    reason: str = ""
    tmp_dir: str = ""
    write_roots: List[str] = field(default_factory=list)
    network: str = ""
    notice: str = ""
    mcp_error: str = ""
    detail: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class VenueRuntime:
    """References-only projection of the server venue row (``_venue_runtime_projection``)."""

    state: str = ""
    reason: str = ""
    lease_expires_at: Optional[str] = None
    source_names: List[str] = field(default_factory=list)
    default_source: str = ""
    web_policy: str = ""
    phase: str = ""


@dataclass(frozen=True)
class SnapshotSession:
    """The ``session`` block of a snapshot: identity, model, modes, venue."""

    id: str = ""
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    model: Optional[str] = None
    profile_default: str = ""
    model_provisioned: bool = False
    needs_model_selection: bool = False
    permission_mode: PermissionModeName = "ask_always"
    trust_source: str = ""
    interaction_mode: str = "chat"
    plan_awaiting_confirmation: bool = False
    plan_pending_request: str = ""
    chat_queue: List[str] = field(default_factory=list)
    write_requires_plan: bool = False
    workflow_armed: bool = False
    knowledge_acquisition: KnowledgeAcquisition = field(default_factory=KnowledgeAcquisition)
    venue: str = "local"
    venue_runtime: Optional[VenueRuntime] = None
    # ADR-099 D-2 (absent from bridges before RS-1).
    web_access: WebAccessChoice = absent(factory=WebAccessChoice)
    # ADR-101 D-12 (absent from bridges before the local tool sandbox).
    sandbox: SandboxState = absent(factory=SandboxState)


@dataclass(frozen=True)
class SnapshotConfig:
    """The ``config`` block: profile and display-only identity (never authoritative)."""

    profile: str = "default"
    gateway_url: str = ""
    project_id: Optional[str] = None
    source_name: str = ""
    username: str = ""
    credential: Literal["jwt", "api-key", "none"] = "none"


@dataclass(frozen=True)
class SnapshotMessage:
    """One transcript row (``_message_to_dict``; content capped)."""

    role: str = "system"
    content: str = ""
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillRow:
    """One registered skill and whether it is active (``_skills_snapshot``)."""

    name: str = ""
    description: str = ""
    source: str = ""
    active: bool = False
    tools: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class UsageSummary:
    """Aggregate LLM usage totals of the session (``UsageSummary.to_dict``)."""

    llm_calls: int = 0
    compaction_calls: int = 0
    subagent_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_calls: int = 0


@dataclass(frozen=True)
class EndpointBudget:
    """Endpoint node-hour budget (``summarize_endpoint_usage``)."""

    node_hours_estimated: float = 0.0
    active_endpoint_count: int = 0
    nearest_expiry_seconds: Optional[int] = None
    warning_threshold_seconds: int = 0
    block_threshold_seconds: int = 0


@dataclass(frozen=True)
class UsageReport:
    """Session usage report (``CompanionSession.usage_report``)."""

    summary: UsageSummary = field(default_factory=UsageSummary)
    active_endpoints: List[Dict[str, Any]] = field(default_factory=list)
    budget: EndpointBudget = field(default_factory=EndpointBudget)


@dataclass(frozen=True)
class ProgressSnapshot:
    """Harness orientation text (null without a harness or prior progress)."""

    orientation: Optional[str] = None


@dataclass(frozen=True)
class StateSnapshot:
    """Bounded UI snapshot of the active session (``get_state``)."""

    session: SnapshotSession = field(default_factory=SnapshotSession)
    config: SnapshotConfig = field(default_factory=SnapshotConfig)
    total_messages: int = 0
    message_window: int = 0
    messages: List[SnapshotMessage] = field(default_factory=list)
    skills: List[SkillRow] = field(default_factory=list)
    usage: UsageReport = field(default_factory=UsageReport)
    active_endpoints: List[Dict[str, Any]] = field(default_factory=list)
    progress: ProgressSnapshot = field(default_factory=ProgressSnapshot)


@dataclass(frozen=True)
class OutputLine:
    """One line a slash command rendered (``CollectingRenderer``); shared by
    dispatch_slash_command and the console family's mode-change output."""

    kind: Literal["info", "system", "error"] = "info"
    text: str = ""


"""Registry root type for a result schema: a dataclass type, or a
``typing.Union`` special form (the honest relay shape of the two
loop-control lanes — exported as inline ``anyOf``)."""
ResultSchemaRoot = Any
