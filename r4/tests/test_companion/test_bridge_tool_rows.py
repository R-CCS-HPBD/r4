"""Tool rows on the JSONL rail open and close with the call's details
(companion sweep 2026-10-21, real bridge against a fake source).

- tool_call_start.args_summary was always "": the agent emits the model's
  arguments as a JSON string and the summary read only dicts.
- A denied call emitted tool_blocked with no data (the console row read
  "tool blocked") and its tool_call_start never got a tool_call_end.
"""

from __future__ import annotations

from typing import Any

from r4.companion.agent import CompanionEvent, CompanionEventType, ResearchCompanion
from r4.companion.ui.bridge_server import CompanionBridgeServer


def _bridge() -> tuple[CompanionBridgeServer, list[dict[str, Any]]]:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    lines: list[dict[str, Any]] = []
    server._emit_line = lines.append
    server._session_id_str = lambda: "s-1"
    server._emit_cost = lambda: None
    return server, lines


def _canonical(lines: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [line["payload"] for line in lines if line.get("type") == kind]


def test_tool_call_start_summarises_json_string_arguments() -> None:
    server, lines = _bridge()
    server._emit_canonical_tool_event(
        "tool_call",
        {"name": "shell_exec", "arguments": '{"command": "ls -la data"}', "tool_call_id": "c1"},
        "",
    )
    assert _canonical(lines, "tool_call_start")[0]["args_summary"] == "ls -la data"


def test_a_blocked_call_closes_its_row() -> None:
    server, lines = _bridge()
    server._emit_canonical_tool_event(
        "tool_blocked", {"name": "shell_exec", "tool_call_id": "c1"}, "Tool shell_exec rejected"
    )
    (end,) = _canonical(lines, "tool_call_end")
    assert (end["tool_call_id"], end["name"], end["status"]) == ("c1", "shell_exec", "error")


def test_the_agent_names_the_denied_call() -> None:
    from unittest.mock import MagicMock

    from r4.companion.context import ContextManager
    from r4.companion.permissions import PermissionManager, PermissionMode
    from r4.companion.skills import SkillRegistry

    client = MagicMock()
    perms = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    perms.set_approval_callback(lambda *_a: False)
    agent = ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=perms,
    )
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    tool = {"name": "shell_exec", "description": "", "parameters": {}, "execute": lambda **_: ""}
    call = {"id": "c9", "function": {"name": "shell_exec", "arguments": '{"command": "ls"}'}}
    agent._execute_tool_call(call, [tool])
    (blocked,) = [e for e in events if e.event_type == CompanionEventType.TOOL_BLOCKED]
    assert blocked.data == {"name": "shell_exec", "tool_call_id": "c9"}
