"""MCP protocol round-trip test (bug.memo MCPS2, 2026-04-25).

Exercises the JSON-RPC handshake that ``r4 mcp register <url>`` triggers
against a real MCP server: ``initialize`` → ``tools/list`` → ``tools/call``.

This test is deliberately in-process (no gateway, no sockets) — it
validates the *protocol shape* we publish at ``/gw/mcp/*``, so that if
either ``base.py`` or the gateway MCP route drifts, the mismatch
surfaces quickly. A higher-fidelity gateway+MCP end-to-end already
lives in ``tests/test_mcp_apps_e2e.py``; this keeps the mcp-servers
unit suite honest on its own.
"""

from __future__ import annotations

import pytest

from r4_mcp.base import MCPServer, Tool, ToolResult


class _EchoServer(MCPServer):
    """Minimal MCP server that exposes a single ``echo`` tool."""

    name = "echo-test"
    version = "0.0.1"

    @Tool(
        name="echo",
        description="Echo the input message back",
        parameters={
            "message": {
                "type": "string",
                "description": "Message to echo",
                "required": True,
            },
        },
    )
    async def echo(self, message: str) -> ToolResult:
        return ToolResult.success(message)


@pytest.mark.asyncio
async def test_mcp_full_roundtrip() -> None:
    server = _EchoServer()

    init = await server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init is not None
    assert init["id"] == 1
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert init["result"]["serverInfo"]["name"] == "echo-test"

    listed = await server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert listed is not None
    names = [t["name"] for t in listed["result"]["tools"]]
    assert "echo" in names

    called = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hello"}},
        }
    )
    assert called is not None
    content = called["result"]["content"]
    assert any("hello" in str(c.get("text", "")) for c in content)


@pytest.mark.asyncio
async def test_unknown_method_returns_jsonrpc_error() -> None:
    server = _EchoServer()
    response = await server.handle_message(
        {"jsonrpc": "2.0", "id": 42, "method": "tools/nope", "params": {}}
    )
    assert response is not None
    assert response["id"] == 42
    assert response["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_content() -> None:
    server = _EchoServer()
    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "does-not-exist", "arguments": {}},
        }
    )
    assert response is not None
    result = response["result"]
    # Unknown tool is surfaced via the ToolResult error channel, not as a
    # JSON-RPC error, so the gateway can forward it to callers with the
    # normal content envelope.
    assert result.get("isError") is True
