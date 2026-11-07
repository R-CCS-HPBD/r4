"""Regression tests for agent workflow models."""

import threading
from unittest.mock import Mock

import pytest

from r4._http import HTTPCore
from r4.agent.core import (
    AgentCancellation,
    AgentCancelledError,
    AgentConfig,
    SciWorkflowAgent,
    WorkflowExecutor,
    WorkflowPlanner,
)
from r4.agent.egress import EgressDecision, EgressGuard
from r4.agent.models import (
    StepStatus,
    StepType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowInput,
    WorkflowOutput,
    WorkflowStatus,
    WorkflowStep,
)


def test_extract_json_object_handles_reasoning_and_fenced_json():
    from r4.agent.core import _extract_json_object

    content = """<think>planning</think>
```json
{"name": "health", "steps": [{"id": "step_1", "step_type": "SHELL_EXEC"}]}
```
"""

    assert _extract_json_object(content) == {
        "name": "health",
        "steps": [{"id": "step_1", "step_type": "SHELL_EXEC"}],
    }


def test_workflow_models_round_trip_with_agent_aliases():
    step = WorkflowStep(
        id="step_1",
        name="Collect context",
        description="Collect context from RAG",
        step_type=StepType.RAG_QUERY,
        tool_name="rag.search",
        inputs=[WorkflowInput(name="query", input_type="string", source="objective")],
        outputs=[WorkflowOutput(name="sources", output_type="json")],
        depends_on=["step_0"],
    )
    workflow = WorkflowDefinition(
        name="analysis",
        description="Round-trip workflow",
        project_id="proj-alpha",
        owner_user_id="alice",
        status=WorkflowStatus.READY,
        steps=[step],
    )

    serialized = workflow.to_dict()
    assert serialized["id"] == workflow.id
    assert serialized["steps"][0]["dependencies"] == ["step_0"]

    restored = WorkflowDefinition.from_dict(serialized)
    assert restored.steps[0].step_id == "step_1"
    assert restored.steps[0].dependencies == ["step_0"]
    assert restored.steps[0].tool_name == "rag.search"

    execution = WorkflowExecution.create(restored, user_id="alice", input_values={"query": "hello"})
    execution.record_step_start("step_1")
    execution.record_step_complete("step_1", {"answer": "done"})
    execution.complete()

    execution_round_trip = WorkflowExecution.from_dict(execution.to_dict())
    assert execution_round_trip.workflow.id == restored.id
    assert execution_round_trip.step_results["step_1"].output == {"answer": "done"}
    assert execution_round_trip.input_values == {"query": "hello"}


def test_editor_governed_executor_blocks_native_execution() -> None:
    executor = WorkflowExecutor(Mock(), AgentConfig(), EgressGuard("test-user", "test-project"))
    executor.set_editor_tool_call(Mock())

    for step_type in (StepType.SHELL_EXEC, StepType.PYTHON_EXEC):
        step = WorkflowStep(
            id=f"step-{step_type.value}", description="blocked", step_type=step_type
        )
        result = executor._execute_step(step, {})

        assert result.status == StepStatus.BLOCKED
        assert result.error == "Native execution is disabled for editor-governed sessions"


def test_cancelled_umrs_step_cancels_submitted_durable_job() -> None:
    client = Mock()
    client.config.project_id = "project"
    client.jobs.create.return_value = Mock(job_id="job-1")
    guard = Mock(spec=EgressGuard)
    guard.check.return_value = EgressDecision.APPROVED
    executor = WorkflowExecutor(client, AgentConfig(), guard)
    executor.set_cancel_check(lambda: True)
    step = WorkflowStep(
        id="job-step",
        description="durable job",
        step_type=StepType.UMRS_JOB,
        config={"job_spec": {}},
    )

    with pytest.raises(AgentCancelledError):
        executor._execute_umrs_job(step, {})

    client.jobs.cancel.assert_called_once_with("job-1")


def test_umrs_step_observes_cancellation_triggered_during_poll() -> None:
    client = Mock()
    client.config.project_id = "project"
    job = Mock(job_id="job-2", status="completed")
    client.jobs.create.return_value = job
    cancelled = False

    def poll_job(job_id: str, *, _turn_cancel: AgentCancellation | None = None) -> Mock:
        nonlocal cancelled
        assert job_id == "job-2"
        assert _turn_cancel is executor._cancellation
        cancelled = True
        return job

    client.jobs.get.side_effect = poll_job
    guard = Mock(spec=EgressGuard)
    guard.check.return_value = EgressDecision.APPROVED
    executor = WorkflowExecutor(client, AgentConfig(), guard)
    executor.set_cancel_check(lambda: cancelled)
    step = WorkflowStep(
        id="polled-job-step",
        description="durable job",
        step_type=StepType.UMRS_JOB,
        config={"job_spec": {}, "poll_interval": 0},
    )

    with pytest.raises(AgentCancelledError):
        executor._execute_umrs_job(step, {})

    client.jobs.cancel.assert_called_once_with("job-2")


def test_agent_cancellation_closes_active_and_late_registered_transports() -> None:
    cancellation = AgentCancellation()
    active_close = Mock()
    late_close = Mock()
    cancellation.register_close(active_close)

    cancellation.request()
    cancellation.register_close(late_close)

    active_close.assert_called_once_with()
    late_close.assert_called_once_with()
    with pytest.raises(AgentCancelledError):
        cancellation.check()


def test_planner_propagates_cancellation_to_llm_request() -> None:
    client = Mock()
    client.config.project_id = "project"
    client.mcp.list_tools.return_value = []
    cancellation = AgentCancellation()

    def cancel_chat(**kwargs: object) -> None:
        assert kwargs["_turn_cancel"] is cancellation
        cancellation.request()
        cancellation.check()

    client.chat.side_effect = cancel_chat
    planner = WorkflowPlanner(client, AgentConfig())
    planner.set_cancellation(cancellation)

    with pytest.raises(AgentCancelledError):
        planner.plan("cancel planning")


def test_planner_tool_discovery_propagates_cancellation() -> None:
    client = Mock()
    cancellation = AgentCancellation()

    def cancel_inventory(**kwargs: object) -> None:
        assert kwargs["_turn_cancel"] is cancellation
        cancellation.request()
        cancellation.check()

    client.mcp.list_tools.side_effect = cancel_inventory
    planner = WorkflowPlanner(client, AgentConfig())
    planner.set_cancellation(cancellation)

    with pytest.raises(AgentCancelledError):
        planner.get_available_tools()


def test_initial_rag_cancellation_does_not_fall_through_to_planning() -> None:
    client = Mock()
    client.config.project_id = "project"
    cancellation = AgentCancellation()

    def cancel_rag(*args: object, **kwargs: object) -> None:
        assert kwargs["_turn_cancel"] is cancellation
        cancellation.request()
        cancellation.check()

    client.rag.search.side_effect = cancel_rag
    agent = SciWorkflowAgent(client, AgentConfig(enable_rag=True))
    agent.set_cancellation(cancellation)

    with pytest.raises(AgentCancelledError):
        agent.run("cancel initial rag")

    client.chat.assert_not_called()


def test_cancellation_wait_is_interruptible() -> None:
    cancellation = AgentCancellation()
    errors: list[Exception] = []

    def wait() -> None:
        try:
            cancellation.wait(10)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=wait)
    thread.start()
    cancellation.request()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AgentCancelledError)


def test_umrs_job_create_and_get_receive_cancellation_token() -> None:
    client = Mock()
    client.config.project_id = "project"
    job = Mock(job_id="job-3", status="completed", commit_attempt_id=None, attempts=[])
    client.jobs.create.return_value = job
    client.jobs.get.return_value = job
    guard = Mock(spec=EgressGuard)
    guard.check.return_value = EgressDecision.APPROVED
    cancellation = AgentCancellation()
    executor = WorkflowExecutor(client, AgentConfig(), guard)
    executor.set_cancellation(cancellation)
    step = WorkflowStep(
        id="job-token-step",
        description="durable job",
        step_type=StepType.UMRS_JOB,
        config={"job_spec": {}, "poll_interval": 0},
    )

    result = executor._execute_umrs_job(step, {})

    assert result.status == StepStatus.COMPLETED
    assert client.jobs.create.call_args.kwargs["_turn_cancel"] is cancellation
    assert client.jobs.get.call_args.kwargs["_turn_cancel"] is cancellation


@pytest.mark.parametrize(
    ("step_type", "client_method"),
    [
        (StepType.MCP_CALL, "mcp.invoke"),
        (StepType.RAG_QUERY, "rag.retrieve"),
        (StepType.LLM_PROMPT, "chat"),
    ],
)
def test_workflow_calls_stop_when_cancelled_during_operation(
    step_type: StepType,
    client_method: str,
) -> None:
    client = Mock()
    client.config.project_id = "project"
    guard = Mock(spec=EgressGuard)
    guard.check.return_value = EgressDecision.APPROVED
    cancellation = AgentCancellation()
    method = client
    for part in client_method.split("."):
        method = getattr(method, part)

    def cancel_operation(*args: object, **kwargs: object) -> Mock:
        assert kwargs["_turn_cancel"] is cancellation
        cancellation.request()
        return Mock(success=True, result="late", answer="late", sources=[], content="late")

    method.side_effect = cancel_operation
    executor = WorkflowExecutor(client, AgentConfig(), guard)
    executor.set_cancellation(cancellation)
    step = WorkflowStep(
        id=f"cancel-{step_type.value}",
        description="cancel during operation",
        step_type=step_type,
        tool_name="test.tool" if step_type == StepType.MCP_CALL else None,
        config={"query": "query", "prompt": "prompt"},
    )

    with pytest.raises(AgentCancelledError):
        executor._execute_step(step, {})


def test_blocked_sse_read_is_closed_by_cancellation() -> None:
    closed = threading.Event()
    reading = threading.Event()
    response = Mock()
    response.status_code = 200

    def iter_lines():
        reading.set()
        closed.wait(timeout=1)
        yield 'data: {"choices":[{"delta":{"content":"late"}}]}'

    response.iter_lines.side_effect = iter_lines
    response.close.side_effect = closed.set
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    response_context = Mock()
    response_context.__enter__ = Mock(return_value=response)
    response_context.__exit__ = Mock(return_value=False)
    client.stream.return_value = response_context
    core = HTTPCore(Mock(api_key=None, timeout=1, verify_ssl=False))
    core._client = Mock(return_value=client)
    cancellation = AgentCancellation()
    errors: list[Exception] = []

    def consume() -> None:
        try:
            list(
                core.iter_sse_content(
                    "http://gateway/v1/chat/completions",
                    cancel_check=cancellation.check,
                    register_abort=cancellation.register_close,
                )
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=consume)
    thread.start()
    assert reading.wait(timeout=1)

    cancellation.request()
    thread.join(timeout=1)

    assert not thread.is_alive()
    response.close.assert_called_once_with()
    assert len(errors) == 1
    assert isinstance(errors[0], AgentCancelledError)


def test_blocked_response_body_is_closed_by_cancellation() -> None:
    closed = threading.Event()
    reading = threading.Event()
    response = Mock()
    response.status_code = 200

    def iter_bytes(chunk_size: int):
        assert chunk_size == 4096
        reading.set()
        closed.wait(timeout=1)
        yield b'{"result":"late"}'

    response.iter_bytes.side_effect = iter_bytes
    response.close.side_effect = closed.set
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    response_context = Mock()
    response_context.__enter__ = Mock(return_value=response)
    response_context.__exit__ = Mock(return_value=False)
    client.stream.return_value = response_context
    core = HTTPCore(Mock(api_key=None, timeout=1, verify_ssl=False))
    core._client = Mock(return_value=client)
    cancellation = AgentCancellation()
    errors: list[Exception] = []

    def request() -> None:
        try:
            core.post(
                "http://gateway/gw/mcp/invoke",
                json_body={"tool": "test"},
                cancel_check=cancellation.check,
                register_abort=cancellation.register_close,
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=request)
    thread.start()
    assert reading.wait(timeout=1)

    cancellation.request()
    thread.join(timeout=1)

    assert not thread.is_alive()
    response.close.assert_called_once_with()
    assert len(errors) == 1
    assert isinstance(errors[0], AgentCancelledError)
