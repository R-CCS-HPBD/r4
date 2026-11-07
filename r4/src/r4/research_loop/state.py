"""Research Loop state model — persistent state for hypothesis-driven science.

Phase 1 scope: dataclass-only. No agent integration yet.
Serializes to/from JSON for local file persistence.

Path: ~/.config/r4/research_loops/<loop_id>/state.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class LoopPhase(Enum):
    """Phases of a research loop iteration."""

    FORMULATING = "formulating"
    DESIGNING = "designing"
    EXECUTING = "executing"
    ANALYZING = "analyzing"
    EVALUATING = "evaluating"
    DECIDING = "deciding"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class LoopDecision(Enum):
    """Decision outcome from the DECIDING phase.

    Home moved here from the deleted test-only ``orchestrator.py``
    (gap row D-32 / defect V-10, 2026-09-22); ``agent_exec.py`` maps it.
    """

    CONTINUE_SAME = "continue_same"  # Continue with same hypothesis, new experiment
    CONTINUE_REVISE = "continue_revise"  # Revise hypothesis, next iteration
    BRANCH = "branch"  # Explore alternative hypothesis
    PUBLISH = "publish"  # Conclusive results, finalize
    STOP = "stop"  # Budget exhausted or dead end


# -- Hypothesis ------------------------------------------------------------


class HypothesisStatus(Enum):
    """Lifecycle status of a hypothesis."""

    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    RETIRED = "retired"


@dataclass
class HypothesisRecord:
    """A single hypothesis in a research loop.

    Mirrors r4.agent.hypothesis.Hypothesis but is serializable
    without depending on the agent module.
    """

    id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = 0.5  # 0.0–1.0
    evidence_for: list[str] = field(default_factory=list)  # EvidenceRecord IDs
    evidence_against: list[str] = field(default_factory=list)  # EvidenceRecord IDs
    created_at: str = ""
    resolved_at: Optional[str] = None
    parent_hypothesis_id: Optional[str] = None  # for revised hypotheses

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def net_evidence_score(self, evidence_by_id: dict[str, "EvidenceRecord"]) -> float:
        """Compute net evidence score: sum(supporting) - sum(contradicting)."""
        supporting = sum(
            evidence_by_id[eid].strength for eid in self.evidence_for if eid in evidence_by_id
        )
        contradicting = sum(
            evidence_by_id[eid].strength for eid in self.evidence_against if eid in evidence_by_id
        )
        return supporting - contradicting


# -- Experiment ------------------------------------------------------------


class ExperimentStatus(Enum):
    """Lifecycle status of an experiment."""

    PENDING = "pending"
    DESIGNED = "designed"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExperimentRecord:
    """A single experiment in a research loop."""

    id: str
    hypothesis_id: str
    name: str = ""
    protocol: dict[str, Any] = field(default_factory=dict)
    pipeline_ref: Optional[str] = None  # UMRS workflow execution ID
    job_id: Optional[str] = None  # UMRS durable job ID
    status: ExperimentStatus = ExperimentStatus.PENDING
    results: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    reward_score: float = 0.0  # ScientificReward.total after evaluation
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = ExperimentStatus(self.status)


# -- Evidence --------------------------------------------------------------


@dataclass
class EvidenceRecord:
    """A piece of evidence linking an experiment result to a hypothesis.

    Created by the DA (Data Analyst) agent after analyzing experiment results.
    Mirrors r4.agent.hypothesis.Evidence.
    """

    id: str
    experiment_id: str
    hypothesis_id: str
    supports: bool  # True = supports hypothesis, False = contradicts
    strength: float  # 0.0 (weak) to 1.0 (decisive)
    summary: str = ""
    source_artifact: Optional[str] = None  # A2L artifact ID
    statistical_test: Optional[str] = None  # e.g. "t-test", "ANOVA", "Cohen's d"
    p_value: Optional[float] = None
    effect_size: Optional[float] = None


# -- Agent Assignments -----------------------------------------------------


class AgentRole(Enum):
    """Research-specialized agent roles."""

    RL = "RL"  # Research Lead
    DA = "DA"  # Data Analyst
    ED = "ED"  # Experiment Designer
    PR = "PR"  # Peer Reviewer


class AgentStatus(Enum):
    """Per-agent execution status."""

    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING = "waiting"
    ERROR = "error"


@dataclass
class AgentAssignment:
    """Tracks which agent is assigned to which role."""

    role: AgentRole
    agent_id: str
    status: AgentStatus = AgentStatus.IDLE
    current_task: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.role, str):
            self.role = AgentRole(self.role)
        if isinstance(self.status, str):
            self.status = AgentStatus(self.status)


# -- Budget ----------------------------------------------------------------


@dataclass
class LoopBudget:
    """Resource budget for a research loop.

    Enforces bounds: max iterations, max wall clock, max compute cost,
    max consecutive failures. Tracks consumption.
    """

    # Limits
    max_iterations: int = 20
    max_wall_clock_hours: float = 48.0
    max_compute_cost_tokens: int = 1_000_000
    max_consecutive_failures: int = 3

    # Consumption tracking
    iterations_completed: int = 0
    tokens_consumed: int = 0
    experiments_run: int = 0
    consecutive_failures: int = 0
    started_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()

    @property
    def _elapsed_hours(self) -> float:
        """Hours since ``started_at``; 0.0 when the stamp is unparseable."""
        try:
            started = datetime.fromisoformat(str(self.started_at))
        except (TypeError, ValueError):
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 3600.0)

    @property
    def is_exhausted(self) -> bool:
        """True if any budget limit is reached."""
        return (
            self.iterations_completed >= self.max_iterations
            or self._elapsed_hours >= self.max_wall_clock_hours
            or self.consecutive_failures >= self.max_consecutive_failures
            or self.tokens_consumed >= self.max_compute_cost_tokens
        )

    @property
    def budget_remaining_pct(self) -> float:
        """Percentage of least-remaining budget dimension."""
        iter_pct = 1.0 - (self.iterations_completed / self.max_iterations)
        wall_pct = (
            1.0
            if self.max_wall_clock_hours == 0
            else 1.0 - (self._elapsed_hours / self.max_wall_clock_hours)
        )
        fail_pct = (
            1.0
            if self.max_consecutive_failures == 0
            else 1.0 - (self.consecutive_failures / self.max_consecutive_failures)
        )
        token_pct = 1.0 - (self.tokens_consumed / self.max_compute_cost_tokens)
        return min(iter_pct, wall_pct, fail_pct, token_pct) * 100

    def record_iteration(self) -> None:
        self.iterations_completed += 1

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def reset_consecutive_failures(self) -> None:
        self.consecutive_failures = 0


# -- ResearchLoopState -----------------------------------------------------


@dataclass
class ResearchLoopState:
    """Persistent state for a research loop session.

    Serialized to JSON at ~/.config/r4/research_loops/<loop_id>/state.json.
    All records (hypotheses, experiments, evidence) are stored inline.

    Usage:
        state = ResearchLoopState(
            loop_id="20260616-mof5-carbon-capture",
            objective="Optimize MOF-5 for carbon capture",
        )
        state.save()
        ...
        loaded = ResearchLoopState.load(state.loop_id)
    """

    loop_id: str
    phase: LoopPhase = LoopPhase.FORMULATING

    # Core research artifacts
    objective: str = ""
    domain: str = ""  # e.g. "chemistry", "materials", "biology"
    hypotheses: dict[str, HypothesisRecord] = field(default_factory=dict)
    current_hypothesis_id: Optional[str] = None
    experiments: dict[str, ExperimentRecord] = field(default_factory=dict)
    evidence_pool: dict[str, EvidenceRecord] = field(default_factory=dict)

    # Agent coordination
    agents: dict[str, AgentAssignment] = field(default_factory=dict)  # role→assignment

    # Iteration tracking
    budget: LoopBudget = field(default_factory=LoopBudget)
    iteration_number: int = 0
    cumulative_reward: float = 0.0  # Sum of ScientificReward.total across experiments

    # History
    decisions: list[dict[str, Any]] = field(default_factory=list)
    phase_history: list[dict[str, Any]] = field(default_factory=list)

    # Provenance
    a2l_execution_ids: list[str] = field(default_factory=list)
    pipeline_job_ids: list[str] = field(default_factory=list)

    # Metadata
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.phase, str):
            self.phase = LoopPhase(self.phase)
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    # -- Record management -------------------------------------------------

    def add_hypothesis(self, statement: str, confidence: float = 0.5) -> str:
        """Add a new hypothesis. Returns the hypothesis ID."""
        h_id = f"H-{len(self.hypotheses) + 1:03d}"
        self.hypotheses[h_id] = HypothesisRecord(
            id=h_id,
            statement=statement,
            confidence=confidence,
        )
        if not self.current_hypothesis_id:
            self.current_hypothesis_id = h_id
        return h_id

    def add_experiment(self, hypothesis_id: str, name: str = "") -> str:
        """Add a new experiment linked to a hypothesis. Returns experiment ID."""
        e_id = f"E-{len(self.experiments) + 1:03d}"
        self.experiments[e_id] = ExperimentRecord(
            id=e_id,
            hypothesis_id=hypothesis_id,
            name=name,
        )
        return e_id

    def add_evidence(
        self,
        experiment_id: str,
        hypothesis_id: str,
        supports: bool,
        strength: float,
        summary: str = "",
    ) -> str:
        """Add evidence and link it to the hypothesis."""
        ev_id = f"EV-{len(self.evidence_pool) + 1:03d}"
        self.evidence_pool[ev_id] = EvidenceRecord(
            id=ev_id,
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            supports=supports,
            strength=strength,
            summary=summary,
        )
        # Link evidence to hypothesis
        if supports:
            self.hypotheses[hypothesis_id].evidence_for.append(ev_id)
        else:
            self.hypotheses[hypothesis_id].evidence_against.append(ev_id)
        return ev_id

    @property
    def current_hypothesis(self) -> Optional[HypothesisRecord]:
        if self.current_hypothesis_id:
            return self.hypotheses.get(self.current_hypothesis_id)
        return None

    # -- Persistence -------------------------------------------------------

    def _state_dir(self) -> Path:
        """Compute the state directory path."""
        return Path.home() / ".config" / "r4" / "research_loops" / self.loop_id

    def _state_path(self) -> Path:
        return self._state_dir() / "state.json"

    def save(self) -> None:
        """Persist state to JSON file."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        d = self._state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(self._state_path(), "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, loop_id: str) -> "ResearchLoopState":
        """Load state from JSON file."""
        path = Path.home() / ".config" / "r4" / "research_loops" / loop_id / "state.json"
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: Path) -> "ResearchLoopState":
        with open(path) as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def list_loops(cls) -> list[str]:
        """List all saved research loop IDs."""
        base = Path.home() / ".config" / "r4" / "research_loops"
        if not base.exists():
            return []
        return sorted(d.name for d in base.iterdir() if d.is_dir() and (d / "state.json").exists())

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "phase": self.phase.value,
            "objective": self.objective,
            "domain": self.domain,
            "hypotheses": {k: _hypothesis_to_dict(v) for k, v in self.hypotheses.items()},
            "current_hypothesis_id": self.current_hypothesis_id,
            "experiments": {k: _experiment_to_dict(v) for k, v in self.experiments.items()},
            "evidence_pool": {k: _evidence_to_dict(v) for k, v in self.evidence_pool.items()},
            "agents": {k: _agent_to_dict(v) for k, v in self.agents.items()},
            "budget": _budget_to_dict(self.budget),
            "iteration_number": self.iteration_number,
            "cumulative_reward": self.cumulative_reward,
            "decisions": self.decisions,
            "phase_history": self.phase_history,
            "a2l_execution_ids": self.a2l_execution_ids,
            "pipeline_job_ids": self.pipeline_job_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchLoopState":
        return cls(
            loop_id=data["loop_id"],
            phase=LoopPhase(data.get("phase", "formulating")),
            objective=data.get("objective", ""),
            domain=data.get("domain", ""),
            hypotheses=_dict_load(data.get("hypotheses", {}), _hypothesis_from_dict),
            current_hypothesis_id=data.get("current_hypothesis_id"),
            experiments=_dict_load(data.get("experiments", {}), _experiment_from_dict),
            evidence_pool=_dict_load(data.get("evidence_pool", {}), _evidence_from_dict),
            agents=_dict_load(data.get("agents", {}), _agent_from_dict),
            budget=_budget_from_dict(data.get("budget", {})),
            iteration_number=data.get("iteration_number", 0),
            cumulative_reward=float(data.get("cumulative_reward", 0.0)),
            decisions=data.get("decisions", []),
            phase_history=data.get("phase_history", []),
            a2l_execution_ids=data.get("a2l_execution_ids", []),
            pipeline_job_ids=data.get("pipeline_job_ids", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# -- Serialization helpers -------------------------------------------------


def _dict_load(
    data: dict[str, Any],
    loader: Any,
) -> dict[str, Any]:
    """Load a dict by mapping a loader function over its values.

    Reduces line-length pressure in from_dict() by factoring out the repeated
    dict comprehension pattern.
    """
    return {k: loader(v) for k, v in data.items()}


def _hypothesis_to_dict(h: HypothesisRecord) -> dict[str, Any]:
    return {
        "id": h.id,
        "statement": h.statement,
        "status": h.status.value,
        "confidence": h.confidence,
        "evidence_for": h.evidence_for,
        "evidence_against": h.evidence_against,
        "created_at": h.created_at,
        "resolved_at": h.resolved_at,
        "parent_hypothesis_id": h.parent_hypothesis_id,
    }


def _hypothesis_from_dict(d: dict[str, Any]) -> HypothesisRecord:
    return HypothesisRecord(
        id=d["id"],
        statement=d["statement"],
        status=HypothesisStatus(d.get("status", "proposed")),
        confidence=float(d.get("confidence", 0.5)),
        evidence_for=d.get("evidence_for", []),
        evidence_against=d.get("evidence_against", []),
        created_at=d.get("created_at", ""),
        resolved_at=d.get("resolved_at"),
        parent_hypothesis_id=d.get("parent_hypothesis_id"),
    )


def _experiment_to_dict(e: ExperimentRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "hypothesis_id": e.hypothesis_id,
        "name": e.name,
        "protocol": e.protocol,
        "pipeline_ref": e.pipeline_ref,
        "job_id": e.job_id,
        "status": e.status.value,
        "results": e.results,
        "error_message": e.error_message,
        "reward_score": e.reward_score,
        "submitted_at": e.submitted_at,
        "completed_at": e.completed_at,
    }


def _experiment_from_dict(d: dict[str, Any]) -> ExperimentRecord:
    return ExperimentRecord(
        id=d["id"],
        hypothesis_id=d["hypothesis_id"],
        name=d.get("name", ""),
        protocol=d.get("protocol", {}),
        pipeline_ref=d.get("pipeline_ref"),
        job_id=d.get("job_id"),
        status=ExperimentStatus(d.get("status", "pending")),
        results=d.get("results"),
        error_message=d.get("error_message"),
        reward_score=float(d.get("reward_score", 0.0)),
        submitted_at=d.get("submitted_at"),
        completed_at=d.get("completed_at"),
    )


def _evidence_to_dict(e: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": e.id,
        "experiment_id": e.experiment_id,
        "hypothesis_id": e.hypothesis_id,
        "supports": e.supports,
        "strength": e.strength,
        "summary": e.summary,
        "source_artifact": e.source_artifact,
        "statistical_test": e.statistical_test,
        "p_value": e.p_value,
        "effect_size": e.effect_size,
    }


def _evidence_from_dict(d: dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        id=d["id"],
        experiment_id=d["experiment_id"],
        hypothesis_id=d["hypothesis_id"],
        supports=bool(d.get("supports", True)),
        strength=float(d.get("strength", 0.5)),
        summary=d.get("summary", ""),
        source_artifact=d.get("source_artifact"),
        statistical_test=d.get("statistical_test"),
        p_value=float(d["p_value"]) if d.get("p_value") is not None else None,
        effect_size=float(d["effect_size"]) if d.get("effect_size") is not None else None,
    )


def _agent_to_dict(a: AgentAssignment) -> dict[str, Any]:
    return {
        "role": a.role.value,
        "agent_id": a.agent_id,
        "status": a.status.value,
        "current_task": a.current_task,
    }


def _agent_from_dict(d: dict[str, Any]) -> AgentAssignment:
    return AgentAssignment(
        role=AgentRole(d["role"]),
        agent_id=d["agent_id"],
        status=AgentStatus(d.get("status", "idle")),
        current_task=d.get("current_task"),
    )


def _budget_to_dict(b: LoopBudget) -> dict[str, Any]:
    return {
        "max_iterations": b.max_iterations,
        "max_wall_clock_hours": b.max_wall_clock_hours,
        "max_compute_cost_tokens": b.max_compute_cost_tokens,
        "max_consecutive_failures": b.max_consecutive_failures,
        "iterations_completed": b.iterations_completed,
        "tokens_consumed": b.tokens_consumed,
        "experiments_run": b.experiments_run,
        "consecutive_failures": b.consecutive_failures,
        "started_at": b.started_at,
    }


def _budget_from_dict(d: dict[str, Any]) -> LoopBudget:
    return LoopBudget(
        max_iterations=int(d.get("max_iterations", 20)),
        max_wall_clock_hours=float(d.get("max_wall_clock_hours", 48.0)),
        max_compute_cost_tokens=int(d.get("max_compute_cost_tokens", 1_000_000)),
        max_consecutive_failures=int(d.get("max_consecutive_failures", 3)),
        iterations_completed=int(d.get("iterations_completed", 0)),
        tokens_consumed=int(d.get("tokens_consumed", 0)),
        experiments_run=int(d.get("experiments_run", 0)),
        consecutive_failures=int(d.get("consecutive_failures", 0)),
        started_at=d.get("started_at", ""),
    )
