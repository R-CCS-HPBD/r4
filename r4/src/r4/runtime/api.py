"""Research Runtime API — the contract between Companion surfaces and loop executors.

Adopted architecture (2026-08): the Research Companion orchestrates; a
Research Runtime owns execution state for autonomous research loops.
Two interchangeable implementations exist behind this single API:

- :class:`~r4.runtime.umrs.UmrsRuntime` — authoritative, durable, distributed
  substrate (UMRS durable Job/Attempt model, Slurm/K8s/Compose backends).
- :class:`~r4.runtime.local.LocalRuntime` — SQLite-backed single-process
  runtime for CI, demos, offline development, and small-scale local work.

Authority rule: exactly one durable runtime is authoritative per research
session. A session created on a LocalRuntime is owned by that runtime's
SQLite store; a session created via UMRS is owned by UMRS Postgres. A loop
is never dual-written. Cross-runtime migration is an explicit export/import
operation, never implicit mirroring.

The Companion (and CLI/console/webview) talks only to this API — never to
backend-specific schedulers. New executors (future HPC/cloud) implement the
same :class:`ResearchRuntime` surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

# Status vocabulary (superset; UMRS uses running/paused/stopped/error).
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"

TERMINAL_STATUSES = frozenset({STATUS_STOPPED, STATUS_COMPLETED, STATUS_ERROR})


def _as_dict(value: Any) -> Dict[str, Any]:
    """Coerce a payload fragment to a plain dict; unknown shapes become {}."""
    return dict(value) if isinstance(value, Mapping) else {}


def _map_str(data: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(data.get(key, default) or default)


def _map_int(data: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = data.get(key, default)
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _map_float(data: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = data.get(key, default)
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LoopSpec:
    """Immutable user intent for starting a research loop.

    ``requires`` lets the caller advertise resource needs that the runtime
    router uses for venue selection (gpu, cluster, cores, …) without the
    agent having to know how those venues map onto backends. It is recorded
    into loop metadata by runtimes that honour it, so later inspections see
    exactly what the task asked for.
    """

    project_id: str
    research_goal: str
    max_iterations: int = 10
    approved: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    requires: Mapping[str, Any] = field(default_factory=dict)
    session_id: str = ""
    agentic: bool = False
    # Registered UMRS workflow every real-mode experiment runs (G-P1-3). Empty
    # lets UMRS resolve it: exactly one registered workflow is used
    # automatically, none/several fail closed at start naming the candidates.
    workflow_name: str = ""
    # ADR-066 amendment 2 (D-34): parallel hypothesis tracks (1 = single
    # branch). Only the UMRS engine schedules tracks today.
    max_tracks: int = 1
    # ADR-092 slices 6, 8b: a team-plan template every iteration runs (empty:
    # the runtime's own iteration). The local venue runs it in this process;
    # UMRS runs each round as one venue-hosted plan run (its sandboxed r4
    # worker), refusing by name where the site has no frontend venue.
    iteration_plan: str = ""
    # The plan's params other than the loop's own, and (UMRS) the venue
    # sources its runs' models come from (pushed sources and/or "site").
    plan_args: Mapping[str, Any] = field(default_factory=dict)
    plan_sources: tuple = ()
    # ADR-066 ask policy of the loop: auto | ask | profile ("" = the runtime's
    # default; a plan loop defaults to ask). ``pause_triggers`` names the
    # trigger classes a profile parks on.
    mode: str = ""
    pause_triggers: tuple = ()
    # ADR-099 D-2 (M-4): the user's web access for a server plan loop's runs
    # (none | allowlist | public; "" = not chosen: UMRS then needs
    # R4_WEB_ACCESS, else the start is refused, never silently chosen).
    plan_web_policy: str = ""
    plan_web_allow_domains: tuple[str, ...] = ()
    # ADR-067 wall-clock cap of the loop, in hours (0 = uncapped). Enforced by
    # the local runtime at iteration boundaries and passed down into a plan
    # iteration's own wall clock; UMRS loads its cap from the project row.
    max_wall_clock_hours: float = 0.0
    # The model for iterations this client hands one to: a plan loop's runs
    # (UMRS plan_default_model) and in-process agent iterations. "" = the
    # client's config.model (runtime/loop_model.py; 2026-11-03).
    model: str = ""

    def __post_init__(self) -> None:
        # Normalize session linkage: empty/None → "" (never None downstream).
        if getattr(self, "session_id", None) is None:
            object.__setattr__(self, "session_id", "")


@dataclass
class LoopState:
    """Snapshot of one research loop, runtime-agnostic."""

    loop_id: str
    project_id: str
    research_goal: str
    status: str
    phase: str = "initialized"
    current_iteration: int = 0
    max_iterations: int = 10
    started_at: str = ""
    completed_at: str = ""
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Progress counters exposed by UMRS LoopStatusResponse; 0 for runtimes
    # that do not track them.
    total_hypotheses_tested: int = 0
    total_experiments_run: int = 0
    total_findings: int = 0
    success_rate: float = 0.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LoopState":
        """Build from an UMRS REST payload or a LocalRuntime record."""
        return cls(
            loop_id=_map_str(data, "loop_id"),
            project_id=_map_str(data, "project_id"),
            research_goal=_map_str(data, "research_goal"),
            status=_map_str(data, "status", "unknown"),
            phase=_map_str(data, "phase"),
            current_iteration=_map_int(data, "current_iteration"),
            max_iterations=_map_int(data, "max_iterations", 10),
            started_at=_map_str(data, "started_at"),
            completed_at=_map_str(data, "completed_at"),
            message=_map_str(data, "message"),
            metadata=_as_dict(data.get("metadata")),
            total_hypotheses_tested=_map_int(data, "total_hypotheses_tested"),
            total_experiments_run=_map_int(data, "total_experiments_run"),
            total_findings=_map_int(data, "total_findings"),
            success_rate=_map_float(data, "success_rate"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize with UMRS-compatible keys (CLI printers consume these)."""
        return {
            "loop_id": self.loop_id,
            "project_id": self.project_id,
            "research_goal": self.research_goal,
            "status": self.status,
            "phase": self.phase,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "message": self.message,
            "metadata": dict(self.metadata),
            "total_hypotheses_tested": self.total_hypotheses_tested,
            "total_experiments_run": self.total_experiments_run,
            "total_findings": self.total_findings,
            "success_rate": self.success_rate,
        }


@dataclass
class IterationState:
    """Snapshot of one research-loop iteration."""

    iteration_id: str
    loop_id: str
    iteration_number: int
    phase: str = ""
    status: str = "completed"
    started_at: str = ""
    completed_at: str = ""
    hypothesis: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "IterationState":
        return cls(
            iteration_id=str(data.get("iteration_id", "")),
            loop_id=str(data.get("loop_id", "") or ""),
            iteration_number=int(data.get("iteration_number", data.get("number", 0)) or 0),
            phase=str(data.get("phase", "") or ""),
            status=str(data.get("status", "") or ""),
            started_at=str(data.get("started_at", "") or ""),
            completed_at=str(data.get("completed_at", "") or ""),
            hypothesis=str(data.get("hypothesis", "") or ""),
            summary=_as_dict(data.get("summary") or data.get("results")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration_id": self.iteration_id,
            "loop_id": self.loop_id,
            "iteration_number": self.iteration_number,
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "hypothesis": self.hypothesis,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Self-description of a runtime implementation.

    Location is informational, not a control knob: the Companion never
    branches on it. ``durable`` reports crash-survivability (UMRS Postgres
    vs. local SQLite). ``backends`` lists execution authorities the runtime
    can submit work to (empty for the local runtime).
    """

    location: str
    durable: bool
    distributed: bool
    backends: tuple = ()
    # Tool-grain approval inbox (ADR-036 (a)). UMRS's is durable; a local
    # loop that runs a plan parks on its own record's list (ADR-092 slice 6),
    # so the local runtime has one too.
    approvals: bool = False


# Executor callback for runtimes that drive iterations in-process (LocalRuntime).
# Receives (loop_id, iteration_number, research_goal); returns a summary dict
# recorded on the iteration. Runtimes with server-side engines ignore it.
IterationExecutor = Callable[[str, int, str], Dict[str, Any]]

# ── ADR-092 slice 6: an iteration executor that runs under the loop's policy ──
#
# A plain executor is the callable above. A *loop iteration executor* also
# declares the action its iteration takes (``iteration_action``) and runs
# with the loop's context (``run_iteration``): the runtime gates the action
# by the loop's ask policy before it runs (ADR-066), hands it the loop's
# remaining wall clock (ADR-067) and a way to ask the loop's human through
# the loop's own approvals list, and acts on the decision it returns.

#: The summary key a loop iteration executor puts its loop decision under,
#: and the decisions: the shared table both loop venues read (slice 8b).
from r4_contracts.loop_plan import (  # noqa: E402, F401 — re-exported
    DECISION_CONTINUE,
    DECISION_PUBLISH,
    DECISION_STOP,
    LOOP_DECISION_KEY,
    LOOP_DECISIONS,
)


class LoopHalted(RuntimeError):
    """The loop was stopped while its iteration waited on a human: nobody
    answered, so the wait ends without an answer (never a "no")."""


@dataclass(frozen=True)
class IterationContext:
    """What a loop iteration executor gets from the loop that runs it.

    ``remaining_wallclock_sec`` is the loop's ADR-067 wall clock left at the
    start of the iteration (``None``: uncapped). ``ask_human(action)`` parks
    the iteration on the loop's own approvals list until a human decides it
    through the loop's decide path, and returns True for a yes; it raises
    :class:`LoopHalted` when the loop is stopped first. It never decides
    anything itself. ``stop_requested()`` is True once the loop is stopped.
    """

    loop_id: str
    project_id: str
    iteration_number: int
    research_goal: str
    remaining_wallclock_sec: Optional[float]
    ask_human: Callable[[Dict[str, Any]], bool]
    stop_requested: Callable[[], bool]


def loop_decision_of(summary: Mapping[str, Any]) -> Dict[str, str]:
    """``{"decision", "reason"}`` an iteration summary carries; ``continue``
    when it carries none or an unknown one (a plain executor never ends a
    loop early)."""
    raw = summary.get(LOOP_DECISION_KEY) if isinstance(summary, Mapping) else None
    if not isinstance(raw, Mapping) or raw.get("decision") not in LOOP_DECISIONS:
        return {"decision": DECISION_CONTINUE, "reason": ""}
    return {"decision": str(raw["decision"]), "reason": str(raw.get("reason") or "")}


def is_loop_iteration_executor(executor: Any) -> bool:
    """An executor that runs under the loop's policy (see above)."""
    return callable(getattr(executor, "run_iteration", None)) and callable(
        getattr(executor, "iteration_action", None)
    )


class ResearchRuntime(Protocol):
    """Runtime-agnostic research-loop control surface (location-agnostic)."""

    def capabilities(self) -> RuntimeCapabilities: ...

    def start_loop(
        self, spec: LoopSpec, executor: Optional[IterationExecutor] = None
    ) -> LoopState: ...

    def get_loop(self, loop_id: str) -> LoopState: ...

    def list_loops(self, project_id: Optional[str] = None) -> List[LoopState]: ...

    def list_iterations(self, loop_id: str, limit: int = 50) -> List[IterationState]: ...

    def stop_loop(self, loop_id: str) -> LoopState: ...

    def pause_loop(self, loop_id: str) -> LoopState: ...

    def resume_loop(self, loop_id: str) -> LoopState: ...

    def list_approvals(self, loop_id: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def decide_approval(self, approval_id: str, decision: str) -> Dict[str, Any]: ...
