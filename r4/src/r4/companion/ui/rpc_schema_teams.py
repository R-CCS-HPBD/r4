"""Bridge RPC result contract, teams family (plan H-4.2).

Verbs: teamup.* and team.*. Conventions: ``rpc_schema`` module docstring.
Derived from the handler code in ``bridge_server.py`` (teamup.*) and
``team_plan_bridge.py`` (team.*), and from what they relay
(``TeamupSession.get_status``/``list_sessions``, ``TeamService.list_runs``,
the plan executor's JSON views in ``team_plan_tools``); contract-tested by
``r4/tests/test_bridge_rpc_contract_teams.py``.

Pure schema: nothing here is imported by the bridge at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .rpc_schema_base import ResultSchemaRoot, absent

# ── teamup.* (interactive roster teams, TeamupSession) ──────────────────────


@dataclass(frozen=True)
class TeamupSessionRow:
    """One stored teamup session (``TeamupSession.list_sessions``)."""

    session_id: str = ""
    name: str = ""
    status: str = ""
    iteration: int = 0
    updated_at: str = ""
    project_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    max_iterations: Optional[int] = None


@dataclass(frozen=True)
class TeamupListResult:
    """teamup.list: every session in the local store, newest first."""

    sessions: List[TeamupSessionRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TeamupAgentRow:
    """One role's state on teamup.status (``AgentState.to_dict`` + prompt and model)."""

    role: str = ""
    status: str = ""
    current_task: Optional[str] = None
    last_action_at: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    messages_sent: int = 0
    messages_received: int = 0
    prompt: str = ""
    prompt_source: Literal["default", "override"] = "default"
    model: Optional[str] = None


@dataclass(frozen=True)
class TeamupSotaEntry:
    """The HPC preset's project-best measurement (``SOTAEntry.to_dict``)."""

    value: float = 0.0
    metric_type: str = ""
    timestamp: str = ""
    agent: Optional[str] = None
    technique: Optional[str] = None
    hardware_target: Optional[str] = None
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    code_ref: Optional[str] = None


@dataclass(frozen=True)
class TeamupStatusResult:
    """teamup.status (``TeamupSession.get_status``): roles keyed by agent key."""

    session_id: str = ""
    name: str = ""
    crew_pack: Optional[str] = None
    status: str = ""
    iteration: int = 0
    max_iterations: int = 0
    agents: Dict[str, TeamupAgentRow] = field(default_factory=dict)
    current_sota: Optional[TeamupSotaEntry] = None
    started_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class TeamupRunResult:
    """teamup.run: one iteration started in the background (events stream)."""

    ok: bool = True
    session_id: str = ""
    started: bool = True
    expected_iteration: int = 0
    status: str = ""


@dataclass(frozen=True)
class TeamupStartResult:
    """teamup.start: the bootstrap was accepted (``team_started`` lands later)."""

    ok: bool = True
    accepted: bool = True
    goal: str = ""
    crew: str = ""


@dataclass(frozen=True)
class TeamupLifecycleResult:
    """teamup.pause / teamup.resume / teamup.cancel: the session's new status."""

    session_id: str = ""
    status: str = ""


@dataclass(frozen=True)
class TeamupCreateResult:
    """teamup.create: the persisted session (started only on request)."""

    session_id: str = ""
    name: str = ""
    status: str = ""
    roles: List[str] = field(default_factory=list)
    started: bool = False


@dataclass(frozen=True)
class TeamupTailorResult:
    """teamup.tailor (``tailor_role_prompts``); ``degraded`` names a failed meta call."""

    session_id: str = ""
    root: Optional[str] = None
    tailored: List[str] = field(default_factory=list)
    kept_default: List[str] = field(default_factory=list)
    answers_chars: int = 0
    degraded: str = absent("")


@dataclass(frozen=True)
class TeamPlanParamSpec:
    """One declared parameter of a plan template (template JSON, verbatim)."""

    type: str = absent("")
    required: bool = absent(False)
    default: Any = absent(None)
    description: str = absent("")


@dataclass(frozen=True)
class TeamupCrewRow:
    """One crew (``r4.teams.crews.list_crews``): hpc, or the plan template it names."""

    name: str = ""
    template: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    phases: List[str] = field(default_factory=list)
    params: Dict[str, TeamPlanParamSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamupCrewsResult:
    """teamup.crews: every crew, hpc first."""

    crews: List[TeamupCrewRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TeamupEngineRow:
    """One installed team engine (ADR-020 engine SPI descriptor + capabilities)."""

    name: str = ""
    version: str = ""
    trust_tier: str = ""
    available: bool = False
    note: str = ""
    workflow_styles: List[str] = field(default_factory=list)
    supports_streaming: bool = False
    supports_checkpointing: bool = False
    supports_graph_execution: bool = False
    supports_parallel_agents: bool = False
    supports_hierarchical_agents: bool = False
    supports_distributed_execution: bool = False
    supports_multi_model: bool = False
    supports_gpu_workers: bool = False
    supports_human_in_loop: bool = False


@dataclass(frozen=True)
class TeamupEnginesResult:
    """teamup.engines: the engine registry."""

    engines: List[TeamupEngineRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TeamupLogsResult:
    """teamup.logs: recent changelog lines, verbatim."""

    session_id: str = ""
    entries: List[str] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TeamupReportResult:
    """teamup.report: the session's Markdown report."""

    session_id: str = ""
    report: str = ""


@dataclass(frozen=True)
class TeamupHardwareRow:
    """One hardware profile (``list_hardware_profiles``)."""

    id: str = ""
    name: str = ""
    description: str = ""


@dataclass(frozen=True)
class TeamupHardwareResult:
    """teamup.hardware: the profiles a session can target."""

    profiles: List[TeamupHardwareRow] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TeamupSpawnAgentSessionResult:
    """teamup.spawn_agent_session: the role's new chat session."""

    session_id: str = ""
    title: str = ""
    role: str = ""


# ── team.* (TeamService plan runs, team_plan_bridge) ────────────────────────


@dataclass(frozen=True)
class TeamPlanTemplateRow:
    """One built-in plan template (``r4.teams.plan.templates.list_templates``)."""

    name: str = ""
    description: str = ""
    params: Dict[str, TeamPlanParamSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamPlanTemplatesResult:
    """team.plan_templates: the template inventory and how to run one."""

    templates: List[TeamPlanTemplateRow] = field(default_factory=list)
    next: str = ""


@dataclass(frozen=True)
class TeamPlanProgress:
    """Live progress of a run still going in this process (``_running_view``)."""

    stage: str = ""
    attempt: str = ""
    phase: str = ""
    agents_ok: int = 0
    agents_null: int = 0
    tokens: int = 0
    budget_warning: bool = False
    elapsed_sec: float = 0.0


@dataclass(frozen=True)
class TeamPlanReply:
    """team.plan_run / team.plan_result / team.plan_cancel: one plan-run view.

    Every branch answers ``ok``; the rest depends on the branch: refused
    (``problems`` + ``note``, or ``status: rejected`` + ``error``), running
    (``progress``, ``next``), finished (``output``, ``steps``, ``budget``…),
    a ``path`` slice (``value``), or a server-venue run (``venue``…).
    """

    ok: bool = False
    run_id: Optional[str] = absent(None)
    plan: Optional[str] = absent(None)
    status: Optional[str] = absent(None)
    error: Optional[str] = absent(None)
    problems: List[str] = absent(factory=list)
    note: str = absent("")
    next: str = absent("")
    attempt: Any = absent(None)
    progress: TeamPlanProgress = absent(factory=TeamPlanProgress)
    result_path: str = absent("")
    output: Any = absent(None)
    steps: Dict[str, Dict[str, Any]] = absent(factory=dict)
    budget: Any = absent(None)
    journal: Any = absent(None)
    durable: Any = absent(None)
    taint_segments: int = absent(0)
    taint_truncated: bool = absent(False)
    output_truncated: bool = absent(False)
    path: str = absent("")
    value: Any = absent(None)
    venue: str = absent("")
    code: str = absent("")
    worker: Optional[str] = absent(None)
    cancel_requested: str = absent("")


@dataclass(frozen=True)
class TeamRunAgents:
    """A run row's agents column: counts (plan runs) or roles (team sessions)."""

    started: Optional[int] = absent(None)
    ok: Optional[int] = absent(None)
    null: Optional[int] = absent(None)
    roles: List[str] = absent(factory=list)


@dataclass(frozen=True)
class TeamRunCost:
    """A run row's cost column (plan runs; team sessions record none)."""

    tokens: Optional[int] = absent(None)
    llm_calls: Optional[int] = absent(None)


@dataclass(frozen=True)
class TeamRunsRow:
    """One TeamService run (``list_runs``), live progress overlaid by this process."""

    kind: str = ""
    run_id: str = ""
    name: Optional[str] = None
    status: str = ""
    engine: str = absent("")
    project_id: Optional[str] = absent(None)
    attempt: str = absent("")
    iteration: Optional[int] = absent(None)
    max_iterations: Optional[int] = absent(None)
    agents: TeamRunAgents = absent(factory=TeamRunAgents)
    cost: TeamRunCost = absent(factory=TeamRunCost)
    error: Optional[str] = absent(None)
    updated_at: Optional[str] = absent(None)
    phase: Optional[str] = absent(None)
    live: bool = absent(False)


@dataclass(frozen=True)
class TeamRunsResult:
    """team.runs: every team run on this machine, newest first."""

    runs: List[TeamRunsRow] = field(default_factory=list)
    count: int = 0


TEAMS_RESULT_TYPES: Dict[str, ResultSchemaRoot] = {
    "teamup.list": TeamupListResult,
    "teamup.status": TeamupStatusResult,
    "teamup.run": TeamupRunResult,
    "teamup.start": TeamupStartResult,
    "teamup.pause": TeamupLifecycleResult,
    "teamup.resume": TeamupLifecycleResult,
    "teamup.cancel": TeamupLifecycleResult,
    "teamup.create": TeamupCreateResult,
    "teamup.tailor": TeamupTailorResult,
    "teamup.crews": TeamupCrewsResult,
    "teamup.engines": TeamupEnginesResult,
    "teamup.logs": TeamupLogsResult,
    "teamup.report": TeamupReportResult,
    "teamup.hardware": TeamupHardwareResult,
    "teamup.spawn_agent_session": TeamupSpawnAgentSessionResult,
    "team.plan_templates": TeamPlanTemplatesResult,
    "team.plan_run": TeamPlanReply,
    "team.plan_result": TeamPlanReply,
    "team.plan_cancel": TeamPlanReply,
    "team.runs": TeamRunsResult,
}
