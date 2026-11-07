"""
RAG Proxy MCP Server (Phase 2i).

Exposes the R4 Platform's RAG query endpoint (``/gw/rag/query``) as an
MCP tool, so AI agents running through any MCP-capable client can search
project documents without direct RAG API knowledge.

This is intentionally minimal — a single ``search_docs`` tool that wraps
the Gateway's ``POST /gw/rag/query`` route.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from ..base import MCPServer, Tool, ToolResult

logger = logging.getLogger(__name__)


class RAGProxyServer(MCPServer):
    """MCP server that proxies RAG queries through the Platform Gateway."""

    name = "rag-proxy"
    version = "1.0.0"

    def __init__(
        self,
        gateway_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        timeout_seconds: int = 30,
        transport: str = "stdio",
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = (
            api_key or os.environ.get("R4_GATEWAY_TOKEN") or os.environ.get("R4_API_KEY")
        )
        self._project_id = project_id or os.environ.get("R4_PROJECT_ID", "default")
        self._timeout = timeout_seconds
        super().__init__(transport=transport)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _http_error_message(self, action: str, resp: httpx.Response) -> str:
        body = resp.text[:500]
        if resp.status_code == 503 and "No active endpoint" in body:
            return (
                f"{action} unavailable: no active RAG endpoint. "
                "Run `r4 endpoint up` or check `r4 endpoint list`. "
                f"Gateway returned: {body}"
            )
        return f"{action} failed (HTTP {resp.status_code}): {body}"

    @staticmethod
    def _extract_documents_payload(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            documents = data.get("documents", [])
            if isinstance(documents, list):
                return [item for item in documents if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_search_payload(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            results = data.get("results") or data.get("sources") or []
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    @Tool(
        name="rag_status",
        description=(
            "Check whether RAG is reachable for the current project and whether "
            "documents are indexed."
        ),
        parameters={},
    )
    async def rag_status(self) -> ToolResult:
        """Check RAG availability via the Gateway document list path."""
        url = f"{self._gateway_url}/gw/rag/documents"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
                    headers=self._build_headers(),
                    params={"project_id": self._project_id, "limit": 1},
                )

            if resp.status_code >= 400:
                return ToolResult.error(self._http_error_message("RAG status", resp))

            docs = self._extract_documents_payload(resp.json())
        except httpx.TimeoutException:
            return ToolResult.error("RAG status check timed out")
        except Exception as e:
            return ToolResult.error(f"RAG status check error: {e}")

        if docs:
            return ToolResult.success(
                f"RAG reachable for project {self._project_id}. Documents are indexed."
            )
        return ToolResult.success(
            f"RAG reachable for project {self._project_id}. No documents indexed yet."
        )

    @Tool(
        name="search_docs",
        description=(
            "Search project documents using RAG (Retrieval-Augmented Generation). "
            "Returns the most relevant document chunks for the given query."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (1-20, default 5)",
                "required": False,
                "default": 5,
            },
            "threshold": {
                "type": "number",
                "description": "Similarity threshold 0.0-1.0 (default 0.7)",
                "required": False,
                "default": 0.7,
            },
        },
    )
    async def search_docs(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> ToolResult:
        """Query the RAG service through the Gateway."""
        url = f"{self._gateway_url}/gw/rag/query"
        payload = {
            "query": query,
            "project_id": self._project_id,
            "top_k": min(max(top_k, 1), 20),
            "threshold": max(0.0, min(threshold, 1.0)),
            "include_sources": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url,
                    headers=self._build_headers(),
                    json=payload,
                )

            if resp.status_code >= 400:
                return ToolResult.error(self._http_error_message("RAG query", resp))

            data = resp.json()
        except httpx.TimeoutException:
            return ToolResult.error("RAG query timed out")
        except Exception as e:
            return ToolResult.error(f"RAG query error: {e}")

        # Format results for LLM consumption
        results = self._extract_search_payload(data)
        if not results:
            return ToolResult.success("No documents found matching the query.")

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            source = r.get("source", r.get("document_id", "unknown"))
            text = r.get("text", r.get("content", ""))
            score = r.get("score", r.get("similarity", ""))
            score_str = f" (score: {score:.3f})" if isinstance(score, (int, float)) else ""
            lines.append(f"[{i}] {source}{score_str}\n{text}")

        return ToolResult.success("\n---\n".join(lines))

    @Tool(
        name="list_documents",
        description="List documents available for RAG search in the current project.",
        parameters={
            "limit": {
                "type": "integer",
                "description": "Max documents to list (default 20)",
                "required": False,
                "default": 20,
            },
        },
    )
    async def list_documents(self, limit: int = 20) -> ToolResult:
        """List indexed documents via the Gateway."""
        url = f"{self._gateway_url}/gw/rag/documents"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
                    headers=self._build_headers(),
                    params={
                        "project_id": self._project_id,
                        "limit": min(limit, 100),
                    },
                )

            if resp.status_code >= 400:
                return ToolResult.error(self._http_error_message("List documents", resp))

            data = resp.json()
        except httpx.TimeoutException:
            return ToolResult.error("List documents timed out")
        except Exception as e:
            return ToolResult.error(f"List documents error: {e}")

        docs = self._extract_documents_payload(data)
        if not docs:
            return ToolResult.success("No documents indexed in this project.")

        lines = []
        for d in docs:
            name = d.get("filename", d.get("name", d.get("id", "?")))
            status = d.get("status", "")
            chunks = d.get("chunk_count", d.get("chunks", "?"))
            lines.append(f"  • {name}  (chunks: {chunks}, status: {status})")

        return ToolResult.success(f"Indexed documents ({len(docs)}):\n" + "\n".join(lines))
