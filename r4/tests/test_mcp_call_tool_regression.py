"""Regression tests for ``MCPClient.call_tool`` (chat tool-calling bug).

Bug: agent session, LangChain integration, and mcp_use integration all call
``client.mcp.call_tool(name, arguments, server)`` — the method did not exist,
so every chat tool call failed with::

    'MCPClient' object has no attribute 'call_tool'

``call_tool`` hits the gateway ``/gw/mcp/call`` route and returns an
:class:`MCPToolCallResult` with raw MCP content blocks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def _mock_http_client_response(payload, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.text = ""
    response.json.return_value = payload
    return response


def test_call_tool_exists_on_mcpclient():
    from r4.mcp import MCPClient

    assert hasattr(MCPClient, "call_tool"), (
        "MCPClient.call_tool missing — chat agent session dispatches this method"
    )


def _run_call_tool(payload, *, arguments=None, server=None):
    """Invoke call_tool against a stubbed gateway; return (result, post_call)."""
    from r4.client import Client
    from r4.config import Config

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = _mock_http_client_response(payload)

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        result = client.mcp.call_tool("ping", arguments, server)

    return result, http_client.post.call_args


def test_call_tool_posts_to_gw_mcp_call_and_parses_response():
    result, call = _run_call_tool(
        {"content": [{"type": "text", "text": "pong"}], "isError": False, "server": "platform"},
        arguments={"arg": 1},
        server="platform",
    )
    assert call.args[0] == "http://gateway.test/gw/mcp/call"
    wire = call.kwargs["json"]
    assert wire == {"tool": "ping", "arguments": {"arg": 1}}

    assert result.content == [{"type": "text", "text": "pong"}]
    assert result.is_error is False
    assert result.server == "platform"
    assert result.error is None
    assert result.success is True


def test_call_tool_default_arguments_payload():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response({"content": [], "isError": False, "server": ""})

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        client.mcp.call_tool("health")

    payload = http_client.post.call_args.kwargs["json"]
    assert payload == {"tool": "health", "arguments": {}}


def test_call_tool_maps_gateway_error_block():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        {"content": [{"type": "text", "text": "boom"}], "isError": True, "server": "platform"}
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        result = client.mcp.call_tool("health")

    assert result.is_error is True
    assert result.success is False
    assert result.error == "boom"


def test_call_tool_transport_failure_is_error_result():
    from r4.client import Client
    from r4.config import Config
    from r4.errors import R4Error

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.side_effect = R4Error("gateway down")

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        result = client.mcp.call_tool("health")

    assert result.is_error is True
    assert result.error and "gateway down" in result.error


def test_agent_session_execute_mcp_tool_uses_call_tool():
    """AgentSession._execute_mcp_tool must not raise AttributeError."""
    from r4.agent.session import AgentSession
    from r4.mcp import MCPToolCallResult

    fake_mcp = MagicMock()
    fake_mcp.server_names = ["platform"]
    fake_mcp.call_tool.return_value = MCPToolCallResult(
        content=[{"type": "text", "text": "ok"}], is_error=False, server="platform"
    )

    session = AgentSession.__new__(AgentSession)
    session.client = MagicMock()
    session.client.mcp = fake_mcp
    session._agent = MagicMock()

    out = session._execute_mcp_tool("health", {})

    fake_mcp.call_tool.assert_called_once_with("health", {}, "platform")
    assert "ok" in out


def test_agent_session_execute_mcp_tool_defaults_server_name():
    from r4.agent.session import AgentSession
    from r4.mcp import MCPToolCallResult

    fake_mcp = MagicMock(spec=[])  # no server_names attribute at all
    fake_mcp.call_tool = MagicMock(
        return_value=MCPToolCallResult(content=[{"type": "text", "text": "x"}])
    )

    session = AgentSession.__new__(AgentSession)
    session.client = MagicMock()
    session.client.mcp = fake_mcp
    session._agent = MagicMock()

    out = session._execute_mcp_tool("health", {})

    fake_mcp.call_tool.assert_called_once_with("health", {}, "default")
    assert "x" in out
