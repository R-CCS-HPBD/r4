"""Integration tests for Week 2: BeliefState + BeliefUpdater.

Tests the full Bayesian updating flow:
- Create BeliefState with multiple hypotheses
- Apply Bayesian updates with supporting/contradicting evidence
- Compute Expected Information Gain (EIG)
- Verify entropy decreases with strong evidence
- Verify EIG-based selection chooses the most informative experiment

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.4
"""

import pytest

from r4.agent.belief_state import BeliefState
from r4.agent.belief_updater import BeliefUpdater


class TestFullBayesianFlow:
    """Test the complete Bayesian update flow."""

    def test_multiple_hypotheses_bayesian_update(self):
        """Multiple hypotheses can be updated independently."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)
        bs.add_hypothesis("h3", confidence=0.5)

        updater = BeliefUpdater()

        # Experiment 1 supports h1
        result1 = updater.update(bs, "h1", supports=True, strength=0.8)
        assert result1.new_confidence > 0.5

        # Experiment 2 contradicts h3
        result2 = updater.update(bs, "h3", supports=False, strength=0.7)
        assert result2.new_confidence < 0.5

        # h1 should now be most likely
        most_likely = bs.most_likely_hypothesis
        assert most_likely is not None
        assert most_likely[0] == "h1"

    def test_entropy_decreases_with_strong_evidence(self):
        """Strong evidence should decrease entropy (increase certainty)."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)

        initial_entropy = bs.entropy

        updater = BeliefUpdater()
        # Apply strong supporting evidence for h1
        for _ in range(5):
            updater.update(bs, "h1", supports=True, strength=0.9)

        final_entropy = bs.entropy

        assert final_entropy < initial_entropy

    def test_eig_based_experiment_selection(self):
        """EIG-based selection chooses the most informative experiment."""
        bs = BeliefState()
        # h1 is well-tested (high confidence)
        bs.add_hypothesis("h1", confidence=0.9)
        # h2 is uncertain (medium confidence)
        bs.add_hypothesis("h2", confidence=0.5)
        # h3 is uncertain (medium confidence)
        bs.add_hypothesis("h3", confidence=0.5)

        updater = BeliefUpdater()

        # Compute EIG for each hypothesis
        eig_h1 = updater.expected_information_gain(bs, "h1", expected_strength=0.5)
        eig_h2 = updater.expected_information_gain(bs, "h2", expected_strength=0.5)
        eig_h3 = updater.expected_information_gain(bs, "h3", expected_strength=0.5)

        # EIG for uncertain hypotheses should be higher than for confident ones
        assert eig_h2 > eig_h1
        assert eig_h3 > eig_h1

        # The experiment that maximizes EIG should be on an uncertain hypothesis
        eig_values = {"h1": eig_h1, "h2": eig_h2, "h3": eig_h3}
        best_hypothesis = max(eig_values, key=eig_values.get)
        assert best_hypothesis in ("h2", "h3")

    def test_eig_decreases_with_more_evidence(self):
        """As we gather more evidence, EIG should decrease."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)

        updater = BeliefUpdater()

        # Initial EIG
        eig_initial = updater.expected_information_gain(bs, "h1", expected_strength=0.7)

        # Apply some evidence
        for _ in range(3):
            updater.update(bs, "h1", supports=True, strength=0.7)

        # EIG after evidence
        eig_after = updater.expected_information_gain(bs, "h1", expected_strength=0.7)

        # EIG should decrease as confidence increases
        assert eig_after < eig_initial

    def test_contradicting_evidence_reduces_confidence(self):
        """Contradicting evidence reduces confidence in a hypothesis."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.8)  # High initial confidence
        bs.add_hypothesis("h2", confidence=0.2)

        updater = BeliefUpdater()

        # Apply strong contradicting evidence
        for _ in range(5):
            updater.update(bs, "h1", supports=False, strength=0.9)

        # Confidence should have dropped significantly
        assert bs.hypotheses["h1"].confidence < 0.5

    def test_serialisation_preserves_beliefs(self):
        """Serialisation and deserialisation preserves belief state."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.7)
        bs.add_hypothesis("h2", confidence=0.3)

        # Serialise
        d = bs.to_dict()

        # Deserialise
        bs2 = BeliefState.from_dict(d)

        # Verify beliefs are preserved
        assert bs2.hypotheses["h1"].confidence == pytest.approx(0.7)
        assert bs2.hypotheses["h2"].confidence == pytest.approx(0.3)
        assert bs2.num_hypotheses == 2

    def test_copy_is_independent(self):
        """copy() creates an independent BeliefState."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        bs_copy = bs.copy()

        # Modify the copy
        updater = BeliefUpdater()
        updater.update(bs_copy, "h1", supports=True, strength=0.9)

        # Original should be unchanged
        assert bs.hypotheses["h1"].confidence == pytest.approx(0.5)
        # Copy should be updated
        assert bs_copy.hypotheses["h1"].confidence > 0.5

    def test_eig_for_experiment_testing_multiple_hypotheses(self):
        """EIG for an experiment testing multiple hypotheses."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)
        bs.add_hypothesis("h3", confidence=0.5)

        updater = BeliefUpdater()

        # An experiment that tests h1 and h2
        eig = updater.information_gain_for_experiment(
            bs, experiment_hypotheses=["h1", "h2"], expected_strength=0.6
        )

        # Should be the sum of individual EIGs
        eig_h1 = updater.expected_information_gain(bs, "h1", expected_strength=0.6)
        eig_h2 = updater.expected_information_gain(bs, "h2", expected_strength=0.6)
        assert eig == pytest.approx(eig_h1 + eig_h2, abs=0.01)


class TestExampleDomainWeek2:
    """Test Week 2 integration with an example detection domain."""

    def test_detection_hypotheses_bayesian_update(self):
        """Test Bayesian update for object detection hypotheses."""
        bs = BeliefState()
        # Three hypotheses about an optimal detection parameter value
        bs.add_hypothesis("h_005", confidence=0.33)  # value A
        bs.add_hypothesis("h_010", confidence=0.33)  # value B
        bs.add_hypothesis("h_015", confidence=0.33)  # value C

        updater = BeliefUpdater()

        # Simulate experiments:
        # Experiment 1: parameter value A performs well (supports h_005)
        updater.update(bs, "h_005", supports=True, strength=0.8)

        # Experiment 2: parameter value C performs poorly (contradicts h_015)
        updater.update(bs, "h_015", supports=False, strength=0.6)

        # h_005 should now be most likely
        most_likely = bs.most_likely_hypothesis
        assert most_likely is not None
        assert most_likely[0] == "h_005"

        # Entropy should be lower than initial (more certainty)
        assert bs.entropy < 1.5  # Initial was ~1.58 bits

    def test_detection_eig_selection(self):
        """Test EIG-based experiment selection for object detection."""
        bs = BeliefState()
        # h_005 has been tested and has high confidence
        bs.add_hypothesis("h_005", confidence=0.8)
        # h_010 is uncertain
        bs.add_hypothesis("h_010", confidence=0.5)
        # h_015 has been refuted
        bs.add_hypothesis("h_015", confidence=0.1)

        updater = BeliefUpdater()

        # Compute EIG for testing each hypothesis
        eig_005 = updater.expected_information_gain(bs, "h_005", expected_strength=0.5)
        eig_010 = updater.expected_information_gain(bs, "h_010", expected_strength=0.5)
        eig_015 = updater.expected_information_gain(bs, "h_015", expected_strength=0.5)

        # The uncertain hypothesis (h_010) should have the highest EIG
        assert eig_010 >= eig_005
        assert eig_010 > eig_005

        # EIG for uncertain hypothesis should be higher than confident one
        assert eig_010 > eig_005

        # EIG-based selection: either h_010 (uncertain) or h_015 (nearly refuted)
        # both have high EIG for different reasons
        eig_values = {"h_005": eig_005, "h_010": eig_010, "h_015": eig_015}
        best_hypothesis = max(eig_values, key=eig_values.get)
        assert best_hypothesis in ("h_010", "h_015")
