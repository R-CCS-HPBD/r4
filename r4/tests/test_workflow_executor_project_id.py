"""R4ClientWorkflowExecutor must pass research project_id to workflows.trigger."""

from __future__ import annotations

from types import SimpleNamespace

from r4.research.workflow_executor import R4ClientWorkflowExecutor


class _FakeWorkflows:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def trigger(self, workflow_name, parameters=None, project_id=None, **kwargs):
        self.calls.append(
            {
                "workflow_name": workflow_name,
                "parameters": dict(parameters or {}),
                "project_id": project_id,
            }
        )
        return SimpleNamespace(
            execution_id="exec-1",
            durable_job_id="job-1",
            status="accepted",
        )


class _FakeClient:
    def __init__(self) -> None:
        self.workflows = _FakeWorkflows()


def test_trigger_forwards_params_project_id() -> None:
    client = _FakeClient()
    executor = R4ClientWorkflowExecutor(client, require_durable=True)
    eid = executor.trigger(
        "demo08_yield_eval",
        {"project_id": "demo08-nl", "iteration": 1},
    )
    assert eid == "exec-1"
    assert client.workflows.calls[0]["project_id"] == "demo08-nl"
    assert client.workflows.calls[0]["parameters"]["project_id"] == "demo08-nl"
