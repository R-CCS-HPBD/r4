"""Unit tests for BeliefUpdater — Bayesian updating and EIG.

Tests:
- Bayes factor computation
- Bayesian update
- Expected Information Gain (EIG)
- Multiple hypothesis updates

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.4
"""

import pytest

from r4.agent.belief_state import BeliefState
from r4.agent.belief_updater import BeliefUpdater


class TestBayesFactor:
    """Test Bayes factor computation."""

    def test_supporting_evidence_factor_gt_1(self):
        """Supporting evidence produces Bayes factor > 1."""
        updater = BeliefUpdater()
        k = updater.compute_bayes_factor(supports=True, strength=0.5)
        assert k > 1.0

    def test_contradicting_evidence_factor_lt_1(self):
        """Contradicting evidence produces Bayes factor < 1."""
        updater = BeliefUpdater()
        k = updater.compute_bayes_factor(supports=False, strength=0.5)
        assert k < 1.0

    def test_zero_strength_factor_is_1(self):
        """Zero strength evidence produces Bayes factor ≈ 1."""
        updater = BeliefUpdater()
        k = updater.compute_bayes_factor(supports=True, strength=0.0)
        assert k == pytest.approx(1.0, abs=0.01)

    def test_max_strength_support_factor(self):
        """Maximum strength supporting evidence produces high Bayes factor."""
        updater = BeliefUpdater()
        k = updater.compute_bayes_factor(supports=True, strength=1.0)
        assert k > 10.0  # Strong evidence

    def test_invalid_strength_raises(self):
        """Invalid strength values raise ValueError."""
        updater = BeliefUpdater()
        with pytest.raises(ValueError):
            updater.compute_bayes_factor(supports=True, strength=1.5)
        with pytest.raises(ValueError):
            updater.compute_bayes_factor(supports=True, strength=-0.1)


class TestBayesianUpdate:
    """Test Bayesian update of beliefs."""

    def test_supporting_evidence_increases_confidence(self):
        """Supporting evidence increases confidence in hypothesis."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        result = updater.update(bs, "h1", supports=True, strength=0.8)

        assert result.new_confidence > result.old_confidence
        assert bs.hypotheses["h1"].confidence > 0.5

    def test_contradicting_evidence_decreases_confidence(self):
        """Contradicting evidence decreases confidence in hypothesis."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        result = updater.update(bs, "h1", supports=False, strength=0.8)

        assert result.new_confidence < result.old_confidence
        assert bs.hypotheses["h1"].confidence < 0.5

    def test_evidence_count_incremented(self):
        """Evidence count is incremented after update."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        updater.update(bs, "h1", supports=True, strength=0.5)
        assert bs.hypotheses["h1"].evidence_for_count == 1

        updater.update(bs, "h1", supports=False, strength=0.5)
        assert bs.hypotheses["h1"].evidence_against_count == 1

    def test_update_nonexistent_raises(self):
        """Updating non-existent hypothesis raises KeyError."""
        bs = BeliefState()
        updater = BeliefUpdater()
        with pytest.raises(KeyError):
            updater.update(bs, "h1", supports=True, strength=0.5)

    def test_multiple_supporting_evidence_accumulates(self):
        """Multiple pieces of supporting evidence accumulate."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        for _ in range(5):
            updater.update(bs, "h1", supports=True, strength=0.7)

        # After 5 pieces of strong supporting evidence, confidence should be high
        assert bs.hypotheses["h1"].confidence > 0.9

    def test_mixed_evidence_converges(self):
        """Mixed evidence should converge to a stable confidence."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        for _ in range(10):
            updater.update(bs, "h1", supports=True, strength=0.5)
            updater.update(bs, "h1", supports=False, strength=0.5)

        # After mixed evidence with equal strength, confidence should be moderate
        conf = bs.hypotheses["h1"].confidence
        assert 0.3 < conf < 0.7


class TestExpectedInformationGain:
    """Test Expected Information Gain (EIG) computation."""

    def test_eig_non_negative(self):
        """EIG should be non-negative."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)

        updater = BeliefUpdater()
        eig = updater.expected_information_gain(bs, "h1", expected_strength=0.5)
        assert eig >= 0.0

    def test_eig_higher_for_uncertain_hypotheses(self):
        """EIG should be higher when there's more uncertainty."""
        # Scenario 1: High uncertainty
        bs1 = BeliefState()
        bs1.add_hypothesis("h1", confidence=0.5)
        bs1.add_hypothesis("h2", confidence=0.5)

        # Scenario 2: Low uncertainty
        bs2 = BeliefState()
        bs2.add_hypothesis("h1", confidence=0.99)
        bs2.add_hypothesis("h2", confidence=0.01)

        updater = BeliefUpdater()
        eig1 = updater.expected_information_gain(bs1, "h1", expected_strength=0.5)
        eig2 = updater.expected_information_gain(bs2, "h1", expected_strength=0.5)

        # EIG should be higher in the uncertain scenario
        assert eig1 > eig2

    def test_eig_increases_with_expected_strength(self):
        """EIG should increase with expected strength of evidence."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)

        updater = BeliefUpdater()
        eig_weak = updater.expected_information_gain(bs, "h1", expected_strength=0.2)
        eig_strong = updater.expected_information_gain(bs, "h1", expected_strength=0.8)

        # Stronger evidence should have higher EIG
        assert eig_strong >= eig_weak

    def test_eig_for_nonexistent_raises(self):
        """EIG for non-existent hypothesis raises KeyError."""
        bs = BeliefState()
        updater = BeliefUpdater()
        with pytest.raises(KeyError):
            updater.expected_information_gain(bs, "h1", expected_strength=0.5)


class TestUpdateAll:
    """Test updating multiple hypotheses at once."""

    def test_update_all_multiple_hypotheses(self):
        """update_all updates multiple hypotheses."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)

        updater = BeliefUpdater()
        results = updater.update_all(
            bs,
            [
                ("h1", True, 0.7),
                ("h2", False, 0.6),
            ],
        )

        assert len(results) == 2
        assert bs.hypotheses["h1"].confidence > 0.5
        assert bs.hypotheses["h2"].confidence < 0.5

    def test_update_all_skips_nonexistent(self):
        """update_all silently skips non-existent hypotheses."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        updater = BeliefUpdater()
        results = updater.update_all(
            bs,
            [
                ("h1", True, 0.7),
                ("nonexistent", True, 0.5),
            ],
        )

        assert len(results) == 1
