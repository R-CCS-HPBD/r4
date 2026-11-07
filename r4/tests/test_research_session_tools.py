"""Milestone 7: research session companion UX (create/select/wire)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.research_loop_tools import (
    _resolve_session_arg,
    build_research_loop_tools,
)


def _client():
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1"))
    client.create_research_session = MagicMock(
        return_value={
            "session": {
                "session_id": "sess-abc",
                "project_id": "proj-1",
                "title": "MERMAID filter",
                "status": "active",
            }
        }
    )
    client.list_research_sessions = MagicMock(
        return_value={
            "sessions": [
                {"session_id": "sess-abc", "status": "active", "title": "MERMAID"},
                {"session_id": "sess-old", "status": "active"},
            ],
            "count": 2,
        }
    )
    client.start_loop = MagicMock(return_value={"loop_id": "loop-9", "status": "running"})
    client.list_loops = MagicMock(return_value={"loops": []})
    return client


def _tool_map(client):
    return {t["name"]: t for t in build_research_loop_tools(client)}


class TestSessionSelector:
    def test_no_session_means_no_link(self):
        assert _resolve_session_arg(_client(), "proj-1", None) is None

    def test_none_selector_explicitly_skips(self):
        assert _resolve_session_arg(_client(), "proj-1", "none") is None

    def test_explicit_id_passthrough(self):
        assert _resolve_session_arg(_client(), "proj-1", "sess-xyz") == "sess-xyz"

    def test_latest_picks_first_active(self):
        client = _client()
        assert _resolve_session_arg(client, "proj-1", "latest") == "sess-abc"
        client.list_research_sessions.assert_called_once_with("proj-1", status="active")

    def test_latest_without_sessions_falls_back_cleanly(self):
        client = _client()
        client.list_research_sessions.return_value = {"sessions": [], "count": 0}
        assert _resolve_session_arg(client, "proj-1", "latest") is None

    def test_api_failure_never_breaks_start(self):
        client = _client()
        client.list_research_sessions.side_effect = RuntimeError("gateway down")
        assert _resolve_session_arg(client, "proj-1", "latest") is None


class TestSessionCreateTool:
    def test_create_session_is_risky_gated(self):
        pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
        assert pm.is_risky("research_session_create") is True
        assert pm.is_safe("research_session_create") is False

    def test_create_returns_session_payload(self):
        client = _client()
        raw = _tool_map(client)["research_session_create"]["execute"](
            research_goal="MERMAID digital filter for seismic peaks",
            title="MERMAID filter",
        )
        payload = json.loads(raw)
        assert payload["type"] == "research_session_created"
        assert payload["session_id"] == "sess-abc"
        assert payload["title"] == "MERMAID filter"
        client.create_research_session.assert_called_once_with(
            "proj-1",
            "MERMAID digital filter for seismic peaks",
            title="MERMAID filter",
        )

    def test_create_without_sessions_api_returns_error(self):
        client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1"))
        raw = _tool_map(client)["research_session_create"]["execute"](research_goal="x")
        payload = json.loads(raw)
        assert payload["code"] == "sessions_unavailable"

    def test_create_failure_structured(self):
        client = _client()
        client.create_research_session.side_effect = RuntimeError("500 server error")
        payload = json.loads(
            _tool_map(client)["research_session_create"]["execute"](research_goal="x")
        )
        assert payload["code"] == "research_session_create_failed"


class TestLoopStartSessionWiring:
    def test_start_forwards_explicit_session(self):
        client = _client()
        raw = _tool_map(client)["research_loop_start"]["execute"](
            research_goal="goal", session="sess-abc"
        )
        payload = json.loads(raw)
        assert payload["type"] == "research_loop_started"
        _, kwargs = client.start_loop.call_args
        assert kwargs.get("session_id") == "sess-abc"

    def test_start_latest_resolves_session(self):
        client = _client()
        _tool_map(client)["research_loop_start"]["execute"](research_goal="goal", session="latest")
        _, kwargs = client.start_loop.call_args
        assert kwargs.get("session_id") == "sess-abc"

    def test_start_default_has_no_session(self):
        client = _client()
        _tool_map(client)["research_loop_start"]["execute"](research_goal="goal")
        _, kwargs = client.start_loop.call_args
        assert "session_id" not in kwargs
        assert "agentic" not in kwargs

    def test_start_forwards_agentic_flag(self):
        client = _client()
        _tool_map(client)["research_loop_start"]["execute"](research_goal="goal", agentic=True)
        _, kwargs = client.start_loop.call_args
        assert kwargs.get("agentic") is True
