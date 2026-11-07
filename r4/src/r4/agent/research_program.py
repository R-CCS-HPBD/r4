"""ResearchProgram — top-level scientific object.

A :class:`ResearchProgram` is the highest-level object in the autonomous
science hierarchy. It owns a portfolio of open research questions, a
shared budget, and a set of scientific metrics to optimize.

Hierarchy::

    ResearchProgram          (the "why")
        ↓
    ResearchQuestion         (a specific question)
        ↓
    Hypothesis               (a testable claim)
        ↓
    Experiment               (a protocol that produces evidence)
        ↓
    Evidence                 (for/against a hypothesis)
        ↓
    Conclusion               (a final answer)

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §5.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProgramStatus(str, Enum):
    """Lifecycle status of a research program."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class Metric:
    """A scientific metric to optimize.

    Attributes:
        name: Metric identifier (e.g. ``"recall"``).
        direction: ``"maximize"`` or ``"minimize"``.
        target: Optional target value for convergence.
        weight: Relative weight in multi-objective optimization.
    """

    name: str
    direction: str = "maximize"
    target: Optional[float] = None
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.direction not in ("maximize", "minimize"):
            raise ValueError(f"direction must be 'maximize' or 'minimize', got {self.direction!r}")
        if self.weight < 0:
            raise ValueError(f"weight must be non-negative, got {self.weight}")


@dataclass
class ProgramBudget:
    """Shared resource budget for a research program.

    Attributes:
        total_gpu_hours: Total GPU-hour budget across all questions.
        total_iterations: Total iteration budget across all questions.
        total_cost: Total dollar-cost budget.
        gpu_hours_used: GPU-hours consumed so far.
        iterations_used: Iterations consumed so far.
        cost_used: Dollar cost consumed so far.
    """

    total_gpu_hours: float = 100.0
    total_iterations: int = 50
    total_cost: float = 1000.0
    gpu_hours_used: float = 0.0
    iterations_used: int = 0
    cost_used: float = 0.0

    @property
    def remaining_gpu_hours(self) -> float:
        return max(0.0, self.total_gpu_hours - self.gpu_hours_used)

    @property
    def remaining_iterations(self) -> int:
        return max(0, self.total_iterations - self.iterations_used)

    @property
    def remaining_cost(self) -> float:
        return max(0.0, self.total_cost - self.cost_used)

    @property
    def is_exhausted(self) -> bool:
        return (
            self.remaining_gpu_hours <= 0.0
            or self.remaining_iterations <= 0
            or self.remaining_cost <= 0.0
        )

    @property
    def fraction_remaining(self) -> float:
        """Minimum fraction remaining across all dimensions."""
        fractions = [
            self.remaining_gpu_hours / max(1.0, self.total_gpu_hours),
            self.remaining_iterations / max(1, self.total_iterations),
            self.remaining_cost / max(1.0, self.total_cost),
        ]
        return min(fractions)

    def consume(
        self,
        gpu_hours: float = 0.0,
        iterations: int = 0,
        cost: float = 0.0,
    ) -> None:
        """Record consumption of budget resources."""
        self.gpu_hours_used += gpu_hours
        self.iterations_used += iterations
        self.cost_used += cost


@dataclass
class ResearchProgram:
    """Top-level research program.

    The ``"why"`` of the research — a portfolio of related questions
    under a shared budget and set of metrics.

    Attributes:
        id: Unique program identifier (e.g. ``"rp_2026_001"``).
        name: Human-readable program name.
        goals: List of high-level goal statements.
        questions: IDs of open research questions in this program.
        budget: Shared resource budget.
        metrics: Scientific metrics to optimize.
        status: Current lifecycle status.
        created_at: When this program was created.
        description: Optional longer description.
    """

    id: str
    name: str
    goals: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    budget: ProgramBudget = field(default_factory=ProgramBudget)
    metrics: list[Metric] = field(default_factory=list)
    status: ProgramStatus = ProgramStatus.ACTIVE
    created_at: datetime = field(default_factory=_utcnow)
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = ProgramStatus(self.status)
        if isinstance(self.budget, dict):
            self.budget = ProgramBudget(**self.budget)

    # ── Status Transitions ──────────────────────────────────────────

    def pause(self) -> None:
        """Pause the program (no new experiments until resumed)."""
        if self.status != ProgramStatus.ACTIVE:
            raise ValueError(f"Cannot pause program in {self.status.value} state")
        self.status = ProgramStatus.PAUSED

    def resume(self) -> None:
        """Resume a paused program."""
        if self.status != ProgramStatus.PAUSED:
            raise ValueError(f"Cannot resume program in {self.status.value} state")
        self.status = ProgramStatus.ACTIVE

    def complete(self) -> None:
        """Mark the program as completed."""
        if self.status in (ProgramStatus.COMPLETED, ProgramStatus.ABANDONED):
            return
        self.status = ProgramStatus.COMPLETED

    def abandon(self) -> None:
        """Mark the program as abandoned."""
        self.status = ProgramStatus.ABANDONED

    # ── Question Management ─────────────────────────────────────────

    def add_question(self, question_id: str) -> None:
        """Add a research question to this program."""
        if question_id not in self.questions:
            self.questions.append(question_id)

    def remove_question(self, question_id: str) -> None:
        """Remove a research question from this program."""
        if question_id in self.questions:
            self.questions.remove(question_id)

    # ── Budget ─────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status == ProgramStatus.ACTIVE

    @property
    def budget_exhausted(self) -> bool:
        return self.budget.is_exhausted

    def can_run_more(self) -> bool:
        """True if the program is active and has remaining budget."""
        return self.is_active and not self.budget_exhausted

    # ── Serialisation ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "goals": self.goals,
            "questions": self.questions,
            "budget": {
                "total_gpu_hours": self.budget.total_gpu_hours,
                "total_iterations": self.budget.total_iterations,
                "total_cost": self.budget.total_cost,
                "gpu_hours_used": self.budget.gpu_hours_used,
                "iterations_used": self.budget.iterations_used,
                "cost_used": self.budget.cost_used,
            },
            "metrics": [
                {
                    "name": m.name,
                    "direction": m.direction,
                    "target": m.target,
                    "weight": m.weight,
                }
                for m in self.metrics
            ],
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchProgram":
        budget_data = data.get("budget", {})
        metrics_data = data.get("metrics", [])

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = _utcnow()

        return cls(
            id=data["id"],
            name=data["name"],
            goals=data.get("goals", []),
            questions=data.get("questions", []),
            budget=ProgramBudget(
                total_gpu_hours=float(budget_data.get("total_gpu_hours", 100.0)),
                total_iterations=int(budget_data.get("total_iterations", 50)),
                total_cost=float(budget_data.get("total_cost", 1000.0)),
                gpu_hours_used=float(budget_data.get("gpu_hours_used", 0.0)),
                iterations_used=int(budget_data.get("iterations_used", 0)),
                cost_used=float(budget_data.get("cost_used", 0.0)),
            ),
            metrics=[
                Metric(
                    name=m["name"],
                    direction=m.get("direction", "maximize"),
                    target=m.get("target"),
                    weight=float(m.get("weight", 1.0)),
                )
                for m in metrics_data
            ],
            status=ProgramStatus(data.get("status", "active")),
            created_at=created_at if isinstance(created_at, datetime) else _utcnow(),
            description=data.get("description", ""),
        )

    def __repr__(self) -> str:
        return (
            f"ResearchProgram(id={self.id!r}, name={self.name!r}, "
            f"status={self.status.value}, questions={len(self.questions)})"
        )
