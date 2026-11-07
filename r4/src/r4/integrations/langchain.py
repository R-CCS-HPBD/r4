"""
LangChain Integration for R4.

Provides LangChain-compatible wrappers for the R4 Platform.

Installation:
    pip install r4[langchain]

Usage:
    from r4.integrations.langchain import R4LLM, R4Retriever

    llm = R4LLM()
    retriever = R4Retriever(collection="my-docs")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid requiring langchain if not installed
if TYPE_CHECKING:
    from langchain_core.callbacks import CallbackManagerForLLMRun  # noqa: F401
    from langchain_core.documents import Document  # noqa: F401
    from langchain_core.language_models import BaseLLM  # noqa: F401
    from langchain_core.outputs import Generation, LLMResult  # noqa: F401
    from langchain_core.retrievers import BaseRetriever  # noqa: F401

_LANGCHAIN_AVAILABLE = None


def _check_langchain_available() -> bool:
    """Check if langchain is installed."""
    global _LANGCHAIN_AVAILABLE
    if _LANGCHAIN_AVAILABLE is None:
        try:
            __import__("langchain_core")
            _LANGCHAIN_AVAILABLE = True
        except ImportError:
            _LANGCHAIN_AVAILABLE = False
    return _LANGCHAIN_AVAILABLE


class LangChainNotInstalledError(ImportError):
    """Raised when langchain is not installed."""

    def __init__(self):
        super().__init__("langchain is not installed. Install with: pip install r4[langchain]")


def _require_langchain():
    """Raise if langchain not available."""
    if not _check_langchain_available():
        raise LangChainNotInstalledError()


class R4LLM:
    """
    LangChain-compatible LLM wrapper for R4 Platform.

    Routes all LLM calls through the Platform Gateway.

    Example:
        >>> from r4.integrations.langchain import R4LLM
        >>> llm = R4LLM(model="rivault-gpt-oss-static")
        >>> response = llm.invoke("What is quantum computing?")
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
        model: str = "rivault-gpt-oss-static",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ):
        """
        Initialize R4LLM.

        Args:
            gateway_url: Gateway URL (uses R4_GATEWAY_URL env if not set)
            project_id: Project ID (uses R4_PROJECT_ID env if not set)
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
        """
        _require_langchain()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._kwargs = kwargs

    @property
    def _llm_type(self) -> str:
        return "r4"

    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs,
    ) -> str:
        """Execute LLM call."""
        response = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stop=stop,
            **kwargs,
        )
        return response.content

    def invoke(self, prompt_input: str, **kwargs) -> str:
        """LangChain invoke interface."""
        return self._call(prompt_input, **kwargs)

    async def ainvoke(self, prompt_input: str, **kwargs) -> str:
        """Async invoke."""
        response = await self._client.achat(
            messages=[{"role": "user", "content": prompt_input}],
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        return response.content

    def stream(self, prompt_input: str, **kwargs) -> Iterator[str]:
        """Stream response tokens."""
        for chunk in self._client.chat_stream(
            messages=[{"role": "user", "content": prompt_input}],
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        ):
            yield chunk.content


class R4Retriever:
    """
    LangChain-compatible retriever for R4 Platform RAG.

    Uses Platform's semantic search for document retrieval.

    Example:
        >>> from r4.integrations.langchain import R4Retriever
        >>> retriever = R4Retriever(collection="research-papers")
        >>> docs = retriever.invoke("quantum entanglement experiments")
    """

    def __init__(
        self,
        collection: str,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
    ):
        """
        Initialize retriever.

        Args:
            collection: RAG collection/index name
            gateway_url: Gateway URL
            project_id: Project ID
            top_k: Number of documents to retrieve
            score_threshold: Minimum similarity score
        """
        _require_langchain()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.collection = collection
        self.top_k = top_k
        self.score_threshold = score_threshold

    def _get_relevant_documents(self, query: str) -> list:
        """The passages the platform keeps for ``query``, best first.

        The retrieve verb (ADR-093 D-10): ``top_k`` caps the count and
        ``score_threshold`` becomes the server's legacy absolute cut; the
        retriever applies no cut of its own.
        """
        from langchain_core.documents import Document

        result = self._client.rag.retrieve(
            query,
            project_id=self.collection or None,
            k_max=self.top_k,
            threshold=self.score_threshold,
        )
        return documents_from_retrieve(result, Document)

    def invoke(self, query_input: str, **kwargs) -> list:
        """LangChain invoke interface."""
        return self._get_relevant_documents(query_input)

    async def ainvoke(self, query_input: str, **kwargs) -> list:
        """Async invoke (the blocking retrieve call runs in a worker thread)."""
        import asyncio

        return await asyncio.to_thread(self._get_relevant_documents, query_input)


def documents_from_retrieve(result: Any, document_cls: Any) -> list:
    """LangChain ``Document`` objects from a ``RetrieveResult``, rank order kept.

    ``metadata["source"]`` is the artifact's name; ``source_id`` is the
    passage handle for ``client.rag.fetch_passage``. An ``insufficient``
    verdict yields no documents: the platform found nothing that answers.
    """
    from r4.rag import passage_score

    if result.sufficiency.status == "insufficient":
        return []
    return [
        document_cls(
            page_content=passage.text,
            metadata={
                "source": passage.source_name or passage.source_id,
                "source_id": passage.source_id,
                "artifact_id": passage.artifact_id,
                "section": passage.section,
                "rank": passage.rank,
                "score": passage_score(passage),
                "lanes": list(passage.lanes),
                "sufficiency": result.sufficiency.status,
            },
        )
        for passage in result.passages
    ]


class R4Toolkit:
    """
    LangChain toolkit wrapping Platform MCP tools.

    Example:
        >>> from r4.integrations.langchain import R4Toolkit
        >>> toolkit = R4Toolkit()
        >>> tools = toolkit.get_tools()
        >>> # Use tools with LangChain agents
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
    ):
        """
        Initialize toolkit.

        Args:
            gateway_url: Gateway URL
            project_id: Project ID
            allowed_tools: List of tool names to include (None = all)
        """
        _require_langchain()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.allowed_tools = allowed_tools

    def get_tools(self) -> list:
        """
        Get LangChain Tool objects for all MCP tools.

        Returns:
            List of LangChain Tool objects
        """
        from langchain_core.tools import StructuredTool

        mcp_tools = self._client.mcp.list_tools()
        langchain_tools = []

        for tool in mcp_tools:
            name = tool["name"]

            # Filter if allowed_tools specified
            if self.allowed_tools and name not in self.allowed_tools:
                continue

            # Create wrapper function
            def make_tool_func(tool_name):
                def tool_func(**kwargs):
                    return self._client.mcp.call_tool(
                        name=tool_name,
                        arguments=kwargs,
                    )

                return tool_func

            lc_tool = StructuredTool.from_function(
                func=make_tool_func(name),
                name=name,
                description=tool.get("description", ""),
                args_schema=tool.get("inputSchema"),
            )
            langchain_tools.append(lc_tool)

        return langchain_tools


__all__ = [
    "R4LLM",
    "R4Retriever",
    "R4Toolkit",
    "LangChainNotInstalledError",
]
