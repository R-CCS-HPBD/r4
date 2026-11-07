"""BeliefUpdater — Bayesian updating and Expected Information Gain.

Updates BeliefState based on new evidence using Bayesian updating:
    P(H|E) = P(E|H) * P(H) / P(E)

Computes Expected Information Gain (EIG) to determine which experiment
most reduces uncertainty:
    EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]

The BeliefUpdater is the core of the scientific reasoning engine.
It enables the ResearchDirector to ask "Which experiment most reduces
uncertainty?" rather than "Which experiment gets the highest reward?"

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §3.2
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .belief_state import BeliefState


@dataclass
class EvidenceResult:
    """Result of evaluating evidence against a hypothesis.

    Attributes:
        hypothesis_id: ID of the hypothesis being updated.
        supports: Whether the evidence supports (True) or contradicts (False).
        strength: Strength of the evidence (0.0–1.0).
        old_confidence: Confidence before update.
        new_confidence: Confidence after update.
        bayes_factor: The Bayes factor computed for this update.
    """

    hypothesis_id: str
    supports: bool
    strength: float
    old_confidence: float
    new_confidence: float
    bayes_factor: float


class BeliefUpdater:
    """Updates BeliefState using Bayesian updating.

    The updater applies Bayes' theorem to update the confidence in each
    hypothesis based on new evidence. The key insight is that experiments
    should maximize Expected Information Gain (EIG), not just expected reward.

    EIG measures how much an experiment will reduce uncertainty:
        EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]

    High EIG = experiment will significantly reduce uncertainty.
    Low EIG = experiment will provide little new information.

    Usage:
        updater = BeliefUpdater()
        result = updater.update(beliefs, hypothesis_id="h1",
                                supports=True, strength=0.8)
        eig = updater.expected_information_gain(beliefs, experiment)
    """

    def __init__(self, noise_floor: float = 0.01) -> None:
        """Initialize the belief updater.

        Args:
            noise_floor: Minimum confidence level to avoid log(0).
                Also used as the maximum confidence ceiling (1 - noise_floor).
        """
        self.noise_floor = noise_floor

    def _clamp(self, confidence: float) -> float:
        """Clamp confidence to [noise_floor, 1 - noise_floor] for numerical stability."""
        return max(self.noise_floor, min(1.0 - self.noise_floor, confidence))

    def compute_bayes_factor(
        self,
        supports: bool,
        strength: float,
    ) -> float:
        """Compute the Bayes factor for a piece of evidence.

        The Bayes factor K is defined as:
            K = P(E|H) / P(E|¬H)

        For supporting evidence: K > 1 (evidence favors H)
        For contradicting evidence: K < 1 (evidence favors ¬H)

        We use a simple model:
            P(E|H) = 0.5 + strength/2  (more likely if H is true)
            P(E|¬H) = 0.5 - strength/2  (less likely if H is false)

        For contradicting evidence, we swap the roles.

        Args:
            supports: Whether the evidence supports the hypothesis.
            strength: Strength of the evidence (0.0–1.0).

        Returns:
            Bayes factor K (> 1 for support, < 1 for contradiction).
        """
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"Strength must be 0.0–1.0, got {strength}")

        # Probability of seeing this evidence if hypothesis is true vs false
        if supports:
            p_e_given_h = 0.5 + strength * 0.5
            p_e_given_not_h = 0.5 - strength * 0.5
        else:
            p_e_given_h = 0.5 - strength * 0.5
            p_e_given_not_h = 0.5 + strength * 0.5

        # Avoid division by zero
        if p_e_given_not_h < self.noise_floor:
            p_e_given_not_h = self.noise_floor

        return p_e_given_h / p_e_given_not_h

    def bayesian_update(
        self,
        prior: float,
        bayes_factor: float,
    ) -> float:
        """Perform a Bayesian update on a prior probability.

        Given:
            P(H) = prior
            K = bayes_factor = P(E|H) / P(E|¬H)

        Compute:
            posterior odds = prior odds * K
            P(H|E) = posterior odds / (1 + posterior odds)

        Args:
            prior: Prior probability P(H) in [0, 1].
            bayes_factor: Bayes factor K (> 1 for support, < 1 for contradiction).

        Returns:
            Posterior probability P(H|E) in [0, 1].
        """
        prior = self._clamp(prior)

        # Convert prior probability to prior odds
        prior_odds = prior / (1.0 - prior)

        # Apply Bayes factor to get posterior odds
        posterior_odds = prior_odds * bayes_factor

        # Convert posterior odds back to posterior probability
        posterior = posterior_odds / (1.0 + posterior_odds)

        return self._clamp(posterior)

    def update(
        self,
        beliefs: BeliefState,
        hypothesis_id: str,
        supports: bool,
        strength: float,
    ) -> EvidenceResult:
        """Update beliefs based on new evidence using Bayesian updating.

        This is the primary method for updating the belief state.
        It applies Bayes' theorem to update the confidence in the specified
        hypothesis based on the new evidence.

        Args:
            beliefs: The current belief state (modified in place).
            hypothesis_id: ID of the hypothesis to update.
            supports: Whether the evidence supports the hypothesis.
            strength: Strength of the evidence (0.0–1.0).

        Returns:
            EvidenceResult with the update details.

        Raises:
            KeyError: If hypothesis_id doesn't exist in beliefs.
            ValueError: If strength is not in [0.0, 1.0].
        """
        if hypothesis_id not in beliefs.hypotheses:
            raise KeyError(f"Hypothesis {hypothesis_id} not found in BeliefState")

        belief = beliefs.hypotheses[hypothesis_id]
        old_confidence = belief.confidence

        # Compute Bayes factor for this evidence
        bayes_factor = self.compute_bayes_factor(supports, strength)

        # Apply Bayesian update
        new_confidence = self.bayesian_update(old_confidence, bayes_factor)

        # Update belief state
        belief.confidence = new_confidence
        belief.last_updated = datetime.now(timezone.utc)
        if supports:
            belief.evidence_for_count += 1
        else:
            belief.evidence_against_count += 1

        return EvidenceResult(
            hypothesis_id=hypothesis_id,
            supports=supports,
            strength=strength,
            old_confidence=old_confidence,
            new_confidence=new_confidence,
            bayes_factor=bayes_factor,
        )

    def expected_information_gain(
        self,
        beliefs: BeliefState,
        hypothesis_id: str,
        expected_strength: float = 0.5,
    ) -> float:
        """Calculate Expected Information Gain (EIG) for an experiment.

        EIG measures how much an experiment will reduce uncertainty:
            EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]

        We compute this by:
        1. Computing current entropy H(beliefs)
        2. Simulating both possible outcomes (supports/contradicts)
        3. Computing expected posterior entropy
        4. EIG = current_entropy - expected_posterior_entropy

        Args:
            beliefs: Current belief state.
            hypothesis_id: ID of the hypothesis the experiment tests.
            expected_strength: Expected strength of evidence (default 0.5).

        Returns:
            Expected Information Gain in bits. Higher = more informative.
        """
        if hypothesis_id not in beliefs.hypotheses:
            raise KeyError(f"Hypothesis {hypothesis_id} not found in BeliefState")

        # Measure uncertainty for the target hypothesis as a binary belief
        # P(H) / P(not H). Using the full normalized confidence vector can
        # overvalue already-refuted hypotheses simply because moving one low
        # confidence entry changes the global distribution. For experiment
        # selection we want the hypothesis-local question: "how uncertain are
        # we about this claim?"
        belief = beliefs.hypotheses[hypothesis_id]
        current_entropy = self._binary_entropy(belief.confidence)

        # Simulate both outcomes
        # Outcome 1: Evidence supports hypothesis
        beliefs_copy_1 = beliefs.copy()
        self.update(
            beliefs_copy_1,
            hypothesis_id,
            supports=True,
            strength=expected_strength,
        )
        entropy_after_support = self._binary_entropy(
            beliefs_copy_1.hypotheses[hypothesis_id].confidence
        )

        # Outcome 2: Evidence contradicts hypothesis
        beliefs_copy_2 = beliefs.copy()
        self.update(
            beliefs_copy_2,
            hypothesis_id,
            supports=False,
            strength=expected_strength,
        )
        entropy_after_contradict = self._binary_entropy(
            beliefs_copy_2.hypotheses[hypothesis_id].confidence
        )

        # Compute probability of each outcome
        # For candidate ranking we damp confidence toward 0.5. Using the raw
        # confidence makes already-high confidence hypotheses look too
        # attractive; using fully symmetric 0.5/0.5 over-explores. This blend
        # preserves convergence while still preferring uncertain hypotheses.
        p_support = self._clamp((belief.confidence + 0.5) / 2.0)
        p_contradict = 1.0 - p_support

        # Expected posterior entropy
        expected_posterior_entropy = (
            p_support * entropy_after_support + p_contradict * entropy_after_contradict
        )

        # EIG = current entropy - expected posterior entropy
        eig = current_entropy - expected_posterior_entropy

        return max(0.0, eig)  # EIG should be non-negative

    def _binary_entropy(self, confidence: float) -> float:
        """Compute binary Shannon entropy for a single hypothesis.

        This peaks at confidence=0.5 and trends to 0 near 0 or 1, which is the
        behavior we want when choosing the next experiment for one hypothesis.
        """
        p = self._clamp(confidence)
        q = 1.0 - p
        return -(p * math.log2(p) + q * math.log2(q))

    def update_all(
        self,
        beliefs: BeliefState,
        evidence_list: list[tuple[str, bool, float]],
    ) -> list[EvidenceResult]:
        """Update beliefs for multiple evidence pieces.

        Args:
            beliefs: The current belief state (modified in place).
            evidence_list: List of (hypothesis_id, supports, strength) tuples.

        Returns:
            List of EvidenceResult for each update.
        """
        results = []
        for hyp_id, supports, strength in evidence_list:
            if hyp_id in beliefs.hypotheses:
                result = self.update(beliefs, hyp_id, supports, strength)
                results.append(result)
        return results

    def information_gain_for_experiment(
        self,
        beliefs: BeliefState,
        experiment_hypotheses: list[str],
        expected_strength: float = 0.5,
    ) -> float:
        """Compute total EIG for an experiment testing multiple hypotheses.

        For an experiment that could provide evidence about multiple
        hypotheses, we compute the total expected information gain by
        summing the EIG for each hypothesis.

        Args:
            beliefs: Current belief state.
            experiment_hypotheses: List of hypothesis IDs the experiment tests.
            expected_strength: Expected strength of evidence (default 0.5).

        Returns:
            Total Expected Information Gain in bits.
        """
        total_eig = 0.0
        for hyp_id in experiment_hypotheses:
            if hyp_id in beliefs.hypotheses:
                eig = self.expected_information_gain(beliefs, hyp_id, expected_strength)
                total_eig += eig
        return total_eig
