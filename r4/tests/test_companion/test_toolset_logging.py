"""Toolset instrumentation + denied-bucket attribution (round-3 task 1).

E-review requirement: prove the offered set per surface with the handshake
context logged — and that harness-level removals (availability gate) are not
mislabeled as user denials.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEvent, CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config.model = "probe-model"
    client._stream_request_collect.side_effect = lambda payload, acc, **kw: iter(["ok."])
    return client


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": f"{name} desc", "parameters": {"type": "object"}}


def _session(acp_cls, chunks: list[str]):
    from r4.companion.acp_session import CompanionAcpSession

    session = object.__new__(CompanionAcpSession)
    fake = type(
        "Fake",
        (),
        {"session_id": "session-e1", "name": "e1", "agent": None, "messages": []},
    )()
    object.__setattr__(session, "_session", fake)
    object.__setattr__(session, "_store", None)
    object.__setattr__(session, "_on_event", None)
    object.__setattr__(session, "_stream_callback", chunks.append)
    object.__setattr__(session, "_event_callback", None)
    object.__setattr__(session, "_cancel", None)
    object.__setattr__(session, "_editor_tools", [])
    object.__setattr__(session, "_editor_tool_call", None)
    object.__setattr__(session, "_agent_listener_attached", False)
    return session


def test_toolset_logged_once_with_handshake_context(caplog):
    from r4.companion.acp_session import CompanionAcpSession

    chunks: list[str] = []
    session = _session(CompanionAcpSession, chunks)
    session.set_acp_context(
        capabilities={"chat": True, "ask_user": True},
        editor_tool_names=["workspace.readFile", "terminal.run"],
        gateway_url="https://gw.test",
        umrs_host="data0.test",
    )
    record = {
        "selected_tools": ["file_read", "shell_exec", "+48 more"],
        "filtered_out": {"unavailable": {"count": 3, "sample": ["tool_search"]}},
    }
    with caplog.at_level(logging.INFO, logger="r4.companion.acp_session"):
        event = CompanionEvent(
            event_type=CompanionEventType.TOOL_SELECTION, message="", data=record
        )
        session._handle_companion_event(event)
        session._handle_companion_event(event)  # second call must NOT log again

    toolset_logs = [r for r in caplog.records if "acp toolset" in r.getMessage()]
    assert len(toolset_logs) == 1
    line = toolset_logs[0].getMessage()
    for needle in (
        "file_read",
        "unavailable",
        "workspace.readFile",
        "ask_user",
        "gw.test",
        "data0.test",
    ):
        assert needle in line, line


def test_unlabeled_availability_removals_not_user_denied(mock_client) -> None:
    """Availability-gate removals appear in `unavailable`, NOT `disabled`.

    Before the fix, filtered_out.disabled duplicated them as 'user_denied'.
    """
    agent = ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    offered = [_tool("file_read"), _tool("tool_search"), _tool("file_list_platform")]
    agent.send("hi", tools=offered, stream=True)
    ctx = agent._selection_ctx
    record = ctx.step_record(step_index=0, turn_id="t1")
    buckets = record["filtered_out"]
    assert buckets["unavailable"]["count"] == 2
    assert "disabled" not in buckets or buckets["disabled"]["count"] == 0
    sample = (buckets.get("disabled") or {}).get("sample", []) or []
    assert all("user_denied" not in name for name in sample)


def test_real_user_denial_still_counted(mock_client) -> None:
    """An actual permission denial still lands in disabled with user provenance."""
    from r4.companion.permission_rules import PermissionRule, RuleSet

    manager = PermissionManager(mode=PermissionMode.TRUST)
    # ADR-104 D-2: a hidden tool stays hidden only while a tool-wide deny holds.
    manager.ruleset = RuleSet([PermissionRule("deny", "shell_exec", "", "user", "shell_exec")])
    agent = ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=manager,
    )
    agent._session_denied_tools.add("shell_exec")
    agent.send("hi", tools=[_tool("shell_exec"), _tool("file_read")], stream=True)
    record = agent._selection_ctx.step_record(step_index=0, turn_id="t1")
    buckets = record["filtered_out"]
    assert buckets.get("disabled", {}).get("count") == 1
    assert "shell_exec (user_denied)" in buckets["disabled"]["sample"]


def test_runtime_attaches_acp_context(tmp_path, monkeypatch):
    """acp runtime passes handshake capabilities + editor tools + gateway id."""
    import r4.acp as acp

    runtime = acp._AgentSessionRuntime(
        server=acp.ACPServer(),
        client_factory=lambda: MagicMock(config=MagicMock(gateway_url="g", umrs_server_host="h")),
        session_factory=lambda client, name, model: MagicMock(spec=[]),
        session_loader=lambda client, sid: MagicMock(spec=[]),
    )
    from types import SimpleNamespace

    runtime.editor_tools = [SimpleNamespace(name="workspace.readFile", description="d")]

    attached: dict[str, Any] = {}

    class FakeSession:
        id = "s1"

        def on_event(self, cb):
            pass

        def set_acp_context(self, **kw):
            attached.update(kw)

    session = FakeSession()
    runtime._attach_acp_context(session)
    assert attached["capabilities"]["ask_user"] is True
    assert attached["editor_tool_names"] == ["workspace.readFile"]
    assert attached["gateway_url"] == "g"
    assert attached["umrs_host"] == "h"
