"""
Agentic Search Core (A-901)

Core search functionality and configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Optional

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)


class SearchScope(Enum):
    """Scope of search."""

    RAG_ONLY = "rag_only"  # Only RAG documents
    WEB_ONLY = "web_only"  # Only web search
    CODE_ONLY = "code_only"  # Only code search
    LAB_ONLY = "lab_only"  # Catalog + raw + RAG; no web
    ALL = "all"  # Catalog + raw + RAG + web + code


class SearchDepth(Enum):
    """Depth of search."""

    SHALLOW = "shallow"  # Quick, single-hop
    MODERATE = "moderate"  # Multi-hop with refinement
    DEEP = "deep"  # Exhaustive multi-hop


class SourceType(Enum):
    """Type of source in search results."""

    RAG_DOCUMENT = "rag_document"
    WEB_PAGE = "web_page"
    CODE_FILE = "code_file"
    API_DOC = "api_doc"
    PAPER = "paper"
    CATALOG_ENTRY = "catalog_entry"
    RAW_FILE = "raw_file"


@dataclass(frozen=True)
class CapabilityUnavailable:
    """Typed signal that a requested search capability is not registered."""

    capability: Literal["web", "code"]
    message: str


@dataclass
class Source:
    """A source reference in search results."""

    type: SourceType
    title: str
    url: Optional[str] = None
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    retrieved_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "title": self.title,
            "url": self.url,
            "content": self.content[:500] + ("..." if len(self.content) > 500 else ""),
            "metadata": self.metadata,
            "relevance_score": self.relevance_score,
            "retrieved_at": self.retrieved_at.isoformat(),
        }

    def to_detail_dict(self, *, content_limit: int = 2000) -> dict[str, Any]:
        """Rich serialization for companion tools and downstream agents."""
        meta = self.metadata or {}
        content = self.content or ""
        if len(content) > content_limit:
            content = content[:content_limit] + "..."
        return {
            "type": self.type.value,
            "title": self.title,
            "url": self.url,
            "content": content,
            "relevance_score": self.relevance_score,
            "s3_key": meta.get("s3_key"),
            "state": meta.get("state"),
            "detected_modality": meta.get("detected_modality"),
            "tags": meta.get("tags", []),
            "deterministic_summary": meta.get("deterministic_summary"),
            "perception_summary": meta.get("perception_summary"),
            "perception_model": meta.get("perception_model"),
            "metadata": meta,
        }


def _unique_sources(sources: list[Source]) -> list[Source]:
    """First occurrence of each source, in order. Passages of one document
    share a title; their node id tells them apart."""
    seen: set[Any] = set()
    unique: list[Source] = []
    for source in sources:
        key = source.url or (source.metadata or {}).get("source_id") or source.title
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


#: The fewest non-RAG sources (web, papers, catalog, raw files, code) a search
#: keeps when the server's passages alone fill ``max_sources``.
OTHER_SOURCES_MIN = 2


def other_sources_room(max_sources: int, passages: int) -> int:
    """How many non-RAG sources a search keeps beside ``passages`` server
    passages: the room left of ``max_sources``, but never less than a third
    of it (at least :data:`OTHER_SOURCES_MIN`, at most ``max_sources``).

    The passages are never cut (CONSTITUTION §1.3), so ``max_sources`` bounds
    the rest: with the defaults (10 sources, ``rag_top_k`` 5) two RAG steps
    left no room at all, and every web, paper, catalog and code hit the plan
    asked for was dropped (fix-regressions-10)."""
    limit = max(int(max_sources), 0)
    reserve = min(limit, max(OTHER_SOURCES_MIN, limit // 3))
    return max(limit - max(int(passages), 0), reserve)


def sources_from_retrieve(result: Any) -> list[Source]:
    """RAG sources from a ``RetrieveResult`` (ADR-093 D-10), in the server's
    rank order and titled by the artifact's ``source_name``.

    An ``insufficient`` verdict yields none: UMRS decided no passage answers
    the query, and R4 does not second-guess that (CONSTITUTION §1.3).
    """
    if result.sufficiency.status == "insufficient":
        return []
    return [_rag_source(passage, result.sufficiency.status) for passage in result.passages]


def _rag_source(passage: Any, sufficiency: str) -> Source:
    from r4.rag import passage_score

    return Source(
        type=SourceType.RAG_DOCUMENT,
        title=passage.source_name or passage.source_id,
        content=passage.text,
        metadata={
            "source_id": passage.source_id,
            "artifact_id": passage.artifact_id,
            "section": passage.section,
            "rank": passage.rank,
            "text_source": passage.text_source,
            "lanes": list(passage.lanes),
            "scores": dict(passage.scores),
            "sufficiency": sufficiency,
        },
        relevance_score=passage_score(passage),
    )


@dataclass
class SearchResult:
    """Result of an agentic search."""

    query: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    confidence: float = 0.0
    search_steps: int = 0
    tokens_used: int = 0
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "answer": self.answer,
            "sources": [s.to_dict() for s in self.sources],
            "confidence": self.confidence,
            "search_steps": self.search_steps,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
            "citations": self.citations,
            "warnings": self.warnings,
        }

    def format_with_citations(self) -> str:
        """Format answer with inline citations."""
        result = self.answer

        if self.sources:
            result += "\n\n---\n**Sources:**\n"
            for i, source in enumerate(self.sources, 1):
                if source.url:
                    result += f"[{i}] [{source.title}]({source.url})\n"
                else:
                    result += f"[{i}] {source.title}\n"

        return result


@dataclass
class SearchConfig:
    """Configuration for agentic search."""

    scope: SearchScope = SearchScope.ALL
    depth: SearchDepth = SearchDepth.MODERATE
    max_steps: int = 5
    max_sources: int = 10
    min_relevance: float = 0.5
    include_citations: bool = True
    project_id: Optional[str] = None  # For RAG project scoping
    # LLM model for planner/synthesizer legs. None = client default — callers
    # thread the SESSION's effective model (2026-09-29 rivault/kimi-k3 class:
    # the gateway default deployment may be dead while the chat's is fine).
    model: Optional[str] = None
    language: str = "en"
    temperature: float = 0.3
    max_tokens: int = 2000

    # Source-specific settings
    rag_top_k: int = 5
    web_max_results: int = 5
    code_max_files: int = 10

    # Tool settings
    enable_web: bool = True
    enable_rag: bool = True
    enable_code: bool = True
    enable_papers: bool = False
    enable_catalog: bool = False
    enable_raw_read: bool = False

    def apply_scope(self) -> None:
        """Adjust tool flags from ``scope`` (e.g. LAB_ONLY)."""
        if self.scope == SearchScope.LAB_ONLY:
            self.enable_catalog = True
            self.enable_raw_read = True
            self.enable_rag = True
            self.enable_web = False
            self.enable_code = False
            self.enable_papers = False
        elif self.scope == SearchScope.ALL:
            # Lab uploads are part of the default agent surface when a project is set.
            self.enable_catalog = True
            self.enable_raw_read = True
        elif self.scope == SearchScope.RAG_ONLY:
            self.enable_web = False
            self.enable_code = False
            self.enable_catalog = False
            self.enable_raw_read = False
        elif self.scope == SearchScope.WEB_ONLY:
            self.enable_rag = False
            self.enable_catalog = False
            self.enable_raw_read = False
            self.enable_code = False
        elif self.scope == SearchScope.CODE_ONLY:
            self.enable_rag = False
            self.enable_web = False
            self.enable_catalog = False
            self.enable_raw_read = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "scope": self.scope.value,
            "depth": self.depth.value,
            "max_steps": self.max_steps,
            "max_sources": self.max_sources,
            "min_relevance": self.min_relevance,
            "include_citations": self.include_citations,
            "project_id": self.project_id,
            "language": self.language,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "rag_top_k": self.rag_top_k,
            "web_max_results": self.web_max_results,
            "code_max_files": self.code_max_files,
            "enable_web": self.enable_web,
            "enable_rag": self.enable_rag,
            "enable_code": self.enable_code,
            "enable_papers": self.enable_papers,
            "enable_catalog": self.enable_catalog,
            "enable_raw_read": self.enable_raw_read,
        }


class AgenticSearch:
    """
    Agentic Search Engine (A-901).

    Multi-hop search that decomposes queries, plans search strategy,
    executes searches with tools, and synthesizes results.
    """

    SEARCH_SYSTEM_PROMPT = """You are an AI research assistant that helps find and \
synthesize information.

Your task is to:
1. Understand the user's query and information needs
2. Search across available sources (documents, web, code)
3. Synthesize findings into a clear, accurate answer
4. Cite all sources used

Always be accurate and cite sources. If you cannot find reliable information, say so.
"""

    def __init__(
        self,
        client: "Client",
        config: Optional[SearchConfig] = None,
    ):
        """
        Initialize agentic search.

        Args:
            client: R4 client for LLM and RAG access
            config: Search configuration
        """
        self.client = client
        self.config = config or SearchConfig()
        self.config.apply_scope()
        self._step_count = 0
        self._sources: list[Source] = []
        self._tokens_used = 0
        self._warnings: list[str] = []

    async def search(self, query: str) -> SearchResult:
        """
        Execute an agentic search.

        Args:
            query: User's query

        Returns:
            SearchResult with answer and sources
        """
        import time

        start_time = time.time()
        self._step_count = 0
        self._sources = []
        self._tokens_used = 0
        self._warnings = []

        logger.info(f"Starting agentic search: {query[:50]}...")

        # Import pipeline components
        from .executor import ToolExecutor
        from .planner import SearchPlanner
        from .synthesizer import ResultSynthesizer

        # Step 1: Plan search strategy
        planner = SearchPlanner(self.client, self.config)
        plan = await planner.create_plan(query, warnings=self._warnings)
        logger.debug(f"Search plan: {len(plan)} steps")

        # Step 2: Execute search steps
        from .core import SourceType
        from .planner import StepType

        executor = ToolExecutor(self.client, self.config, warnings=self._warnings)
        catalog_sources: list = []
        raw_s3_keys: set[str] = set()

        for step in plan:
            if self._step_count >= self.config.max_steps:
                logger.warning("Max search steps reached")
                break

            if step.step_type == StepType.LAB_FILE_READ and step.parameters.get("auto"):
                continue

            step_sources = await executor.execute_step(step)
            for source in step_sources:
                if source.type == SourceType.CATALOG_ENTRY:
                    catalog_sources.append(source)
                elif source.type == SourceType.RAW_FILE:
                    key = (source.metadata or {}).get("s3_key")
                    if key:
                        raw_s3_keys.add(key)
            self._sources.extend(step_sources)
            self._step_count += 1

        if self.config.enable_raw_read and catalog_sources:
            auto_raw = await executor.execute_raw_reads_for_catalog_hits(
                catalog_sources,
                query=query,
                already_read=raw_s3_keys,
            )
            if auto_raw:
                self._sources.extend(auto_raw)
                self._step_count += 1

        if not self._sources and self.config.enable_web:
            self._warnings.append(
                "No search sources found. Web/code search requires "
                "registered MCP tools on the gateway."
            )

        # Step 3: Filter and rank sources
        self._sources = self._filter_sources(self._sources)

        # Step 4: Synthesize answer
        synthesizer = ResultSynthesizer(self.client, self.config)
        answer, citations, confidence, synthesis_warnings = await synthesizer.synthesize(
            query, self._sources
        )
        self._warnings.extend(synthesis_warnings)

        latency_ms = int((time.time() - start_time) * 1000)

        return SearchResult(
            query=query,
            answer=answer,
            sources=self._sources,
            confidence=confidence,
            search_steps=self._step_count,
            tokens_used=self._tokens_used,
            latency_ms=latency_ms,
            citations=citations,
            warnings=self._warnings,
        )

    def _filter_sources(self, sources: list[Source]) -> list[Source]:
        """De-duplicate, then: RAG passages as UMRS returned them, then the
        other sources ranked by relevance in the room left of ``max_sources``,
        never less than :func:`other_sources_room`'s reserve.

        UMRS ranked, cut and judged the RAG passages (ADR-093 D-3, CONSTITUTION
        §1.3). Their ``relevance_score`` is a display value on another scale
        (rerank p, else dense cosine, while a fused cut ranks by RRF), so R4
        never re-sorts, re-cuts or drops them for a higher-scored catalog,
        raw-file or web hit; they keep the server's order, calls in plan order.
        Nor do they starve the lanes the plan also asked for: a plan whose RAG
        steps alone fill ``max_sources`` still keeps the best web, paper,
        catalog and code hits (fix-regressions-10).
        """
        unique_sources = _unique_sources(sources)
        passages = [s for s in unique_sources if s.type == SourceType.RAG_DOCUMENT]
        others = [
            s
            for s in unique_sources
            if s.type != SourceType.RAG_DOCUMENT and s.relevance_score >= self.config.min_relevance
        ]
        others.sort(key=lambda s: s.relevance_score, reverse=True)
        return passages + others[: other_sources_room(self.config.max_sources, len(passages))]

    async def search_rag(self, query: str, project_id: Optional[str] = None) -> list[Source]:
        """
        Search RAG documents only.

        Args:
            query: Search query
            project_id: Project ID (optional, uses config if not provided)

        Returns:
            List of sources from RAG
        """
        project = project_id or self.config.project_id
        if not project:
            logger.warning("No project_id for RAG search")
            return []

        from r4.model_scale import configured_reader_fields

        # The synthesizer reads the passages with config.model (else the
        # client's): name it and its D-14 profile (r4-scale-router-10).
        reader = configured_reader_fields(self.config.model, self.client)
        try:
            result = self.client.rag.retrieve(
                query,
                project_id=project,
                k_max=self.config.rag_top_k,
                **reader,
            )
        except Exception as e:
            logger.warning(f"RAG search failed: {e}")
            return []
        return sources_from_retrieve(result)

    async def search_web(self, query: str) -> list[Source] | CapabilityUnavailable:
        """
        Search web sources.

        Args:
            query: Search query

        Returns:
            List of sources from web search
        """
        from .mcp_sources import invoke_search_tool, resolve_search_tool

        tool = resolve_search_tool(self.client, "web")
        if tool is None:
            return CapabilityUnavailable(
                capability="web",
                message="web search unavailable — no MCP search tool registered",
            )
        return invoke_search_tool(
            self.client,
            tool,
            kind="web",
            query=query,
            limit=self.config.web_max_results,
        )

    async def search_code(
        self,
        query: str,
        paths: Optional[list[str]] = None,
    ) -> list[Source] | CapabilityUnavailable:
        """
        Search code files.

        Args:
            query: Search query
            paths: Optional paths to search within

        Returns:
            List of sources from code search
        """
        from .mcp_sources import invoke_search_tool, resolve_search_tool, search_local_code

        tool = resolve_search_tool(self.client, "code")
        if tool is not None:
            return invoke_search_tool(
                self.client,
                tool,
                kind="code",
                query=query,
                limit=self.config.code_max_files,
                paths=paths,
            )
        if paths:
            return search_local_code(query, paths, max_files=self.config.code_max_files)
        return CapabilityUnavailable(
            capability="code",
            message="code search unavailable — no MCP search tool registered",
        )


class QuickSearch:
    """
    Simplified interface for common search patterns.
    """

    def __init__(self, client: "Client"):
        """Initialize quick search."""
        self.client = client

    async def answer(
        self,
        question: str,
        project_id: Optional[str] = None,
        depth: SearchDepth = SearchDepth.SHALLOW,
    ) -> str:
        """
        Get a quick answer to a question.

        Args:
            question: Question to answer
            project_id: Optional project for RAG
            depth: Search depth

        Returns:
            Answer string
        """
        config = SearchConfig(
            depth=depth,
            project_id=project_id,
            max_steps=3 if depth == SearchDepth.SHALLOW else 5,
        )
        search = AgenticSearch(self.client, config)
        result = await search.search(question)
        return result.format_with_citations()

    async def research(
        self,
        topic: str,
        project_id: Optional[str] = None,
    ) -> SearchResult:
        """
        Deep research on a topic.

        Args:
            topic: Topic to research
            project_id: Optional project for RAG

        Returns:
            Full SearchResult
        """
        config = SearchConfig(
            depth=SearchDepth.DEEP,
            project_id=project_id,
            max_steps=10,
            max_sources=20,
        )
        search = AgenticSearch(self.client, config)
        return await search.search(topic)
