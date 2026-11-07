"""Stub golden-path tests for Companion workflow lifecycle tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from r4_contracts.workflows import WorkflowResult, WorkflowUploadResult

from r4.companion.workflow_tools import build_workflow_tools


def _workflow_tools(client) -> dict[str, object]:
    return {tool["name"]: tool["execute"] for tool in build_workflow_tools(client)}


@pytest.fixture
def workflow_client():
    client = MagicMock()
    client.config = SimpleNamespace(
        profile="default",
        gateway_url="http://gateway.test",
        project_id="project-alpha",
        model="test-model",
    )
    client._config.model = "test-model"
    client.list_endpoints.return_value = {
        "endpoints": [{"instance_id": "ep-test", "model": "test-model", "status": "ready"}],
        "total": 1,
    }
    client.workflows.describe.return_value = {
        "workflow_name": "alpha",
        "parameter_schema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}},
            "required": ["bucket"],
        },
    }
    client.workflows.trigger.return_value = WorkflowResult(
        execution_id="exec-42",
        workflow_name="alpha",
        status="submitted",
        artifacts=[],
        outputs={},
        error=None,
    )
    client.workflows.wait_for_completion.return_value = WorkflowResult(
        execution_id="exec-42",
        workflow_name="alpha",
        status="succeeded",
        artifacts=["report:alpha.md", "data:results.csv"],
        outputs={"score": 0.99},
        error=None,
    )
    client.workflows.get_results.return_value = client.workflows.wait_for_completion.return_value
    client.workflows.cancel.return_value = WorkflowResult(
        execution_id="exec-42",
        workflow_name="alpha",
        status="cancelled",
        error=None,
    )
    client.workflows.upload.return_value = WorkflowUploadResult(
        workflow_name="alpha",
        status="accepted",
        accepted=True,
        source_path="/tmp/alpha",
        message="uploaded",
        error=None,
    )
    return client


def test_workflow_lifecycle_tool_chain_returns_artifacts(workflow_client):
    """describe → run → wait → results without a live LLM."""
    tools = _workflow_tools(workflow_client)

    describe = json.loads(tools["workflow_describe"](workflow_name="alpha"))
    run = json.loads(
        tools["workflow_run"](
            workflow_name="alpha",
            parameters='{"bucket": "demo-bucket"}',
        )
    )
    wait = json.loads(tools["workflow_wait"](execution_id=run["execution_id"]))
    results = json.loads(tools["workflow_results"](execution_id=run["execution_id"]))

    assert describe["parameter_schema"]["required"] == ["bucket"]
    assert run["execution_id"] == "exec-42"
    assert wait["status"] == "succeeded"
    assert "report:alpha.md" in wait["artifacts"]
    assert results["artifacts"] == wait["artifacts"]


def test_workflow_cancel_path(workflow_client):
    tools = _workflow_tools(workflow_client)
    payload = json.loads(tools["workflow_cancel"](execution_id="exec-42"))
    assert payload["execution_id"] == "exec-42"
    assert payload["status"] == "cancelled"


def test_workflow_upload_allowed_by_default_policy(workflow_client):
    """Default posture: local policy allows upload; server role gate decides."""
    tools = _workflow_tools(workflow_client)
    payload = json.loads(
        tools["workflow_upload"](workflow_path="/tmp/alpha.zip", workflow_name="alpha")
    )
    assert payload["accepted"] is True


def test_workflow_upload_denied_by_restrictive_policy(workflow_client, monkeypatch, tmp_path):
    """The local guard remains a veto layer for operators (2026-08-30)."""
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps({"version": "1", "defaults": {"upload": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(policy_file))
    tools = _workflow_tools(workflow_client)
    payload = json.loads(
        tools["workflow_upload"](workflow_path="/tmp/alpha.zip", workflow_name="alpha")
    )
    assert payload["error"] == "policy_denied"
    assert payload["surface"] == "r4_companion.local_policy"


def test_workflow_upload_allowed_when_policy_enables_upload(workflow_client, monkeypatch, tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "version": "1",
                "defaults": {
                    "list": True,
                    "describe": True,
                    "status": True,
                    "results": True,
                    "wait": True,
                    "run": True,
                    "cancel": True,
                    "upload": True,
                },
            }
        )
    )
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(policy_file))
    tools = _workflow_tools(workflow_client)
    payload = json.loads(
        tools["workflow_upload"](workflow_path="/tmp/alpha.zip", workflow_name="alpha")
    )
    assert payload["accepted"] is True


def test_headless_turn_prints_agent_response(workflow_client, capsys):
    """Headless entry prints the final assistant line from a stubbed turn."""
    from r4.companion.ui import _launch_headless

    session_response = "Workflow alpha finished. Artifacts: report:alpha.md, data:results.csv"

    with patch("r4.companion.session.CompanionSession") as session_cls:
        session = MagicMock()
        session.skills.activate.return_value = True
        session.send.return_value = session_response
        session_cls.return_value = session
        with patch("r4.companion.session_store.CompanionSessionStore") as store_cls:
            store_cls.return_value.save = MagicMock()
            _launch_headless(
                client=workflow_client,
                prompt='run alpha with {"bucket": "demo-bucket"}',
                session_id=None,
                model=None,
                skill=None,
                system=None,
            )

    captured = capsys.readouterr()
    assert session_response in captured.out
    session.send.assert_called_once()
