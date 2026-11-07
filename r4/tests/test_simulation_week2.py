"""Simulation tests for Week 2 — EIG vs Random vs Greedy.

This is the **critical acceptance test** for the autonomous science loop.
It empirically demonstrates that EIG-based experiment selection reduces
the number of experiments needed to reach a confidence threshold by
≥30% vs. random selection AND ≥30% vs. greedy (highest-confidence)
selection.

The simulation:
1. Creates N hypotheses with known "ground-truth" support levels.
2. Each experiment tests one hypothesis and returns Bayesian evidence
   whose strength depends on the ground truth.
3. Three strategies are compared:
   - **Random**: pick a hypothesis uniformly at random.
   - **Greedy**: pick the hypothesis with the *highest* current
     confidence (exploitation only).
   - **EIG**: pick the hypothesis with the *highest* Expected
     Information Gain (uncertainty-driven exploration).
4. We measure the number of experiments each strategy needs to push
   the most-likely hypothesis above a confidence threshold.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.4 (Simulation Tests)
"""

from __future__ import annotations

import random
import statistics

import pytest

from r4.agent.belief_state import BeliefState
from r4.agent.belief_updater import BeliefUpdater

# ── Simulation Harness ────────────────────────────────────────────────


def _make_hypotheses(n: int = 5) -> list[str]:
    """Create n hypothesis IDs."""
    return [f"h_{i:02d}" for i in range(n)]


def _seed_belief_state(hyp_ids: list[str], prior: float = 0.5) -> BeliefState:
    """Create a fresh BeliefState with all hypotheses at the same prior."""
    bs = BeliefState()
    for hid in hyp_ids:
        bs.add_hypothesis(hid, confidence=prior)
    return bs


def _evidence_strength(ground_truth: float, noise: float = 0.15) -> tuple[bool, float]:
    """Sample evidence strength for a hypothesis.

    Args:
        ground_truth: The true support level for this hypothesis
                      (0.0 = false, 1.0 = true).
        noise: Standard deviation of the noise added to the signal.

    Returns:
        (supports, strength) tuple.  ``supports`` is True when the
        sampled signal exceeds 0.5; ``strength`` is the absolute
        distance from 0.5, clamped to [0.05, 0.95].
    """
    signal = ground_truth + random.gauss(0.0, noise)
    signal = max(0.05, min(0.95, signal))
    supports = signal > 0.5
    strength = abs(signal - 0.5) * 2.0  # rescale to [0, 1]
    return supports, strength


def _select_target(
    strategy: str,
    hypothesis_ids: list[str],
    beliefs: BeliefState,
    updater: BeliefUpdater,
    rng: random.Random,
) -> str:
    """Select the next hypothesis to test for a strategy."""
    if strategy == "random":
        return rng.choice(hypothesis_ids)
    if strategy == "greedy":
        return max(hypothesis_ids, key=lambda hid: beliefs.hypotheses[hid].confidence)
    if strategy == "eig":
        return max(
            hypothesis_ids,
            key=lambda hid: updater.expected_information_gain(beliefs, hid, expected_strength=0.5),
        )
    raise ValueError(f"Unknown strategy: {strategy}")


def _has_converged(
    beliefs: BeliefState,
    hypothesis_ids: list[str],
    true_hypothesis_id: str,
    threshold: float,
) -> bool:
    """Return True when the true hypothesis wins and alternatives are suppressed."""
    most_likely = beliefs.most_likely_hypothesis
    if most_likely is None:
        return False

    alternatives_ruled_down = sum(
        1
        for hid in hypothesis_ids
        if hid != true_hypothesis_id and beliefs.hypotheses[hid].confidence < 0.35
    )
    required_alternatives = max(1, len(hypothesis_ids) - 2)

    return (
        most_likely[0] == true_hypothesis_id
        and most_likely[1] >= threshold
        and alternatives_ruled_down >= required_alternatives
    )


def _failure_count(counts: list[int], max_experiments: int) -> int:
    """Count simulation runs that hit the experiment cap."""
    return sum(1 for count in counts if count >= max_experiments)


def _run_strategy(
    strategy: str,
    ground_truths: dict[str, float],
    true_hypothesis_id: str,
    threshold: float = 0.85,
    max_experiments: int = 80,
    prior: float = 0.5,
) -> int:
    """Run a single strategy trial.

    The strategy succeeds only when it both:
    1. identifies the true hypothesis as the current best hypothesis, and
    2. gathers enough negative evidence to suppress most alternatives.

    That benchmark is more realistic than merely driving *some* hypothesis
    above a threshold, because greedy reinforcement can do that for the wrong
    branch after an early noisy win.

    Returns the number of experiments needed to converge, or
    ``max_experiments`` if the strategy fails to converge.
    """
    hyp_ids = list(ground_truths.keys())
    bs = _seed_belief_state(hyp_ids, prior=prior)
    updater = BeliefUpdater()
    rng = random.Random(strategy + str(ground_truths))

    for exp_count in range(1, max_experiments + 1):
        target = _select_target(strategy, hyp_ids, bs, updater, rng)

        # Run the "experiment" — sample evidence from ground truth
        supports, strength = _evidence_strength(ground_truths[target])
        updater.update(bs, target, supports=supports, strength=strength)

        if _has_converged(bs, hyp_ids, true_hypothesis_id, threshold):
            return exp_count

    return max_experiments  # Did not converge


def _run_simulation(
    n_trials: int = 200,
    n_hypotheses: int = 5,
    threshold: float = 0.85,
    seed: int = 42,
) -> dict[str, list[int]]:
    """Run the full simulation across all strategies.

    Returns a dict mapping strategy name → list of experiment counts
    (one per trial).
    """
    random.seed(seed)

    strategies = ["random", "greedy", "eig"]
    results: dict[str, list[int]] = {s: [] for s in strategies}

    for trial in range(n_trials):
        # Create a single true hypothesis and several plausible alternatives.
        # This is the realistic research setting EIG is meant for: identify
        # the correct branch while ruling out competing explanations.
        hyp_ids = _make_hypotheses(n_hypotheses)
        true_hypothesis_id = random.choice(hyp_ids)
        ground_truths: dict[str, float] = {}
        for hid in hyp_ids:
            ground_truths[hid] = 0.9 if hid == true_hypothesis_id else 0.2

        for strategy in strategies:
            exp_count = _run_strategy(
                strategy=strategy,
                ground_truths=ground_truths,
                true_hypothesis_id=true_hypothesis_id,
                threshold=threshold,
            )
            results[strategy].append(exp_count)

    return results


# ── Simulation Tests ──────────────────────────────────────────────────


class TestEIGSimulation:
    """Empirical validation that EIG reduces experiments needed."""

    @pytest.fixture(scope="class")
    def simulation_results(self) -> dict[str, list[int]]:
        """Run the simulation once and share across tests."""
        return _run_simulation(n_trials=200, n_hypotheses=10, threshold=0.9)

    def test_eig_beats_random(self, simulation_results):
        """EIG needs ≥30% fewer experiments than random selection."""
        random_counts = simulation_results["random"]
        eig_counts = simulation_results["eig"]

        random_mean = statistics.mean(random_counts)
        eig_mean = statistics.mean(eig_counts)

        reduction = (random_mean - eig_mean) / random_mean

        # EIG should reduce experiments by ≥30% vs random
        assert reduction >= 0.30, (
            f"EIG reduction vs random = {reduction:.1%} "
            f"(random_mean={random_mean:.1f}, eig_mean={eig_mean:.1f}). "
            f"Expected ≥30%."
        )

    def test_eig_beats_greedy(self, simulation_results):
        """EIG needs ≥30% fewer experiments than greedy selection."""
        greedy_counts = simulation_results["greedy"]
        eig_counts = simulation_results["eig"]

        greedy_mean = statistics.mean(greedy_counts)
        eig_mean = statistics.mean(eig_counts)

        reduction = (greedy_mean - eig_mean) / greedy_mean

        # EIG should reduce experiments by ≥30% vs greedy
        assert reduction >= 0.30, (
            f"EIG reduction vs greedy = {reduction:.1%} "
            f"(greedy_mean={greedy_mean:.1f}, eig_mean={eig_mean:.1f}). "
            f"Expected ≥30%."
        )

    def test_eig_converges_faster_median(self, simulation_results):
        """EIG median experiment count is lower than random and greedy."""
        eig_median = statistics.median(simulation_results["eig"])
        random_median = statistics.median(simulation_results["random"])
        greedy_median = statistics.median(simulation_results["greedy"])

        assert eig_median < random_median
        assert eig_median < greedy_median

    def test_eig_never_fails_to_converge_more_than_baseline(self, simulation_results):
        """EIG failure rate (hit max_experiments) is ≤ baseline."""
        max_exp = 80  # Must match _run_strategy default

        eig_failures = _failure_count(simulation_results["eig"], max_exp)
        random_failures = _failure_count(simulation_results["random"], max_exp)
        greedy_failures = _failure_count(simulation_results["greedy"], max_exp)

        # EIG should not fail more often than the baselines
        assert eig_failures <= random_failures or eig_failures == 0
        assert eig_failures <= greedy_failures or eig_failures == 0


class TestEIGSimulationDetailed:
    """Detailed single-trial tests for debugging."""

    def test_single_trial_eig_converges(self):
        """A single trial of EIG converges within budget."""
        random.seed(123)
        hyp_ids = _make_hypotheses(5)
        ground_truths = {hid: random.uniform(0.1, 0.95) for hid in hyp_ids}

        true_hypothesis_id = max(ground_truths, key=ground_truths.get)
        exp_count = _run_strategy(
            strategy="eig",
            ground_truths=ground_truths,
            true_hypothesis_id=true_hypothesis_id,
            threshold=0.85,
            max_experiments=100,
        )

        # Should converge within 100 experiments
        assert exp_count < 100, f"EIG did not converge in 100 experiments (needed {exp_count})"

    def test_single_trial_random_baseline(self):
        """A single trial of random selection runs without error."""
        random.seed(456)
        hyp_ids = _make_hypotheses(5)
        ground_truths = {hid: random.uniform(0.1, 0.95) for hid in hyp_ids}

        true_hypothesis_id = max(ground_truths, key=ground_truths.get)
        exp_count = _run_strategy(
            strategy="random",
            ground_truths=ground_truths,
            true_hypothesis_id=true_hypothesis_id,
            threshold=0.85,
            max_experiments=100,
        )

        # Just verify it returns a valid count
        assert 1 <= exp_count <= 100

    def test_ground_truth_high_confidence_converges_fast(self):
        """When one hypothesis is clearly true, convergence is fast."""
        ground_truths = {
            "h_00": 0.2,  # False
            "h_01": 0.95,  # Clearly true
            "h_02": 0.3,  # False
            "h_03": 0.4,  # False
            "h_04": 0.1,  # False
        }

        eig_count = _run_strategy(
            strategy="eig",
            ground_truths=ground_truths,
            true_hypothesis_id="h_01",
            threshold=0.85,
            max_experiments=50,
        )

        # With one clearly true hypothesis, should converge quickly
        assert eig_count <= 20, (
            f"EIG took {eig_count} experiments to converge on a clearly "
            f"true hypothesis — expected ≤20"
        )
