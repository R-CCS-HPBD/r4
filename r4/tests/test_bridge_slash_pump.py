"""dispatch_slash_command keeps the stdin loop served (TUI audit, 2026-10-17).

A slash command ran on the bridge stdin thread: while a slow one (``/code
test``, ``/workflow trigger --wait``, an HTTP call near its timeout) was
running, nothing read stdin, so /btw, cancel and polls waited for it to end.
It now runs through the same pump as a chat turn.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from test_side_question import _run_turn_with_rpc, _wait_for_reply, mid_turn_server  # noqa: F401

from r4.companion.ui import bridge_server as bs


@pytest.fixture()
def slow_slash(monkeypatch, mid_turn_server):  # noqa: F811
    server, events, emitted = mid_turn_server
    server._session._turn_cancel = None
    server._session.request_turn_cancel = lambda: server._session._turn_cancel.request()

    def _slow_dispatch(command, *, session, store, renderer):
        del session, store
        for _ in range(12):  # ~0.6 s of work
            if getattr(server, "_active_turn_cancel", None) and server._active_turn_cancel.is_set():
                break
            time.sleep(0.05)
        events.append("slash_end")
        renderer.system(f"ran {command}")
        return True

    monkeypatch.setattr(bs, "dispatch_shared_command", _slow_dispatch)
    return server, events, emitted


def _run_slash_with_rpc(server, rpc_line):
    # _run_turn_with_rpc sends params {"text": "go"}; dispatch needs "command".
    original = server.handle_request

    def _handle(request):
        if request.get("method") == "dispatch_slash_command":
            request = {**request, "params": {"command": "/code test"}}
        return original(request)

    server.handle_request = _handle
    try:
        return _run_turn_with_rpc(server, rpc_line, "dispatch_slash_command")
    finally:
        server.handle_request = original


def _texts(result):
    return [row["text"] for row in result["response"]["result"]["output"]]


def test_btw_answers_while_a_slash_command_runs(slow_slash):
    server, events, emitted = slow_slash
    btw = '{"id": 7, "method": "session.side_question", "params": {"question": "q"}}'
    result = _run_slash_with_rpc(server, btw)
    assert _texts(result) == ["ran /code test"]
    reply = _wait_for_reply(emitted, 7)
    assert reply["result"]["answer"] == "btw answer"
    assert events.index(("emit", 7)) < events.index("slash_end")


def test_polls_answer_while_the_slash_command_runs(slow_slash):
    """Plan H-8: a read is served from the read pool mid-command (it parked
    until the command ended before)."""
    server, events, emitted = slow_slash
    poll = '{"id": 8, "method": "get_state", "params": {}}'
    result = _run_slash_with_rpc(server, poll)
    assert result["response"]["ok"] is True
    reply = _wait_for_reply(emitted, 8)
    assert reply["ok"] is True
    assert events.index(("emit", 8)) < events.index("slash_end")


def test_mutations_park_and_drain_after_the_slash_command(slow_slash):
    server, events, emitted = slow_slash
    server.session_rename = lambda params: events.append("rename") or {"name": params["name"]}
    rename = '{"id": 9, "method": "session.rename", "params": {"name": "n"}}'
    result = _run_slash_with_rpc(server, rename)
    assert result["response"]["ok"] is True
    assert _wait_for_reply(emitted, 9)["result"] == {"name": "n"}
    assert events.index("slash_end") < events.index("rename") < events.index(("emit", 9))
    assert events.index(("emit", 1)) < events.index("rename")  # after the command's reply (L2)


def test_cancel_stops_a_running_slash_command(slow_slash):
    server, events, emitted = slow_slash
    cancel = '{"cancel_turn": true}'  # the wire line v3 writes (jsonl-client.ts)
    started = time.monotonic()
    result = _run_slash_with_rpc(server, cancel)
    assert time.monotonic() - started < 0.55
    out = result["response"]["result"]
    assert out["cancelled"] is True
    assert any("stopped" in text for text in _texts(result))
    assert server._active_turn_cancel is None
    assert server._session._turn_cancel is None


@pytest.mark.parametrize("command", ["/dashboard", "/sessions"])
def test_bridge_local_slash_branches_still_answer(slow_slash, command):
    server, _events, _emitted = slow_slash
    server.backend_dashboard = lambda _params: {"dashboard": {}}
    server._store = SimpleNamespace(save=lambda _s: None, list_sessions=lambda limit: [])
    out = server.dispatch_slash_command({"command": command})
    assert out["handled"] is True
    assert "cancelled" not in out
