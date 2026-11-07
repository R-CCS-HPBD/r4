"""A cancel from an earlier turn must not cancel the next turn's approval
(stabilization sweep 2026-10-21, chat#0).

The stdin multiplexer copies every cancel_turn line onto the approval and
ask_user waiter queues so a BLOCKED waiter sees it. When no waiter was
blocked (Esc while the reply streamed), the copies stayed parked; the next
turn's first approval wait took one, cancelled that new turn and answered
its approval "no" while the TUI card said "gate approved".
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion.ui import bridge_server as bs
from r4.companion.ui.bridge_server import (
    CompanionBridgeServer,
    StdinMultiplexer,
    _handle_bridge_line,
)


def _line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


class _Session(SimpleNamespace):
    """Just enough of CompanionSession for send_message's turn path."""

    def request_turn_cancel(self) -> None:
        self.cancel_requests += 1


def _server(mux: StdinMultiplexer) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._stdin_mux = mux
    server._active_turn_cancel = None
    server._pending_approvals = {}
    server._approval_seq = 0
    server._emitted: list[dict[str, Any]] = []
    server._emit_line = server._emitted.append
    server._session = _Session(metadata={}, cancel_requests=0, _current_turn_id=None)
    server._store = SimpleNamespace(save=lambda _s: None)
    server._register_approval_intent = lambda *a: None
    server._decide_approval_intent = lambda *a: None
    server._session_id_str = lambda: "s-1"
    server._emit_budget_state = lambda: None
    server._emit_cost = lambda: None
    server._maybe_emit_auto_title = lambda: None
    server._turn_result = lambda r, c, _t: {"response": r, "cancelled": c}
    return server


def _approve_when_asked(server: CompanionBridgeServer, mux: StdinMultiplexer) -> None:
    """The user answers "yes" once the card is up (the TUI's only line)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        asks = [e for e in server._emitted if "approval_request" in e]
        if asks:
            rid = asks[0]["approval_request"]["id"]
            mux._route(_line({"approval_response": {"id": rid, "decision": "yes"}}))
            return
        time.sleep(0.01)


def test_turn_start_drops_cancels_parked_on_waiter_queues() -> None:
    mux = StdinMultiplexer()
    mux._route(_line({"approval_response": {"id": 4, "decision": "yes"}}))
    mux._route(_line({"cancel_turn": True}))
    mux.drop_stale_cancels()
    assert mux._take(mux._ask_user_queue, 0.01) is None
    kept = mux._take(mux._approval_queue, 0.01)
    assert kept is not None and "approval_response" in kept  # answers stay
    assert mux._take(mux._approval_queue, 0.01) is None


def test_approval_after_an_earlier_interrupt_runs_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "ensure_model_ready", lambda _s: None)
    mux = StdinMultiplexer()
    server = _server(mux)
    # Esc earlier (no turn, no waiter): the TUI sends 3 cancel_turn lines;
    # the main loop handles its copies as a no-op.
    for _ in range(3):
        mux._route(_line({"cancel_turn": True}))
    while (main := mux.get(timeout=0.01)) is not None:
        _handle_bridge_line(server, main)
    approved: dict[str, bool] = {}

    def send(_text: str, stream: bool = True) -> str:
        approved["ok"] = server._on_tool_approval(
            "shell_exec", "Run shell command: ls", {"command": "ls"}
        )
        return "ran" if approved["ok"] else "denied"

    server._session.send = send
    answer = threading.Thread(target=_approve_when_asked, args=(server, mux), daemon=True)
    answer.start()
    result = server.send_message({"text": "go"})
    answer.join(5)
    assert approved == {"ok": True}
    assert result == {"response": "ran", "cancelled": False}
    assert server._session.cancel_requests == 0
