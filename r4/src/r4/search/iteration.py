"""
Iteration Controller (A-909)

Controls iterative search with gap detection and re-search capability.
Ensures comprehensive answers by detecting missing information and
triggering additional search rounds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class IterationReason(Enum):
    """Reason for triggering new iteration."""

    GAP_DETECTED = "gap_detected"  # Missing information
    LOW_CONFIDENCE = "low_confidence"  # Low confidence in answer
    CONFLICTING_INFO = "conflicting"  # Conflicting sources
    DEPTH_NEEDED = "depth_needed"  # Need more detail
    CLARIFICATION = "clarification"  # Ambiguous results
    USER_REQUEST = "user_request"  # User asked for more


@dataclass
class Gap:
    """Detected knowledge gap."""

    description: str
    importance: float  # 0.0 to 1.0
    suggested_query: Optional[str] = None
    related_to: Optional[str] = None  # Sub-question ID
    detected_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "description": self.description,
            "importance": self.importance,
            "suggested_query": self.suggested_query,
            "related_to": self.related_to,
        }


@dataclass
class IterationDecision:
    """Decision about whether to iterate."""

    should_iterate: bool
    reason: Optional[IterationReason] = None
    gaps: list[Gap] = field(default_factory=list)
    confidence: float = 0.0
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "should_iterate": self.should_iterate,
            "reason": self.reason.value if self.reason else None,
            "gaps": [g.to_dict() for g in self.gaps],
            "confidence": self.confidence,
            "explanation": self.explanation,
        }


@dataclass
class IterationConfig:
    """Configuration for iteration control."""

    max_iterations: int = 10
    min_confidence: float = 0.7
    gap_importance_threshold: float = 0.5
    conflict_tolerance: int = 2  # Max conflicting sources before re-search
    diminishing_returns_limit: int = 3  # Stop if no improvement for N iterations


@dataclass
class IterationState:
    """State of iteration process."""

    iteration_number: int = 0
    total_queries: int = 0
    processed_gaps: list[str] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    no_improvement_count: int = 0


class IterationController:
    """
    Controls iterative search process (A-909).

    Detects gaps in knowledge, assesses answer completeness,
    and decides whether additional search rounds are needed.
    """

    def __init__(self, config: Optional[IterationConfig] = None):
        """Initialize controller."""
        self.config = config or IterationConfig()
        self.state = IterationState()

    def reset(self) -> None:
        """Reset iteration state."""
        self.state = IterationState()

    async def assess_completeness(
        self,
        question: str,
        answer: str,
        sources: list[dict[str, Any]],
        sub_questions: Optional[list[str]] = None,
    ) -> IterationDecision:
        """
        Assess answer completeness and decide on iteration.

        Args:
            question: Original question
            answer: Current synthesized answer
            sources: Sources used in answer
            sub_questions: Decomposed sub-questions

        Returns:
            Decision about whether to iterate
        """
        self.state.iteration_number += 1

        # Check iteration limit
        if self.state.iteration_number >= self.config.max_iterations:
            return IterationDecision(
                should_iterate=False,
                confidence=self._estimate_confidence(answer, sources),
                explanation="Maximum iterations reached",
            )

        # Detect gaps
        gaps = await self._detect_gaps(question, answer, sub_questions)

        # Check for conflicts
        conflicts = self._detect_conflicts(sources)

        # Calculate confidence
        confidence = self._estimate_confidence(answer, sources)
        self.state.confidence_history.append(confidence)

        # Check diminishing returns
        if len(self.state.confidence_history) >= 2:
            improvement = confidence - self.state.confidence_history[-2]
            if improvement <= 0.01:  # Less than 1% improvement
                self.state.no_improvement_count += 1
            else:
                self.state.no_improvement_count = 0

        if self.state.no_improvement_count >= self.config.diminishing_returns_limit:
            return IterationDecision(
                should_iterate=False,
                confidence=confidence,
                explanation="Diminishing returns - no significant improvement",
            )

        # Decide based on gaps
        important_gaps = [g for g in gaps if g.importance >= self.config.gap_importance_threshold]

        if important_gaps:
            return IterationDecision(
                should_iterate=True,
                reason=IterationReason.GAP_DETECTED,
                gaps=important_gaps,
                confidence=confidence,
                explanation=f"Found {len(important_gaps)} important knowledge gaps",
            )

        # Decide based on confidence
        if confidence < self.config.min_confidence:
            return IterationDecision(
                should_iterate=True,
                reason=IterationReason.LOW_CONFIDENCE,
                gaps=gaps,
                confidence=confidence,
                explanation=(
                    f"Confidence {confidence:.2f} below threshold {self.config.min_confidence}"
                ),
            )

        # Decide based on conflicts
        if conflicts >= self.config.conflict_tolerance:
            return IterationDecision(
                should_iterate=True,
                reason=IterationReason.CONFLICTING_INFO,
                confidence=confidence,
                explanation=f"Found {conflicts} conflicting sources",
            )

        # All good, no iteration needed
        return IterationDecision(
            should_iterate=False,
            confidence=confidence,
            explanation="Answer is complete and confident",
        )

    async def _detect_gaps(
        self,
        question: str,
        answer: str,
        sub_questions: Optional[list[str]],
    ) -> list[Gap]:
        """Detect knowledge gaps in answer."""
        gaps = []

        # Simple heuristic gap detection
        # In production, this would use LLM-based analysis

        # Check for "I don't know" patterns
        uncertainty_patterns = [
            "i don't know",
            "i'm not sure",
            "unclear",
            "no information",
            "could not find",
            "not available",
            "unknown",
            "uncertain",
        ]

        answer_lower = answer.lower()
        for pattern in uncertainty_patterns:
            if pattern in answer_lower:
                gaps.append(
                    Gap(
                        description=f"Answer contains uncertainty: '{pattern}'",
                        importance=0.6,
                    )
                )

        # Check if all sub-questions are addressed
        if sub_questions:
            for sq in sub_questions:
                # Simple check: are key words from sub-question in answer?
                key_words = [w for w in sq.lower().split() if len(w) > 4]
                addressed = sum(1 for w in key_words if w in answer_lower)

                if addressed < len(key_words) * 0.5:
                    gaps.append(
                        Gap(
                            description=f"Sub-question may not be fully addressed: {sq}",
                            importance=0.7,
                            suggested_query=sq,
                            related_to=sq,
                        )
                    )

        # Check answer length relative to question complexity
        question_words = len(question.split())
        answer_words = len(answer.split())

        if question_words > 20 and answer_words < 100:
            gaps.append(
                Gap(
                    description="Answer seems too short for complex question",
                    importance=0.5,
                    suggested_query=f"More details about: {question}",
                )
            )

        # Remove already processed gaps
        gaps = [g for g in gaps if g.description not in self.state.processed_gaps]

        return gaps

    def _detect_conflicts(self, sources: list[dict[str, Any]]) -> int:
        """Detect conflicting information in sources."""
        # Simple conflict detection based on source type and recency
        # In production, this would use semantic comparison

        conflicts = 0

        # Check for sources with very different timestamps
        timestamps = []
        for source in sources:
            ts = source.get("timestamp") or source.get("date")
            if ts:
                timestamps.append(ts)

        if len(set(timestamps)) > 3:
            # Multiple different timestamps might indicate outdated info
            conflicts += 1

        # Check for sources from different domains saying opposite things
        # (placeholder - would need NLP)

        return conflicts

    def _estimate_confidence(
        self,
        answer: str,
        sources: list[dict[str, Any]],
    ) -> float:
        """Estimate confidence in the answer."""
        confidence = 0.5  # Base confidence

        # Boost for more sources
        if len(sources) >= 3:
            confidence += 0.1
        if len(sources) >= 5:
            confidence += 0.1

        # Boost for longer, more detailed answers
        answer_words = len(answer.split())
        if answer_words >= 100:
            confidence += 0.1
        if answer_words >= 300:
            confidence += 0.1

        # Reduce for uncertainty language
        uncertainty_count = sum(
            1
            for phrase in ["might", "maybe", "possibly", "could be", "unclear"]
            if phrase in answer.lower()
        )
        confidence -= uncertainty_count * 0.05

        # Reduce for very short answers
        if answer_words < 50:
            confidence -= 0.2

        return max(0.0, min(1.0, confidence))

    def generate_follow_up_queries(
        self,
        decision: IterationDecision,
    ) -> list[str]:
        """Generate follow-up queries based on gaps."""
        queries = []

        for gap in decision.gaps:
            if gap.suggested_query:
                queries.append(gap.suggested_query)
            else:
                # Generate query from gap description
                queries.append(f"More information about: {gap.description}")

        return queries

    def mark_gaps_processed(self, gaps: list[Gap]) -> None:
        """Mark gaps as processed to avoid re-detecting."""
        for gap in gaps:
            self.state.processed_gaps.append(gap.description)

    def get_state(self) -> dict[str, Any]:
        """Get current iteration state."""
        return {
            "iteration_number": self.state.iteration_number,
            "total_queries": self.state.total_queries,
            "processed_gaps": len(self.state.processed_gaps),
            "confidence_history": self.state.confidence_history,
            "no_improvement_count": self.state.no_improvement_count,
        }


class GapDetector:
    """
    Specialized gap detection for agentic search.

    Uses multiple strategies to identify missing information.
    """

    def __init__(self):
        """Initialize detector."""
        self.detection_strategies = [
            self._detect_question_coverage,
            self._detect_uncertainty_language,
            self._detect_shallow_coverage,
            self._detect_missing_comparisons,
            self._detect_missing_examples,
        ]

    async def detect(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]] = None,
    ) -> list[Gap]:
        """
        Detect all gaps using multiple strategies.

        Args:
            question: Original question
            answer: Current answer
            context: Additional context

        Returns:
            List of detected gaps
        """
        all_gaps = []

        for strategy in self.detection_strategies:
            gaps = await strategy(question, answer, context)
            all_gaps.extend(gaps)

        # Deduplicate and rank
        return self._rank_gaps(all_gaps)

    async def _detect_question_coverage(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]],
    ) -> list[Gap]:
        """Detect if question aspects are not covered."""
        gaps = []

        # Extract question keywords
        question_words = set(question.lower().split())
        stop_words = {"what", "how", "why", "when", "where", "is", "are", "the", "a", "an"}
        keywords = question_words - stop_words

        answer_lower = answer.lower()

        for keyword in keywords:
            if len(keyword) > 3 and keyword not in answer_lower:
                gaps.append(
                    Gap(
                        description=f"Keyword '{keyword}' from question not addressed",
                        importance=0.5,
                        suggested_query=f"Information about {keyword}",
                    )
                )

        return gaps

    async def _detect_uncertainty_language(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]],
    ) -> list[Gap]:
        """Detect uncertainty in answer."""
        gaps = []

        patterns = {
            "might be": 0.3,
            "could be": 0.3,
            "possibly": 0.4,
            "perhaps": 0.4,
            "i'm not sure": 0.6,
            "unclear": 0.6,
            "no definitive": 0.5,
            "more research needed": 0.7,
        }

        answer_lower = answer.lower()

        for pattern, importance in patterns.items():
            if pattern in answer_lower:
                gaps.append(
                    Gap(
                        description=f"Uncertainty detected: '{pattern}'",
                        importance=importance,
                    )
                )

        return gaps

    async def _detect_shallow_coverage(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]],
    ) -> list[Gap]:
        """Detect if answer is too shallow."""
        gaps = []

        # Check for lack of detail indicators
        if len(answer.split()) < 100 and len(question.split()) > 10:
            gaps.append(
                Gap(
                    description="Answer may lack sufficient detail",
                    importance=0.5,
                    suggested_query=f"Detailed explanation of: {question}",
                )
            )

        # Check for lack of specifics
        specific_indicators = ["specifically", "for example", "such as", "namely", "including"]
        has_specifics = any(ind in answer.lower() for ind in specific_indicators)

        if not has_specifics and len(answer.split()) > 50:
            gaps.append(
                Gap(
                    description="Answer lacks specific examples",
                    importance=0.4,
                )
            )

        return gaps

    async def _detect_missing_comparisons(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]],
    ) -> list[Gap]:
        """Detect if comparative questions lack comparisons."""
        gaps = []

        comparison_words = ["compare", "versus", "vs", "difference", "better", "worse"]
        is_comparison_question = any(w in question.lower() for w in comparison_words)

        if is_comparison_question:
            comparison_answered = any(
                w in answer.lower() for w in ["compared to", "unlike", "whereas", "while"]
            )
            if not comparison_answered:
                gaps.append(
                    Gap(
                        description=(
                            "Comparison question not fully answered with comparative analysis"
                        ),
                        importance=0.7,
                        suggested_query=f"Comparison: {question}",
                    )
                )

        return gaps

    async def _detect_missing_examples(
        self,
        question: str,
        answer: str,
        context: Optional[dict[str, Any]],
    ) -> list[Gap]:
        """Detect if practical examples are missing."""
        gaps = []

        example_request_words = ["example", "instance", "case", "demonstrate", "show"]
        wants_examples = any(w in question.lower() for w in example_request_words)

        example_provided = any(
            w in answer.lower() for w in ["for example", "for instance", "such as", "e.g."]
        )

        if wants_examples and not example_provided:
            gaps.append(
                Gap(
                    description="Question asks for examples but none provided",
                    importance=0.7,
                    suggested_query=f"Examples of: {question}",
                )
            )

        return gaps

    def _rank_gaps(self, gaps: list[Gap]) -> list[Gap]:
        """Rank and deduplicate gaps."""
        # Remove duplicates (by description)
        seen = set()
        unique_gaps = []
        for gap in gaps:
            if gap.description not in seen:
                seen.add(gap.description)
                unique_gaps.append(gap)

        # Sort by importance
        return sorted(unique_gaps, key=lambda g: g.importance, reverse=True)
