"""Integration tests for ResearchOrchestrator + ScienceDomain wiring.

Tests that the orchestrator:
- Accepts a science_domain parameter
- Computes rewards via the domain after experiment completion
- Persists reward_score on experiments
- Accumulates cumulative_reward on the session

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §4.5
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from science.domain import Evidence
from science.reward import ScientificReward

from r4.agent.experiment import (
    Experiment,
    ExperimentResults,
    ExperimentStatus,
)
from r4.agent.research_orchestrator import (
    ResearchOrchestrator,
    ResearchSession,
)


class FakeScienceDomain:
    """Fake ScienceDomain for testing the orchestrator wiring."""

    def evaluate_results(self, experiment, results):
        """Return evidence with fixed strength."""
        return Evidence(
            experiment_id=experiment.id if hasattr(experiment, "id") else "",
            hypothesis_id=experiment.hypothesis_id,
            supports=True,
            strength=0.8,
            summary=f"recall={results.metrics.get('recall', 0.0):.3f}",
        )

    def compute_reward(self, experiment, evidence):
        """Return a reward with known performance and cost."""
        recall = 0.0
        if experiment.results:
            recall = experiment.results.metrics.get("recall", 0.0)
        return ScientificReward(
            performance=recall,
            cost=0.1,
        )


class TestOrchestratorScienceDomainWiring:
    """Test that ResearchOrchestrator correctly wires ScienceDomain."""

    def test_orchestrator_accepts_science_domain(self):
        """ResearchOrchestrator accepts a science_domain parameter."""
        mock_client = MagicMock()
        domain = FakeScienceDomain()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
            science_domain=domain,
        )

        assert orchestrator._science_domain is domain

    def test_orchestrator_without_domain(self):
        """ResearchOrchestrator works without a science_domain."""
        mock_client = MagicMock()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
        )

        assert orchestrator._science_domain is None

    def test_compute_reward_persists_to_experiment(self):
        """_compute_and_persist_reward sets reward_score on experiment."""
        mock_client = MagicMock()
        domain = FakeScienceDomain()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
            science_domain=domain,
        )
        orchestrator._session = ResearchSession(goal="test")

        experiment = Experiment(
            name="test_exp",
            hypothesis_id="h_001",
            parameters={"threshold": 0.05},
            status=ExperimentStatus.COMPLETED,
        )
        results = ExperimentResults(
            exit_code=0,
            stdout_summary="completed",
            parsed_outputs={"recall": 0.82},
        )

        reward = orchestrator._compute_and_persist_reward(experiment, results)

        assert reward is not None
        assert reward.performance == pytest.approx(0.82)
        assert experiment.reward_score == pytest.approx(reward.total)

    def test_cumulative_reward_accumulates(self):
        """cumulative_reward accumulates across multiple experiments."""
        mock_client = MagicMock()
        domain = FakeScienceDomain()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
            science_domain=domain,
        )
        orchestrator._session = ResearchSession(goal="test")

        # Run 3 experiments
        for i in range(3):
            experiment = Experiment(
                name=f"test_exp_{i}",
                hypothesis_id="h_001",
                parameters={"threshold": 0.05 * (i + 1)},
                status=ExperimentStatus.COMPLETED,
            )
            results = ExperimentResults(
                exit_code=0,
                parsed_outputs={"recall": 0.7 + 0.05 * i},
            )
            orchestrator._compute_and_persist_reward(experiment, results)

        # cumulative_reward should be the sum of all rewards
        assert orchestrator._session.cumulative_reward > 0
        assert orchestrator._session.cumulative_reward == pytest.approx(
            sum(
                ScientificReward(
                    performance=0.7 + 0.05 * i,
                    cost=0.1,
                ).total
                for i in range(3)
            )
        )

    def test_compute_reward_returns_none_without_domain(self):
        """Without a science_domain, _compute_and_persist_reward returns None."""
        mock_client = MagicMock()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
            science_domain=None,
        )
        orchestrator._session = ResearchSession(goal="test")

        experiment = Experiment(name="test_exp", hypothesis_id="h_001")
        results = ExperimentResults(exit_code=0)

        reward = orchestrator._compute_and_persist_reward(experiment, results)

        assert reward is None
        assert experiment.reward_score == 0.0

    def test_reward_reflects_experiment_quality(self):
        """Higher recall produces higher reward."""
        mock_client = MagicMock()
        domain = FakeScienceDomain()

        orchestrator = ResearchOrchestrator(
            client=mock_client,
            science_domain=domain,
        )
        orchestrator._session = ResearchSession(goal="test")

        # High recall experiment
        exp_high = Experiment(name="high", hypothesis_id="h_001")
        res_high = ExperimentResults(
            exit_code=0,
            parsed_outputs={"recall": 0.95},
        )
        reward_high = orchestrator._compute_and_persist_reward(exp_high, res_high)

        # Low recall experiment
        exp_low = Experiment(name="low", hypothesis_id="h_001")
        res_low = ExperimentResults(
            exit_code=0,
            parsed_outputs={"recall": 0.30},
        )
        reward_low = orchestrator._compute_and_persist_reward(exp_low, res_low)

        assert reward_high.total > reward_low.total
        assert reward_high.performance > reward_low.performance
