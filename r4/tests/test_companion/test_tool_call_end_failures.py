"""Every started tool call ends (cycle 3, L1; driven through the real JSONL bridge).

A ``tool_call_start`` for an unknown tool or for arguments that do not parse
never got a ``tool_call_end``: those failures returned before any result
event, so the console row stayed "running" for the rest of the session. The
same held for an executor that raised, a tool without an executor, and the
calls a cancel cut short. Each now ends once, with ``status`` and the reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEvent, CompanionEventType, ResearchCompanion
from r4.companion.cancellation import TurnCancellation, TurnCancelledError
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import CompanionBridgeServer

_ENDS = (CompanionEventType.TOOL_RESULT, CompanionEventType.TOOL_BLOCKED)


def _agent() -> tuple[ResearchCompanion, list[CompanionEvent]]:
    client = MagicMock()
    client._config.model = "test-model"
    agent = ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    return agent, events


def _tool(name: str, execute: Any = "ok") -> dict[str, Any]:
    tool: dict[str, Any] = {"name": name, "description": "", "parameters": {"type": "object"}}
    if execute == "ok":
        tool["execute"] = lambda **_kw: "fine"
    elif execute is not None:
        tool["execute"] = execute
    return tool


def _call(call_id: str, name: str, arguments: str = "{}") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _ends(events: list[CompanionEvent], call_id: str) -> list[CompanionEvent]:
    return [
        e for e in events if e.event_type in _ENDS and (e.data or {}).get("tool_call_id") == call_id
    ]


def _boom(**_kw: Any) -> str:
    raise RuntimeError("disk on fire")


@pytest.mark.parametrize(
    ("call", "tools", "reason"),
    [
        (_call("c1", "rm_everything", '{"path": "/"}'), [_tool("file_read")], "unknown tool"),
        (_call("c2", "file_read", '{"path": "x.txt", '), [_tool("file_read")], "invalid JSON"),
        (_call("c3", "file_read"), [_tool("file_read", execute=None)], "has no executor"),
        (_call("c4", "file_read"), [_tool("file_read", execute=_boom)], "disk on fire"),
    ],
)
def test_a_failed_call_ends_once_with_its_reason(call, tools, reason) -> None:
    agent, events = _agent()
    agent._execute_tool_calls([call], tools)
    (end,) = _ends(events, call["id"])
    assert end.event_type is CompanionEventType.TOOL_RESULT
    assert end.data["status"] == "error"
    assert end.data["name"] == call["function"]["name"]
    assert reason in end.message


def test_a_successful_call_still_ends_exactly_once() -> None:
    agent, events = _agent()
    agent._execute_tool_calls([_call("ok1", "file_read")], [_tool("file_read")])
    (end,) = _ends(events, "ok1")
    assert end.data.get("status") is None  # the executor's own result event


def test_a_call_a_cancel_cuts_short_ends_as_cancelled() -> None:
    agent, events = _agent()
    token = TurnCancellation()
    agent._turn_cancel = token
    token.request()  # the cancel lands right after the call's start event
    with pytest.raises(TurnCancelledError):
        agent._execute_tool_calls([_call("k1", "file_read")], [_tool("file_read")])
    (end,) = _ends(events, "k1")
    assert end.data["status"] == "cancelled"


def test_the_bridge_closes_the_row_with_status_error_and_the_reason() -> None:
    agent, events = _agent()
    agent._execute_tool_calls([_call("c7", "rm_everything")], [_tool("file_read")])
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    lines: list[dict[str, Any]] = []
    server._emit_line = lines.append
    server._session_id_str = lambda: "s-1"
    server._emit_cost = lambda: None
    for event in events:
        server._emit_canonical_tool_event(event.event_type.value, event.data or {}, event.message)
    rows = [(line["type"], line["payload"]) for line in lines if "payload" in line]
    assert [(kind, payload["tool_call_id"]) for kind, payload in rows] == [
        ("tool_call_start", "c7"),
        ("tool_call_end", "c7"),
    ]
    end = rows[1][1]
    assert end["status"] == "error"
    assert "unknown tool 'rm_everything'" in end["result_preview"]
