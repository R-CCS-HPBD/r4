"""ScienceDomain — pluggable scientific domain interface.

Each domain provides the methods needed to run the autonomous
science loop for that specific field of inquiry:

- formulate_hypothesis(): Generate a testable hypothesis.
- design_experiment():     Design an experiment to test it.
- evaluate_results():      Interpret results as evidence.
- compute_reward():        Compute the multi-class reward.

Domains are :class:`typing.Protocol` implementations — they are
structurally typed, not inheritance-based.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from science.experiment import Experiment, ExperimentResults
from science.reward import ScientificReward


@dataclass
class ResearchContext:
    """Context for a single research iteration.

    Attributes:
        question_id: The research question being investigated.
        program_id: The research program this question belongs to.
        prior_results: Results from previous experiments.
        budget_remaining: Remaining budget for this question.
    """

    question_id: str = ""
    program_id: str = ""
    prior_results: list[ExperimentResults] = field(default_factory=list)
    budget_remaining: float = 0.0


@dataclass
class Constraints:
    """Constraints on experiment design.

    Attributes:
        max_duration_seconds: Maximum wall-clock time.
        max_cost: Maximum cost in arbitrary units.
        max_iterations: Maximum number of iterations.
    """

    max_duration_seconds: float = 3600.0
    max_cost: float = 100.0
    max_iterations: int = 10


@dataclass
class Hypothesis:
    """A testable scientific hypothesis.

    Attributes:
        id: Unique hypothesis identifier.
        question_id: The research question this hypothesis addresses.
        statement: The hypothesis statement (natural language).
        confidence: Current confidence level (0.0–1.0).
        status: Lifecycle status of the hypothesis.
        parameters: Specific parameter values implied by the hypothesis.
    """

    question_id: str
    statement: str
    id: str = ""
    confidence: float = 0.5
    status: str = "proposed"
    parameters: dict[str, float] = field(default_factory=dict)


@dataclass
class Evidence:
    """Evidence linking an experiment result to a hypothesis.

    Attributes:
        experiment_id: The experiment that produced this evidence.
        hypothesis_id: The hypothesis this evidence relates to.
        supports: True if evidence supports the hypothesis.
        strength: Strength of the evidence (0.0–1.0).
        summary: Human-readable description.
    """

    experiment_id: str
    hypothesis_id: str
    supports: bool
    strength: float
    summary: str = ""


@runtime_checkable
class ScienceDomain(Protocol):
    """A pluggable scientific domain.

    Each domain provides the methods needed to run the autonomous
    science loop for that specific field of inquiry.

    Implementations should be stateless or hold only configuration —
    experiment state is managed by :class:`~science.experiment.Experiment`.
    """

    def formulate_hypothesis(
        self,
        question: str,
        context: ResearchContext,
    ) -> Hypothesis:
        """Generate a testable hypothesis from the research question.

        Args:
            question: The research question in natural language.
            context: Research context with prior results and budget.

        Returns:
            A :class:`Hypothesis` with a testable statement and
            implied parameter values.
        """
        ...

    def design_experiment(
        self,
        hypothesis: Hypothesis,
        constraints: Constraints,
    ) -> Experiment:
        """Design an experiment to test the hypothesis.

        Args:
            hypothesis: The hypothesis to test.
            constraints: Constraints on the experiment design.

        Returns:
            An :class:`Experiment` ready for execution.
        """
        ...

    def evaluate_results(
        self,
        experiment: Experiment,
        results: ExperimentResults,
    ) -> Evidence:
        """Interpret experiment results as evidence for/against.

        Args:
            experiment: The experiment that was executed.
            results: The results of the experiment.

        Returns:
            :class:`Evidence` linking the results to the hypothesis.
        """
        ...

    def compute_reward(
        self,
        experiment: Experiment,
        evidence: Evidence,
    ) -> ScientificReward:
        """Compute the multi-class scientific reward.

        Args:
            experiment: The experiment that was executed.
            evidence: The evidence derived from the results.

        Returns:
            :class:`ScientificReward` with all components populated.
        """
        ...
