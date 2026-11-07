"""Tests for CompanionAcpSession (ACP ↔ Companion adapter)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.acp import create_default_server
from r4.agent.core import AgentCancelledError
from r4.companion.acp_session import (
    CompanionAcpSession,
    create_companion_acp_session,
    list_companion_acp_sessions,
    load_companion_acp_session,
)
from r4.companion.agent import CompanionEvent, CompanionEventType
from r4.companion.cancellation import TurnCancelledError
from r4.companion.session_store import CompanionSessionStore


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
    return CompanionSessionStore(db_path=tmp_path / "acp_sessions.db")


def test_create_and_list_companion_acp_sessions(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="ACP Session", store=store)
    assert adapter.id
    assert adapter.name == "ACP Session"
    listed = list_companion_acp_sessions(store=store)
    assert any(item["id"] == adapter.id for item in listed)


def test_load_companion_acp_session(store, mock_client):
    created = create_companion_acp_session(mock_client, name="Saved ACP", store=store)
    loaded = load_companion_acp_session(mock_client, created.id, store=store)
    assert loaded.id == created.id
    assert loaded.name == "Saved ACP"


def test_load_missing_companion_acp_session_raises(store, mock_client):
    with pytest.raises(FileNotFoundError):
        load_companion_acp_session(mock_client, "missing-id", store=store)


def test_chat_streams_chunks_and_maps_tool_events(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Stream", store=store)
    chunks: list[str] = []
    structured: list[dict] = []

    def fake_send(message: str, *, stream: bool = False) -> str:
        adapter._handle_companion_event(CompanionEvent(CompanionEventType.RESPONSE_CHUNK, "Hello "))
        adapter._handle_companion_event(CompanionEvent(CompanionEventType.RESPONSE_CHUNK, "world"))
        adapter._handle_companion_event(
            CompanionEvent(
                CompanionEventType.TOOL_CALL,
                "Calling tool",
                data={"name": "workflow_list", "arguments": {}},
            )
        )
        adapter._handle_companion_event(
            CompanionEvent(
                CompanionEventType.TOOL_RESULT,
                "2 workflows",
                data={"name": "workflow_list", "status": "ok"},
            )
        )
        return "Hello world"

    adapter._session.send = fake_send  # type: ignore[method-assign]
    adapter.set_stream_callback(chunks.append)
    adapter.set_event_callback(structured.append)

    response = adapter.chat("hi")
    assert response == "Hello world"
    assert chunks == ["Hello ", "world"]
    # Canonical envelopes: tool_call_start + tool_call_end (sanitized fields).
    start, end = structured[0], structured[1]
    assert start["type"] == "tool_call_start"
    assert end["type"] == "tool_call_end"
    assert start["payload"]["name"] == "workflow_list"
    assert start["payload"]["risk_tier"] == "risky"
    assert end["payload"]["status"] == "done"


def test_tool_call_start_summary_is_sanitized(store, mock_client):
    """args_summary comes from the allowlist; raw arguments never leak."""
    adapter = create_companion_acp_session(mock_client, name="Sanitize", store=store)
    structured: list[dict] = []

    def fake_send(message: str, *, stream: bool = False) -> str:
        adapter._handle_companion_event(
            CompanionEvent(
                CompanionEventType.TOOL_CALL,
                "Calling tool",
                data={
                    "name": "lab_file_read",
                    "tool_call_id": "tc-1",
                    "arguments": {"s3_key": "data/catalog/x.csv", "token": "sk-abcdef1234"},
                },
            )
        )
        return "done"

    adapter._session.send = fake_send  # type: ignore[method-assign]
    adapter.set_event_callback(structured.append)

    adapter.chat("hi")

    payload = [e for e in structured if e.get("type") == "tool_call_start"][0]["payload"]
    assert payload["args_summary"] == "data/catalog/x.csv"
    assert "sk-abcdef1234" not in str(payload)
    assert payload["tool_call_id"] == "tc-1"


def _chat_and_collect_budget_payload(store, mock_client, *, name="Budget"):
    adapter = create_companion_acp_session(mock_client, name=name, store=store)
    structured: list[dict] = []
    adapter._session.send = lambda message, *, stream=False: "ok"
    adapter.set_event_callback(structured.append)
    adapter.chat("hi")
    budgets = [e for e in structured if e.get("type") == "budget_state"]
    return adapter, budgets


def test_chat_emits_budget_state_after_turn(store, mock_client):
    _adapter, budgets = _chat_and_collect_budget_payload(store, mock_client)
    assert len(budgets) == 1
    payload = budgets[0]["payload"]
    assert payload["version"] == 1
    assert payload["session_id"] == _adapter.id
    assert payload["llm_calls_limit"] is None  # no session cap configured by default


def test_budget_state_carry_counters_and_limits(store, mock_client):
    from r4.companion.agent import DEFAULT_MAX_TOOL_ROUNDS

    _adapter, budgets = _chat_and_collect_budget_payload(store, mock_client, name="Budget2")
    payload = budgets[0]["payload"]
    assert payload["tool_rounds_limit"] == DEFAULT_MAX_TOOL_ROUNDS
    assert payload["tool_rounds_used"] >= 0
    assert payload["llm_calls_used"] >= 0
    assert payload["context_limit_tokens"] >= 4096
    assert payload["context_used_tokens"] >= 0


def test_budget_state_reports_last_turn_llm_rounds(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Rounds", store=store)
    adapter._session._current_turn_llm_calls = 3  # previous turn used 3 rounds
    structured: list[dict] = []
    adapter._session.send = lambda message, *, stream=False: "ok"
    adapter.set_event_callback(structured.append)

    adapter.chat("hi")

    payload = [e for e in structured if e.get("type") == "budget_state"][0]["payload"]
    assert payload["tool_rounds_used"] == 3


def test_budget_state_skips_without_callback(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Quiet", store=store)
    adapter._session.send = lambda message, *, stream=False: "ok"
    assert adapter.chat("hi") == "ok"


def test_cancel_raises_agent_cancelled_error(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Cancel", store=store)

    def fake_send(message: str, *, stream: bool = False) -> str:
        raise TurnCancelledError()

    adapter._session.send = fake_send  # type: ignore[method-assign]
    with pytest.raises(AgentCancelledError):
        adapter.chat("stop me")


def test_prepare_cancel_is_cancelled(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Token", store=store)
    adapter.prepare_request()
    assert adapter.is_cancelled() is False
    adapter.cancel()
    assert adapter.is_cancelled() is True


def test_rename_and_set_model_persist(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Rename Me", store=store)
    adapter.rename("Renamed")
    adapter.set_model("gpt-test")
    reloaded = load_companion_acp_session(mock_client, adapter.id, store=store)
    assert reloaded.name == "Renamed"
    assert reloaded.model == "gpt-test"


def test_set_mode_defaults_to_chat_ask(store, mock_client):
    from r4.companion.permissions import PermissionMode

    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    assert adapter.interaction_mode == "chat"
    assert adapter._session.permissions.mode == PermissionMode.ASK_ALWAYS


def test_set_mode_agent_trusts_tools_without_prompts(store, mock_client):
    from r4.companion.permissions import PermissionMode

    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    adapter.set_mode("agent")
    assert adapter.interaction_mode == "autopilot"
    assert adapter._session.permissions.mode == PermissionMode.TRUST
    # ADR-104 D-1: the mode alone says autopilot; no second flag.
    assert "auto_approve_tools" not in adapter._session.metadata


def test_set_mode_plan_and_ask_restore_manual_approval(store, mock_client):
    from r4.companion.permissions import PermissionMode

    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    adapter.set_mode("plan")
    assert adapter.interaction_mode == "plan"
    # ADR-104 D-1: chat and plan are AUTO_SAFE on every surface (the bridge
    # already raised ask_always to auto_safe; ACP alone did not).
    assert adapter._session.permissions.mode == PermissionMode.AUTO_SAFE

    adapter.set_mode("ask")
    assert adapter.interaction_mode == "chat"
    assert adapter._session.permissions.mode == PermissionMode.AUTO_SAFE


def test_acp_autopilot_survives_resume_as_trust(store, mock_client):
    """ADR-104 defect 3: ACP autopilot came back ASK_ALWAYS after a resume
    (only auto_approve_tools recorded it) while the badge said autopilot."""
    from r4.companion.permissions import PermissionMode

    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    adapter.set_mode("autopilot")
    reloaded = load_companion_acp_session(mock_client, adapter.id, store=store)
    assert reloaded._session.permissions.mode == PermissionMode.TRUST


def test_set_mode_persists_across_load(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    adapter.set_mode("autopilot")
    reloaded = load_companion_acp_session(mock_client, adapter.id, store=store)
    assert reloaded.interaction_mode == "autopilot"


def test_set_mode_rejects_unknown_mode(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Modes", store=store)
    with pytest.raises(ValueError, match="unknown mode"):
        adapter.set_mode("bogus")


def test_acp_sessions_set_mode_routes_to_adapter(store, mock_client):
    import json

    adapter = create_companion_acp_session(mock_client, name="ACP Mode", store=store)
    server = create_default_server(
        client_factory=lambda: mock_client,
        session_factory=lambda client, name, model: adapter,
        session_loader=lambda client, sid: adapter,
        session_lister=lambda: list_companion_acp_sessions(store=store),
    )
    output: list[dict] = []
    server._write = output.append  # type: ignore[method-assign]
    server._handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sessions/set_mode",
                "params": {"session_id": adapter.id, "mode": "agent"},
            }
        )
    )
    assert output[-1]["result"]["session"]["mode"] == "autopilot"
    assert adapter.interaction_mode == "autopilot"


def test_acp_chat_send_uses_companion_adapter(store, mock_client):
    adapter = create_companion_acp_session(mock_client, name="Via ACP", store=store)

    def fake_send(message: str, *, stream: bool = False) -> str:
        return f"echo:{message}"

    adapter._session.send = fake_send  # type: ignore[method-assign]

    server = create_default_server(
        client_factory=lambda: mock_client,
        session_factory=lambda client, name, model: adapter,
        session_loader=lambda client, sid: adapter,
        session_lister=lambda: list_companion_acp_sessions(store=store),
    )
    output: list[dict] = []
    server._write = output.append  # type: ignore[method-assign]
    import json

    server._handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "chat/send",
                "params": {"message": "ping", "request_id": "req-1"},
            }
        )
    )
    result = output[-1]["result"]
    assert result["status"] == "completed"
    assert result["session_id"] == adapter.id
    assert result["response"] == "echo:ping"


def test_default_factories_use_companion_store(store, mock_client, monkeypatch):
    monkeypatch.setenv("R4_COMPANION_DB", str(store.db_path))
    # Force store path via CompanionSessionStore default override used by module.
    from r4.companion import session as session_mod

    monkeypatch.setattr(session_mod, "DB_PATH", store.db_path)

    from r4.acp import _default_session_factory, _default_session_lister

    created = _default_session_factory(mock_client, "Default Factory", "test-model")
    assert isinstance(created, CompanionAcpSession)
    assert created.name == "Default Factory"
    listed = _default_session_lister()
    assert any(item["id"] == created.id for item in listed)
