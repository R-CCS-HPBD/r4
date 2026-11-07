"""Tests for CompanionSession and CompanionSessionStore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import COMPANION_SYSTEM_PROMPT
from r4.companion.permissions import PermissionMode
from r4.companion.session import CompanionSession, CompanionSessionStore, SessionMessage


def _assert_plan_read_set(names) -> None:
    """Plan-turn contract (ADR-037 amendment Rule 2b): the read set is the
    SAFE_TOOLS minus mutating verbs — no write/exec leaves the offer."""
    assert names is not None
    assert "file_read" in names
    assert "grep_search" in names
    assert "knowledge_graph_propose" not in names
    assert "project_research_readiness" not in names
    assert "shell_exec" not in names
    assert "file_write" not in names


def _assert_pinned_sorted_first(summary: list[dict], target_id) -> None:
    assert summary[0]["id"] == target_id
    assert summary[0]["pinned"] is True
    assert summary[0]["pinned_at"]
    for later in summary[1:]:
        assert later["pinned"] is False


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._request.return_value = MagicMock(
        content="Test response.",
        model="test-model",
        usage=MagicMock(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        raw_response={
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
            "choices": [
                {
                    "message": {
                        "content": "Test response.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ],
        },
    )
    client._config.model = "test-model"
    client.config = SimpleNamespace(
        profile="default",
        gateway_url="http://gateway.test",
        project_id="project-alpha",
        model="test-model",
    )
    client.list_endpoints.return_value = {
        "endpoints": [
            {"instance_id": "ep-test", "model": "test-model", "status": "ready"},
        ],
        "total": 1,
    }
    return client


@pytest.fixture
def store(tmp_path):
    return CompanionSessionStore(db_path=tmp_path / "test_sessions.db")


def test_create_session(store, mock_client):
    session = store.create_session(mock_client, name="Test Session")
    assert session.session_id
    assert session.name == "Test Session"


def test_list_sessions(store, mock_client):
    store.create_session(mock_client, name="Session 1")
    store.create_session(mock_client, name="Session 2")
    sessions = store.list_sessions()
    assert len(sessions) == 2


def test_save_and_load(store, mock_client):
    session = store.create_session(mock_client, name="Persist Test")
    session.messages.append(
        __import__("r4.companion.session", fromlist=["SessionMessage"]).SessionMessage(
            role="user", content="Hello"
        )
    )
    store.save(session)

    loaded = store.load(session.session_id, mock_client)
    assert loaded is not None
    assert loaded.name == "Persist Test"
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "Hello"


def test_delete_session(store, mock_client):
    session = store.create_session(mock_client, name="Delete Me")
    assert store.delete(session.session_id)
    assert store.load(session.session_id, mock_client) is None


def test_delete_nonexistent(store):
    assert not store.delete("nonexistent-id")


def test_list_sessions_filters_endpoint_project_and_archive(store, mock_client):
    session = store.create_session(mock_client, name="Metadata Test")
    session.active_endpoints = [{"instance_id": "endpoint-alpha"}]
    store.save(session)

    sessions = store.list_sessions(endpoint="endpoint-alpha", project="project-alpha")

    assert len(sessions) == 1
    assert sessions[0]["endpoint"] == "endpoint-alpha"
    assert sessions[0]["project_id"] == "project-alpha"
    assert store.archive(session.session_id)
    assert store.list_sessions() == []
    archived = store.list_sessions(include_archived=True)
    assert archived[0]["archived_at"]


def test_get_session_record_includes_messages_and_metadata(store, mock_client):
    session = store.create_session(mock_client, name="Show Test")
    session.messages.append(SessionMessage(role="user", content="Hello"))
    session.active_endpoints = [{"instance_id": "endpoint-beta"}]
    store.save(session)

    record = store.get_session_record(session.session_id)

    assert record is not None
    assert record["messages"][0]["content"] == "Hello"
    assert record["metadata"]["active_endpoints"][0]["instance_id"] == "endpoint-beta"


def test_export_markdown(store, mock_client):
    session = store.create_session(mock_client, name="Export Test")
    session.messages.append(SessionMessage(role="user", content="Question"))
    session.messages.append(SessionMessage(role="assistant", content="Answer"))
    md = session.export_markdown()
    assert "# Export Test" in md
    assert "Question" in md
    assert "Answer" in md


def test_usage_persisted_after_send(store, mock_client):
    session = store.create_session(mock_client, name="Usage Test")

    response = session.send("Hello")
    assert response == "Test response."
    assert session.usage_summary.llm_calls == 1
    assert session.usage_summary.total_tokens == 18

    store.save(session)
    loaded = store.load(session.session_id, mock_client)

    assert loaded is not None
    assert loaded.usage_summary.llm_calls == 1
    assert loaded.usage_summary.total_tokens == 18
    assert len(loaded.usage_records) == 1


def test_endpoint_refresh_stored_in_metadata(store, mock_client):
    created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    mock_client.list_endpoints.return_value = {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "job_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "slurm",
            }
        ],
        "total": 1,
    }
    mock_client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "qwen-test",
        "status": "ready",
        "backend": "slurm",
        "created_at": created_at,
        "expires_at": expires_at,
        "endpoint_url": "http://node:8001",
    }

    session = store.create_session(mock_client, name="Endpoint Test")
    session._refresh_active_endpoints(force=True)

    assert len(session.active_endpoints) == 1
    assert session.active_endpoints[0]["instance_id"] == "ep-1"
    assert session.active_endpoints[0]["remaining_seconds"] is not None
    assert session.active_endpoints[0]["node_hours_estimate"] > 0
    assert session.active_endpoints[0]["expiry_state"] == "healthy"

    store.save(session)
    loaded = store.load(session.session_id, mock_client)
    assert loaded is not None
    assert loaded.active_endpoints[0]["backend"] == "slurm"


def test_usage_report_includes_endpoint_budget(store, mock_client):
    session = store.create_session(mock_client, name="Usage Budget")
    session.active_endpoints = [
        {
            "instance_id": "ep-1",
            "remaining_seconds": 900,
            "node_hours_estimate": 1.25,
        },
        {
            "instance_id": "ep-2",
            "remaining_seconds": 1800,
            "node_hours_estimate": 0.75,
        },
    ]

    report = session.usage_report()

    assert report["budget"]["node_hours_estimated"] == 2.0
    assert report["budget"]["nearest_expiry_seconds"] == 900
    assert report["budget"]["active_endpoint_count"] == 2


def test_session_llm_call_limit_prevents_send(store, mock_client):
    session = CompanionSession(
        session_id="cap-test",
        name="Cap Test",
        client=mock_client,
        max_llm_calls_per_session=0,
    )

    response = session.send("Hello")

    assert "Budget limit reached" in response
    assert session.usage_summary.llm_calls == 0


def test_send_fits_full_payload_before_agent_call(store, mock_client):
    session = store.create_session(mock_client, name="Payload Fit")
    session.context.max_context_tokens = 1_200
    session.context.response_reserve_tokens = 120
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    session._harness.post_turn.return_value = None

    session.context.remember("large", "x" * 1_600, source="user")
    session.messages.extend(
        [
            SessionMessage(role="user", content="old message " * 120),
            SessionMessage(role="assistant", content="old answer " * 120),
            SessionMessage(role="user", content="recent question " * 80),
            SessionMessage(role="assistant", content="recent answer " * 80),
        ]
    )

    captured: dict[str, object] = {}

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        captured["user_message"] = user_message
        captured["history"] = history or []
        captured["extra_system"] = extra_system
        captured["tools"] = tools or []
        return "Trimmed response"

    session.agent.send = fake_send

    response = session.send("final question")

    assert response == "Trimmed response"
    budget = session.context.estimate_payload_budget(
        system_prompt=COMPANION_SYSTEM_PROMPT,
        extra_system=str(captured["extra_system"]),
        history=list(captured["history"]),
        user_message="final question",
        tools=list(captured["tools"]),
    )
    assert not budget.hard_limit_exceeded


def test_plan_mode_first_turn_is_plan_only(store, mock_client):
    session = store.create_session(mock_client, name="Plan Mode")
    session.metadata["interaction_mode"] = "plan"
    captured: dict[str, object] = {}

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        captured["user_message"] = user_message
        captured["extra_system"] = extra_system
        captured["tools"] = tools or []
        captured["offer_only_names"] = offer_only_names
        return "Plan draft with questions."

    session.agent.send = fake_send

    response = session.send("Implement endpoint cleanup")

    # ADR-037 amendment: plan turns receive the FULL catalog gated to the
    # read set (mode_readonly); wave 80: the catalog is the session's stable,
    # name-sorted array, and mutating verbs are refused at execution.
    names = [tool["name"] for tool in captured["tools"]]
    assert names == sorted(tool["name"] for tool in session.tools)
    _assert_plan_read_set(captured["offer_only_names"])
    assert "may read" in str(captured["extra_system"]).lower()
    assert "**Plan mode**" in response
    assert "**Awaiting your approval**" in response
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True
    assert session.metadata["plan_mode_state"]["pending_request"] == "Implement endpoint cleanup"


def test_plan_mode_revision_keeps_original_pending_request(store, mock_client):
    session = store.create_session(mock_client, name="Plan Revision")
    session.metadata["interaction_mode"] = "plan"
    calls: list[dict[str, object]] = []

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        calls.append(
            {
                "user_message": user_message,
                "tools": list(tools or []),
                "offer_only_names": offer_only_names,
            }
        )
        return "Revised plan."

    session.agent.send = fake_send

    session.send("Implement endpoint cleanup")
    session.send("Focus on storage mounts only")

    assert len(calls) == 2
    for call in calls:
        names = call["offer_only_names"]
        assert names is not None and "file_read" in names
        assert "knowledge_graph_propose" not in names
    assert "Implement endpoint cleanup" in str(calls[1]["user_message"])
    assert session.metadata["plan_mode_state"]["pending_request"] == "Implement endpoint cleanup"


def test_plan_mode_cancel_clears_state(store, mock_client):
    session = store.create_session(mock_client, name="Plan Cancel")
    session.metadata["interaction_mode"] = "plan"

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        return "Plan draft."

    session.agent.send = fake_send

    session.send("Implement endpoint cleanup")
    response = session.send("no, cancel")

    assert "cancelled" in response.lower()
    assert "plan_mode_state" not in session.metadata


def _plan_session(store, mock_client, name):
    session = store.create_session(mock_client, name=name)
    session.metadata["interaction_mode"] = "plan"
    calls: list[str] = []

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        calls.append(str(user_message))
        return "Plan draft."

    session.agent.send = fake_send
    session.send("Refactor tool approval matrix")
    return session, calls


def test_plan_mode_a_negated_confirmation_never_runs_the_plan(store, mock_client):
    """2026-10-02: "no, don't do it" contains "do it" — it used to execute."""
    session, calls = _plan_session(store, mock_client, "Plan Negated")
    response = session.send("no, don't do it")
    assert "cancelled" in response.lower()
    assert len(calls) == 1  # no execution turn
    assert "plan_mode_state" not in session.metadata


def test_plan_mode_yes_with_a_change_revises_instead_of_running(store, mock_client):
    session, calls = _plan_session(store, mock_client, "Plan Yes But")
    session.send("yes, but use 4 GPUs")
    assert len(calls) == 2
    assert "Execution approved." not in calls[1]
    assert "use 4 GPUs" in calls[1]
    state = session.metadata["plan_mode_state"]
    assert state["awaiting_confirmation"] is True
    assert state["pending_request"] == "Refactor tool approval matrix"


def test_plan_mode_hesitation_asks_again_and_keeps_the_plan(store, mock_client):
    session, calls = _plan_session(store, mock_client, "Plan Unclear")
    response = session.send("not yet")
    assert "Reply **yes** to run it" in response
    assert len(calls) == 1  # no model call
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True


def _with_decision_model(mock_client, letters):
    """Decision model on (ADR-076) with fixed candidate logprobs.

    Candidates: A=execute B=cancel C=revise D=unclear."""
    mock_client.config.decision_model = "Bespoke-Nimble-9B"
    calls: list[dict] = []

    def text_completion(payload, *, timeout=None):
        calls.append(payload)
        return {"choices": [{"text": "A", "logprobs": {"top_logprobs": [letters]}}], "usage": {}}

    mock_client.text_completion = text_completion
    mock_client.list_gateway_models = lambda *, timeout=30: ["Bespoke-Nimble-9B"]
    return calls


def test_plan_mode_decision_model_turns_an_unreadable_yes_into_one_question(store, mock_client):
    """The rules cannot read はい; a confident decision-model reading asks for
    a one-word yes — it never runs the plan itself (ADR-076 D-3)."""
    nimble = _with_decision_model(mock_client, {"A": 0.0, "B": -6.0, "C": -6.0, "D": -6.0})
    session, calls = _plan_session(store, mock_client, "Plan Nimble")
    response = session.send("はい、お願いします")
    assert "Reply **yes** to run it" in response
    assert len(calls) == 1 and len(nimble) == 1  # no execution, one decision
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True
    assert session.metadata["decision_log"][-1]["choice"] == "execute"
    session.send("yes")
    assert "Execution approved." in calls[-1] and len(nimble) == 1  # a short yes needs no model


def test_plan_mode_decision_model_reading_revise_keeps_the_rules(store, mock_client):
    _with_decision_model(mock_client, {"A": -6.0, "B": -6.0, "C": 0.0, "D": -6.0})
    session, calls = _plan_session(store, mock_client, "Plan Nimble Revise")
    session.send("yes, but use 4 GPUs")
    assert len(calls) == 2 and "use 4 GPUs" in calls[1]  # re-planned, as the rules read it


def test_confirm_plan_execute_auto_uses_trust(store, mock_client):
    session = store.create_session(mock_client, name="Plan Confirm Auto")
    session.metadata["interaction_mode"] = "plan"
    calls: list[dict[str, object]] = []

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        calls.append(
            {
                "user_message": user_message,
                "tools": list(tools or []),
                "offer_only_names": offer_only_names,
            }
        )
        return "done"

    session.agent.send = fake_send

    session.send("Implement endpoint cleanup")
    session.confirm_plan("execute_auto")

    assert len(calls) == 2
    assert calls[0]["offer_only_names"] is not None  # plan read set gated
    assert calls[1]["offer_only_names"] is None  # execution: passthrough offer
    assert calls[1]["tools"] != []
    assert session.permissions.mode == PermissionMode.TRUST
    assert "plan_mode_state" not in session.metadata
    # ADR-104 D-1: accepting switched the mode visibly; no sticky override.
    assert session.metadata["interaction_mode"] == "autopilot"
    assert "permission_override" not in session.metadata


def test_leaving_autopilot_after_an_auto_plan_drops_trust(store, mock_client):
    """ADR-104 defect 1: "accept & auto-approve" wrote a sticky
    permission_override=trust that every later mode switch honoured."""
    from r4.companion.agent_mode import apply_mode

    session = store.create_session(mock_client, name="Plan Then Chat")
    session.metadata["interaction_mode"] = "plan"
    session.agent.send = lambda *_a, **_k: "done"
    session.send("Implement endpoint cleanup")
    session.confirm_plan("execute_auto")
    apply_mode(session, "chat")
    assert session.permissions.mode == PermissionMode.AUTO_SAFE
    store.save(session)
    resumed = store.load(session.session_id, mock_client)
    assert resumed.permissions.mode == PermissionMode.AUTO_SAFE


def test_confirm_plan_execute_ask_runs_in_chat(store, mock_client):
    session = store.create_session(mock_client, name="Plan Confirm Ask")
    session.metadata["interaction_mode"] = "plan"
    session.permissions.set_mode(PermissionMode.TRUST)

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        return "done"

    session.agent.send = fake_send

    session.send("Implement endpoint cleanup")
    session.confirm_plan("execute_ask")

    # ADR-104 D-1: "manual" = chat: read-only tools run, every other asks.
    assert session.metadata["interaction_mode"] == "chat"
    assert session.permissions.mode == PermissionMode.AUTO_SAFE
    assert "plan_mode_state" not in session.metadata


def test_confirm_plan_keep_planning_is_noop(store, mock_client):
    session = store.create_session(mock_client, name="Plan Keep")
    session.metadata["interaction_mode"] = "plan"

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        return "Plan draft."

    session.agent.send = fake_send

    session.send("Implement endpoint cleanup")
    response = session.confirm_plan("keep_planning")

    assert response == ""
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True


def test_execute_approved_plan_restores_state_when_stopped(store, mock_client):
    session = store.create_session(mock_client, name="Plan Stop Restore")
    session.metadata["interaction_mode"] = "plan"

    def plan_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        return "Plan draft."

    def execute_send(user_message, *, history=None, extra_system="", tools=None, stream=False):
        return "(Generation stopped.)"

    session.agent.send = plan_send
    session.send("Implement endpoint cleanup")
    session.agent.send = execute_send

    response = session.confirm_plan("execute_ask")

    assert response == "(Generation stopped.)"
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True
    assert session.metadata["plan_mode_state"]["pending_request"] == "Implement endpoint cleanup"


def test_plan_mode_confirmation_executes_original_request(store, mock_client):
    session = store.create_session(mock_client, name="Plan Execute")
    session.metadata["interaction_mode"] = "plan"
    calls: list[dict[str, object]] = []

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        calls.append(
            {
                "user_message": user_message,
                "extra_system": extra_system,
                "tools": list(tools or []),
                "offer_only_names": offer_only_names,
            }
        )
        return "ok"

    session.agent.send = fake_send

    session.send("Refactor tool approval matrix")
    session.send("yes, execute now")

    assert len(calls) == 2
    _assert_plan_read_set(calls[0]["offer_only_names"])
    assert calls[1]["offer_only_names"] is None  # execution: passthrough offer
    assert calls[1]["tools"] != []
    assert "Execution approved." in str(calls[1]["user_message"])
    assert "Refactor tool approval matrix" in str(calls[1]["user_message"])
    assert "plan_mode_state" not in session.metadata


def test_send_blocks_expensive_request_when_endpoint_near_expiry(store, mock_client):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    mock_client.list_endpoints.return_value = {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "job_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "slurm",
            }
        ],
        "total": 1,
    }
    mock_client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "qwen-test",
        "status": "ready",
        "backend": "slurm",
        "expires_at": expires_at,
        "endpoint_url": "http://node:8001",
    }

    session = store.create_session(mock_client, name="Expiry Block")
    session.agent.model = "qwen-test"
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    session._harness.post_turn.return_value = None
    # The guard only blocks when expensive exec tools are actually available;
    # activate a skill carrying shell_exec/python_exec explicitly (the old
    # test relied on an accidental duplicate 'workflow' skill, now merged).
    session.skills.deactivate("umrs")
    session.skills.auto_activate = lambda text: []
    session.skills.activate("code")

    response = session.send("run a full workflow across the dataset")

    assert "expires in" in response
    assert "blocked this request" in response
    assert "shell_exec" in response or "python_exec" in response
    assert session.usage_summary.llm_calls == 0


def test_send_warns_expensive_request_when_endpoint_near_expiry(store, mock_client):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    mock_client.list_endpoints.return_value = {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "job_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "k8s",
            }
        ],
        "total": 1,
    }
    mock_client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "qwen-test",
        "status": "ready",
        "backend": "k8s",
        "expires_at": expires_at,
        "endpoint_url": "http://node:8001",
    }

    session = store.create_session(mock_client, name="Expiry Warn")
    session.agent.model = "qwen-test"
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    session._harness.post_turn.return_value = None
    # Prevent skill auto-activation from filtering out python_exec
    # (e.g. "embed" triggers data_management skill which excludes python_exec)
    session.skills.auto_activate = lambda text: []
    # Default-active baseline (umrs) would also restrict the tool set
    session.skills.deactivate("umrs")

    captured: dict[str, object] = {}

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        captured["extra_system"] = extra_system
        return "Proceed carefully"

    session.agent.send = fake_send

    response = session.send("run embedding export on this corpus")

    assert response == "Proceed carefully"
    assert "[Endpoint expiry guard]" in str(captured["extra_system"])
    assert "expires in" in str(captured["extra_system"])
    assert "python_exec" in str(captured["extra_system"])


def test_send_does_not_block_when_expensive_tool_is_unavailable(store, mock_client):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    mock_client.list_endpoints.return_value = {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "job_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "slurm",
            }
        ],
        "total": 1,
    }
    mock_client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "qwen-test",
        "status": "ready",
        "backend": "slurm",
        "expires_at": expires_at,
        "endpoint_url": "http://node:8001",
    }

    session = store.create_session(mock_client, name="Expiry Safe")
    session.agent.model = "qwen-test"
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    session._harness.post_turn.return_value = None
    # Default-active baseline (umrs) includes list_models; this test isolates
    # the no-available-tools path
    session.skills.deactivate("umrs")
    session.tools = [
        {
            "name": "list_models",
            "description": "List available AI models on the platform",
        }
    ]

    captured: dict[str, object] = {}

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
        reminder="",
        tool_refusals=None,
    ):
        captured["extra_system"] = extra_system + reminder
        captured["tools"] = tools or []
        captured["tool_refusals"] = tool_refusals or {}
        return "Safe to continue"

    session.agent.send = fake_send

    response = session.send("submit this batch job")

    assert response == "Safe to continue"
    assert "[Endpoint expiry guard]" not in str(captured["extra_system"])
    # Wave 80: the active skills do not list it, so it stays in the stable
    # array and its calls are refused this turn.
    assert [tool["name"] for tool in captured["tools"]] == ["list_models"]
    assert "list_models" in captured["tool_refusals"]


def test_send_allows_short_shell_request_near_expiry(store, mock_client):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    mock_client.list_endpoints.return_value = {
        "endpoints": [
            {
                "instance_id": "ep-1",
                "job_id": "ep-1",
                "model": "qwen-test",
                "status": "ready",
                "backend": "slurm",
            }
        ],
        "total": 1,
    }
    mock_client.get_endpoint_status.return_value = {
        "instance_id": "ep-1",
        "job_id": "ep-1",
        "model": "qwen-test",
        "status": "ready",
        "backend": "slurm",
        "expires_at": expires_at,
        "endpoint_url": "http://node:8001",
    }

    session = store.create_session(mock_client, name="Expiry Short Shell")
    session.agent.model = "qwen-test"
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    session._harness.post_turn.return_value = None

    captured: dict[str, object] = {}

    def fake_send(
        user_message,
        *,
        history=None,
        extra_system="",
        tools=None,
        offer_only_names=None,
        stream=False,
    ):
        captured["extra_system"] = extra_system
        captured["tools"] = tools or []
        return "Short command is fine"

    session.agent.send = fake_send

    response = session.send("run pwd in the current project directory")

    assert response == "Short command is fine"
    assert "[Endpoint expiry guard]" not in str(captured["extra_system"])
    assert any(tool.get("name") == "shell_exec" for tool in list(captured["tools"]))


# ── C2: session pin / rename_record ────────────────────────────────────────


def test_pin_persists_flag_and_sorts_first(store, mock_client):
    __s1 = store.create_session(mock_client, name="older unpinned")
    s2 = store.create_session(mock_client, name="pinned target")
    __s3 = store.create_session(mock_client, name="newer unpinned")

    assert store.set_pinned(s2.session_id, True)
    summary = store.list_sessions()
    _assert_pinned_sorted_first(summary, s2.session_id)

    # Unpin returns the session to the unpinned ordering (no pinned rows remain).
    assert store.set_pinned(s2.session_id, False)
    summary = store.list_sessions()
    assert all(not s["pinned"] for s in summary)
    assert summary[0]["id"] != s2.session_id  # ties fall back to updated_at, the fresh ones lead


def test_pin_survives_live_save(store, mock_client):
    session = store.create_session(mock_client, name="pinned live")
    assert store.set_pinned(session.session_id, True)
    # A normal save (like a turn end) must NOT clobber pinned_at.
    store.save(session)
    summary = store.list_sessions(limit=1)
    assert summary[0]["pinned"] is True


def test_set_pinned_unknown_session_does_not_create(store):
    assert store.set_pinned("does-not-exist", True) is False
    assert store.list_sessions() == []


def test_rename_record_by_id(store, mock_client):
    session = store.create_session(mock_client, name="Old Name")
    assert store.rename_record(session.session_id, "New Name")
    assert store.list_sessions(limit=1)[0]["name"] == "New Name"
    with pytest.raises(ValueError):
        store.rename_record(session.session_id, "   ")
    assert store.rename_record("missing-id", "x") is False


class TestTurnIds:
    """ADR-057 D-1: one retainable id per committed turn; stamped on the
    turn's messages; counter survives in session.metadata["turn_seq"]."""

    def test_first_turn_assigns_t1_on_user_and_assistant(self, mock_client):
        session = CompanionSession(
            session_id="turn-test-1",
            name="TurnTest",
            client=mock_client,
            max_llm_calls_per_session=0,
        )
        session._start_turn("hello")
        assert session.messages[-1].role == "user"
        assert session.messages[-1].metadata["turn_id"] == "t1"
        session._finish_turn("hello", "world")
        assert session.messages[-1].role == "assistant"
        assert session.messages[-1].metadata["turn_id"] == "t1"

    def test_turn_seq_is_monotonic_and_recorded_in_metadata(self, mock_client):
        session = CompanionSession(
            session_id="turn-test-2",
            name="TurnTest2",
            client=mock_client,
            max_llm_calls_per_session=0,
        )
        session._start_turn("one")
        session._finish_turn("one", "r1")
        session._start_turn("two")
        session._finish_turn("two", "r2")
        ids = [m.metadata.get("turn_id") for m in session.messages]
        assert ids == ["t1", "t1", "t2", "t2"]
        assert session.metadata.get("turn_seq") == 2
