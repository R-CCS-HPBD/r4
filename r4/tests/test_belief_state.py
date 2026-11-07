"""Unit tests for BeliefState — the scientific reasoning engine state.

Tests:
- Adding/removing hypotheses
- Updating confidence
- Shannon entropy computation
- Normalized entropy
- Most/least likely hypothesis
- Serialisation

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.4
"""

import pytest

from r4.agent.belief_state import BeliefState, HypothesisBelief


class TestBeliefStateConstruction:
    """Test BeliefState construction and basic properties."""

    def test_empty_belief_state(self):
        """Empty BeliefState has zero entropy and no hypotheses."""
        bs = BeliefState()
        assert bs.num_hypotheses == 0
        assert bs.entropy == 0.0
        assert bs.most_likely_hypothesis is None
        assert bs.least_likely_hypothesis is None

    def test_add_hypothesis(self):
        """Adding a hypothesis increases the count."""
        bs = BeliefState()
        belief = bs.add_hypothesis("h1", confidence=0.5)
        assert bs.num_hypotheses == 1
        assert belief.hypothesis_id == "h1"
        assert belief.confidence == 0.5

    def test_add_duplicate_hypothesis_raises(self):
        """Adding a duplicate hypothesis raises ValueError."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        with pytest.raises(ValueError, match="already exists"):
            bs.add_hypothesis("h1", confidence=0.6)

    def test_remove_hypothesis(self):
        """Removing a hypothesis decreases the count."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.remove_hypothesis("h1")
        assert bs.num_hypotheses == 0

    def test_remove_nonexistent_raises(self):
        """Removing a non-existent hypothesis raises KeyError."""
        bs = BeliefState()
        with pytest.raises(KeyError):
            bs.remove_hypothesis("h1")


class TestBeliefStateEntropy:
    """Test Shannon entropy computation."""

    def test_single_hypothesis_zero_entropy(self):
        """A single hypothesis has zero entropy (no uncertainty)."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        assert bs.entropy == 0.0

    def test_two_equal_hypotheses_max_entropy(self):
        """Two hypotheses with equal confidence have max entropy (1 bit)."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)
        assert bs.entropy == pytest.approx(1.0, abs=0.01)

    def test_two_unequal_hypotheses_lower_entropy(self):
        """Two hypotheses with unequal confidence have lower entropy."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.9)
        bs.add_hypothesis("h2", confidence=0.1)
        assert bs.entropy < 1.0
        assert bs.entropy > 0.0

    def test_dominant_hypothesis_low_entropy(self):
        """When one hypothesis clearly dominates, entropy is low."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.99)
        bs.add_hypothesis("h2", confidence=0.01)
        assert bs.entropy < 0.2  # Very low entropy

    def test_normalized_entropy_range(self):
        """Normalized entropy is in [0, 1]."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)
        assert 0.0 <= bs.normalized_entropy <= 1.0

    def test_max_entropy_computation(self):
        """Max entropy is log2(n) for n hypotheses."""
        import math

        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.5)
        bs.add_hypothesis("h3", confidence=0.5)
        bs.add_hypothesis("h4", confidence=0.5)
        assert bs.max_entropy == pytest.approx(math.log2(4), abs=0.01)


class TestBeliefStateConfidence:
    """Test confidence updates."""

    def test_update_confidence(self):
        """Updating confidence changes the belief."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.update_confidence("h1", 0.8)
        assert bs.hypotheses["h1"].confidence == 0.8

    def test_update_confidence_nonexistent_raises(self):
        """Updating confidence for non-existent hypothesis raises KeyError."""
        bs = BeliefState()
        with pytest.raises(KeyError):
            bs.update_confidence("h1", 0.8)

    def test_update_confidence_out_of_range_raises(self):
        """Updating confidence to invalid value raises ValueError."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        with pytest.raises(ValueError):
            bs.update_confidence("h1", 1.5)
        with pytest.raises(ValueError):
            bs.update_confidence("h1", -0.1)


class TestBeliefStateMostLikely:
    """Test most/least likely hypothesis."""

    def test_most_likely_hypothesis(self):
        """Most likely hypothesis is the one with highest confidence."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.3)
        bs.add_hypothesis("h2", confidence=0.7)
        result = bs.most_likely_hypothesis
        assert result is not None
        assert result[0] == "h2"
        assert result[1] == 0.7

    def test_least_likely_hypothesis(self):
        """Least likely hypothesis is the one with lowest confidence."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.3)
        bs.add_hypothesis("h2", confidence=0.7)
        result = bs.least_likely_hypothesis
        assert result is not None
        assert result[0] == "h1"
        assert result[1] == 0.3


class TestBeliefStateSerialisation:
    """Test serialisation and deserialisation."""

    def test_to_dict(self):
        """to_dict returns a valid dictionary."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.7)

        d = bs.to_dict()
        assert "hypotheses" in d
        assert "entropy" in d
        assert "num_hypotheses" in d
        assert d["num_hypotheses"] == 2

    def test_from_dict(self):
        """from_dict correctly reconstructs the BeliefState."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)
        bs.add_hypothesis("h2", confidence=0.7)

        d = bs.to_dict()
        bs2 = BeliefState.from_dict(d)

        assert bs2.num_hypotheses == 2
        assert bs2.hypotheses["h1"].confidence == 0.5
        assert bs2.hypotheses["h2"].confidence == 0.7

    def test_copy(self):
        """copy creates an independent BeliefState."""
        bs = BeliefState()
        bs.add_hypothesis("h1", confidence=0.5)

        bs_copy = bs.copy()
        bs_copy.update_confidence("h1", 0.9)

        # Original should be unchanged
        assert bs.hypotheses["h1"].confidence == 0.5
        assert bs_copy.hypotheses["h1"].confidence == 0.9


class TestHypothesisBelief:
    """Test HypothesisBelief dataclass."""

    def test_construction(self):
        """HypothesisBelief can be constructed with all fields."""
        b = HypothesisBelief(
            hypothesis_id="h1",
            confidence=0.5,
            evidence_for_count=0,
            evidence_against_count=0,
            prior=0.5,
        )
        assert b.hypothesis_id == "h1"
        assert b.confidence == 0.5

    def test_to_dict(self):
        """to_dict returns a valid dictionary."""
        b = HypothesisBelief(hypothesis_id="h1", confidence=0.5)
        d = b.to_dict()
        assert d["hypothesis_id"] == "h1"
        assert d["confidence"] == 0.5

    def test_from_dict(self):
        """from_dict correctly reconstructs the HypothesisBelief."""
        b = HypothesisBelief(hypothesis_id="h1", confidence=0.5)
        d = b.to_dict()
        b2 = HypothesisBelief.from_dict(d)
        assert b2.hypothesis_id == "h1"
        assert b2.confidence == 0.5
