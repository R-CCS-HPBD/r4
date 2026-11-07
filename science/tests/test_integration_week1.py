"""Integration tests for Week 1: Experiment + Reward + Domain Plugin.

Tests the full flow:
    formulate_hypothesis → design_experiment → execute → evaluate → compute_reward

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.4
"""

from science.domain import (
    Constraints,
    Evidence,
    Hypothesis,
    ResearchContext,
    ScienceDomain,
)
from science.experiment import Experiment, ExperimentResults, ExperimentStatus
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

        if not results.succeeded:
            return Evidence(
                experiment_id=experiment.id,
                hypothesis_id=experiment.hypothesis_id,
                supports=False,
                strength=0.0,
                summary=f"Experiment failed: {results.error}",
            )
        recall = results.metrics.get("recall", 0.0)
        fpr = results.metrics.get("false_positive_rate", 1.0)
        score = recall * (1.0 - fpr)
        return Evidence(
            experiment_id=experiment.id,
            hypothesis_id=experiment.hypothesis_id,
            supports=score > 0.3,
            strength=min(1.0, score),
            summary=f"recall={recall:.3f}, fpr={fpr:.3f}",
        )

    def compute_reward(
        self,
        experiment: Experiment,
        evidence: Evidence,
    ) -> ScientificReward:
        recall = 0.0
        processing_time = 0.0
        if experiment.results:
            recall = experiment.results.metrics.get("recall", 0.0)
            processing_time = experiment.results.metrics.get("processing_time", 0.0)
        max_time = 3600.0
        return ScientificReward(
            performance=recall,
            cost=processing_time / max_time if max_time > 0 else 0.0,
        )


# ── Integration Tests ──────────────────────────────────────────────


class TestFullAutonomousFlow:
    """End-to-end: formulate → design → execute → evaluate → reward."""

    def test_complete_flow(self):
        """The full autonomous science flow completes successfully."""
        domain = EchoDomain()

        # 1. Formulate hypothesis
        ctx = ResearchContext(question_id="q_001")
        hyp = domain.formulate_hypothesis("Can tuning improve detection?", ctx)
        assert isinstance(hyp, Hypothesis)
        assert hyp.question_id == "q_001"

        # 2. Design experiment
        exp = domain.design_experiment(hyp, Constraints())
        assert isinstance(exp, Experiment)
        assert exp.status == ExperimentStatus.PENDING
        assert "threshold" in exp.protocol

        # 3. Execute (mock: inject results directly)
        exp.results = ExperimentResults(
            metrics={"recall": 0.85, "false_positive_rate": 0.12},
        )
        exp.status = ExperimentStatus.COMPLETED

        # 4. Evaluate results
        evidence = domain.evaluate_results(exp, exp.results)
        assert isinstance(evidence, Evidence)
        assert evidence.supports  # High recall, low FPR

        # 5. Compute reward
        reward = domain.compute_reward(exp, evidence)
        assert isinstance(reward, ScientificReward)
        assert reward.performance > 0.0
        assert reward.total > 0.0

    def test_failed_experiment_flow(self):
        """A failed experiment still produces evidence (contradicts)."""
        domain = EchoDomain()
        ctx = ResearchContext(question_id="q_001")
        hyp = domain.formulate_hypothesis("test question", ctx)
        exp = domain.design_experiment(hyp, Constraints())

        # Simulate failure
        exp.results = ExperimentResults(error="Workflow crashed")
        exp.status = ExperimentStatus.FAILED

        evidence = domain.evaluate_results(exp, exp.results)
        assert not evidence.supports
        assert evidence.strength == 0.0


class TestExperimentExecution:
    """Tests for the Experiment.execute() method."""

    def test_execute_with_mock_runner(self):
        """execute() delegates to the workflow runner correctly."""

        class MockRecord:
            results = {"recall": 0.82, "processing_time": 60.0}
            artifacts = ["s3://bucket/output.json"]
            logs = "Processing complete"

        def mock_runner(workflow_json, **kwargs):
            return MockRecord()

        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"threshold": 0.05},
            workflow_ref="example_analyze",
        )
        results = exp.execute(
            workflow_runner=mock_runner,
            workflow_json={"steps": []},
        )

        assert results.succeeded
        assert results.metrics["recall"] == 0.82
        assert exp.status == ExperimentStatus.COMPLETED

    def test_execute_failure_sets_status(self):
        """A failed execution sets the experiment status to FAILED."""

        def failing_runner(workflow_json, **kwargs):
            raise RuntimeError("Workflow crashed")

        exp = Experiment(hypothesis_id="h_001")
        results = exp.execute(
            workflow_runner=failing_runner,
            workflow_json={},
        )

        assert exp.status == ExperimentStatus.FAILED
        assert not results.succeeded


class TestRewardComputation:
    """Tests for the ScientificReward computation from experiments."""

    def test_high_recall_high_reward(self):
        """High recall experiments produce higher rewards."""
        domain = EchoDomain()

        # High recall
        exp_high = Experiment(
            hypothesis_id="h_001",
            results=ExperimentResults(metrics={"recall": 0.95}),
        )
        ev_high = Evidence(
            experiment_id=exp_high.id,
            hypothesis_id="h_001",
            supports=True,
            strength=0.9,
        )
        reward_high = domain.compute_reward(exp_high, ev_high)

        # Low recall
        exp_low = Experiment(
            hypothesis_id="h_001",
            results=ExperimentResults(metrics={"recall": 0.30}),
        )
        ev_low = Evidence(
            experiment_id=exp_low.id,
            hypothesis_id="h_001",
            supports=False,
            strength=0.1,
        )
        reward_low = domain.compute_reward(exp_low, ev_low)

        assert reward_high.performance > reward_low.performance
        assert reward_high.total > reward_low.total

    def test_reward_cost_increases_with_processing_time(self):
        """Longer experiments incur higher cost."""
        domain = EchoDomain()

        # Fast experiment
        exp_fast = Experiment(
            hypothesis_id="h_001",
            results=ExperimentResults(
                metrics={"recall": 0.8, "processing_time": 30.0},
            ),
        )
        ev_fast = Evidence(
            experiment_id=exp_fast.id,
            hypothesis_id="h_001",
            supports=True,
            strength=0.8,
        )
        reward_fast = domain.compute_reward(exp_fast, ev_fast)

        # Slow experiment
        exp_slow = Experiment(
            hypothesis_id="h_001",
            results=ExperimentResults(
                metrics={"recall": 0.8, "processing_time": 3000.0},
            ),
        )
        ev_slow = Evidence(
            experiment_id=exp_slow.id,
            hypothesis_id="h_001",
            supports=True,
            strength=0.8,
        )
        reward_slow = domain.compute_reward(exp_slow, ev_slow)

        assert reward_slow.cost > reward_fast.cost


class TestDomainPluginInterface:
    """Tests that verify the ScienceDomain Protocol is correctly implemented."""

    def test_echo_domain_is_science_domain(self):
        """EchoDomain satisfies the ScienceDomain Protocol."""
        domain = EchoDomain()
        assert isinstance(domain, ScienceDomain)

    def test_all_protocol_methods_present(self):
        """All ScienceDomain Protocol methods are present."""
        domain = EchoDomain()

        assert hasattr(domain, "formulate_hypothesis")
        assert hasattr(domain, "design_experiment")
        assert hasattr(domain, "evaluate_results")
        assert hasattr(domain, "compute_reward")

        assert callable(domain.formulate_hypothesis)
        assert callable(domain.design_experiment)
        assert callable(domain.evaluate_results)
        assert callable(domain.compute_reward)
