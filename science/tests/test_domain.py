"""Unit tests for ScienceDomain Protocol and core science types.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.4
"""

from science.domain import (
    Constraints,
    Evidence,
    Hypothesis,
    ResearchContext,
    ScienceDomain,
)
from science.experiment import Experiment, ExperimentResults
from science.reward import ScientificReward

# ── Test ScienceDomain implementation ──────────────────────────────


class EchoDomain:
    """Minimal ScienceDomain for testing the Protocol contract."""

    def formulate_hypothesis(
        self,
        question: str,
        context: ResearchContext,
    ) -> Hypothesis:
        return Hypothesis(
            question_id=context.question_id,
            statement=f"Hypothesis for: {question}",
            confidence=0.5,
            parameters={"threshold": 0.1},
        )

    def design_experiment(
        self,
        hypothesis: Hypothesis,
        constraints: Constraints,
    ) -> Experiment:

        return Experiment(
            hypothesis_id=hypothesis.question_id,
            protocol={"threshold": 0.1},
            workflow_ref="echo",
        )

    def evaluate_results(
        self,
        experiment: Experiment,
        results: ExperimentResults,
    ) -> Evidence:

        return Evidence(
            experiment_id=experiment.id,
            hypothesis_id=experiment.hypothesis_id,
            supports=results.succeeded,
            strength=0.7,
        )

    def compute_reward(
        self,
        experiment: Experiment,
        evidence: Evidence,
    ) -> ScientificReward:

        return ScientificReward(
            performance=0.8,
            cost=0.1,
        )


class TestResearchContext:
    """Tests for ResearchContext."""

    def test_default_creation(self):
        ctx = ResearchContext()
        assert ctx.question_id == ""
        assert ctx.program_id == ""
        assert ctx.prior_results == []
        assert ctx.budget_remaining == 0.0

    def test_with_question(self):
        ctx = ResearchContext(question_id="q_001")
        assert ctx.question_id == "q_001"


class TestConstraints:
    """Tests for Constraints."""

    def test_defaults(self):
        c = Constraints()
        assert c.max_duration_seconds == 3600.0
        assert c.max_cost == 100.0
        assert c.max_iterations == 10


class TestHypothesis:
    """Tests for Hypothesis."""

    def test_creation(self):
        h = Hypothesis(
            question_id="q_001",
            statement="threshold=0.05 improves recall",
        )
        assert h.question_id == "q_001"
        assert h.confidence == 0.5
        assert h.status == "proposed"

    def test_with_parameters(self):
        h = Hypothesis(
            question_id="q_001",
            statement="test",
            parameters={"distance_threshold": 0.05},
        )
        assert h.parameters == {"distance_threshold": 0.05}


class TestEvidence:
    """Tests for Evidence."""

    def test_creation(self):
        e = Evidence(
            experiment_id="exp_001",
            hypothesis_id="h_001",
            supports=True,
            strength=0.8,
        )
        assert e.supports
        assert e.strength == 0.8

    def test_failed_experiment_evidence(self):
        e = Evidence(
            experiment_id="exp_001",
            hypothesis_id="h_001",
            supports=False,
            strength=0.0,
            summary="Experiment failed",
        )
        assert not e.supports


class TestEchoDomainProtocol:
    """Tests that EchoDomain satisfies the ScienceDomain Protocol."""

    def test_is_science_domain(self):
        """EchoDomain is recognised as a ScienceDomain."""
        domain = EchoDomain()
        assert isinstance(domain, ScienceDomain)


class TestEchoDomainFormulateHypothesis:
    """Tests for EchoDomain.formulate_hypothesis()."""

    def test_generates_hypothesis(self):
        domain = EchoDomain()
        ctx = ResearchContext(question_id="q_001")
        hyp = domain.formulate_hypothesis("test question", ctx)

        assert isinstance(hyp, Hypothesis)
        assert hyp.question_id == "q_001"
        assert len(hyp.statement) > 0
        assert hyp.confidence == 0.5
        assert "threshold" in hyp.parameters


class TestEchoDomainDesignExperiment:
    """Tests for EchoDomain.design_experiment()."""

    def test_designs_experiment_from_hypothesis(self):
        domain = EchoDomain()
        hyp = Hypothesis(
            question_id="q_001",
            statement="test",
            parameters={"threshold": 0.08},
        )
        exp = domain.design_experiment(hyp, Constraints())

        assert isinstance(exp, Experiment)
        assert "threshold" in exp.protocol


class TestEchoDomainEvaluateResults:
    """Tests for EchoDomain.evaluate_results()."""

    def test_evaluates_successful_results(self):
        domain = EchoDomain()
        exp = Experiment(hypothesis_id="h_001")
        results = ExperimentResults(
            metrics={"recall": 0.85, "false_positive_rate": 0.10},
        )
        evidence = domain.evaluate_results(exp, results)

        assert isinstance(evidence, Evidence)
        assert evidence.experiment_id == exp.id
        assert evidence.supports
        assert evidence.strength > 0.5

    def test_evaluates_failed_results(self):
        domain = EchoDomain()
        exp = Experiment(hypothesis_id="h_001")
        results = ExperimentResults(error="Workflow crashed")
        evidence = domain.evaluate_results(exp, results)

        assert not evidence.supports


class TestEchoDomainComputeReward:
    """Tests for EchoDomain.compute_reward()."""

    def test_computes_reward_from_experiment(self):
        domain = EchoDomain()
        exp = Experiment(
            hypothesis_id="h_001",
            results=ExperimentResults(
                metrics={"recall": 0.82, "processing_time": 120.0},
            ),
        )
        evidence = Evidence(
            experiment_id=exp.id,
            hypothesis_id="h_001",
            supports=True,
            strength=0.8,
        )
        reward = domain.compute_reward(exp, evidence)

        assert isinstance(reward, ScientificReward)
        assert reward.performance == 0.8
        assert reward.cost > 0.0
