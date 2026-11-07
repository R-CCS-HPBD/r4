from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def _mock_http_client_response(
    payload, status_code: int = 200, headers: dict | None = None, text: str = ""
):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text
    response.json.return_value = payload
    return response


def test_list_tools_accepts_bare_list_and_input_schema():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        [
            {
                "name": "hello_world",
                "description": "Say hello",
                "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
                "server": "demo-server",
            }
        ]
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.get.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        tools = client.mcp.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "hello_world"
    assert tools[0].parameters["type"] == "object"
    assert tools[0].server == "demo-server"


def test_list_servers_accepts_bare_list_payload():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        [
            {
                "id": "demo-server",
                "server_id": "demo-server",
                "name": "demo-server",
                "status": "healthy",
                "url": "http://demo-server.local",
                "tools": [],
            }
        ]
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.get.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        servers = client.mcp.list_servers()

    assert servers[0]["name"] == "demo-server"
    assert servers[0]["server_id"] == "demo-server"


def test_invoke_normalizes_gateway_tool_call_response():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        {
            "content": [{"type": "text", "text": "Hello from MCP"}],
            "isError": False,
            "server": "demo-server",
        }
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        result = client.mcp.invoke("hello_world", name="Copilot")

    assert result.success is True
    assert result.result == "Hello from MCP"
    assert result.error is None


def test_invoke_forwards_chain_metadata():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        {
            "content": [{"type": "text", "text": "Hello from MCP"}],
            "isError": False,
            "server": "demo-server",
        }
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        client.mcp.invoke(
            "hello_world",
            _meta={"chain_id": "chain-1", "target_tool": "summarize"},
            name="Copilot",
        )

    payload = http_client.post.call_args.kwargs["json"]
    assert payload["arguments"] == {"name": "Copilot"}
    assert payload["_meta"] == {"chain_id": "chain-1", "target_tool": "summarize"}


def test_workflow_executor_forwards_mcp_chain_metadata():
    from r4.agent.core import AgentConfig, WorkflowExecutor
    from r4.agent.egress import EgressDecision
    from r4.agent.models import StepType, WorkflowDefinition, WorkflowStep
    from r4.mcp import MCPToolResult

    calls = []

    class DummyMCP:
        def invoke(self, tool_name, **kwargs):
            calls.append({"tool_name": tool_name, "kwargs": kwargs})
            return MCPToolResult(tool_name=tool_name, result={"ok": True})

    class DummyClient:
        mcp = DummyMCP()

    class AllowGuard:
        def check(self, **kwargs):
            return EgressDecision.APPROVED

    workflow = WorkflowDefinition.create(
        name="demo",
        description="demo workflow",
        project_id="project-a",
        owner_user_id="user-a",
    )
    workflow.add_step(
        WorkflowStep(
            id="step-1",
            description="search",
            step_type=StepType.MCP_CALL,
            tool_name="search",
            config={
                "arguments": {"query": "test"},
                "target_tool": "summarize",
            },
        )
    )
    executor = WorkflowExecutor(DummyClient(), AgentConfig(), AllowGuard())

    execution = executor.execute_workflow(workflow, inputs={})

    metadata = calls[0]["kwargs"]["_meta"]
    assert metadata["chain_id"] == execution.execution_id
    assert metadata["workflow_step_id"] == "step-1"
    assert metadata["target_tool"] == "summarize"


def test_invoke_normalizes_gateway_error_response():
    from r4.client import Client
    from r4.config import Config

    response = _mock_http_client_response(
        {
            "content": [{"type": "text", "text": "Tool failed"}],
            "isError": True,
            "server": "demo-server",
        }
    )

    with patch("httpx.Client") as mock_http_client:
        http_client = mock_http_client.return_value.__enter__.return_value
        http_client.post.return_value = response

        client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
        result = client.mcp.invoke("hello_world", name="Copilot")

    assert result.success is False
    assert result.result is None
    assert result.error == "Tool failed"
