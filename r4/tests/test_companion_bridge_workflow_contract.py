"""Pre-6b contract smoke: workflow.list RPC and slash parity through the bridge."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from r4.companion.permissions import PermissionManager
from r4.companion.session_models import SessionMessage, UsageSummary
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import CompanionBridgeServer


class FakeStore:
    def __init__(self, session):
        self.session = session
        self.saved = []

    def create_session(self, client, name=None, *, model=None):
        self.session.client = client
        if model:
            self.session.agent.model = model
        return self.session

    def load(self, session_id, client):
        if session_id != self.session.session_id:
            return None
        self.session.client = client
        return self.session

    def save(self, session):
        self.saved.append(session.session_id)

    def list_sessions(self, limit=50):
        return [
            {
                "id": self.session.session_id,
                "name": self.session.name,
                "created_at": self.session.created_at,
                "updated_at": self.session.updated_at,
                "model": self.session.agent.model,
            }
        ][:limit]


def _fake_session() -> SimpleNamespace:
    client = SimpleNamespace(
        config=SimpleNamespace(
            profile="research",
            gateway_url="http://gateway.test",
            project_id="demo",
            model="config-model",
            endpoint_url=None,
            endpoint_provider=None,
        ),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-test",
                        "status": "ready",
                        "model": "test-model",
                    }
                ]
            }
        ),
    )
    messages: list[SessionMessage] = []

    def send(text: str, **_kwargs: Any) -> str:
        messages.append(SessionMessage(role="user", content=text))
        messages.append(SessionMessage(role="assistant", content="answer"))
        return "answer"

    return SimpleNamespace(
        session_id="session-1",
        name="Demo",
        client=client,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        agent=SimpleNamespace(model="test-model"),
        messages=messages,
        skills=SkillRegistry(),
        permissions=PermissionManager(),
        context=SimpleNamespace(_memory={}),
        usage_summary=UsageSummary(),
        active_endpoints=[],
        metadata={},
        _harness=None,
        send=send,
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
        export_markdown=MagicMock(return_value="# Demo"),
    )


def _workflow_records() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            workflow_id="wf-alpha",
            name="alpha",
            description="Alpha workflow",
            tags=["demo"],
        ),
        SimpleNamespace(
            workflow_id=None,
            name="beta",
            description="Beta workflow",
            tags=[],
        ),
    ]


def _bridge_with_workflows() -> CompanionBridgeServer:
    session = _fake_session()
    workflow_client = MagicMock()
    workflow_client.list_workflows.return_value = _workflow_records()
    workflow_client.status.return_value = SimpleNamespace(
        execution_id="exec-42",
        workflow_name="alpha",
        workflow_id="wf-alpha",
        status="running",
        progress="50%",
        error=None,
    )
    workflow_client.trigger.return_value = SimpleNamespace(
        execution_id="exec-99",
        workflow_name="alpha",
        workflow_id="wf-alpha",
        status="queued",
        artifacts=["out/result.json"],
        outputs={"score": 0.9},
        error=None,
    )
    workflow_client.cancel.return_value = SimpleNamespace(
        execution_id="exec-42",
        workflow_name="alpha",
        workflow_id="wf-alpha",
        status="cancelled",
        progress=None,
        error=None,
    )
    workflow_client.definition_package.return_value = {
        "workflow_name": "alpha",
        "steps": [{"name": "seg", "depends_on": []}],
        "resources": {"gpu_count": 1},
        "files": [
            {
                "path": "steps/seg.py",
                "bytes": 8,
                "lines": 1,
                "content": "x = 1\n",
                "truncated": False,
            }
        ],
        "files_truncated": False,
    }
    workflow_client.get_results.return_value = SimpleNamespace(
        execution_id="exec-42",
        workflow_name="alpha",
        workflow_id="wf-alpha",
        status="completed",
        artifacts=["out/result.json"],
        outputs={"score": 0.9},
        error=None,
    )
    session.client.workflows = workflow_client
    bridge = CompanionBridgeServer(client=session.client, store=FakeStore(session))
    bridge.initialize({})
    return bridge


def _workflow_names_from_rpc(result: dict) -> set[str]:
    return {item["workflow_name"] for item in result["workflows"]}


def _workflow_names_from_slash_output(output: list[dict]) -> set[str]:
    names: set[str] = set()
    for line in output:
        text = str(line.get("text", "")).strip()
        if not text or text.startswith("("):
            continue
        parts = text.split()
        if parts:
            names.add(parts[0])
    return names


def test_workflow_list_rpc_returns_catalog() -> None:
    bridge = _bridge_with_workflows()

    response = bridge.handle_request({"id": 1, "method": "workflow.list", "params": {}})

    assert response["ok"] is True
    result = response["result"]
    assert result["count"] == 2
    assert _workflow_names_from_rpc(result) == {"alpha", "beta"}
    assert result["workflows"][0]["tags"] == ["demo"]


def test_workflow_list_rpc_matches_slash_list() -> None:
    bridge = _bridge_with_workflows()

    rpc_response = bridge.handle_request({"id": 1, "method": "workflow.list", "params": {}})
    slash_result = bridge.dispatch_slash_command({"command": "/workflow list"})

    assert slash_result["handled"] is True
    rpc_names = _workflow_names_from_rpc(rpc_response["result"])
    slash_names = _workflow_names_from_slash_output(slash_result["output"])
    assert rpc_names == slash_names


def test_workflow_status_slash_round_trip() -> None:
    bridge = _bridge_with_workflows()
    workflow_client = bridge.session.client.workflows

    result = bridge.dispatch_slash_command({"command": "/workflow status exec-42"})

    assert result["handled"] is True
    text = "\n".join(line["text"] for line in result["output"])
    assert "exec-42" in text
    assert "running" in text
    workflow_client.status.assert_called_once_with("exec-42")


def test_list_slash_commands_includes_workflow_subcommands() -> None:
    bridge = _bridge_with_workflows()

    result = bridge.list_slash_commands({})
    workflow_cmd = next(c for c in result["commands"] if c["name"] == "workflow")

    assert set(workflow_cmd["subcommands"]) == {
        "list",
        "trigger",
        "upload",
        "delete",
        "status",
        "cancel",
    }


def test_workflow_status_rpc_matches_slash() -> None:
    bridge = _bridge_with_workflows()

    rpc_response = bridge.handle_request(
        {"id": 2, "method": "workflow.status", "params": {"execution_id": "exec-42"}},
    )
    slash_result = bridge.dispatch_slash_command({"command": "/workflow status exec-42"})

    assert rpc_response["ok"] is True
    assert rpc_response["result"]["execution_id"] == "exec-42"
    assert rpc_response["result"]["status"] == "running"
    assert slash_result["handled"] is True
    slash_text = "\n".join(line["text"] for line in slash_result["output"])
    assert "exec-42" in slash_text
    assert "running" in slash_text


def test_workflow_run_rpc_triggers_workflow() -> None:
    bridge = _bridge_with_workflows()
    workflow_client = bridge.session.client.workflows

    response = bridge.handle_request(
        {
            "id": 3,
            "method": "workflow.run",
            "params": {"workflow_name": "alpha", "parameters": {"k": "v"}},
        },
    )

    assert response["ok"] is True
    assert response["result"]["execution_id"] == "exec-99"
    assert response["result"]["status"] == "queued"
    workflow_client.trigger.assert_called_once_with(
        "alpha",
        parameters={"k": "v"},
        input_files=None,
        project_id=None,
        wait=False,
    )


def test_workflow_package_rpc_passes_definition_through() -> None:
    """2026-10-01: the work-pane definition card rides one RPC."""
    bridge = _bridge_with_workflows()
    response = bridge.handle_request(
        {"id": 9, "method": "workflow.package", "params": {"workflow_name": "alpha"}},
    )
    assert response["ok"] is True
    result = response["result"]
    assert result["workflow_name"] == "alpha"
    assert result["steps"][0]["name"] == "seg"
    assert result["resources"] == {"gpu_count": 1}
    assert result["files"][0]["content"] == "x = 1\n"


def test_workflow_package_rpc_requires_a_name() -> None:
    bridge = _bridge_with_workflows()
    response = bridge.handle_request(
        {"id": 10, "method": "workflow.package", "params": {"workflow_name": ""}},
    )
    assert response["ok"] is False
    assert "workflow_name is required" in response["error"]["message"]


def test_workflow_cancel_rpc_returns_ok() -> None:
    bridge = _bridge_with_workflows()
    workflow_client = bridge.session.client.workflows

    response = bridge.handle_request(
        {"id": 4, "method": "workflow.cancel", "params": {"execution_id": "exec-42"}},
    )

    assert response["ok"] is True
    assert response["result"]["ok"] is True
    assert response["result"]["status"] == "cancelled"
    workflow_client.cancel.assert_called_once_with("exec-42")


def test_workflow_results_rpc_returns_payload() -> None:
    bridge = _bridge_with_workflows()
    workflow_client = bridge.session.client.workflows

    response = bridge.handle_request(
        {"id": 5, "method": "workflow.results", "params": {"execution_id": "exec-42"}},
    )

    assert response["ok"] is True
    assert response["result"]["status"] == "completed"
    assert response["result"]["artifacts"] == ["out/result.json"]
    workflow_client.get_results.assert_called_once_with("exec-42")
