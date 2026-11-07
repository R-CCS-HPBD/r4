"""Hypothesis-Driven Research Framework for Autonomous Science.

Provides explicit hypothesis lifecycle management for the ResearchOrchestrator.
Science requires hypothesis-level reasoning: formulate → test → evaluate → revise.

Key components:
- ``Hypothesis`` — a testable scientific statement with confidence and evidence
- ``Evidence`` — a piece of evidence that supports or contradicts a hypothesis
- ``HypothesisManager`` — create, evaluate, revise, and track hypotheses

Integration with A2L:
- Each hypothesis is recorded as an A2L artifact (type="hypothesis")
- Each piece of evidence links to its parent hypothesis via A2L derive
- Hypothesis revisions create child A2L artifacts from parent

Usage:
    from r4.agent.hypothesis import Hypothesis, Evidence, HypothesisManager

    mgr = HypothesisManager()
    h = mgr.create("Increasing temperature accelerates the reaction rate")

    e1 = Evidence(
        source="experiment_001", strength=0.8, summary="Rate doubled at +10K", supports=True
    )
    e2 = Evidence(
        source="literature", strength=0.6, summary="Arrhenius equation predicts this", supports=True
    )

    status = mgr.evaluate(h.id, [e1, e2])
    print(f"Hypothesis is now {status}")  # SUPPORTED
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Models ──────────────────────────────────────────────────────────────────


class HypothesisStatus(str, Enum):
    """Status of a scientific hypothesis."""

    PROPOSED = "proposed"  # Newly formulated, not yet tested
    TESTING = "testing"  # Experiment underway to test this
    SUPPORTED = "supported"  # Evidence supports the hypothesis
    REFUTED = "refuted"  # Evidence contradicts the hypothesis
    INCONCLUSIVE = "inconclusive"  # Evidence is mixed or insufficient
    RETIRED = "retired"  # Superseded by a revision, no longer active


class EvidenceStrength(float, Enum):
    """Standardized evidence strength scale."""

    WEAK = 0.2
    MODERATE = 0.5
    STRONG = 0.8
    DECISIVE = 0.95


@dataclass
class Evidence:
    """A piece of evidence for or against a hypothesis.

    Evidence can come from experiments, literature, data analysis,
    or any source that provides support or contradiction.

    Attributes:
        source: Origin of the evidence (experiment ID, RAG doc ref, etc.)
        strength: Strength of the evidence (0.0–1.0).
        summary: Human-readable description of what the evidence shows.
        supports: True if evidence supports the hypothesis, False if contradicts.
        metadata: Arbitrary metadata (e.g., experiment params, RAG score).
        created_at: When this evidence was collected.
    """

    source: str
    strength: float
    summary: str
    supports: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"Evidence strength must be 0.0–1.0, got {self.strength}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "strength": self.strength,
            "summary": self.summary,
            "supports": self.supports,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() + "Z",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        created = data.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created = _utcnow()
        return cls(
            source=data.get("source", ""),
            strength=float(data.get("strength", 0.5)),
            summary=data.get("summary", ""),
            supports=bool(data.get("supports", True)),
            metadata=data.get("metadata", {}),
            created_at=created if isinstance(created, datetime) else _utcnow(),
        )


@dataclass
class Hypothesis:
    """A testable scientific hypothesis with confidence tracking.

    A hypothesis evolves through a lifecycle: PROPOSED → TESTING →
    SUPPORTED/REFUTED/INCONCLUSIVE. It can be revised, creating a
    child hypothesis that references the parent.

    Attributes:
        id: Unique identifier (auto-generated UUID).
        statement: The hypothesis as a testable statement.
        confidence: Current confidence level (0.0–1.0).
        evidence_for: Evidence supporting the hypothesis.
        evidence_against: Evidence contradicting the hypothesis.
        status: Current lifecycle status.
        parent_hypothesis_id: ID of parent hypothesis if this is a revision.
        revision_reason: Why this hypothesis was created from parent.
        created_at: When the hypothesis was first formulated.
        evaluated_at: When the hypothesis was last evaluated.
        metadata: Arbitrary metadata (A2L artifact ID, etc.).
    """

    statement: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    confidence: float = 0.5
    evidence_for: list[Evidence] = field(default_factory=list)
    evidence_against: list[Evidence] = field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    parent_hypothesis_id: Optional[str] = None
    revision_reason: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    evaluated_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0–1.0, got {self.confidence}")

    @property
    def is_terminal(self) -> bool:
        """Check if the hypothesis is in a terminal (non-active) state."""
        return self.status in (
            HypothesisStatus.SUPPORTED,
            HypothesisStatus.REFUTED,
            HypothesisStatus.RETIRED,
        )

    @property
    def is_active(self) -> bool:
        """Check if the hypothesis is still active (could be tested further)."""
        return self.status in (
            HypothesisStatus.PROPOSED,
            HypothesisStatus.TESTING,
            HypothesisStatus.INCONCLUSIVE,
        )

    @property
    def evidence_score(self) -> float:
        """Net evidence score: sum(supporting strength) - sum(contradicting strength).

        Positive = more supporting evidence. Negative = more contradicting.
        """
        support = sum(e.strength for e in self.evidence_for)
        against = sum(e.strength for e in self.evidence_against)
        return support - against

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "confidence": self.confidence,
            "evidence_for": [e.to_dict() for e in self.evidence_for],
            "evidence_against": [e.to_dict() for e in self.evidence_against],
            "status": self.status.value,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "revision_reason": self.revision_reason,
            "created_at": self.created_at.isoformat() + "Z",
            "evaluated_at": self.evaluated_at.isoformat() + "Z" if self.evaluated_at else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        def _parse_dt(val: Any) -> Optional[datetime]:
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            return None

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            statement=data.get("statement", ""),
            confidence=float(data.get("confidence", 0.5)),
            evidence_for=[Evidence.from_dict(e) for e in data.get("evidence_for", [])],
            evidence_against=[Evidence.from_dict(e) for e in data.get("evidence_against", [])],
            status=HypothesisStatus(data.get("status", "proposed")),
            parent_hypothesis_id=data.get("parent_hypothesis_id"),
            revision_reason=data.get("revision_reason", ""),
            created_at=_parse_dt(data.get("created_at")) or _utcnow(),
            evaluated_at=_parse_dt(data.get("evaluated_at")),
            metadata=data.get("metadata", {}),
        )


# ── Hypothesis Manager ─────────────────────────────────────────────────────


# Thresholds for automatic status determination
_SUPPORT_THRESHOLD = 0.3  # Net evidence score above this → SUPPORTED
_REFUTE_THRESHOLD = -0.3  # Net evidence score below this → REFUTED
_MIN_EVIDENCE_COUNT = 1  # Minimum evidence items to make a determination


class HypothesisManager:
    """Manages hypothesis lifecycle within a research session.

    Provides create, evaluate, revise, and query operations.
    Hypotheses are stored in-memory; persistence is handled by
    the ResearchSession serialization + A2L checkpointing.

    Example:
        mgr = HypothesisManager()
        h = mgr.create("Temperature increases reaction rate")
        mgr.evaluate(h.id, [e1, e2])
        h2 = mgr.revise(h.id, "Temperature increases rate exponentially", "New data")
    """

    def __init__(self) -> None:
        self._hypotheses: dict[str, Hypothesis] = {}

    # ── CRUD ───────────────────────────────────────────────────────────

    def create(
        self,
        statement: str,
        parent_id: Optional[str] = None,
        revision_reason: str = "",
        initial_confidence: float = 0.5,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Hypothesis:
        """Create a new hypothesis.

        Args:
            statement: Testable hypothesis statement.
            parent_id: Optional parent hypothesis ID (for revisions).
            revision_reason: Why this hypothesis was created from parent.
            initial_confidence: Starting confidence (0.0–1.0).
            metadata: Optional metadata dict.

        Returns:
            The newly created Hypothesis.
        """
        hypothesis = Hypothesis(
            statement=statement,
            confidence=initial_confidence,
            parent_hypothesis_id=parent_id,
            revision_reason=revision_reason,
            metadata=metadata or {},
        )
        self._hypotheses[hypothesis.id] = hypothesis

        # If parent exists, retire it
        if parent_id and parent_id in self._hypotheses:
            parent = self._hypotheses[parent_id]
            if parent.is_active:
                parent.status = HypothesisStatus.RETIRED

        logger.info(
            "Hypothesis created: %s (confidence=%.2f, parent=%s)",
            hypothesis.id[:8],
            hypothesis.confidence,
            parent_id,
        )
        return hypothesis

    def get(self, hypothesis_id: str) -> Optional[Hypothesis]:
        """Get a hypothesis by ID."""
        return self._hypotheses.get(hypothesis_id)

    def get_active(self) -> list[Hypothesis]:
        """Get all non-terminal (active) hypotheses."""
        return [h for h in self._hypotheses.values() if h.is_active]

    def get_all(self) -> list[Hypothesis]:
        """Get all hypotheses."""
        return list(self._hypotheses.values())

    def get_lineage(self, hypothesis_id: str) -> list[Hypothesis]:
        """Trace the revision lineage from this hypothesis to its root.

        Returns list ordered from root ancestor to current hypothesis.
        """
        chain: list[Hypothesis] = []
        current = self._hypotheses.get(hypothesis_id)
        if current is None:
            return chain

        # Walk up to root
        visited: set[str] = set()
        nodes: list[Hypothesis] = []
        h = current
        while h is not None:
            if h.id in visited:
                break  # Cycle guard
            visited.add(h.id)
            nodes.append(h)
            h = self._hypotheses.get(h.parent_hypothesis_id) if h.parent_hypothesis_id else None

        # Reverse: root first
        nodes.reverse()
        return nodes

    # ── Evaluate ───────────────────────────────────────────────────────

    def evaluate(
        self,
        hypothesis_id: str,
        evidence_list: list[Evidence],
    ) -> HypothesisStatus:
        """Evaluate a hypothesis against new evidence.

        Updates the hypothesis confidence and status based on evidence.
        Returns the new status.

        Evaluation logic:
        1. Add evidence to the hypothesis (for/against)
        2. Compute net evidence score
        3. Update confidence using Bayesian-inspired update
        4. Determine status: SUPPORTED, REFUTED, or INCONCLUSIVE

        Args:
            hypothesis_id: ID of the hypothesis to evaluate.
            evidence_list: New evidence to incorporate.

        Returns:
            Updated HypothesisStatus.
        """
        hypothesis = self._hypotheses.get(hypothesis_id)
        if hypothesis is None:
            logger.warning("Cannot evaluate unknown hypothesis: %s", hypothesis_id)
            return HypothesisStatus.PROPOSED

        # Add evidence
        for e in evidence_list:
            if e.supports:
                hypothesis.evidence_for.append(e)
            else:
                hypothesis.evidence_against.append(e)

        # Update confidence using evidence score
        total_evidence_count = len(hypothesis.evidence_for) + len(hypothesis.evidence_against)
        net_score = hypothesis.evidence_score

        # Confidence update: shift towards 1.0 or 0.0 based on net evidence
        if total_evidence_count > 0:
            # Normalized shift proportional to evidence strength
            shift = net_score / max(total_evidence_count, 1) * 0.3
            hypothesis.confidence = max(0.0, min(1.0, hypothesis.confidence + shift))

        # Determine status
        old_status = hypothesis.status
        new_status = self._determine_status(hypothesis)
        hypothesis.status = new_status
        hypothesis.evaluated_at = _utcnow()

        if old_status != new_status:
            logger.info(
                "Hypothesis %s: %s → %s (confidence=%.2f, evidence_score=%.2f)",
                hypothesis_id[:8],
                old_status.value,
                new_status.value,
                hypothesis.confidence,
                net_score,
            )

        return new_status

    def _determine_status(self, hypothesis: Hypothesis) -> HypothesisStatus:
        """Determine hypothesis status from evidence.

        Rules:
        - No evidence → stay PROPOSED (or INCONCLUSIVE if previously TESTING)
        - Net score > threshold + min evidence → SUPPORTED
        - Net score < -threshold + min evidence → REFUTED
        - Otherwise → INCONCLUSIVE
        """
        total = len(hypothesis.evidence_for) + len(hypothesis.evidence_against)
        net = hypothesis.evidence_score

        if total < _MIN_EVIDENCE_COUNT:
            # No evidence yet
            if hypothesis.status == HypothesisStatus.TESTING:
                return HypothesisStatus.INCONCLUSIVE
            return hypothesis.status  # Keep current (likely PROPOSED)

        if net > _SUPPORT_THRESHOLD:
            return HypothesisStatus.SUPPORTED
        if net < _REFUTE_THRESHOLD:
            return HypothesisStatus.REFUTED
        return HypothesisStatus.INCONCLUSIVE

    # ── Revise ─────────────────────────────────────────────────────────

    def revise(
        self,
        hypothesis_id: str,
        new_statement: str,
        reason: str,
    ) -> Hypothesis:
        """Revise a hypothesis by creating a child with a new statement.

        The parent hypothesis is automatically retired.

        Args:
            hypothesis_id: ID of the hypothesis to revise.
            new_statement: The revised hypothesis statement.
            reason: Why the revision is needed.

        Returns:
            The new (child) Hypothesis.
        """
        parent = self._hypotheses.get(hypothesis_id)
        if parent is None:
            raise ValueError(f"Cannot revise unknown hypothesis: {hypothesis_id}")

        # Inherit some confidence from parent but lower it (revision = uncertainty)
        inherited_confidence = max(0.3, parent.confidence * 0.7)

        child = self.create(
            statement=new_statement,
            parent_id=hypothesis_id,
            revision_reason=reason,
            initial_confidence=inherited_confidence,
            metadata={"revised_from": hypothesis_id},
        )

        logger.info(
            "Hypothesis revised: %s → %s (reason: %s)",
            hypothesis_id[:8],
            child.id[:8],
            reason[:50],
        )

        return child

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize all hypotheses to a dict for session persistence."""
        return {
            "hypotheses": {hid: h.to_dict() for hid, h in self._hypotheses.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HypothesisManager":
        """Restore HypothesisManager from serialized dict."""
        mgr = cls()
        for hid, hdata in data.get("hypotheses", {}).items():
            mgr._hypotheses[hid] = Hypothesis.from_dict(hdata)
        return mgr

    # ── Query helpers ──────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable summary of all hypotheses."""
        if not self._hypotheses:
            return "No hypotheses formulated."

        lines: list[str] = []
        for h in self._hypotheses.values():
            indent = "  " if h.parent_hypothesis_id else ""
            lines.append(
                f"{indent}[{h.status.value}] {h.statement} "
                f"(confidence={h.confidence:.2f}, "
                f"for={len(h.evidence_for)}, against={len(h.evidence_against)})"
            )
        return "\n".join(lines)
