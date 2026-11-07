"""Unit tests for Companion workflow_* tool executors."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import (
    WorkflowCatalogItem,
    WorkflowResult,
    WorkflowStatus,
    WorkflowUploadResult,
)

from r4.companion.workflow_tools import build_workflow_tools


def _executors(client) -> dict[str, object]:
    return {tool["name"]: tool["execute"] for tool in build_workflow_tools(client)}


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.config = SimpleNamespace(project_id="project-alpha")
    client.workflows.list_workflows.return_value = [
        WorkflowCatalogItem(
            workflow_name="alpha",
            name="alpha",
            description="demo workflow",
            tags=["demo"],
        )
    ]
    client.workflows.describe.return_value = {
        "workflow_name": "alpha",
        "parameter_schema": {"type": "object", "required": ["bucket"]},
    }
    client.workflows.trigger.return_value = WorkflowResult(
        execution_id="exec-42",
        workflow_name="alpha",
        status="submitted",
        artifacts=[],
        outputs={},
        error=None,
    )
    client.workflows.status.return_value = WorkflowStatus(
        execution_id="exec-42",
        workflow_name="alpha",
        status="running",
        progress=0.5,
    )
    client.workflows.wait_for_completion.return_value = WorkflowResult(
        execution_id="exec-42",
        workflow_name="alpha",
        status="succeeded",
        artifacts=["report:alpha.md"],
        outputs={"score": 1.0},
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


def test_workflow_list_returns_catalog(mock_client):
    payload = json.loads(_executors(mock_client)["workflow_list"]())
    assert payload["count"] == 1
    assert payload["workflows"][0]["workflow_name"] == "alpha"


def test_workflow_describe_returns_schema(mock_client):
    payload = json.loads(_executors(mock_client)["workflow_describe"](workflow_name="alpha"))
    assert payload["workflow_name"] == "alpha"
    assert payload["parameter_schema"]["required"] == ["bucket"]


def test_workflow_run_returns_execution(mock_client):
    payload = json.loads(
        _executors(mock_client)["workflow_run"](
            workflow_name="alpha",
            parameters='{"bucket": "demo"}',
        )
    )
    assert payload["execution_id"] == "exec-42"
    assert payload["status"] == "submitted"


def test_workflow_trigger_alias_delegates_to_run(mock_client):
    payload = json.loads(
        _executors(mock_client)["workflow_trigger"](
            workflow_name="alpha",
            parameters='{"bucket": "demo"}',
        )
    )
    assert payload["execution_id"] == "exec-42"


def test_workflow_status_wait_results_and_cancel(mock_client):
    tools = _executors(mock_client)
    status = json.loads(tools["workflow_status"](execution_id="exec-42"))
    wait = json.loads(tools["workflow_wait"](execution_id="exec-42"))
    results = json.loads(tools["workflow_results"](execution_id="exec-42"))
    cancel = json.loads(tools["workflow_cancel"](execution_id="exec-42"))

    assert status["status"] == "running"
    assert wait["status"] == "succeeded"
    assert wait["artifacts"] == ["report:alpha.md"]
    assert results["artifacts"] == ["report:alpha.md"]
    assert cancel["status"] == "cancelled"


def test_workflow_upload_returns_acceptance(mock_client, monkeypatch, tmp_path):
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
    payload = json.loads(
        _executors(mock_client)["workflow_upload"](
            workflow_path="/tmp/alpha.zip",
            workflow_name="alpha",
        )
    )
    assert payload["accepted"] is True
    assert payload["workflow_name"] == "alpha"


def test_workflow_run_rejects_invalid_parameters_json(mock_client):
    payload = json.loads(
        _executors(mock_client)["workflow_run"](
            workflow_name="alpha",
            parameters="not-json",
        )
    )
    assert payload["error"] == "workflow_error"
    assert payload["code"] == "invalid_json"


def test_workflow_run_policy_denied_when_mutations_disabled(mock_client, monkeypatch, tmp_path):
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
                    "run": False,
                    "cancel": False,
                    "upload": False,
                },
            }
        )
    )
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(policy_file))
    payload = json.loads(_executors(mock_client)["workflow_run"](workflow_name="alpha"))
    assert payload["error"] == "policy_denied"
    assert payload["code"] == "workflow_operation_disabled"
