"""Tests for RAGProxyServer MCP tools (Phase 2i)."""

from __future__ import annotations

import json

import httpx
import pytest

from r4_mcp.servers.rag_proxy import RAGProxyServer


@pytest.fixture
def server():
    """Create a RAGProxyServer with a test gateway URL."""
    return RAGProxyServer(
        gateway_url="http://gateway-test:8000",
        api_key="test-key",
        project_id="test-project",
        timeout_seconds=5,
    )


class TestToolDiscovery:
    """Verify expected tools are registered."""

    EXPECTED_TOOLS = ["search_docs", "list_documents", "rag_status"]

    def test_all_tools_registered(self, server: RAGProxyServer):
        for name in self.EXPECTED_TOOLS:
            assert name in server._tools, f"Missing tool: {name}"

    def test_tool_count(self, server: RAGProxyServer):
        assert len(server._tools) == len(self.EXPECTED_TOOLS)

    def test_tools_have_descriptions(self, server: RAGProxyServer):
        for t in server._tools.values():
            assert t.description, f"Tool {t.name} has no description"


class TestServerConfig:
    def test_gateway_url_trailing_slash(self):
        srv = RAGProxyServer(gateway_url="http://host:8000/")
        assert srv._gateway_url == "http://host:8000"

    def test_headers_with_api_key(self, server: RAGProxyServer):
        headers = server._build_headers()
        assert headers["Authorization"] == "Bearer test-key"

    def test_headers_without_api_key(self, monkeypatch):
        monkeypatch.delenv("R4_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("R4_API_KEY", raising=False)
        srv = RAGProxyServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert "Authorization" not in headers

    def test_headers_with_env_gateway_token(self, monkeypatch):
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "env-token")
        monkeypatch.delenv("R4_API_KEY", raising=False)
        srv = RAGProxyServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer env-token"

    def test_headers_with_env_api_key(self, monkeypatch):
        monkeypatch.delenv("R4_GATEWAY_TOKEN", raising=False)
        monkeypatch.setenv("R4_API_KEY", "env-api-key")
        srv = RAGProxyServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer env-api-key"

    def test_explicit_api_key_precedes_env(self, monkeypatch):
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "env-token")
        srv = RAGProxyServer(gateway_url="http://host:8000", api_key="explicit-token")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer explicit-token"

    def test_project_id_default(self, monkeypatch):
        monkeypatch.delenv("R4_PROJECT_ID", raising=False)
        srv = RAGProxyServer(gateway_url="http://host:8000")
        assert srv._project_id == "default"

    def test_project_id_explicit(self, server: RAGProxyServer):
        assert server._project_id == "test-project"

    def test_server_metadata(self, server: RAGProxyServer):
        assert server.name == "rag-proxy"
        assert server.version == "1.0.0"


class TestMCPFormat:
    def test_mcp_format_output(self, server: RAGProxyServer):
        for tool_def in server._tools.values():
            fmt = tool_def.to_mcp_format()
            assert "name" in fmt
            assert "description" in fmt
            assert "inputSchema" in fmt
            schema = fmt["inputSchema"]
            assert schema["type"] == "object"

    def test_search_docs_required_params(self, server: RAGProxyServer):
        fmt = server._tools["search_docs"].to_mcp_format()
        assert "query" in fmt["inputSchema"]["required"]


class TestSearchDocs:
    def _patch_client(self, monkeypatch, handler):
        """Replace httpx.AsyncClient in rag_proxy with a mock-transport version."""
        import r4_mcp.servers.rag_proxy as mod

        _orig = httpx.AsyncClient

        class PatchedClient(_orig):
            def __init__(self, **kwargs):
                kwargs.pop("timeout", None)
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(
            mod,
            "httpx",
            type(
                "M",
                (),
                {
                    "AsyncClient": PatchedClient,
                    "TimeoutException": httpx.TimeoutException,
                },
            )(),
        )

    @pytest.mark.asyncio
    async def test_search_docs_success(self, server, monkeypatch):
        response_data = {
            "results": [
                {"source": "paper.pdf", "text": "Result content", "score": 0.92},
                {"source": "notes.md", "text": "Another result", "score": 0.85},
            ]
        }
        self._patch_client(monkeypatch, lambda req: httpx.Response(200, json=response_data))
        result = await server.search_docs("test query", top_k=5)
        assert not result.is_error
        assert "paper.pdf" in result.content
        assert "0.920" in result.content

    @pytest.mark.asyncio
    async def test_search_docs_no_results(self, server, monkeypatch):
        self._patch_client(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
        result = await server.search_docs("nothing")
        assert not result.is_error
        assert "No documents found" in result.content

    @pytest.mark.asyncio
    async def test_search_docs_accepts_gateway_sources(self, server, monkeypatch):
        response_data = {
            "answer": "summary",
            "sources": [{"document_id": "doc-1", "content": "Gateway source", "score": 0.91}],
        }
        self._patch_client(monkeypatch, lambda req: httpx.Response(200, json=response_data))

        result = await server.search_docs("test query", top_k=5)

        assert not result.is_error
        assert "doc-1" in result.content
        assert "Gateway source" in result.content

    @pytest.mark.asyncio
    async def test_search_docs_http_error(self, server, monkeypatch):
        self._patch_client(
            monkeypatch, lambda req: httpx.Response(500, text="Internal Server Error")
        )
        result = await server.search_docs("fail")
        assert result.is_error
        assert "500" in result.content

    @pytest.mark.asyncio
    async def test_search_docs_timeout(self, server, monkeypatch):
        def handler(req):
            raise httpx.TimeoutException("timed out")

        self._patch_client(monkeypatch, handler)
        result = await server.search_docs("slow")
        assert result.is_error
        assert "timed out" in result.content

    @pytest.mark.asyncio
    async def test_top_k_clamped(self, server, monkeypatch):
        captured_payloads = []

        def handler(req):
            captured_payloads.append(json.loads(req.content))
            return httpx.Response(200, json={"results": []})

        self._patch_client(monkeypatch, handler)
        await server.search_docs("q", top_k=100)
        assert captured_payloads[-1]["top_k"] == 20

        await server.search_docs("q", top_k=-5)
        assert captured_payloads[-1]["top_k"] == 1


class TestRAGStatus:
    def _patch_client(self, monkeypatch, handler):
        import r4_mcp.servers.rag_proxy as mod

        _orig = httpx.AsyncClient

        class PatchedClient(_orig):
            def __init__(self, **kwargs):
                kwargs.pop("timeout", None)
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(
            mod,
            "httpx",
            type(
                "M",
                (),
                {
                    "AsyncClient": PatchedClient,
                    "TimeoutException": httpx.TimeoutException,
                },
            )(),
        )

    @pytest.mark.asyncio
    async def test_rag_status_reachable_without_documents(self, server, monkeypatch):
        self._patch_client(monkeypatch, lambda req: httpx.Response(200, json=[]))

        result = await server.rag_status()

        assert not result.is_error
        assert "RAG reachable" in result.content
        assert "No documents indexed" in result.content

    @pytest.mark.asyncio
    async def test_rag_status_no_endpoint_has_actionable_hint(self, server, monkeypatch):
        self._patch_client(
            monkeypatch,
            lambda req: httpx.Response(503, text="No active endpoint available."),
        )

        result = await server.rag_status()

        assert result.is_error
        assert "r4 endpoint up" in result.content
