"""stdin EOF while a turn runs ends the bridge (companion sweep 2026-10-21).

The TUI closing (or dying) closes the bridge's stdin. The multiplexer put one
EOF marker on each queue; when a turn was running, its 20 ms poll took the
main-queue marker and dropped it, so after the turn the main loop blocked in
``mux.get()`` forever: one orphaned bridge per closed TUI. A turn waiting on
an approval never ended at all (nobody can answer it any more).
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion.ui import bridge_server as bs
from r4.companion.ui.bridge_server import CompanionBridgeServer, StdinMultiplexer, _serve


class _Session(SimpleNamespace):
    def request_turn_cancel(self) -> None:
        self._turn_cancel.request()  # set by the bridge's _start_turn_cancel


def _server(mux: StdinMultiplexer) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._stdin_mux = mux
    server._active_turn_cancel = None
    server._pending_approvals = {}
    server._approval_seq = 0
    server.should_exit = False
    server._emitted: list[dict[str, Any]] = []
    server._emit_line = server._emitted.append
    server._session = _Session(metadata={}, _current_turn_id=None, _turn_cancel=None)
    server._store = SimpleNamespace(save=lambda _s: None)
    server._register_approval_intent = lambda *a: None
    server._decide_approval_intent = lambda *a: None
    server._session_id_str = lambda: "s-1"
    server._emit_budget_state = lambda: None
    server._emit_cost = lambda: None
    server._maybe_emit_auto_title = lambda: None
    server._turn_result = lambda r, c, _t: {"response": r, "cancelled": c}
    return server


def _serve_in_thread(server: CompanionBridgeServer, mux: StdinMultiplexer) -> threading.Thread:
    thread = threading.Thread(target=_serve, args=(server, mux), daemon=True)
    thread.start()
    return thread


def _send(server: CompanionBridgeServer, mux: StdinMultiplexer) -> None:
    mux._route(json.dumps({"id": 1, "method": "send_message", "params": {"text": "go"}}) + "\n")


@pytest.fixture(autouse=True)
def _model_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "ensure_model_ready", lambda _s: None)


def test_eof_during_a_streaming_turn_stops_it_and_the_bridge_exits() -> None:
    mux = StdinMultiplexer()
    server = _server(mux)
    started = threading.Event()

    def send(_text: str, stream: bool = True) -> str:
        started.set()
        server._active_turn_cancel._event.wait(10)  # streams until stopped
        return "partial"

    server._session.send = send
    loop = _serve_in_thread(server, mux)
    _send(server, mux)
    assert started.wait(5)
    mux._route("")  # the TUI went away
    loop.join(5)
    assert not loop.is_alive(), "bridge main loop never exited after stdin EOF"
    reply = next(e for e in server._emitted if e.get("id") == 1)
    assert reply["result"]["cancelled"] is True


def test_eof_while_an_approval_waits_denies_it_and_the_bridge_exits() -> None:
    mux = StdinMultiplexer()
    server = _server(mux)
    decided: dict[str, bool] = {}

    def send(_text: str, stream: bool = True) -> str:
        decided["approved"] = server._on_tool_approval(
            "shell_exec", "Run shell command: rm -rf data", {"command": "rm -rf data"}
        )
        return "done"

    server._session.send = send
    loop = _serve_in_thread(server, mux)
    _send(server, mux)
    for _ in range(500):
        if any("approval_request" in e for e in server._emitted):
            break
        threading.Event().wait(0.01)
    mux._route("")
    loop.join(5)
    assert not loop.is_alive(), "bridge main loop never exited after stdin EOF"
    for _ in range(200):
        if decided:
            break
        threading.Event().wait(0.01)
    assert decided == {"approved": False}


def test_eof_marker_stays_visible_after_a_poll_took_it() -> None:
    mux = StdinMultiplexer()
    mux._route("")
    assert mux.get(timeout=0.01) is None
    assert mux.closed
    # A second reader (the main loop after the turn's poll) still sees EOF
    # at once instead of waiting forever.
    second: list[Any] = []
    reader = threading.Thread(target=lambda: second.append(mux.get()), daemon=True)
    reader.start()
    reader.join(2)
    assert second == [None]


# -- a stopped turn's worker vs the next turn (companion sweep 2026-10-21) ----


def _two_turn_server(release: threading.Event, log: list[str]) -> CompanionBridgeServer:
    """A session whose first turn ignores the stop until ``release`` (a
    provider that has not sent a byte yet) and whose second turn just runs."""
    server = _server(StdinMultiplexer())
    calls = {"n": 0}

    def send(_text: str, stream: bool = True) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            release.wait(10)
            log.append("first turn unwound")
            return "late"
        log.append("second turn ran")
        return "second"

    server._session.send = send
    return server


def _stop_first_turn(server: CompanionBridgeServer) -> dict[str, Any]:
    out: dict[str, Any] = {}
    first = threading.Thread(
        target=lambda: out.update(server.send_message({"text": "one"})), daemon=True
    )
    first.start()
    for _ in range(500):
        if server._active_turn_cancel is not None:
            break
        threading.Event().wait(0.01)
    server.cancel_turn({})
    first.join(5)
    return out


def test_next_turn_waits_for_the_stopped_turn_to_unwind() -> None:
    release, log = threading.Event(), []
    server = _two_turn_server(release, log)
    assert _stop_first_turn(server)["cancelled"] is True
    threading.Timer(0.3, release.set).start()
    second = server.send_message({"text": "two"})
    assert second["response"] == "second"
    assert log == ["first turn unwound", "second turn ran"]


def test_next_turn_refuses_while_the_stopped_turn_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs, "_STOPPED_TURN_GRACE_SEC", 0.2)
    release, log = threading.Event(), []
    server = _two_turn_server(release, log)
    _stop_first_turn(server)
    with pytest.raises(RuntimeError, match="still finishing"):
        server.send_message({"text": "two"})
    release.set()
    assert "second turn ran" not in log
