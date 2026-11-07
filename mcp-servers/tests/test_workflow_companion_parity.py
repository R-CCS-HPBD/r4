"""Contract-equivalent payload parity between MCP and Companion workflow tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowResult

from r4_mcp.servers.platform import PlatformServer

# mcp-servers does not depend on r4; the self-hosted CI runner has it installed
# (shared interpreter), a clean `pip install -e mcp-servers` does not.
build_workflow_tools = pytest.importorskip("r4.companion.workflow_tools").build_workflow_tools

RUN_BODY = {
    "execution_id": "exec-42",
    "workflow_name": "alpha",
    "status": "submitted",
    "artifacts": ["report:alpha.md"],
    "outputs": {"score": 1.0},
    "error": None,
}

RESULT_KEYS = frozenset(
    {"execution_id", "workflow_name", "status", "artifacts", "outputs", "error"}
)
COMPANION_EXTRA_KEYS = frozenset({"site", "handle"})


def _companion_run_payload(client) -> dict:
    tools = {tool["name"]: tool["execute"] for tool in build_workflow_tools(client)}
    return json.loads(
        tools["workflow_run"](
            workflow_name="alpha",
            parameters='{"bucket": "demo"}',
        )
    )


@pytest.mark.asyncio
async def test_workflow_run_payload_matches_companion_tool(monkeypatch):
    server = PlatformServer(gateway_url="http://gateway-test:8000", api_key="test-key")

    async def mock_request(method, path, **kwargs):
        assert method == "POST"
        assert path.endswith("/gw/workflows/alpha/trigger")
        return {"status": 200, "body": dict(RUN_BODY)}

    monkeypatch.setattr(server, "_request", mock_request)
    # S5: workflow_run is generated from the manifest (a registered tool, not a method).
    run = server._tools["workflow_run"].handler
    mcp_result = await run(workflow_name="alpha", parameters={"bucket": "demo"})
    mcp_payload = json.loads(mcp_result.content)

    client = MagicMock()
    client.config = SimpleNamespace(project_id="project-alpha")
    client.workflows.trigger.return_value = WorkflowResult(
        execution_id=RUN_BODY["execution_id"],
        workflow_name=RUN_BODY["workflow_name"],
        status=RUN_BODY["status"],
        artifacts=list(RUN_BODY["artifacts"]),
        outputs=dict(RUN_BODY["outputs"]),
        error=RUN_BODY["error"],
    )
    companion_payload = _companion_run_payload(client)

    assert set(mcp_payload.keys()) >= RESULT_KEYS
    # The companion tags every platform result with the site it ran on
    # and its id@site handle (multi-HPC S3); nothing else goes beyond the contract.
    assert set(companion_payload.keys()) - RESULT_KEYS == COMPANION_EXTRA_KEYS
    assert set(companion_payload.keys()) >= RESULT_KEYS
    for key in RESULT_KEYS:
        assert companion_payload[key] == mcp_payload.get(key)
