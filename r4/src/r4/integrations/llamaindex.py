"""
LlamaIndex Integration for R4.

Provides LlamaIndex-compatible components for the R4 Platform.

Installation:
    pip install r4[llamaindex]

Usage:
    from r4.integrations.llamaindex import R4LLM, R4VectorStore
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

_LLAMAINDEX_AVAILABLE = None


def _check_llamaindex_available() -> bool:
    """Check if llama-index is installed."""
    global _LLAMAINDEX_AVAILABLE
    if _LLAMAINDEX_AVAILABLE is None:
        try:
            __import__("llama_index")
            _LLAMAINDEX_AVAILABLE = True
        except ImportError:
            _LLAMAINDEX_AVAILABLE = False
    return _LLAMAINDEX_AVAILABLE


class LlamaIndexNotInstalledError(ImportError):
    """Raised when llama-index is not installed."""

    def __init__(self):
        super().__init__("llama-index is not installed. Install with: pip install r4[llamaindex]")


def _require_llamaindex():
    """Raise if llama-index not available."""
    if not _check_llamaindex_available():
        raise LlamaIndexNotInstalledError()


class R4LLM:
    """
    LlamaIndex-compatible LLM for R4 Platform.

    Example:
        >>> from r4.integrations.llamaindex import R4LLM
        >>> llm = R4LLM(model="rivault-gpt-oss-static")
        >>> response = llm.complete("Explain quantum computing")
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
        """Initialize LlamaIndex LLM wrapper."""
        _require_llamaindex()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._kwargs = kwargs

    @property
    def metadata(self) -> dict:
        """LLM metadata."""
        return {
            "model_name": self.model,
            "is_chat_model": True,
        }

    def complete(self, prompt: str, **kwargs) -> Any:
        """Complete a prompt."""
        from llama_index.core.llms import CompletionResponse

        response = self._client.chat(
            prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )

        return CompletionResponse(text=response.content)

    def chat(self, messages: List[dict], **kwargs) -> Any:
        """Chat completion."""
        from llama_index.core.llms import ChatMessage, ChatResponse

        from r4 import ChatMessage as R4ChatMessage

        # Convert LlamaIndex messages to our format
        formatted = []
        for msg in messages:
            if hasattr(msg, "role"):
                formatted.append(R4ChatMessage(role=msg.role.value, content=msg.content))
            else:
                formatted.append(
                    R4ChatMessage(
                        role=msg["role"],
                        content=msg["content"],
                    )
                )

        response = self._client.chat_completion(
            formatted,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )

        return ChatResponse(message=ChatMessage(role="assistant", content=response.content))

    def stream_complete(self, prompt: str, **kwargs):
        """Stream completion."""
        from llama_index.core.llms import CompletionResponseGen

        def gen():
            for chunk in self._client.chat_stream(prompt, model=self.model, **kwargs):
                yield CompletionResponseGen(text=chunk, delta=chunk)

        return gen()


class R4VectorStore:
    """
    LlamaIndex-compatible VectorStore backed by Platform RAG.

    Example:
        >>> from r4.integrations.llamaindex import R4VectorStore
        >>> from llama_index.core import VectorStoreIndex
        >>>
        >>> vector_store = R4VectorStore(collection="my-docs")
        >>> index = VectorStoreIndex.from_vector_store(vector_store)
    """

    def __init__(
        self,
        collection: str,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        """Initialize vector store."""
        _require_llamaindex()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.collection = collection

    def query(self, query: Any, similarity_top_k: int = 5, **kwargs: Any) -> Any:
        """Query by text: a LlamaIndex ``VectorStoreQuery`` (its ``query_str``
        and ``similarity_top_k``) or a plain string.

        The platform embeds the query itself, in the project's one embedding
        space, and keeps only the relevant passages (ADR-093 D-2, D-3); a bare
        embedding vector from a client-side model cannot be searched.
        """
        from llama_index.core.schema import TextNode
        from llama_index.core.vector_stores import VectorStoreQueryResult

        text, top_k = _query_text(query, similarity_top_k)
        result = self._client.rag.retrieve(text, project_id=self.collection or None, k_max=top_k)
        nodes, similarities, ids = nodes_from_retrieve(result, TextNode)
        return VectorStoreQueryResult(nodes=nodes, similarities=similarities, ids=ids)

    def add(self, nodes: Sequence[Any], **kwargs) -> List[str]:
        """Add nodes to the vector store."""
        # Upload documents to collection
        ids = []
        for node in nodes:
            # This would require the platform to support single-doc uploads
            # For now, we'd batch them
            result = self._client.rag.upload(
                content=node.get_content(),
                collection=self.collection,
                metadata=node.metadata,
            )
            ids.append(result.id)

        return ids


def _query_text(query: Any, default_top_k: int) -> tuple[str, int]:
    """``(text, top_k)`` from a ``VectorStoreQuery`` or a string."""
    if isinstance(query, str):
        return query, default_top_k
    text = getattr(query, "query_str", None)
    if not text:
        raise ValueError(
            "R4VectorStore searches by text: pass a VectorStoreQuery with query_str, "
            "or a string. The platform embeds queries itself (one space per project)."
        )
    return str(text), int(getattr(query, "similarity_top_k", None) or default_top_k)


def nodes_from_retrieve(result: Any, node_cls: Any) -> tuple[list, list, list]:
    """``(nodes, similarities, ids)`` from a ``RetrieveResult``, rank order kept;
    node ids are the passage handles. ``insufficient`` yields nothing."""
    from r4.rag import passage_score

    if result.sufficiency.status == "insufficient":
        return [], [], []
    nodes, similarities, ids = [], [], []
    for passage in result.passages:
        metadata = {
            "source_name": passage.source_name,
            "artifact_id": passage.artifact_id,
            "section": passage.section,
            "rank": passage.rank,
            "sufficiency": result.sufficiency.status,
        }
        nodes.append(node_cls(id_=passage.source_id, text=passage.text, metadata=metadata))
        similarities.append(passage_score(passage))
        ids.append(passage.source_id)
    return nodes, similarities, ids


class R4Embedding:
    """
    LlamaIndex-compatible embedding model using Platform.

    Example:
        >>> from r4.integrations.llamaindex import R4Embedding
        >>> embed = R4Embedding()
        >>> vectors = embed.get_text_embedding("Hello world")
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        project_id: Optional[str] = None,
        model: str = "BAAI/bge-large-en-v1.5",
    ):
        """Initialize embedding wrapper."""
        _require_llamaindex()

        from r4 import Client

        self._client = Client(gateway_url=gateway_url, project_id=project_id)
        self.model = model

    def get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for text."""
        result = self._client.rag.embed(text=text, model=self.model)
        return result.embedding

    def get_text_embedding_batch(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts."""
        results = self._client.rag.embed_batch(texts=texts, model=self.model)
        return [r.embedding for r in results]

    async def aget_text_embedding(self, text: str) -> List[float]:
        """Async get embedding."""
        result = await self._client.rag.aembed(text=text, model=self.model)
        return result.embedding


__all__ = [
    "R4LLM",
    "R4VectorStore",
    "R4Embedding",
    "LlamaIndexNotInstalledError",
]
