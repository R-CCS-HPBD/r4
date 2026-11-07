"""BeliefState — scientific reasoning engine state.

Tracks confidence and uncertainty across all hypotheses. This is the
scientific reasoning engine's state — the operational counterpart to
the scientific knowledge stored in A2L.

Key properties:
- ``entropy``: Shannon entropy of the belief distribution. Higher = more uncertain.
- ``most_likely_hypothesis``: (hypothesis_id, confidence) of the most likely hypothesis.

The BeliefState is updated by BeliefUpdater using Bayesian updating:
P(H|E) = P(E|H) * P(H) / P(E)

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.2
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HypothesisBelief:
    """Belief in a single hypothesis.

    Attributes:
        hypothesis_id: ID of the hypothesis this belief tracks.
        confidence: Current confidence level (0.0–1.0).
        evidence_for_count: Number of supporting evidence pieces.
        evidence_against_count: Number of contradicting evidence pieces.
        last_updated: When this belief was last updated.
        prior: Prior probability before any evidence (default 0.5).
    """

    hypothesis_id: str
    confidence: float = 0.5
    evidence_for_count: int = 0
    evidence_against_count: int = 0
    last_updated: datetime = field(default_factory=_utcnow)
    prior: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0–1.0, got {self.confidence}")
        if not 0.0 <= self.prior <= 1.0:
            raise ValueError(f"Prior must be 0.0–1.0, got {self.prior}")

    def to_dict(self) -> dict:
        """Serialise to dictionary."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "confidence": self.confidence,
            "evidence_for_count": self.evidence_for_count,
            "evidence_against_count": self.evidence_against_count,
            "last_updated": self.last_updated.isoformat(),
            "prior": self.prior,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HypothesisBelief":
        """Deserialise from dictionary."""
        last_updated = data.get("last_updated")
        if isinstance(last_updated, str):
            try:
                last_updated = datetime.fromisoformat(last_updated)
            except (ValueError, TypeError):
                last_updated = _utcnow()
        return cls(
            hypothesis_id=data["hypothesis_id"],
            confidence=float(data.get("confidence", 0.5)),
            evidence_for_count=int(data.get("evidence_for_count", 0)),
            evidence_against_count=int(data.get("evidence_against_count", 0)),
            last_updated=last_updated if isinstance(last_updated, datetime) else _utcnow(),
            prior=float(data.get("prior", 0.5)),
        )


@dataclass
class BeliefState:
    """Current beliefs about all hypotheses.

    This is the scientific reasoning engine's state. It tracks:
    - Confidence in each hypothesis (0.0–1.0)
    - Evidence supporting/contradicting each hypothesis
    - Overall uncertainty (Shannon entropy) of the belief distribution

    The BeliefState is a mutable, decision-optimized working memory
    that the ResearchDirector queries to make decisions.

    Usage:
        beliefs = BeliefState()
        beliefs.add_hypothesis("h1", confidence=0.5)
        beliefs.update_confidence("h1", 0.8)
        print(beliefs.entropy)  # Lower = more certain
        print(beliefs.most_likely_hypothesis)  # ("h1", 0.8)
    """

    hypotheses: dict[str, HypothesisBelief] = field(default_factory=dict)

    # ── Hypothesis management ──────────────────────────────────────────

    def add_hypothesis(
        self,
        hypothesis_id: str,
        confidence: float = 0.5,
        prior: Optional[float] = None,
    ) -> HypothesisBelief:
        """Add a new hypothesis to the belief state.

        Args:
            hypothesis_id: Unique identifier for the hypothesis.
            confidence: Initial confidence level (default 0.5).
            prior: Prior probability before any evidence (default = confidence).

        Returns:
            The created HypothesisBelief.

        Raises:
            ValueError: If hypothesis_id already exists.
        """
        if hypothesis_id in self.hypotheses:
            raise ValueError(f"Hypothesis {hypothesis_id} already exists in BeliefState")
        belief = HypothesisBelief(
            hypothesis_id=hypothesis_id,
            confidence=confidence,
            prior=prior if prior is not None else confidence,
        )
        self.hypotheses[hypothesis_id] = belief
        return belief

    def remove_hypothesis(self, hypothesis_id: str) -> None:
        """Remove a hypothesis from the belief state.

        Args:
            hypothesis_id: ID of the hypothesis to remove.

        Raises:
            KeyError: If hypothesis_id doesn't exist.
        """
        if hypothesis_id not in self.hypotheses:
            raise KeyError(f"Hypothesis {hypothesis_id} not found in BeliefState")
        del self.hypotheses[hypothesis_id]

    def get_belief(self, hypothesis_id: str) -> Optional[HypothesisBelief]:
        """Get the belief for a specific hypothesis.

        Args:
            hypothesis_id: ID of the hypothesis.

        Returns:
            HypothesisBelief if found, None otherwise.
        """
        return self.hypotheses.get(hypothesis_id)

    def update_confidence(self, hypothesis_id: str, confidence: float) -> None:
        """Update the confidence for a specific hypothesis.

        Args:
            hypothesis_id: ID of the hypothesis.
            confidence: New confidence level (0.0–1.0).

        Raises:
            KeyError: If hypothesis_id doesn't exist.
            ValueError: If confidence is not in [0.0, 1.0].
        """
        if hypothesis_id not in self.hypotheses:
            raise KeyError(f"Hypothesis {hypothesis_id} not found in BeliefState")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0–1.0, got {confidence}")
        belief = self.hypotheses[hypothesis_id]
        belief.confidence = confidence
        belief.last_updated = _utcnow()

    def increment_evidence(
        self,
        hypothesis_id: str,
        supports: bool,
    ) -> None:
        """Increment evidence count for a hypothesis.

        Args:
            hypothesis_id: ID of the hypothesis.
            supports: True if evidence supports, False if contradicts.

        Raises:
            KeyError: If hypothesis_id doesn't exist.
        """
        if hypothesis_id not in self.hypotheses:
            raise KeyError(f"Hypothesis {hypothesis_id} not found in BeliefState")
        belief = self.hypotheses[hypothesis_id]
        if supports:
            belief.evidence_for_count += 1
        else:
            belief.evidence_against_count += 1
        belief.last_updated = _utcnow()

    # ── Information-theoretic properties ────────────────────────────────

    @property
    def entropy(self) -> float:
        """Shannon entropy of the belief distribution.

        Higher entropy = more uncertain (more hypotheses with similar confidence).
        Lower entropy = more certain (one hypothesis clearly dominant).

        For n hypotheses with confidences p_1, ..., p_n (normalized to sum to 1):
            H = -sum(p_i * log2(p_i))

        Returns:
            Shannon entropy in bits (0.0 if no hypotheses).

        Note:
            If there's only one hypothesis, entropy is 0.0 (no uncertainty).
            If all hypotheses have equal confidence, entropy is log2(n) (maximum uncertainty).
        """
        if not self.hypotheses:
            return 0.0

        # Extract confidences
        confidences = [b.confidence for b in self.hypotheses.values()]

        # Normalize to probability distribution
        total = sum(confidences)
        if total == 0:
            # All confidences are 0 — uniform distribution
            n = len(confidences)
            return math.log2(n) if n > 0 else 0.0

        probabilities = [c / total for c in confidences]

        # Compute Shannon entropy: H = -sum(p * log2(p))
        entropy = 0.0
        for p in probabilities:
            if p > 0:
                entropy -= p * math.log2(p)

        return entropy

    @property
    def max_entropy(self) -> float:
        """Maximum possible entropy for the current number of hypotheses.

        This is log2(n) where n is the number of hypotheses.
        Useful for normalizing entropy to [0, 1].

        Returns:
            Maximum entropy in bits (0.0 if fewer than 2 hypotheses).
        """
        n = len(self.hypotheses)
        if n < 2:
            return 0.0
        return math.log2(n)

    @property
    def normalized_entropy(self) -> float:
        """Entropy normalized to [0, 1] range.

        0.0 = completely certain (one hypothesis has all confidence)
        1.0 = completely uncertain (all hypotheses have equal confidence)

        Returns:
            Normalized entropy (0.0 if no hypotheses or only one).
        """
        max_ent = self.max_entropy
        if max_ent == 0:
            return 0.0
        return self.entropy / max_ent

    @property
    def most_likely_hypothesis(self) -> Optional[tuple[str, float]]:
        """Return (hypothesis_id, confidence) of the most likely hypothesis.

        Returns:
            Tuple of (hypothesis_id, confidence), or None if no hypotheses.
        """
        if not self.hypotheses:
            return None

        best_id = max(self.hypotheses, key=lambda hid: self.hypotheses[hid].confidence)
        best_belief = self.hypotheses[best_id]
        return (best_id, best_belief.confidence)

    @property
    def least_likely_hypothesis(self) -> Optional[tuple[str, float]]:
        """Return (hypothesis_id, confidence) of the least likely hypothesis.

        Returns:
            Tuple of (hypothesis_id, confidence), or None if no hypotheses.
        """
        if not self.hypotheses:
            return None

        worst_id = min(self.hypotheses, key=lambda hid: self.hypotheses[hid].confidence)
        worst_belief = self.hypotheses[worst_id]
        return (worst_id, worst_belief.confidence)

    @property
    def num_hypotheses(self) -> int:
        """Number of hypotheses in the belief state."""
        return len(self.hypotheses)

    @property
    def total_evidence(self) -> int:
        """Total number of evidence pieces across all hypotheses."""
        return sum(
            b.evidence_for_count + b.evidence_against_count for b in self.hypotheses.values()
        )

    # ── Serialisation ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to dictionary."""
        return {
            "hypotheses": {hid: b.to_dict() for hid, b in self.hypotheses.items()},
            "entropy": self.entropy,
            "max_entropy": self.max_entropy,
            "normalized_entropy": self.normalized_entropy,
            "num_hypotheses": self.num_hypotheses,
            "total_evidence": self.total_evidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BeliefState":
        """Deserialise from dictionary."""
        beliefs = cls()
        for hid, bdata in data.get("hypotheses", {}).items():
            beliefs.hypotheses[hid] = HypothesisBelief.from_dict(bdata)
        return beliefs

    def copy(self) -> "BeliefState":
        """Create a deep copy of this belief state."""
        return BeliefState.from_dict(self.to_dict())

    def __repr__(self) -> str:
        return (
            f"BeliefState(n={self.num_hypotheses}, "
            f"H={self.entropy:.3f} bits, "
            f"H_norm={self.normalized_entropy:.3f})"
        )
