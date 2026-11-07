"""ScientificReward — multi-class reward function.

Concrete MVP approximations for each component:
  performance:     metric_delta (e.g., recall_new - recall_old)
  novelty:         L2 distance in parameter space from all prior experiments
  information_gain: belief_entropy_before - belief_entropy_after
                    (requires BeliefState from Week 2; for Week 1, approx 0.0)
  reproducibility: 1.0 if result replicated else 0.0
  cost:            normalized_gpu_hours (gpu_hours / max_gpu_hours)

Weights are tunable per program type.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScientificReward:
    """Multi-class scientific reward.

    Attributes:
        performance: Improvement in the primary metric (e.g. recall delta).
        novelty: Distance from previous experiments in parameter space.
        information_gain: Reduction in belief entropy (Week 2).
        reproducibility: Whether the result was independently replicated.
        cost: Normalised GPU-hours consumed by the experiment.

    The ``total`` property returns the weighted sum.  Weights default to
    performance-heavy but can be overridden per program type.
    """

    performance: float = 0.0
    novelty: float = 0.0
    information_gain: float = 0.0
    reproducibility: float = 0.0
    cost: float = 0.0

    # Weights (tunable per program type)
    performance_weight: float = 0.5
    novelty_weight: float = 0.1
    information_gain_weight: float = 0.2
    reproducibility_weight: float = 0.1
    cost_weight: float = 0.1

    @property
    def total(self) -> float:
        """Weighted sum of all reward components."""
        return (
            self.performance_weight * self.performance
            + self.novelty_weight * self.novelty
            + self.information_gain_weight * self.information_gain
            + self.reproducibility_weight * self.reproducibility
            - self.cost_weight * self.cost
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dictionary for A2L metadata."""
        return {
            "performance": self.performance,
            "novelty": self.novelty,
            "information_gain": self.information_gain,
            "reproducibility": self.reproducibility,
            "cost": self.cost,
            "total": self.total,
            "weights": {
                "performance": self.performance_weight,
                "novelty": self.novelty_weight,
                "information_gain": self.information_gain_weight,
                "reproducibility": self.reproducibility_weight,
                "cost": self.cost_weight,
            },
        }

    @classmethod
    def from_experiment(
        cls,
        metrics: dict[str, float],
        previous_metrics: dict[str, float] | None = None,
        parameter_space: dict[str, float] | None = None,
        previous_parameter_spaces: list[dict[str, float]] | None = None,
        replicated: bool = False,
        gpu_hours: float = 0.0,
        max_gpu_hours: float = 100.0,
        belief_entropy_before: float = 0.0,
        belief_entropy_after: float = 0.0,
    ) -> ScientificReward:
        """Compute reward from experiment results.

        Args:
            metrics: Current experiment metrics (e.g. {"recall": 0.82}).
            previous_metrics: Previous experiment metrics for delta computation.
            parameter_space: Current experiment parameters.
            previous_parameter_spaces: All prior experiment parameters.
            replicated: Whether the result was independently replicated.
            gpu_hours: GPU-hours consumed by this experiment.
            max_gpu_hours: Maximum GPU-hours for normalisation.
            belief_entropy_before: Belief entropy before experiment (Week 2).
            belief_entropy_after: Belief entropy after experiment (Week 2).

        Returns:
            ScientificReward with computed components.
        """
        # Performance: metric_delta (sum of all metric deltas)
        performance = 0.0
        if previous_metrics:
            for key, value in metrics.items():
                if key in previous_metrics:
                    performance += value - previous_metrics[key]
        elif metrics:
            # No previous metrics: use first metric as baseline
            first_metric = next(iter(metrics.values()))
            performance = first_metric

        # Novelty: L2 distance in parameter space from all prior experiments
        novelty = 0.0
        if parameter_space and previous_parameter_spaces:
            min_distance = float("inf")
            for prev_params in previous_parameter_spaces:
                distance = _l2_distance(parameter_space, prev_params)
                if distance < min_distance:
                    min_distance = distance
            novelty = min_distance if min_distance != float("inf") else 0.0

        # Information gain: belief entropy reduction (Week 2)
        information_gain = belief_entropy_before - belief_entropy_after

        # Reproducibility: 1.0 if result replicated else 0.0
        reproducibility = 1.0 if replicated else 0.0

        # Cost: normalised GPU-hours
        cost = gpu_hours / max_gpu_hours if max_gpu_hours > 0 else 0.0

        return cls(
            performance=performance,
            novelty=novelty,
            information_gain=information_gain,
            reproducibility=reproducibility,
            cost=cost,
        )


def _l2_distance(params_a: dict[str, float], params_b: dict[str, float]) -> float:
    """Compute L2 distance between two parameter spaces.

    Only shared keys contribute to the distance.  Missing keys in either
    space are treated as 0.0.
    """
    all_keys = set(params_a.keys()) | set(params_b.keys())
    sum_squared = 0.0
    for key in all_keys:
        diff = params_a.get(key, 0.0) - params_b.get(key, 0.0)
        sum_squared += diff * diff
    return sum_squared**0.5
