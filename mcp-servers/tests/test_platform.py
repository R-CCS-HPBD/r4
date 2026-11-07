"""Tests for PlatformServer MCP tools."""

from __future__ import annotations

import json

import httpx
import pytest

from r4_mcp.servers.platform import PlatformServer


@pytest.fixture
def server():
    """Create a PlatformServer with a test gateway URL."""
    return PlatformServer(
        gateway_url="http://gateway-test:8000",
        api_key="test-key",
        timeout_seconds=5,
    )


# ── Unit tests: tool discovery ───────────────────────────────────────


class TestToolDiscovery:
    """Verify all expected tools are registered."""

    EXPECTED_TOOLS = [
        "endpoint_list",
        "endpoint_status",
        "endpoint_provision",
        "endpoint_stop",
        "job_list",
        "job_status",
        "job_cancel",
        "workflow_list",
        "workflow_describe",
        "workflow_run",
        "workflow_trigger",
        "workflow_status",
        "workflow_wait",
        "workflow_results",
        "workflow_cancel",
        "workflow_upload",
        "model_list",
        "model_get",
        "platform_health",
        "platform_guide",
        # ADR-105 D-2 (S5): manifest rows new to MCP (names = operation_id).
        "job_events",
        "job_retry",
        "workflow_plan",
        "rag_query",
        "rag_list_documents",
    ]

    def test_all_tools_registered(self, server: PlatformServer):
        tool_names = [t.name for t in server.tools]
        for expected in self.EXPECTED_TOOLS:
            assert expected in tool_names, f"Missing tool: {expected}"

    def test_tool_count(self, server: PlatformServer):
        assert len(server.tools) == len(self.EXPECTED_TOOLS)

    def test_tools_have_descriptions(self, server: PlatformServer):
        for tool in server.tools:
            assert tool.description, f"Tool {tool.name} has no description"

    def test_mcp_format(self, server: PlatformServer):
        for tool in server.tools:
            fmt = tool.to_mcp_format()
            assert "name" in fmt
            assert "description" in fmt
            assert "inputSchema" in fmt
            assert fmt["inputSchema"]["type"] == "object"


# ── Unit tests: initialization ───────────────────────────────────────


class TestInitialization:
    def test_gateway_url_trailing_slash_stripped(self):
        srv = PlatformServer(gateway_url="http://host:8000/")
        assert srv._gateway_url == "http://host:8000"

    def test_headers_with_api_key(self, server: PlatformServer):
        headers = server._build_headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Accept"] == "application/json"

    def test_headers_without_api_key(self, monkeypatch):
        monkeypatch.delenv("R4_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("R4_API_KEY", raising=False)
        srv = PlatformServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert "Authorization" not in headers

    def test_headers_with_env_gateway_token(self, monkeypatch):
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "env-token")
        monkeypatch.delenv("R4_API_KEY", raising=False)
        srv = PlatformServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer env-token"

    def test_headers_with_env_api_key(self, monkeypatch):
        monkeypatch.delenv("R4_GATEWAY_TOKEN", raising=False)
        monkeypatch.setenv("R4_API_KEY", "env-api-key")
        srv = PlatformServer(gateway_url="http://host:8000")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer env-api-key"

    def test_explicit_api_key_precedes_env(self, monkeypatch):
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "env-token")
        srv = PlatformServer(gateway_url="http://host:8000", api_key="explicit-token")
        headers = srv._build_headers()
        assert headers["Authorization"] == "Bearer explicit-token"

    def test_server_metadata(self, server: PlatformServer):
        assert server.name == "platform"
        assert server.version == "1.0.0"


# ── Unit tests: response formatting ─────────────────────────────────


class TestFormatting:
    def test_success_format(self, server: PlatformServer):
        result = server._format({"status": 200, "body": {"ok": True}})
        assert not result.is_error
        parsed = json.loads(result.content)
        assert parsed["ok"] is True

    def test_error_format(self, server: PlatformServer):
        result = server._format({"status": 500, "body": {"detail": "fail"}})
        assert result.is_error
        assert "500" in result.content


# ── Integration tests: tool handlers (mocked HTTP) ──────────────────


class TestToolHandlers:
    """Test tool handlers with mocked HTTP transport."""

    @pytest.fixture
    def mock_transport(self):
        """Create a mock httpx transport that returns canned responses."""
        responses: dict[str, httpx.Response] = {}

        class _Transport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                path = request.url.path
                for pattern, resp in responses.items():
                    if pattern in path:
                        return resp
                return httpx.Response(404, json={"detail": "not found"})

        return _Transport(), responses

    async def _call_tool(self, server, tool_name, arguments, mock_transport):
        """Call a tool via the MCP handle_call_tool interface."""
        transport_obj, responses = mock_transport
        # Monkey-patch _request to use mock transport
        original_request = server._request

        async def patched_request(method, path, *, params=None, json_body=None):
            url = f"{server._gateway_url}{path}"
            if params:
                params = {k: v for k, v in params.items() if v is not None}
            async with httpx.AsyncClient(transport=transport_obj) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=server._build_headers(),
                    params=params or None,
                    json=json_body,
                )
                return {"status": resp.status_code, "body": resp.json()}

        server._request = patched_request
        try:
            result = await server.handle_call_tool({"name": tool_name, "arguments": arguments})
            return result
        finally:
            server._request = original_request

    @pytest.mark.asyncio
    async def test_endpoint_list(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/endpoints"] = httpx.Response(
            200,
            json={"endpoints": [{"job_id": "j1", "model": "test", "status": "ready"}], "total": 1},
        )
        result = await self._call_tool(server, "endpoint_list", {}, mock_transport)
        assert not result["isError"]
        body = json.loads(result["content"][0]["text"])
        assert body["total"] == 1

    @pytest.mark.asyncio
    async def test_endpoint_status(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/endpoints/j1"] = httpx.Response(
            200, json={"job_id": "j1", "status": "ready"}
        )
        result = await self._call_tool(server, "endpoint_status", {"job_id": "j1"}, mock_transport)
        assert not result["isError"]
        body = json.loads(result["content"][0]["text"])
        assert body["status"] == "ready"

    @pytest.mark.asyncio
    async def test_endpoint_provision(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/endpoints/provision"] = httpx.Response(
            202, json={"job_id": "j2", "status": "QUEUED"}
        )
        result = await self._call_tool(
            server,
            "endpoint_provision",
            {"model": "Qwen/Qwen2.5-7B-Instruct"},
            mock_transport,
        )
        assert not result["isError"]
        body = json.loads(result["content"][0]["text"])
        assert body["job_id"] == "j2"

    @pytest.mark.asyncio
    async def test_endpoint_stop(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/endpoints/j1"] = httpx.Response(200, json={"message": "deprovisioned"})
        result = await self._call_tool(server, "endpoint_stop", {"job_id": "j1"}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_job_list(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/jobs"] = httpx.Response(200, json={"jobs": [], "total": 0})
        result = await self._call_tool(server, "job_list", {}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_job_status(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/jobs/j1"] = httpx.Response(
            200,
            json={"job_id": "j1", "status": "RUNNING", "attempts": []},
        )
        result = await self._call_tool(server, "job_status", {"job_id": "j1"}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_job_cancel(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/jobs/j1/cancel"] = httpx.Response(200, json={"message": "cancelled"})
        result = await self._call_tool(server, "job_cancel", {"job_id": "j1"}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_workflow_list(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/workflows"] = httpx.Response(200, json={"workflows": []})
        result = await self._call_tool(server, "workflow_list", {}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_workflow_trigger(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/workflows/build-index/trigger"] = httpx.Response(
            200, json={"run_id": "r1", "status": "started"}
        )
        result = await self._call_tool(
            server,
            "workflow_trigger",
            {"workflow_name": "build-index", "parameters": {"k": "v"}},
            mock_transport,
        )
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_model_list(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/models"] = httpx.Response(
            200, json={"models": [{"name": "qwen3-8b", "scope": "system"}]}
        )
        result = await self._call_tool(server, "model_list", {}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_model_get(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/models/qwen3-8b"] = httpx.Response(
            200, json={"name": "qwen3-8b", "scope": "system"}
        )
        result = await self._call_tool(
            server, "model_get", {"model_name": "qwen3-8b"}, mock_transport
        )
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_platform_health(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/endpoints/health"] = httpx.Response(
            200, json={"status": "healthy", "endpoints": 3}
        )
        result = await self._call_tool(server, "platform_health", {}, mock_transport)
        assert not result["isError"]

    @pytest.mark.asyncio
    async def test_error_response(self, server, mock_transport):
        _, responses = mock_transport
        responses["/gw/jobs/bad"] = httpx.Response(404, json={"detail": "Job not found"})
        result = await self._call_tool(server, "job_status", {"job_id": "bad"}, mock_transport)
        assert result["isError"]
        assert "404" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_unknown_tool(self, server):
        result = await server.handle_call_tool({"name": "nonexistent", "arguments": {}})
        assert result["isError"]
        assert "Unknown tool" in result["content"][0]["text"]
