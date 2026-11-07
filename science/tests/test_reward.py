"""Unit tests for ScientificReward.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.4
"""

import pytest
from science.reward import ScientificReward, _l2_distance


class TestScientificRewardTotal:
    """Tests for the ``total`` property."""

    def test_zero_reward(self):
        """All-zero components yield zero total."""
        reward = ScientificReward()
        assert reward.total == 0.0

    def test_performance_only(self):
        """Only performance contributes (default weights)."""
        reward = ScientificReward(performance=0.8)
        # total = 0.5 * 0.8 = 0.4
        assert pytest.approx(reward.total) == 0.4

    def test_all_components(self):
        """All components contribute with their weights."""
        reward = ScientificReward(
            performance=0.9,
            novelty=0.5,
            information_gain=0.3,
            reproducibility=0.8,
            cost=0.2,
        )
        # total = 0.5*0.9 + 0.1*0.5 + 0.2*0.3 + 0.1*0.8 - 0.1*0.2
        #       = 0.45 + 0.05 + 0.06 + 0.08 - 0.02 = 0.62
        assert pytest.approx(reward.total, abs=1e-6) == 0.62

    def test_cost_reduces_total(self):
        """Higher cost reduces the total reward."""
        low_cost = ScientificReward(performance=0.5, cost=0.1)
        high_cost = ScientificReward(performance=0.5, cost=0.9)
        assert low_cost.total > high_cost.total


class TestScientificRewardFromExperiment:
    """Tests for the ``from_experiment`` classmethod."""

    def test_performance_delta(self):
        """Performance is the sum of metric deltas."""
        reward = ScientificReward.from_experiment(
            metrics={"recall": 0.82},
            previous_metrics={"recall": 0.75},
        )
        assert pytest.approx(reward.performance) == 0.07

    def test_no_previous_metrics(self):
        """Without previous metrics, first metric is baseline."""
        reward = ScientificReward.from_experiment(
            metrics={"recall": 0.82},
        )
        assert pytest.approx(reward.performance) == 0.82

    def test_novelty_l2_distance(self):
        """Novelty is the minimum L2 distance to prior experiments."""
        reward = ScientificReward.from_experiment(
            metrics={"recall": 0.8},
            parameter_space={"distance_threshold": 0.05},
            previous_parameter_spaces=[
                {"distance_threshold": 0.10},
                {"distance_threshold": 0.15},
            ],
        )
        # Closest is 0.10, distance = |0.05 - 0.10| = 0.05
        assert pytest.approx(reward.novelty) == 0.05

    def test_reproducibility_flag(self):
        """Reproducibility is 1.0 if replicated, else 0.0."""
        replicated = ScientificReward.from_experiment(
            metrics={"recall": 0.8},
            replicated=True,
        )
        not_replicated = ScientificReward.from_experiment(
            metrics={"recall": 0.8},
            replicated=False,
        )
        assert replicated.reproducibility == 1.0
        assert not_replicated.reproducibility == 0.0

    def test_cost_normalisation(self):
        """Cost is normalised GPU-hours."""
        reward = ScientificReward.from_experiment(
            metrics={"recall": 0.8},
            gpu_hours=50.0,
            max_gpu_hours=100.0,
        )
        assert pytest.approx(reward.cost) == 0.5

    def test_information_gain_from_belief_entropy(self):
        """Information gain is belief_entropy_before - after."""
        reward = ScientificReward.from_experiment(
            metrics={"recall": 0.8},
            belief_entropy_before=1.5,
            belief_entropy_after=0.8,
        )
        assert pytest.approx(reward.information_gain) == 0.7


class TestL2Distance:
    """Tests for the ``_l2_distance`` helper."""

    def test_identical_spaces(self):
        """Identical parameter spaces have zero distance."""
        params = {"a": 1.0, "b": 2.0}
        assert _l2_distance(params, params) == 0.0

    def test_single_param_difference(self):
        """Single parameter difference."""
        a = {"threshold": 0.05}
        b = {"threshold": 0.10}
        assert pytest.approx(_l2_distance(a, b)) == 0.05

    def test_multi_param_difference(self):
        """Multiple parameter differences."""
        a = {"x": 0.0, "y": 0.0}
        b = {"x": 3.0, "y": 4.0}
        # sqrt(9 + 16) = 5.0
        assert pytest.approx(_l2_distance(a, b)) == 5.0


class TestScientificRewardSerialisation:
    """Tests for serialisation methods."""

    def test_to_dict_contains_all_components(self):
        """``to_dict`` includes all reward components and weights."""
        reward = ScientificReward(performance=0.8, cost=0.1)
        d = reward.to_dict()
        assert "performance" in d
        assert "novelty" in d
        assert "information_gain" in d
        assert "reproducibility" in d
        assert "cost" in d
        assert "total" in d
        assert "weights" in d

    def test_to_dict_total_matches_property(self):
        """``to_dict`` total matches the ``total`` property."""
        reward = ScientificReward(performance=0.8, cost=0.1)
        assert pytest.approx(reward.to_dict()["total"]) == reward.total
