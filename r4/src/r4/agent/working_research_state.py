"""WorkingResearchState — mutable, decision-optimized working memory.

The Director queries this, not A2L directly. A2L is Git (immutable);
WorkingResearchState is the IDE Index + Build Cache (mutable, queryable).

This module provides the WorkingResearchState dataclass and related
types for maintaining the current state of the research program.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §4.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .belief_state import BeliefState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Data Classes ───────────────────────────────────────────────────


@dataclass
class ResearchQuestion:
    """A research question within a program.

    Attributes:
        id: Unique identifier (e.g. "q_001").
        statement: The question text.
        program_id: ID of the parent research program.
        status: "open" | "investigating" | "answered" | "abandoned"
        created_at: When this question was created.
    """

    id: str
    statement: str
    program_id: str
    status: str = "open"
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class ResearchProgram:
    """Top-level research program.

    Attributes:
        id: Unique identifier (e.g. "rp_2026_001").
        name: Human-readable name.
        goals: List of goal statements.
        questions: List of question IDs.
        budget_gpu_hours: Total GPU-hour budget.
        budget_iterations: Total iteration budget.
        budget_cost: Total cost budget (dollars).
        status: "active" | "paused" | "completed"
        created_at: When this program was created.
    """

    id: str
    name: str
    goals: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    budget_gpu_hours: float = 100.0
    budget_iterations: int = 50
    budget_cost: float = 1000.0
    status: str = "active"
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class RewardEntry:
    """A single reward entry in the reward history.

    Attributes:
        experiment_id: ID of the experiment.
        reward: Total reward value.
        performance: Performance component.
        novelty: Novelty component.
        information_gain: Information gain component.
        reproducibility: Reproducibility component.
        cost: Cost component.
        timestamp: When this reward was recorded.
    """

    experiment_id: str
    reward: float
    performance: float = 0.0
    novelty: float = 0.0
    information_gain: float = 0.0
    reproducibility: float = 0.0
    cost: float = 0.0
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class BudgetState:
    """Current budget consumption state.

    Attributes:
        total_gpu_hours: Total GPU-hours allocated.
        used_gpu_hours: GPU-hours consumed so far.
        total_iterations: Total iterations allocated.
        used_iterations: Iterations consumed so far.
        total_cost: Total cost budget.
        used_cost: Cost consumed so far.
    """

    total_gpu_hours: float = 100.0
    used_gpu_hours: float = 0.0
    total_iterations: int = 50
    used_iterations: int = 0
    total_cost: float = 1000.0
    used_cost: float = 0.0

    @property
    def remaining_gpu_hours(self) -> float:
        """Remaining GPU-hours."""
        return max(0.0, self.total_gpu_hours - self.used_gpu_hours)

    @property
    def remaining_iterations(self) -> int:
        """Remaining iterations."""
        return max(0, self.total_iterations - self.used_iterations)

    @property
    def remaining_cost(self) -> float:
        """Remaining cost budget."""
        return max(0.0, self.total_cost - self.used_cost)

    @property
    def is_exhausted(self) -> bool:
        """True if any budget dimension is exhausted."""
        return (
            self.remaining_gpu_hours <= 0
            or self.remaining_iterations <= 0
            or self.remaining_cost <= 0
        )

    @property
    def fraction_remaining(self) -> float:
        """Fraction of budget remaining (0.0 to 1.0)."""
        fractions = [
            self.remaining_gpu_hours / max(1.0, self.total_gpu_hours),
            self.remaining_iterations / max(1, self.total_iterations),
            self.remaining_cost / max(1.0, self.total_cost),
        ]
        return min(fractions)


@dataclass
class PortfolioState:
    """State of the research portfolio.

    Attributes:
        active_question_ids: IDs of currently active questions.
        abandoned_question_ids: IDs of abandoned questions.
        answered_question_ids: IDs of answered questions.
        total_experiments: Total experiments run across all questions.
        total_reward: Cumulative reward across all experiments.
    """

    active_question_ids: list[str] = field(default_factory=list)
    abandoned_question_ids: list[str] = field(default_factory=list)
    answered_question_ids: list[str] = field(default_factory=list)
    total_experiments: int = 0
    total_reward: float = 0.0

    def record_experiment(self, reward: float) -> None:
        """Record a completed experiment."""
        self.total_experiments += 1
        self.total_reward += reward

    def abandon_question(self, question_id: str) -> None:
        """Mark a question as abandoned."""
        if question_id in self.active_question_ids:
            self.active_question_ids.remove(question_id)
        self.abandoned_question_ids.append(question_id)

    def answer_question(self, question_id: str) -> None:
        """Mark a question as answered."""
        if question_id in self.active_question_ids:
            self.active_question_ids.remove(question_id)
        self.answered_question_ids.append(question_id)


# ── WorkingResearchState ───────────────────────────────────────────


@dataclass
class WorkingResearchState:
    """Mutable, decision-optimized working memory.

    The Director queries this, not A2L directly.
    A2L is Git (immutable); WorkingResearchState is the
    IDE Index + Build Cache (mutable, queryable).

    Attributes:
        program: The current research program.
        questions: Map of question_id → ResearchQuestion.
        beliefs: Current BeliefState (persisted from Week 2).
        reward_history: List of past reward entries.
        budget: Current budget consumption state.
        portfolio: Cross-question portfolio state.
        last_synced_at: When this state was last synced from A2L.
    """

    program: Optional[ResearchProgram] = None
    questions: dict[str, ResearchQuestion] = field(default_factory=dict)
    beliefs: BeliefState = field(default_factory=BeliefState)
    reward_history: list[RewardEntry] = field(default_factory=list)
    budget: BudgetState = field(default_factory=BudgetState)
    portfolio: PortfolioState = field(default_factory=PortfolioState)
    last_synced_at: Optional[datetime] = None

    # ── Queries ────────────────────────────────────────────────────

    @property
    def current_entropy(self) -> float:
        """Current Shannon entropy of the belief state."""
        return self.beliefs.entropy

    @property
    def cumulative_reward(self) -> float:
        """Total reward accumulated so far."""
        return self.portfolio.total_reward

    @property
    def adr(self) -> float:
        """Autonomous Discovery Rate: cumulative_reward / total_experiments."""
        if self.portfolio.total_experiments == 0:
            return 0.0
        return self.portfolio.total_reward / self.portfolio.total_experiments

    @property
    def budget_remaining_fraction(self) -> float:
        """Fraction of budget remaining (0.0 to 1.0)."""
        return self.budget.fraction_remaining

    @property
    def budget_is_exhausted(self) -> bool:
        """True if any budget dimension is exhausted."""
        return self.budget.is_exhausted

    def get_question(self, question_id: str) -> Optional[ResearchQuestion]:
        """Get a research question by ID."""
        return self.questions.get(question_id)

    def get_active_questions(self) -> list[ResearchQuestion]:
        """Get all active questions."""
        return [
            self.questions[qid]
            for qid in self.portfolio.active_question_ids
            if qid in self.questions
        ]

    # ── Mutations ──────────────────────────────────────────────────

    def set_program(self, program: ResearchProgram) -> None:
        """Set the current research program."""
        self.program = program

    def add_question(self, question: ResearchQuestion) -> None:
        """Add a research question."""
        self.questions[question.id] = question
        self.portfolio.active_question_ids.append(question.id)

    def add_hypothesis(
        self,
        hypothesis_id: str,
        confidence: float = 0.5,
    ) -> None:
        """Add a hypothesis to the belief state."""
        self.beliefs.add_hypothesis(hypothesis_id, confidence)

    def record_experiment(
        self,
        experiment_id: str,
        reward: float,
        gpu_hours: float = 0.0,
        cost: float = 0.0,
    ) -> None:
        """Record a completed experiment."""
        self.portfolio.record_experiment(reward)
        self.budget.used_gpu_hours += gpu_hours
        self.budget.used_cost += cost
        self.budget.used_iterations += 1
        self.reward_history.append(
            RewardEntry(
                experiment_id=experiment_id,
                reward=reward,
            )
        )

    # ── Sync ───────────────────────────────────────────────────────

    def mark_synced(self) -> None:
        """Mark this state as synced from A2L."""
        self.last_synced_at = _utcnow()

    def to_dict(self) -> dict:
        """Serialise to dictionary for persistence."""
        return {
            "program": {
                "id": self.program.id,
                "name": self.program.name,
                "goals": self.program.goals,
                "questions": self.program.questions,
                "status": self.program.status,
            }
            if self.program
            else None,
            "questions": {
                qid: {
                    "id": q.id,
                    "statement": q.statement,
                    "program_id": q.program_id,
                    "status": q.status,
                }
                for qid, q in self.questions.items()
            },
            "beliefs": self.beliefs.to_dict(),
            "reward_history": [
                {
                    "experiment_id": e.experiment_id,
                    "reward": e.reward,
                }
                for e in self.reward_history
            ],
            "budget": {
                "total_gpu_hours": self.budget.total_gpu_hours,
                "used_gpu_hours": self.budget.used_gpu_hours,
                "total_iterations": self.budget.total_iterations,
                "used_iterations": self.budget.used_iterations,
                "total_cost": self.budget.total_cost,
                "used_cost": self.budget.used_cost,
            },
            "portfolio": {
                "active_question_ids": self.portfolio.active_question_ids,
                "abandoned_question_ids": self.portfolio.abandoned_question_ids,
                "answered_question_ids": self.portfolio.answered_question_ids,
                "total_experiments": self.portfolio.total_experiments,
                "total_reward": self.portfolio.total_reward,
            },
            "adr": self.adr,
            "cumulative_reward": self.cumulative_reward,
            "current_entropy": self.current_entropy,
            "last_synced_at": (self.last_synced_at.isoformat() if self.last_synced_at else None),
        }
