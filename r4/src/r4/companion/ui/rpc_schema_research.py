"""Bridge RPC result contract, research family (plan H-4.2).

Verbs: project_*, loops.*, traces.*, research_loop.*, research_session.view,
research_knowledge.status, rag.query, knowledge reads and writes
(get_knowledge, add_pattern/add_anti_pattern/add_lesson), circles.*,
grants.mine, usage.me, quota.units, evidence.list, citation.open, knowledge.save,
checkpoints.
Conventions: ``rpc_schema`` module docstring. Derived from the handler code
in ``bridge_server.py`` and ``project_bridge.py``, and from what they relay
(UMRS ``api/loop.py`` response models, ``jobs/execution_trace_store.py``
``TraceRecord.to_dict``, ``api/rag.py`` ``RAGQueryResponse``,
``api/circles.py`` ``_row_response``/member rows, ``api/quota_status.py``
``PoleRow``, ``grants/store.py`` ``to_dict``, ``knowledge/graph_registry.py``
content rows, the companion knowledge-base sqlite rows, ``runtime/session_view.py``);
contract-tested by ``r4/tests/test_bridge_rpc_contract_research.py``.

Pure schema: nothing here is imported by the bridge at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

from .rpc_schema_base import ResultSchemaRoot, absent, wire

# ── project_* lifecycle (project_bridge.py) ─────────────────────────────────


@dataclass(frozen=True)
class ProjectRow:
    """One project in the shared inventory listing."""

    project_id: str = ""
    name: Optional[str] = None
    active: bool = False
    gateway_url: Optional[str] = None
    model: Optional[str] = None


@dataclass(frozen=True)
class ProjectListResult:
    """project_list: inventory rows and the CLI-default current id."""

    projects: List[ProjectRow] = field(default_factory=list)
    current: Optional[str] = None


@dataclass(frozen=True)
class ProjectInfoRow:
    """The current project's own config row."""

    project_id: str = ""
    name: Optional[str] = None
    gateway_url: Optional[str] = None
    model: Optional[str] = None


@dataclass(frozen=True)
class ProjectCurrentResult:
    """project_current: the active id and its config (null when unset)."""

    current: Optional[str] = None
    project: Optional[ProjectInfoRow] = None


@dataclass(frozen=True)
class ProjectCreateResult:
    """project_create: create + tab-scoped select."""

    ok: bool = False
    project_id: str = ""
    created: bool = False
    selected: bool = False
    config_changed: bool = False


@dataclass(frozen=True)
class ProjectRegisterResult:
    """project_register: the platform row (POST /gw/projects) + local record.

    ``ok`` false = not registered; ``message`` says why and what to do.
    ``local``: added / linked (gateway recorded) / kept; "" on failure.
    """

    ok: bool = False
    project_id: str = ""
    message: str = ""
    gateway_url: str = ""
    local: str = ""
    name: str = absent("")
    owner: str = absent("")


@dataclass(frozen=True)
class ProjectSwitchResult:
    """project_switch: tab-scoped select; ``note`` only after a real change."""

    ok: bool = False
    project_id: str = ""
    selected: bool = False
    config_changed: bool = False
    note: str = absent("")


@dataclass(frozen=True)
class ProjectDeleteResult:
    """project_delete: shared-inventory delete; selection dropped only here."""

    ok: bool = False
    project_id: str = ""
    deleted: bool = False
    was_selected: bool = False
    config_changed: bool = False


@dataclass(frozen=True)
class ProjectJobStart:
    """project_import/init/verify/readiness/plan/run: the async CLI job;
    ``note`` flags the ignored legacy simulate flag on run."""

    jobId: str = ""
    operation: str = ""
    note: str = absent("")


@dataclass(frozen=True)
class ProjectCancelJobResult:
    """project_cancel_job: signalled (jobId) or unknown (error)."""

    ok: bool = False
    jobId: str = absent("")
    error: str = absent("")


@dataclass(frozen=True)
class ProjectStatusResult:
    """project_status: the UMRS-side project summary payload."""

    status: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectFindingsResult:
    """project_findings: the verification findings summary payload."""

    result: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectIngestResult:
    """project_ingest: the knowledge.graph_ingest payload verbatim."""

    ingest: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectSessionCreateResult:
    """project_session_create: the durable research session row (ADR-025)."""

    session: Dict[str, Any] = field(default_factory=dict)


# ── loops.* (UMRS research loops, T5 rail) ──────────────────────────────────


@dataclass(frozen=True)
class UmrsLoopRow:
    """One UMRS loop listing row (``api/loop.py`` list)."""

    loop_id: str = ""
    status: str = ""
    phase: str = ""
    companion_session_id: str = ""
    project_id: str = ""


@dataclass(frozen=True)
class LoopsListResult:
    """loops.list: the caller's UMRS loops."""

    loops: List[UmrsLoopRow] = field(default_factory=list)
    count: Optional[int] = None


@dataclass(frozen=True)
class UmrsLoopStatus:
    """loops.status (UMRS lane): the full LoopStatusResponse."""

    loop_id: str = ""
    project_id: str = ""
    research_goal: str = ""
    status: str = ""
    phase: str = ""
    current_iteration: int = 0
    max_iterations: int = 0
    total_hypotheses_tested: int = 0
    total_experiments_run: int = 0
    total_findings: int = 0
    success_rate: float = 0.0
    started_at: str = ""
    completed_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UmrsLoopAck:
    """loops.stop/pause (UMRS lane): StopLoopResponse."""

    loop_id: str = ""
    status: str = ""
    message: str = ""


@dataclass(frozen=True)
class UmrsLoopStart:
    """loops.resume (UMRS lane): StartLoopResponse."""

    loop_id: str = ""
    project_id: str = ""
    status: str = ""
    phase: str = ""
    message: str = ""
    workflow_name: str = ""


@dataclass(frozen=True)
class LocalLoopControl:
    """loops.stop/pause/resume (local/hybrid lane): serialized loop + location."""

    loop_id: Optional[str] = None
    status: Optional[str] = None
    phase: Optional[str] = None
    research_goal: Optional[str] = None
    current_iteration: Optional[int] = None
    max_iterations: Optional[int] = None
    tracks: Optional[List[Any]] = absent(None)
    max_tracks: Optional[int] = absent(None)
    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)


# ── traces.* ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraceRow:
    """One execution trace (``TraceRecord.to_dict``)."""

    trace_id: str = ""
    job_id: str = ""
    step_name: str = ""
    step_type: str = ""
    attempt_id: Optional[str] = None
    execution_id: Optional[str] = None
    parent_trace_id: Optional[str] = None
    status: str = ""
    progress: float = 0.0
    inputs_hash: Optional[str] = None
    outputs_hash: Optional[str] = None
    inputs_ref: Optional[str] = None
    outputs_ref: Optional[str] = None
    model_ref: Optional[Dict[str, Any]] = None
    artifact_refs: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_refs: List[Dict[str, Any]] = field(default_factory=list)
    routing_decision: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_detail: Optional[Dict[str, Any]] = None
    inputs_snapshot: Optional[Dict[str, Any]] = None
    outputs_snapshot: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None
    ttl_expires_at: Optional[str] = None


@dataclass(frozen=True)
class TracesListResult:
    """traces.job / traces.execution: caller-scoped trace rows."""

    traces: List[TraceRow] = field(default_factory=list)
    count: Optional[int] = None


# ── research_loop.* / research_session.view / research_knowledge.status ──────


@dataclass(frozen=True)
class BridgeLoopRow:
    """One loop in the research_loop.* projections (``_serialize_loop``)."""

    loop_id: Optional[str] = None
    status: Optional[str] = None
    phase: Optional[str] = None
    research_goal: Optional[str] = None
    current_iteration: Optional[int] = None
    max_iterations: Optional[int] = None
    tracks: Optional[List[Any]] = absent(None)
    max_tracks: Optional[int] = absent(None)


@dataclass(frozen=True)
class RuntimeLocation:
    """Runtime location disclosure (``_runtime_location``)."""

    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchLoopListResult:
    """research_loop.list: live loops for the project."""

    type: Literal["research_loop_list"] = "research_loop_list"
    project_id: Optional[str] = None
    loops: List[BridgeLoopRow] = field(default_factory=list)
    active_count: int = 0
    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchLoopStatusFound:
    """research_loop.status, visible loop: serialized state + recent iterations."""

    type: Literal["research_loop_status"] = "research_loop_status"
    project_id: Optional[str] = None
    loop_id: Optional[str] = None
    status: Optional[str] = None
    phase: Optional[str] = None
    research_goal: Optional[str] = None
    current_iteration: Optional[int] = None
    max_iterations: Optional[int] = None
    tracks: Optional[List[Any]] = absent(None)
    max_tracks: Optional[int] = absent(None)
    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)
    recent_iterations: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchLoopStatusNotFound:
    """research_loop.status, hidden loop (owner-scoped 404 card)."""

    type: Literal["research_loop_status"] = "research_loop_status"
    project_id: Optional[str] = None
    loop_id: str = ""
    found: bool = False
    status: str = ""
    message: str = ""


@dataclass(frozen=True)
class SessionLoopItem:
    """One loop row inside the aggregate session view."""

    loop_id: str = ""
    status: str = ""
    phase: str = ""
    iterations: str = ""
    venue: str = ""
    research_goal: str = ""
    session_id: Optional[str] = None


@dataclass(frozen=True)
class SessionLoopsBlock:
    """``loops`` block of the aggregate view."""

    count: int = 0
    active_count: int = 0
    items: List[SessionLoopItem] = field(default_factory=list)


@dataclass(frozen=True)
class DurableSessionItem:
    """One ADR-025 durable session row."""

    session_id: Optional[str] = None
    title: str = ""
    status: str = ""
    loop_count: int = 0


@dataclass(frozen=True)
class SessionSessionsBlock:
    """``sessions`` block (``reason`` only when unavailable)."""

    available: bool = False
    count: int = 0
    items: List[DurableSessionItem] = field(default_factory=list)
    reason: str = absent("")


@dataclass(frozen=True)
class SessionKnowledgeBlock:
    """``knowledge`` block (``nodes``/``edges`` only when available)."""

    available: bool = False
    reason: str = absent("")
    nodes: int = absent(0)
    edges: int = absent(0)


@dataclass(frozen=True)
class ResearchSessionViewResult:
    """research_session.view: loops + sessions + knowledge digest + note."""

    type: Literal["research_session_view"] = "research_session_view"
    project_id: Optional[str] = None
    authoritative_runtime: str = ""
    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)
    loops: SessionLoopsBlock = field(default_factory=SessionLoopsBlock)
    sessions: SessionSessionsBlock = field(default_factory=SessionSessionsBlock)
    knowledge: SessionKnowledgeBlock = field(default_factory=SessionKnowledgeBlock)
    authority_note: str = ""


@dataclass(frozen=True)
class ResearchKnowledgeStatusResult:
    """research_knowledge.status: location + graph digest."""

    type: Literal["research_knowledge_status"] = "research_knowledge_status"
    project_id: Optional[str] = None
    runtime: str = ""
    durable: bool = False
    distributed: bool = False
    backends: List[str] = field(default_factory=list)
    available: bool = False
    reason: str = absent("")
    nodes: int = absent(0)
    edges: int = absent(0)


# ── rag.query ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RagSourceRow:
    """One retrieval source (UMRS ``RAGSource``)."""

    document_id: str = ""
    content: str = ""
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagQueryResult:
    """rag.query: the answer and its sources (UMRS ``RAGQueryResponse``)."""

    answer: str = ""
    sources: List[RagSourceRow] = field(default_factory=list)
    usage: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── knowledge base (companion sqlite store) ──────────────────────────────────


@dataclass(frozen=True)
class KnowledgeStats:
    """Knowledge-base counters (``get_stats``)."""

    patterns: int = 0
    anti_patterns: int = 0
    lessons: int = 0
    avg_confidence: float = 0.0


@dataclass(frozen=True)
class KnowledgePatternRow:
    """One knowledge_patterns row."""

    id: str = ""
    pattern_type: str = ""
    title: str = ""
    description: str = ""
    evidence: str = ""
    confidence: float = 0.0
    created_at: str = ""


@dataclass(frozen=True)
class KnowledgeLessonRow:
    """One knowledge_lessons row."""

    id: str = ""
    lesson: str = ""
    context: str = ""
    category: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class GetKnowledgeResult:
    """get_knowledge: stats and the newest patterns/lessons."""

    stats: KnowledgeStats = field(default_factory=KnowledgeStats)
    patterns: List[KnowledgePatternRow] = field(default_factory=list)
    lessons: List[KnowledgeLessonRow] = field(default_factory=list)
    session_id: str = ""


@dataclass(frozen=True)
class AddPatternResult:
    """add_pattern / add_anti_pattern: the new row id."""

    pattern_id: str = ""


@dataclass(frozen=True)
class AddLessonResult:
    """add_lesson: the new row id."""

    lesson_id: str = ""


# ── knowledge_graph_content ──────────────────────────────────────────────────


@dataclass(frozen=True)
class GraphContentNode:
    """One node row of the bounded content view."""

    id: str = ""
    node_id: str = ""
    name: str = ""
    label: str = ""
    type: str = ""
    project_id: str = ""
    graph_id: str = ""
    created_at: Optional[str] = None


@dataclass(frozen=True)
class GraphContentEdge:
    """One edge row of the bounded content view (``from`` is on the wire)."""

    from_: str = wire("from")
    to: str = ""
    label: str = ""
    type: str = ""
    graph_id: str = ""
    created_at: Optional[str] = None


@dataclass(frozen=True)
class KnowledgeGraphContentResult:
    """knowledge_graph_content: bounded nodes+edges with lampshade truncation."""

    graph_id: str = ""
    name: str = ""
    kind: str = ""
    project_id: Optional[str] = None
    owner_id: str = ""
    nodes: List[GraphContentNode] = field(default_factory=list)
    edges: List[GraphContentEdge] = field(default_factory=list)
    truncated: bool = False


# ── circles / grants / usage / quota / evidence ──────────────────────────────


@dataclass(frozen=True)
class CircleRow:
    """One circle listing row (``api/circles.py`` ``_row_response``)."""

    project_id: str = ""
    circle_id: str = ""
    name: str = ""
    owner_id: str = ""
    status: str = ""
    retired_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class CirclesListResult:
    """circles.list: owned or member circles."""

    circles: List[CircleRow] = field(default_factory=list)
    count: Optional[int] = None


@dataclass(frozen=True)
class CircleMemberRow:
    """One circle member (the store's member row)."""

    user_id: str = ""
    role: str = ""
    added_by: Optional[str] = None
    added_at: Optional[str] = None


@dataclass(frozen=True)
class CirclesMembersResult:
    """circles.members: the roster of one circle."""

    members: List[CircleMemberRow] = field(default_factory=list)
    project_id: str = ""
    circle_id: str = ""


@dataclass(frozen=True)
class CirclesCreateResult:
    """circles.create: the ensured circle row (ADR-086 amendment 2026-10-19)."""

    circle: CircleRow = field(default_factory=CircleRow)
    created: bool = False


@dataclass(frozen=True)
class CirclesMemberAddResult:
    """circles.member_add: the circle plus its roster after the add."""

    circle: CircleRow = field(default_factory=CircleRow)
    members: List[CircleMemberRow] = field(default_factory=list)


@dataclass(frozen=True)
class GrantRow:
    """One live grant (``grants/store.py`` ``to_dict``)."""

    grant_id: str = ""
    asset_kind: str = ""
    asset_id: str = ""
    grantee_kind: str = ""
    grantee_id: str = ""
    version_pin: Optional[str] = None
    expires_at: Optional[str] = None
    tombstoned_at: Optional[str] = None
    created_by: str = ""
    created_at: Optional[str] = None


@dataclass(frozen=True)
class GrantsMineResult:
    """grants.mine: live grants received by the caller."""

    grants: List[GrantRow] = field(default_factory=list)
    count: Optional[int] = None


@dataclass(frozen=True)
class UsageMeResult:
    """usage.me: caller-attributed rows, grouped."""

    user_id: Optional[str] = None
    group_by: str = ""
    rows: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class QuotaPoleRow:
    """One ADR-051 pole gauge (``PoleRow``)."""

    current: int = 0
    limit: int = 0


@dataclass(frozen=True)
class QuotaUnitsResult:
    """quota.units: both poles, caller-attributed."""

    user_id: str = ""
    cpu_process: QuotaPoleRow = field(default_factory=QuotaPoleRow)
    disk_bytes: QuotaPoleRow = field(default_factory=QuotaPoleRow)


@dataclass(frozen=True)
class EvidenceRow:
    """One evidence entry (``api/evidence.py`` ``_to_response``)."""

    evidence_id: str = ""
    project_id: str = ""
    claim_id: str = ""
    source_type: str = ""
    source_id: str = ""
    content: str = ""
    content_uri: Optional[str] = None
    relevance_score: float = 0.0
    citation: Optional[str] = None
    status: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class EvidenceListResult:
    """evidence.list: caller-scoped evidence rows."""

    evidence: List[EvidenceRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class CitationOpenResult:
    """citation.open: one cited passage with its context (K1.4)."""

    sid: str = ""
    title: str = ""
    artifact_id: str = ""
    source_id: str = ""
    text: str = ""
    window: int = 0
    chunks: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeSaveResult:
    """knowledge.save: one turn kept in project knowledge, verbatim (K1.6)."""

    job_id: str = ""
    project_id: str = ""
    graph_id: str = ""
    turn_id: str = ""
    role: str = ""
    tier: str = ""
    origin: str = ""
    title: str = ""
    chars: int = 0
    line: str = ""


# ── checkpoints ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SaveCheckpointResult:
    """save_checkpoint: the new checkpoint id."""

    checkpoint_id: str = ""


@dataclass(frozen=True)
class CheckpointRow:
    """One checkpoint listing row."""

    id: str = ""
    agent_id: str = ""
    timestamp: str = ""
    checkpoint_type: str = ""


@dataclass(frozen=True)
class ListCheckpointsResult:
    """list_checkpoints: newest first."""

    checkpoints: List[CheckpointRow] = field(default_factory=list)


@dataclass(frozen=True)
class LoadLatestCheckpointResult:
    """load_latest_checkpoint: the stored state (null when none exists)."""

    state: Any = None


RESEARCH_RESULT_TYPES: Dict[str, ResultSchemaRoot] = {
    "project_list": ProjectListResult,
    "project_current": ProjectCurrentResult,
    "project_create": ProjectCreateResult,
    "project_register": ProjectRegisterResult,
    "project_switch": ProjectSwitchResult,
    "project_delete": ProjectDeleteResult,
    "project_import": ProjectJobStart,
    "project_init": ProjectJobStart,
    "project_verify": ProjectJobStart,
    "project_readiness": ProjectJobStart,
    "project_plan": ProjectJobStart,
    "project_run": ProjectJobStart,
    "project_cancel_job": ProjectCancelJobResult,
    "project_status": ProjectStatusResult,
    "project_findings": ProjectFindingsResult,
    "project_ingest": ProjectIngestResult,
    "project_session_create": ProjectSessionCreateResult,
    "loops.list": LoopsListResult,
    "loops.status": UmrsLoopStatus,
    "loops.stop": Union[UmrsLoopAck, LocalLoopControl],
    "loops.pause": Union[UmrsLoopAck, LocalLoopControl],
    "loops.resume": Union[UmrsLoopStart, LocalLoopControl],
    "traces.get": TraceRow,
    "traces.job": TracesListResult,
    "traces.execution": TracesListResult,
    "research_loop.list": ResearchLoopListResult,
    "research_loop.status": Union[ResearchLoopStatusFound, ResearchLoopStatusNotFound],
    "research_session.view": ResearchSessionViewResult,
    "research_knowledge.status": ResearchKnowledgeStatusResult,
    "rag.query": RagQueryResult,
    "get_knowledge": GetKnowledgeResult,
    "add_pattern": AddPatternResult,
    "add_anti_pattern": AddPatternResult,
    "add_lesson": AddLessonResult,
    "knowledge_graph_content": KnowledgeGraphContentResult,
    "circles.list": CirclesListResult,
    "circles.members": CirclesMembersResult,
    "circles.create": CirclesCreateResult,
    "circles.member_add": CirclesMemberAddResult,
    "circles.member_remove": CirclesMembersResult,
    "grants.mine": GrantsMineResult,
    "usage.me": UsageMeResult,
    "quota.units": QuotaUnitsResult,
    "evidence.list": EvidenceListResult,
    "citation.open": CitationOpenResult,
    "knowledge.save": KnowledgeSaveResult,
    "save_checkpoint": SaveCheckpointResult,
    "list_checkpoints": ListCheckpointsResult,
    "load_latest_checkpoint": LoadLatestCheckpointResult,
}
