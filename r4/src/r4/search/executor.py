"""
Search Tool Executor (A-903)

Execute search steps using available tools.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from r4.client import Client

    from .core import SearchConfig, Source
    from .planner import SearchStep

logger = logging.getLogger(__name__)


class ToolStatus(Enum):
    """Status of a tool execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ToolResult:
    """Result of a tool execution."""

    status: ToolStatus
    sources: list["Source"]
    message: str = ""
    error: Optional[str] = None


class SearchTool(ABC):
    """
    Base class for search tools (A-903).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass

    @abstractmethod
    async def execute(self, query: str, **kwargs) -> ToolResult:
        """
        Execute the search.

        Args:
            query: Search query
            **kwargs: Tool-specific parameters

        Returns:
            ToolResult with sources
        """
        pass


class RAGSearchTool(SearchTool):
    """
    RAG document search tool.
    """

    def __init__(self, client: "Client", project_id: str, top_k: int = 5, model: str | None = None):
        """Initialize RAG search tool. ``model`` reads the passages (the
        synthesis model; None = the client's configured model)."""
        self.client = client
        self.project_id = project_id
        self.top_k = top_k
        self.model = model

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return "Search internal documents and knowledge base"

    async def execute(self, query: str, **kwargs) -> ToolResult:
        """Execute RAG search on the retrieve verb (ADR-093 D-10): passages the
        server kept, no hidden answer generation."""
        from r4.model_scale import configured_reader_fields

        from .core import sources_from_retrieve

        # Name the model that reads the passages and its D-14 profile
        # (r4-scale-router-10): UMRS otherwise sizes the read for a 3B.
        reader = configured_reader_fields(self.model, self.client)
        try:
            result = self.client.rag.retrieve(
                query,
                project_id=kwargs.get("project_id", self.project_id),
                k_max=kwargs.get("top_k", self.top_k),
                **reader,
            )
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(e),
            )

        sources = sources_from_retrieve(result)
        return ToolResult(
            status=ToolStatus.SUCCESS if sources else ToolStatus.PARTIAL,
            sources=sources,
            message=f"Found {len(sources)} passages (sufficiency: {result.sufficiency.status})",
        )


class CatalogSearchTool(SearchTool):
    """Search the UMRS data catalog via gateway."""

    def __init__(self, client: "Client", project_id: str, limit: int = 20):
        self.client = client
        self.project_id = project_id
        self.limit = limit

    @property
    def name(self) -> str:
        return "lab_catalog_search"

    @property
    def description(self) -> str:
        return "Search uploaded lab data catalog metadata (summaries, tags, s3_key)"

    async def execute(self, query: str, **kwargs) -> ToolResult:
        from .catalog_format import format_catalog_entry
        from .core import Source, SourceType

        try:
            params: dict[str, object] = {
                "q": query,
                "limit": kwargs.get("limit", self.limit),
            }
            for key in ("modality", "state", "tag", "prefix"):
                if kwargs.get(key):
                    params[key] = kwargs[key]

            data = self.client.get(
                f"{getattr(self.client, '_api_prefix', '/gw')}/catalog/search", params=params
            )
            results = data.get("results", []) if isinstance(data, dict) else []
            sources = []
            for item in results:
                entry = format_catalog_entry(item)
                s3_key = entry["s3_key"]
                sources.append(
                    Source(
                        type=SourceType.CATALOG_ENTRY,
                        title=s3_key.rsplit("/", 1)[-1] or s3_key,
                        url=None,
                        content=entry.get("summary", ""),
                        metadata=entry,
                        relevance_score=entry["relevance_score"],
                    )
                )
            return ToolResult(
                status=ToolStatus.SUCCESS if sources else ToolStatus.PARTIAL,
                sources=sources,
                message=(
                    f"Found {len(sources)} catalog entries"
                    if sources
                    else (
                        "Zero hits in the governed Gateway catalog. Do not retry the same "
                        "query. Local RC1 files: knowledge_show or r4 project verify."
                    )
                ),
            )
        except Exception as e:
            logger.debug("Catalog search failed: %s", e)
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(e),
                message="Catalog search failed",
            )


class LabFileReadTool(SearchTool):
    """Read uploaded lab file content from catalog storage (byte-bounded)."""

    def __init__(self, client: "Client", project_id: str, max_length: int = 1_048_576):
        self.client = client
        self.project_id = project_id
        self.max_length = max_length

    @property
    def name(self) -> str:
        return "lab_file_read"

    @property
    def description(self) -> str:
        return (
            "Read uploaded lab file content by s3_key. "
            "Use as=content (default); as=extracted_text or as=raw_bytes when needed."
        )

    async def execute(self, query: str, **kwargs) -> ToolResult:
        from .catalog_format import format_lab_file_read_response, map_read_as_to_source
        from .core import Source, SourceType

        s3_key = kwargs.get("s3_key") or query.strip()
        if not s3_key:
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error="s3_key required",
                message="lab_file_read requires s3_key",
            )
        try:
            gateway_source = map_read_as_to_source(kwargs.get("as", "content"))
        except ValueError as exc:
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(exc),
                message=str(exc),
            )
        try:
            data = self.client.get(
                f"{getattr(self.client, '_api_prefix', '/gw')}/catalog/read",
                params={
                    "s3_key": s3_key,
                    "offset": kwargs.get("offset", 0),
                    "length": kwargs.get("length", self.max_length),
                    "source": gateway_source,
                },
            )
            if not isinstance(data, dict):
                return ToolResult(
                    status=ToolStatus.FAILED,
                    sources=[],
                    error="unexpected response",
                    message="Unexpected catalog read response",
                )
            formatted = format_lab_file_read_response(data)
            text = formatted.get("text")
            content = text if text is not None else f"(binary, {formatted.get('length', 0)} bytes)"
            sources = [
                Source(
                    type=SourceType.RAW_FILE,
                    title=s3_key.rsplit("/", 1)[-1] or s3_key,
                    content=content,
                    metadata=formatted,
                    relevance_score=1.0,
                )
            ]
            return ToolResult(
                status=ToolStatus.SUCCESS,
                sources=sources,
                message="Read lab file",
            )
        except Exception as e:
            logger.debug("Lab file read failed: %s", e)
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(e),
                message="Lab file read failed",
            )


class WebSearchTool(SearchTool):
    """
    Web search tool.

    Uses MCP tool if available, otherwise falls back to built-in.
    """

    def __init__(self, client: "Client", max_results: int = 5):
        """Initialize web search tool."""
        self.client = client
        self.max_results = max_results

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for information"

    async def execute(self, query: str, **kwargs) -> ToolResult:
        """Execute web search."""
        from .core import CapabilityUnavailable
        from .mcp_sources import invoke_search_tool, resolve_search_tool

        try:
            tool = resolve_search_tool(self.client, "web")
            if tool is None:
                unavailable = CapabilityUnavailable(
                    "web",
                    "web search unavailable — no MCP search tool registered",
                )
                return ToolResult(
                    status=ToolStatus.SKIPPED,
                    sources=[],
                    message=unavailable.message,
                )
            sources = invoke_search_tool(
                self.client,
                tool,
                kind="web",
                query=query,
                limit=kwargs.get("max_results", self.max_results),
            )
            return ToolResult(
                status=ToolStatus.SUCCESS if sources else ToolStatus.PARTIAL,
                sources=sources,
                message=f"Found {len(sources)} web results",
            )
        except Exception as e:
            logger.error("Web search failed: %s", e)
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(e),
                message="Web search failed",
            )


class CodeSearchTool(SearchTool):
    """
    Code search tool.
    """

    def __init__(self, client: "Client", max_files: int = 10):
        """Initialize code search tool."""
        self.client = client
        self.max_files = max_files

    @property
    def name(self) -> str:
        return "code_search"

    @property
    def description(self) -> str:
        return "Search code files and repositories"

    async def execute(self, query: str, **kwargs) -> ToolResult:
        """Execute code search."""
        from .core import CapabilityUnavailable
        from .mcp_sources import invoke_search_tool, resolve_search_tool, search_local_code

        try:
            paths = kwargs.get("paths")
            tool = resolve_search_tool(self.client, "code")
            if tool is not None:
                sources = invoke_search_tool(
                    self.client,
                    tool,
                    kind="code",
                    query=query,
                    limit=kwargs.get("max_files", self.max_files),
                    paths=paths,
                )
            elif paths:
                sources = search_local_code(query, paths, max_files=self.max_files)
            else:
                unavailable = CapabilityUnavailable(
                    "code",
                    "code search unavailable — no MCP search tool registered",
                )
                return ToolResult(
                    status=ToolStatus.SKIPPED,
                    sources=[],
                    message=unavailable.message,
                )
            return ToolResult(
                status=ToolStatus.SUCCESS if sources else ToolStatus.PARTIAL,
                sources=sources,
                message=f"Found {len(sources)} code files",
            )
        except Exception as e:
            logger.error("Code search failed: %s", e)
            return ToolResult(
                status=ToolStatus.FAILED,
                sources=[],
                error=str(e),
                message="Code search failed",
            )


class ToolExecutor:
    """
    Tool Executor (A-903).

    Executes search steps using appropriate tools.
    """

    def __init__(
        self,
        client: "Client",
        config: "SearchConfig",
        warnings: list[str] | None = None,
    ):
        """
        Initialize tool executor.

        Args:
            client: R4 client
            config: Search configuration
        """
        self.client = client
        self.config = config
        self.warnings = warnings
        self._tools: dict[str, SearchTool] = {}
        self._init_tools()

    def _init_tools(self) -> None:
        """Initialize available tools."""

        if self.config.enable_rag and self.config.project_id:
            self._tools["rag_query"] = RAGSearchTool(
                self.client,
                self.config.project_id,
                self.config.rag_top_k,
                model=self.config.model,
            )

        if self.config.enable_catalog and self.config.project_id:
            self._tools["lab_catalog_search"] = CatalogSearchTool(
                self.client,
                self.config.project_id,
            )

        if self.config.enable_raw_read and self.config.project_id:
            self._tools["lab_file_read"] = LabFileReadTool(
                self.client,
                self.config.project_id,
            )

        if self.config.enable_web:
            self._tools["web_search"] = WebSearchTool(
                self.client,
                self.config.web_max_results,
            )

        if self.config.enable_code:
            self._tools["code_search"] = CodeSearchTool(
                self.client,
                self.config.code_max_files,
            )

    async def execute_step(self, step: "SearchStep") -> list["Source"]:
        """
        Execute a search step.

        Args:
            step: SearchStep to execute

        Returns:
            List of sources found
        """
        tool_name = step.step_type.value
        tool = self._tools.get(tool_name)

        if not tool:
            logger.warning(f"No tool available for step type: {step.step_type}")
            return []

        logger.info(f"Executing {tool_name}: {step.query[:50]}...")

        result = await tool.execute(step.query, **step.parameters)

        if result.status in (ToolStatus.SUCCESS, ToolStatus.PARTIAL):
            logger.info(f"Step completed: {result.message}")
        elif result.status == ToolStatus.FAILED:
            logger.warning(f"Step failed: {result.error}")
            if self.warnings is not None:
                self.warnings.append(result.error or result.message)
        elif result.status == ToolStatus.SKIPPED:
            logger.info(f"Step skipped: {result.message}")
            if self.warnings is not None:
                self.warnings.append(result.message)

        return result.sources

    async def execute_raw_reads_for_catalog_hits(
        self,
        catalog_sources: list,
        *,
        query: str,
        already_read: set[str] | None = None,
    ) -> list:
        """Run lab_file_read for catalog hits that need ground-truth content."""
        from .catalog_ground_truth import pick_catalog_keys_for_raw_read

        tool = self._tools.get("lab_file_read")
        if tool is None:
            return []

        read_keys = pick_catalog_keys_for_raw_read(
            catalog_sources,
            query=query,
            already_read=already_read or set(),
        )
        sources = []
        for s3_key in read_keys:
            result = await tool.execute("", s3_key=s3_key)
            if result.status in (ToolStatus.SUCCESS, ToolStatus.PARTIAL):
                sources.extend(result.sources)
        return sources

    def get_available_tools(self) -> list[str]:
        """Get list of available tool names."""
        return list(self._tools.keys())
