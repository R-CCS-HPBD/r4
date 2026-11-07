"""A person's "no" at the approval prompt is named as theirs, never as a
policy block (stabilization sweep 2026-10-21, chat#8).

The transcript said "Tool shell_exec blocked by policy" and the model read
"The session policy denied this action" after the USER rejected the call.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from r4.companion import agent as agent_mod
from r4.companion.agent import ResearchCompanion, note_user_rejection
from r4.companion.cancellation import TurnCancellation
from r4.companion.ui.bridge_server import CompanionBridgeServer


class _Permissions:
    def __init__(self, user_said_no: bool):
        self.user_said_no = user_said_no

    def set_taint_registry(self, _registry: Any) -> None:
        pass

    def check(self, name: str, _args: dict) -> bool:
        if self.user_said_no:
            note_user_rejection(name)  # what the console's approval callback does
        return False


def _gate(user_said_no: bool) -> tuple[Any, list[str]]:
    agent = ResearchCompanion(MagicMock(), model="m", call_type="main")
    agent._permissions = _Permissions(user_said_no)
    blocked: list[str] = []
    agent.on_event(
        lambda e: blocked.append(e.message) if e.event_type.value == "tool_blocked" else None
    )
    return agent._permission_gate("shell_exec", {"command": "ls"}), blocked


def test_user_rejection_is_named_as_the_users() -> None:
    prepared, blocked = _gate(user_said_no=True)
    assert blocked == ["Tool shell_exec rejected by you"]
    assert "The user rejected" in prepared.error_result
    assert "policy" not in prepared.error_result


def test_policy_deny_keeps_the_policy_wording() -> None:
    prepared, blocked = _gate(user_said_no=False)
    assert blocked == ["Tool shell_exec blocked by policy"]
    assert "permission policy" in prepared.error_result


def _bridge(cancelled: bool) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._approval_seq = 0
    server._emit_line = lambda _line: None
    server._register_approval_intent = lambda *a: None
    server._decide_approval_intent = lambda *a: None
    server._session_id_str = lambda: "s-1"
    server._await_approval = lambda _rid, _q: "no"
    cancel = TurnCancellation()
    if cancelled:
        cancel.request()
    server._active_turn_cancel = cancel
    server._session = SimpleNamespace(permissions=MagicMock())
    return server


def _marked_after(server: CompanionBridgeServer) -> bool:
    out: dict[str, bool] = {}

    def run() -> None:  # a fresh thread: the mark is per thread
        server._on_tool_approval("shell_exec", "Run shell command: ls", {"command": "ls"})
        out["marked"] = agent_mod._take_user_rejection("shell_exec")

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    return out["marked"]


def test_bridge_marks_a_user_no_but_not_a_turn_cancel() -> None:
    assert _marked_after(_bridge(cancelled=False)) is True
    assert _marked_after(_bridge(cancelled=True)) is False
