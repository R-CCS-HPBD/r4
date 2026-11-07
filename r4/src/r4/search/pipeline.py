"""
Agentic Search Pipeline (A-906)

Full pipeline for agentic search.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

if TYPE_CHECKING:
    from r4.client import Client

from .core import (
    AgenticSearch,
    SearchConfig,
    SearchDepth,
    SearchResult,
    SearchScope,
)
from .executor import ToolExecutor
from .planner import SearchPlanner
from .synthesizer import IncrementalSynthesizer

logger = logging.getLogger(__name__)


@dataclass
class PipelineEvent:
    """Event during pipeline execution."""

    event_type: str
    message: str
    data: Optional[dict[str, Any]] = None


class AgenticSearchPipeline:
    """
    Full Agentic Search Pipeline (A-906).

    Provides a unified interface for agentic search with:
    - Multi-hop query planning
    - Tool execution
    - Result synthesis
    - Citation tracking
    - Streaming support
    """

    def __init__(
        self,
        client: "Client",
        project_id: Optional[str] = None,
        **config_kwargs,
    ):
        """
        Initialize the pipeline.

        Args:
            client: R4 client for LLM and RAG access
            project_id: Default project ID for RAG searches
            **config_kwargs: Additional SearchConfig parameters
        """
        self.client = client
        self.config = SearchConfig(project_id=project_id, **config_kwargs)
        self._on_event: Optional[Callable[[PipelineEvent], None]] = None

    def on_event(self, callback: Callable[[PipelineEvent], None]) -> None:
        """Set event callback for pipeline progress."""
        self._on_event = callback

    def _emit(self, event_type: str, message: str, data: Optional[dict] = None) -> None:
        """Emit a pipeline event."""
        if self._on_event:
            self._on_event(PipelineEvent(event_type, message, data))

    async def search(
        self,
        query: str,
        depth: Optional[SearchDepth] = None,
        scope: Optional[SearchScope] = None,
    ) -> SearchResult:
        """
        Execute a search query.

        Args:
            query: Search query
            depth: Override search depth
            scope: Override search scope

        Returns:
            SearchResult with answer and sources
        """
        import copy

        # Apply overrides — use copy to preserve enum types (to_dict
        # serialises enums to strings which breaks re-construction).
        config = copy.copy(self.config)
        if depth:
            config.depth = depth
        if scope:
            config.scope = scope

        self._emit("start", f"Starting search: {query[:50]}...")

        search = AgenticSearch(self.client, config)
        result = await search.search(query)

        # Surface search warnings as pipeline events
        for warning in result.warnings:
            self._emit("warn", warning)

        self._emit("complete", f"Search complete ({result.search_steps} steps)")

        return result

    async def search_stream(
        self,
        query: str,
        depth: Optional[SearchDepth] = None,
    ) -> AsyncIterator[str]:
        """
        Stream search results incrementally.

        Args:
            query: Search query
            depth: Search depth

        Yields:
            Incremental answer chunks
        """
        config = SearchConfig(**self.config.to_dict())
        if depth:
            config.depth = depth

        # Use incremental synthesizer
        inc_synth = IncrementalSynthesizer(self.client, config)

        # Create planner
        planner = SearchPlanner(self.client, config)
        plan = await planner.create_plan(query)

        yield "Searching...\n"

        # Execute steps and yield partial results
        executor = ToolExecutor(self.client, config)
        for i, step in enumerate(plan):
            sources = await executor.execute_step(step)
            for source in sources:
                inc_synth.add_source(source)

            # Yield progress update
            yield f"\n[Step {i + 1}/{len(plan)}: {step.step_type.value}]\n"

            # Yield partial answer after each step
            partial = await inc_synth.get_partial_answer()
            yield partial + "\n"

        # Final synthesis
        yield "\n---\n**Final Answer:**\n"
        answer, citations, confidence = await inc_synth.get_final_answer(query)
        yield answer

    async def answer(self, question: str) -> str:
        """
        Get a quick answer to a question.

        Args:
            question: Question to answer

        Returns:
            Answer string with citations
        """
        result = await self.search(question, depth=SearchDepth.SHALLOW)
        return result.format_with_citations()

    async def research(self, topic: str) -> SearchResult:
        """
        Deep research on a topic.

        Args:
            topic: Topic to research

        Returns:
            Full SearchResult with extensive sources
        """
        return await self.search(topic, depth=SearchDepth.DEEP)

    async def compare(self, items: list[str]) -> SearchResult:
        """
        Compare multiple items or concepts.

        Args:
            items: List of items to compare

        Returns:
            SearchResult with comparison
        """
        query = f"Compare and contrast: {', '.join(items)}"
        return await self.search(query, depth=SearchDepth.MODERATE)

    async def explain(self, concept: str, level: str = "general") -> str:
        """
        Explain a concept.

        Args:
            concept: Concept to explain
            level: Explanation level (general, technical, expert)

        Returns:
            Explanation string
        """
        query = f"Explain {concept} at a {level} level"
        result = await self.search(query, depth=SearchDepth.SHALLOW)
        return result.answer

    async def summarize(self, topic: str, max_words: int = 200) -> str:
        """
        Summarize a topic.

        Args:
            topic: Topic to summarize
            max_words: Maximum words in summary

        Returns:
            Summary string
        """
        result = await self.search(
            f"Summarize {topic} in {max_words} words or less",
            depth=SearchDepth.SHALLOW,
        )
        return result.answer


class BatchSearchPipeline:
    """
    Batch search pipeline for multiple queries.
    """

    def __init__(self, pipeline: AgenticSearchPipeline, concurrency: int = 3):
        """
        Initialize batch pipeline.

        Args:
            pipeline: Base pipeline
            concurrency: Max concurrent searches
        """
        self.pipeline = pipeline
        self.concurrency = concurrency

    async def search_batch(
        self,
        queries: list[str],
        depth: SearchDepth = SearchDepth.MODERATE,
    ) -> list[SearchResult]:
        """
        Execute multiple searches in parallel.

        Args:
            queries: List of queries
            depth: Search depth for all queries

        Returns:
            List of SearchResults
        """
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _search_with_limit(query: str) -> SearchResult:
            async with semaphore:
                return await self.pipeline.search(query, depth=depth)

        tasks = [_search_with_limit(q) for q in queries]
        return await asyncio.gather(*tasks)


class ConversationalSearchPipeline:
    """
    Conversational search pipeline with memory.
    """

    def __init__(self, pipeline: AgenticSearchPipeline, max_history: int = 10):
        """
        Initialize conversational pipeline.

        Args:
            pipeline: Base pipeline
            max_history: Maximum conversation history
        """
        self.pipeline = pipeline
        self.max_history = max_history
        self.history: list[tuple[str, str]] = []

    async def ask(self, question: str) -> str:
        """
        Ask a question with conversation context.

        Args:
            question: Question to ask

        Returns:
            Answer string
        """
        # Build context from history
        context = self._build_context()

        # Combine context with current question
        if context:
            full_query = f"Given this conversation:\n{context}\n\nNow answer: {question}"
        else:
            full_query = question

        result = await self.pipeline.search(full_query)

        # Add to history
        self.history.append((question, result.answer))
        if len(self.history) > self.max_history:
            self.history.pop(0)

        return result.format_with_citations()

    def _build_context(self) -> str:
        """Build context from conversation history."""
        if not self.history:
            return ""

        lines = []
        for q, a in self.history[-3:]:  # Use last 3 exchanges
            lines.append(f"User: {q}")
            lines.append(f"Assistant: {a[:200]}...")

        return "\n".join(lines)

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.history.clear()

    def get_history(self) -> list[tuple[str, str]]:
        """Get conversation history."""
        return self.history.copy()
