"""Deep-research loop for r4.

Implements a bounded multi-step reasoning loop that:
1. Decomposes a question into sub-questions
2. Searches UMRS RAG + federated sources for each sub-question
3. Synthesizes findings with citations
4. Stores the final artifact in UMRS

The loop is depth-budgeted: each iteration may spawn follow-up
queries, but total depth is capped.

This is r4-owned. UMRS is the data/artifact store. The loop logic
lives here because it is a client-side agentic workflow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResearchSource:
    """A citation from the research process."""

    title: str = ""
    url: Optional[str] = None
    snippet: str = ""
    relevance: float = 0.0


@dataclass
class ResearchStep:
    """One step in the research loop."""

    query: str
    depth: int = 0
    sources_found: List[ResearchSource] = field(default_factory=list)
    answer: str = ""
    follow_ups: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass
class ResearchResult:
    """Final output from the deep-research loop."""

    original_query: str
    synthesis: str = ""
    steps: List[ResearchStep] = field(default_factory=list)
    total_sources: int = 0
    total_elapsed: float = 0.0

    @property
    def citations(self) -> List[ResearchSource]:
        """All unique sources across all steps."""
        seen: set[str] = set()
        out: List[ResearchSource] = []
        for step in self.steps:
            for src in step.sources_found:
                key = src.title or src.url or src.snippet[:50]
                if key not in seen:
                    seen.add(key)
                    out.append(src)
        return out


# Callback types
SearchFn = Callable[[str], List[ResearchSource]]
SynthesizeFn = Callable[[str, List[ResearchSource]], str]
DecomposeFn = Callable[[str, str], List[str]]


class DeepResearchLoop:
    """Bounded multi-step research loop.

    The caller supplies three callbacks:
    - ``search_fn``: query → list of sources (calls UMRS RAG/federated)
    - ``synthesize_fn``: (query, sources) → answer text (calls LLM)
    - ``decompose_fn``: (query, partial_answer) → follow-up queries (calls LLM)
    """

    def __init__(
        self,
        search_fn: SearchFn,
        synthesize_fn: SynthesizeFn,
        decompose_fn: DecomposeFn,
        max_depth: int = 3,
        max_sources_per_step: int = 10,
        max_total_steps: int = 15,
    ) -> None:
        self._search = search_fn
        self._synthesize = synthesize_fn
        self._decompose = decompose_fn
        self.max_depth = max_depth
        self.max_sources_per_step = max_sources_per_step
        self.max_total_steps = max_total_steps

    def run(self, query: str) -> ResearchResult:
        """Run the research loop."""
        result = ResearchResult(original_query=query)
        start = time.monotonic()

        queue: List[tuple[str, int]] = [(query, 0)]
        step_count = 0

        while queue and step_count < self.max_total_steps:
            current_query, depth = queue.pop(0)
            if depth > self.max_depth:
                continue

            step_start = time.monotonic()

            # Search
            sources = self._search(current_query)
            sources = sources[: self.max_sources_per_step]

            # Synthesize
            answer = self._synthesize(current_query, sources)

            # Decompose into follow-ups (only if not at max depth)
            follow_ups: List[str] = []
            if depth < self.max_depth and answer:
                follow_ups = self._decompose(current_query, answer)
                for fq in follow_ups:
                    queue.append((fq, depth + 1))

            step = ResearchStep(
                query=current_query,
                depth=depth,
                sources_found=sources,
                answer=answer,
                follow_ups=follow_ups,
                elapsed_seconds=time.monotonic() - step_start,
            )
            result.steps.append(step)
            step_count += 1

        # Final synthesis across all steps
        all_sources = result.citations
        result.total_sources = len(all_sources)
        result.total_elapsed = time.monotonic() - start

        if result.steps:
            combined_context = "\n\n".join(
                f"Q: {s.query}\nA: {s.answer}" for s in result.steps if s.answer
            )
            result.synthesis = self._synthesize(
                f"Based on the following research, answer: {query}\n\n{combined_context}",
                all_sources,
            )

        return result
