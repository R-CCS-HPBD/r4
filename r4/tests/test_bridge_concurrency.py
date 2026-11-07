"""Bridge concurrency (plan H-8, 2026-10-18).

Reads answer from the read pool, during a turn too; mutations stay on the
stdin thread, in arrival order, exclusive of reads; every served verb has a
class. Before H-8 a mid-turn poll parked until the turn ended, so the
mid-turn tests below time out on the old code.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import threading
import time
from pathlib import Path

import pytest
from test_side_question import _FakeSession, _wait_for_reply, mid_turn_server  # noqa: F401

from r4.capabilities import CAPABILITIES
from r4.companion.ui import bridge_server as bs
from r4.companion.ui.rpc_concurrency import (
    RPC_CLASSES,
    TURN_RPCS,
    ReadPool,
    RpcClass,
    StateLock,
    rpc_class,
    serves_concurrently,
)

REPO = Path(__file__).resolve().parents[2]


def _line(request_id: int, method: str, **params) -> str:
    return json.dumps({"id": request_id, "method": method, "params": params})


# -- classification ---------------------------------------------------------


def test_every_served_rpc_has_a_concurrency_class():
    server = bs.CompanionBridgeServer.__new__(bs.CompanionBridgeServer)
    served = set(bs._bridge_rpc_bindings(server))
    assert sorted(served - set(RPC_CLASSES)) == [], "classify the new verb in rpc_concurrency.py"
    assert sorted(set(RPC_CLASSES) - served) == [], "a classified verb is no longer served"


def test_an_unknown_verb_is_serialized():
    assert rpc_class("no.such_verb") is RpcClass.MUTATE
    assert not serves_concurrently("no.such_verb")


def test_a_capability_declared_mutating_is_never_a_pool_read():
    for cap in CAPABILITIES:
        if cap.bridge is None:
            continue
        assert cap.bridge in RPC_CLASSES, cap.name
        if cap.mutating:
            assert RPC_CLASSES[cap.bridge] is not RpcClass.READ, cap.name


def test_v3_turn_methods_match_the_turn_class():
    # TURN_METHODS lives in the shared client wire (ADR-103 D-3, 2026-10-31).
    source = (REPO / "packages/r4-tui-v3/src/bridge/wire.ts").read_text()
    block = re.search(r"TURN_METHODS[^=]*=\s*new Set\(\[(.*?)\]\)", source, re.S)
    assert block is not None
    assert set(re.findall(r'"([^"]+)"', block.group(1))) == set(TURN_RPCS)


# -- the state lock -----------------------------------------------------------


def test_a_mutation_waits_for_an_admitted_read_released_by_another_thread():
    lock = StateLock()
    lock.acquire_read()  # admitted on the "stdin" thread
    order: list[str] = []
    writer = threading.Thread(target=lambda: (lock.acquire_write(), order.append("write")))
    writer.start()
    time.sleep(0.05)
    assert order == []
    releaser = threading.Thread(target=lambda: (order.append("read done"), lock.release_read()))
    releaser.start()
    writer.join(2)
    assert order == ["read done", "write"]
    lock.release_write()


def test_the_writer_may_reenter_and_read_inline():
    lock = StateLock()
    with lock.write():
        with lock.write():
            with lock.read():
                pass
    with lock.read():  # fully released
        pass


def test_pool_threads_are_daemons():
    pool = ReadPool(workers=2)
    done = threading.Event()
    pool.submit(done.set)
    assert done.wait(2)
    names = {t.name: t.daemon for t in threading.enumerate() if t.name.startswith("bridge-read-")}
    assert names and all(names.values())


# -- serving ----------------------------------------------------------------


class _GatedSession(_FakeSession):
    """A turn that runs until the test releases it (or cancel stops it)."""

    def __init__(self, events):
        super().__init__(events)
        self.started = threading.Event()
        self.release = threading.Event()
        self.usage = {"llm_calls": 3}

    def usage_report(self):
        return dict(self.usage)

    def send(self, _text, stream=True):
        del stream
        self.started.set()
        self.release.wait(10)
        self._events.append("turn_end")
        return "final answer"

    def request_turn_cancel(self):
        self._events.append("cancel_requested")


@pytest.fixture()
def gated(mid_turn_server):  # noqa: F811
    server, events, emitted = mid_turn_server
    session = _GatedSession(events)
    server._session = session
    yield server, session, events, emitted
    session.release.set()


def _start_turn(server, session) -> tuple[threading.Thread, dict]:
    result: dict = {}

    def _turn():
        result["response"] = server.handle_request(
            {"id": 1, "method": "send_message", "params": {"text": "go"}}
        )
        result["at"] = time.monotonic()

    thread = threading.Thread(target=_turn)
    thread.start()
    assert session.started.wait(5)
    return thread, result


def test_mid_turn_reads_answer_before_the_turn_ends(gated):
    server, session, events, emitted = gated
    thread, result = _start_turn(server, session)
    sent = time.monotonic()
    server._stdin_mux._route(_line(8, "get_state"))
    server._stdin_mux._route(_line(9, "get_usage"))
    state = _wait_for_reply(emitted, 8)
    usage = _wait_for_reply(emitted, 9)
    assert time.monotonic() - sent < 2.0  # its own latency, not the turn's
    assert "turn_end" not in events
    assert state == {"id": 8, "ok": True, "result": {"fake": True}}
    assert usage["result"] == {"usage": {"llm_calls": 3}}
    session.release.set()
    thread.join(5)
    assert result["response"]["result"]["response"] == "final answer"


def test_cancel_turn_rpc_answers_mid_turn(gated):
    server, session, events, emitted = gated
    thread, result = _start_turn(server, session)
    server._stdin_mux._route(_line(5, "cancel_turn"))
    assert _wait_for_reply(emitted, 5)["result"] == {"ok": True}
    thread.join(5)
    assert result["response"]["result"]["cancelled"] is True
    assert "turn_end" not in events


def test_cancel_is_not_held_up_by_a_slow_read_in_flight(gated):
    server, session, events, emitted = gated
    read_release = threading.Event()
    server.get_usage = lambda _params: (read_release.wait(5), {"usage": "late"})[1]
    thread, result = _start_turn(server, session)
    server._stdin_mux._route(_line(8, "get_usage"))
    time.sleep(0.05)
    cancelled_at = time.monotonic()
    server._stdin_mux._route('{"cancel_turn": true}')
    thread.join(5)
    assert result["response"]["result"]["cancelled"] is True
    assert result["at"] - cancelled_at < 1.0
    assert not [p for p in emitted if p.get("id") == 8]  # the read is still running
    read_release.set()
    assert _wait_for_reply(emitted, 8)["result"] == {"usage": "late"}


def test_mutations_sent_mid_turn_run_after_it_in_order(gated):
    server, session, events, emitted = gated
    server.session_rename = lambda params: events.append(f"rename {params['name']}") or params
    thread, result = _start_turn(server, session)
    server._stdin_mux._route(_line(11, "session.rename", name="a"))
    server._stdin_mux._route(_line(12, "session.rename", name="b"))
    time.sleep(0.1)
    assert not [p for p in emitted if p.get("id") in (11, 12)]  # parked
    session.release.set()
    thread.join(5)
    server._drain_oob_parked()  # the stdin loop's next step after the reply
    ids = [p.get("id") for p in emitted if p.get("id") in (11, 12)]
    assert ids == [11, 12]
    assert events.index("turn_end") < events.index("rename a") < events.index("rename b")


def test_a_send_message_sent_mid_turn_replies_after_the_first_turn(loop):
    """Cycle 3, L2: the parked second turn ran inside the first turn's
    ``finally``, so its events and reply came before the first turn's reply."""
    server, mux, events, emitted = loop
    session = server._session
    mux._route(_line(1, "send_message", text="first"))
    assert session.started.wait(5)
    mux._route(_line(2, "send_message", text="second"))
    time.sleep(0.1)
    session.release.set()
    _wait_for_reply(emitted, 2)
    replies = [p.get("id") for p in emitted if p.get("id") in (1, 2)]
    assert replies == [1, 2]
    assert events.index(("emit", 1)) < events.index(("emit", 2))


# -- the stdin loop (main) ----------------------------------------------------


@pytest.fixture()
def loop(gated):
    server, session, events, emitted = gated
    mux = server._stdin_mux
    thread = threading.Thread(target=bs._serve, args=(server, mux), daemon=True)
    thread.start()
    yield server, mux, events, emitted
    mux._route("")  # EOF
    thread.join(5)


def test_two_mutations_keep_their_order(loop):
    server, mux, events, emitted = loop

    def _rename(params):
        events.append(f"start {params['name']}")
        time.sleep(0.15 if params["name"] == "slow" else 0)
        events.append(f"end {params['name']}")
        return params

    server.session_rename = _rename
    mux._route(_line(1, "session.rename", name="slow"))
    mux._route(_line(2, "session.rename", name="fast"))
    _wait_for_reply(emitted, 2)
    assert events == [
        "start slow",
        "end slow",
        ("emit", 1),
        "start fast",
        "end fast",
        ("emit", 2),
    ]


def test_reads_never_see_a_half_applied_mutation(loop):
    server, mux, _events, emitted = loop
    state = {"a": 0, "b": 0}

    def _slow_read(**_kw):
        a = state["a"]
        time.sleep(0.1)  # a mutation running now would land between a and b
        return {"a": a, "b": state["b"]}

    def _two_step_write(params):
        state["a"] = params["n"]
        time.sleep(0.02)
        state["b"] = params["n"]
        return params

    server.snapshot = _slow_read
    server.session_rename = _two_step_write
    script = [
        ("get_state", {}),
        ("session.rename", {"n": 1}),
        ("get_state", {}),
        ("get_state", {}),
        ("session.rename", {"n": 2}),
        ("get_state", {}),
    ]
    for request_id, (method, params) in enumerate(script, start=1):
        mux._route(_line(request_id, method, **params))
    reads = {rid: _wait_for_reply(emitted, rid)["result"] for rid in (1, 3, 4, 6)}
    assert all(r["a"] == r["b"] for r in reads.values()), reads
    assert [reads[rid]["a"] for rid in (1, 3, 4, 6)] == [0, 1, 1, 2]


def test_a_read_asked_inside_a_mutation_answers_inline(mid_turn_server):  # noqa: F811
    server, _events, _emitted = mid_turn_server
    with server._rw().write():  # a mutation that asks a read itself
        reply = server.handle_request({"id": 2, "method": "get_state", "params": {}})
    assert reply == {"id": 2, "ok": True, "result": {"fake": True}}


def test_a_read_before_initialize_answers_inline(mid_turn_server):  # noqa: F811
    server, _events, _emitted = mid_turn_server
    server._session = None
    server.snapshot = lambda **_kw: {"inline": True}
    reply = server.handle_request({"id": 3, "method": "get_state", "params": {}})
    assert reply == {"id": 3, "ok": True, "result": {"inline": True}}


# -- replies ------------------------------------------------------------------


def test_a_torn_read_is_retried(mid_turn_server):  # noqa: F811
    server, _events, _emitted = mid_turn_server
    calls = {"n": 0}

    def _flaky(_params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return {"usage": {}}

    server.get_usage = _flaky
    reply = server._answer_read(4, "get_usage", {})
    assert reply == {"id": 4, "ok": True, "result": {"usage": {}}}
    assert calls["n"] == 2


def test_replies_reach_the_bound_wire_while_stdout_is_redirected(mid_turn_server):  # noqa: F811
    server, _events, _emitted = mid_turn_server
    wire, captured = io.StringIO(), io.StringIO()
    server._wire = wire
    del server._emit_line  # the fixture's spy; use the real method
    with contextlib.redirect_stdout(captured):  # e.g. a tool capturing CLI output
        server._emit_line({"id": 1, "ok": True, "result": {}})
    assert captured.getvalue() == ""
    assert json.loads(wire.getvalue()) == {"id": 1, "ok": True, "result": {}}


def test_venue_projection_is_one_update():
    class _Meta(dict):
        writes = 0

        def __setitem__(self, key, value):
            type(self).writes += 1
            super().__setitem__(key, value)

    session = type("S", (), {})()
    session.metadata = _Meta()
    bs._project_runtime(session, {"state": "running"})
    assert session.metadata == {"venue_runtime": {"state": "running"}, "state": "ongoing"}
    assert _Meta.writes == 0  # no per-key writes a concurrent snapshot could split
