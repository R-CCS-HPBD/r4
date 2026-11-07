"""/btw side questions (Claude-Code parity, 2026-09-24): answered from the
current conversation, no tools, never recorded; the bridge answers them on a
worker thread so the stdin loop keeps serving."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from r4.companion.side_question import SIDE_SYSTEM, answer_side_question, build_side_prompt
from r4.companion.ui import bridge_server as bs
from r4.companion.ui.bridge_server import CompanionBridgeServer


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


def test_prompt_carries_the_conversation_tail_and_the_question():
    msgs = [_msg("system", "sys"), _msg("user", "What is X?"), _msg("assistant", "X is Y.")]
    prompt = build_side_prompt(msgs, "and Z?")
    assert "[user] What is X?" in prompt and "[assistant] X is Y." in prompt
    assert "sys" not in prompt
    assert prompt.endswith("Side question: and Z?")


def test_long_messages_are_clipped():
    prompt = build_side_prompt([_msg("user", "a" * 5000)], "q", per_message=100)
    assert "a" * 100 + " …" in prompt and "a" * 101 not in prompt


def test_answer_uses_the_session_model_without_tools_and_changes_nothing():
    calls = []

    class _Client:
        def chat(self, prompt, **kw):
            calls.append((prompt, kw))
            return SimpleNamespace(content=" the answer ")

    messages = [_msg("user", "hi")]
    session = SimpleNamespace(
        client=_Client(), agent=SimpleNamespace(model="m-1"), messages=messages
    )
    assert answer_side_question(session, "why?") == "the answer"
    assert calls[0][1]["system"] == SIDE_SYSTEM and calls[0][1]["model"] == "m-1"
    assert session.messages == messages and len(messages) == 1  # never recorded


def test_empty_question_is_refused():
    with pytest.raises(ValueError):
        answer_side_question(SimpleNamespace(client=object()), "  ")


def test_bridge_answers_side_questions_off_the_stdin_loop(monkeypatch):
    emitted = []
    done = threading.Event()
    fake = SimpleNamespace()
    fake._emit_line = lambda payload: (emitted.append(payload), done.set())
    fake._handle_now = lambda rid, method, params: {"id": rid, "ok": True, "result": method}
    reply = CompanionBridgeServer.handle_request(
        fake, {"id": 7, "method": "session.side_question", "params": {"question": "q"}}
    )
    assert reply is None  # nothing emitted inline
    assert done.wait(5)
    assert emitted == [{"id": 7, "ok": True, "result": "session.side_question"}]


# ---------------------------------------------------------------------------
# Mid-turn routing (2026-10-06): the turn pump used to consume and discard any
# non-OOB stdin line — a /btw thrown mid-turn went permanently silent. Now
# deferred verbs answer during the turn, other RPCs park + drain at turn end.
# ---------------------------------------------------------------------------


class _FakeClient:
    def chat(self, _prompt, **_kw):
        time.sleep(0.05)  # model round trip
        return SimpleNamespace(content="btw answer")


class _FakeSession:
    def __init__(self, events):
        self._events = events
        self.session_id = "t-1"
        self.name = "t"
        self.metadata = {}
        self.messages = [SimpleNamespace(role="user", content="hi")]
        self.client = _FakeClient()
        self.agent = SimpleNamespace(model="fake-model", on_event=None)
        self.active_endpoints = []
        self.skills = SimpleNamespace(list_active=lambda: [], list_all=lambda: [])

    def usage_report(self):
        return {}

    def send(self, _text, stream=True):
        del stream
        for _ in range(12):  # ~0.6 s of streaming
            time.sleep(0.05)
        self._events.append("turn_end")
        return "final answer"

    def confirm_plan(self, _action, stream=True):
        del stream
        return self.send("plan")


@pytest.fixture()
def mid_turn_server(monkeypatch):
    """Bare server with real routing methods + a fake session mid-turn."""
    events: list = []
    server = bs.CompanionBridgeServer.__new__(bs.CompanionBridgeServer)
    server._session = _FakeSession(events)
    server._store = SimpleNamespace(save=lambda _s: None)
    server._stdin_mux = bs.StdinMultiplexer()  # reader thread not started
    server._active_turn_cancel = None
    server._pending_approvals = {}
    server._subscribed_agent_id = None
    server._output_lock = threading.Lock()
    server.should_exit = False
    emitted: list = []
    orig_emit = server._emit_line

    def _emit_spy(payload):
        emitted.append(payload)
        marker = payload.get("id") if isinstance(payload, dict) else None
        events.append(("emit", marker))
        orig_emit(payload)

    server._emit_line = _emit_spy
    server._emit_budget_state = lambda: None
    server._emit_cost = lambda: None
    server._maybe_emit_auto_title = lambda: None
    server._session_is_frontend = lambda: False
    server.snapshot = lambda **_kw: {"fake": True}
    monkeypatch.setattr(bs, "ensure_model_ready", lambda _s: None)
    return server, events, emitted


def _run_turn_with_rpc(server, rpc_line, method):
    """Drive handle_request(send_message) on a thread; inject rpc mid-turn."""
    result: dict = {}

    def _turn():
        try:
            result["response"] = server.handle_request(
                {"id": 1, "method": method, "params": {"text": "go"}}
            )
            # What the stdin loop (bs._serve) does next: the turn's reply,
            # then the requests parked during it (cycle 3, L2).
            server._emit_line(result["response"])
            server._drain_oob_parked()
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_turn)
    thread.start()
    time.sleep(0.1)  # turn is mid-flight
    server._stdin_mux._route(rpc_line)  # == the stdin reader thread
    thread.join(timeout=10)
    assert not thread.is_alive()
    return result


def _wait_for_reply(emitted, request_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        replies = [p for p in emitted if p.get("id") == request_id]
        if replies:
            return replies[0]
        time.sleep(0.02)
    raise AssertionError(f"no reply for request id {request_id}")


def test_btw_answers_while_the_turn_is_still_running(mid_turn_server):
    server, events, emitted = mid_turn_server
    btw = '{"id": 7, "method": "session.side_question", "params": {"question": "q"}}'
    result = _run_turn_with_rpc(server, btw, "send_message")
    assert result["response"]["result"]["response"] == "final answer"
    assert "cancelled" not in result["response"]["result"]
    reply = _wait_for_reply(emitted, 7)
    assert reply["result"]["answer"] == "btw answer"
    # W2 promise: the answer landed while the turn was still streaming.
    assert events.index(("emit", 7)) < events.index("turn_end")


def test_mutating_rpc_mid_turn_parks_and_drains_at_turn_end(mid_turn_server):
    # Plan H-8: reads answer mid-turn (test_bridge_concurrency.py); a
    # mutation still parks and runs once the turn has finished.
    server, events, emitted = mid_turn_server
    server.session_rename = lambda params: events.append("rename") or {"name": params["name"]}
    rename = '{"id": 8, "method": "session.rename", "params": {"name": "n"}}'
    result = _run_turn_with_rpc(server, rename, "send_message")
    assert result["response"]["result"]["response"] == "final answer"
    replies = [p for p in emitted if p.get("id") == 8]
    assert replies and replies[0]["ok"] is True
    # …and the parked mutation drained only after the turn finished and
    # replied (L2: it ran inside the turn, before the turn's own reply).
    assert events.index("turn_end") < events.index("rename") < events.index(("emit", 8))
    assert events.index(("emit", 1)) < events.index("rename")
