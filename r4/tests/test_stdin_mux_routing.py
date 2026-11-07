"""StdinMultiplexer routing — approval/ask-user answers reach their blocked
waiters even when the main-loop OOB poll is busy (starvation regression).

The pre-routing single queue let the 20 ms `_poll_turn_once` draw swallow an
`approval_response` as a "stray" line while `_read_approval` waited forever
(the live "turn hangs instead of finishing after approval" class).
"""

from __future__ import annotations

import json

from r4.companion.ui.bridge_server import StdinMultiplexer


def _mux() -> StdinMultiplexer:
    """Routing runs synchronously in _route — no reader thread needed."""
    return StdinMultiplexer()


def _line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def test_approval_response_goes_to_dedicated_queue_not_main() -> None:
    mux = _mux()
    mux._route(_line({"approval_response": {"id": 7, "decision": "yes"}}))
    assert mux.get(timeout=0.01) is None  # main queue stays clean
    out = mux.get_approval()
    assert out is not None and "approval_response" in out


def test_ask_user_response_goes_to_its_own_queue() -> None:
    mux = _mux()
    mux._route(_line({"ask_user_response": {"id": 3, "answer": "42"}}))
    assert mux.get(timeout=0.01) is None
    assert mux._take(mux._approval_queue, 0.01) is None  # approval queue untouched
    out = mux.get_ask_user()
    assert out is not None and "ask_user_response" in out


def test_cancel_turn_reaches_main_and_both_waiters() -> None:
    mux = _mux()
    mux._route(_line({"cancel_turn": True}))
    assert mux.get(timeout=0.01) is not None
    assert mux._take(mux._approval_queue, 0.01) is not None
    assert mux._take(mux._ask_user_queue, 0.01) is not None


def test_eof_wakes_every_consumer() -> None:
    mux = _mux()
    mux._route("")
    assert mux.get(timeout=0.01) is None
    assert mux.get_approval() is None
    assert mux.get_ask_user() is None


def test_plain_rpc_and_noise_stay_on_main_queue_only() -> None:
    mux = _mux()
    mux._route(_line({"id": 1, "method": "get_state", "params": {}}))
    mux._route("not json at all\n")
    assert mux.get(timeout=0.01) is not None
    assert mux.get(timeout=0.01) is not None
    # No waiter traffic routed from either line.
    assert mux._take(mux._approval_queue, 0.01) is None
    assert mux._take(mux._ask_user_queue, 0.01) is None


def test_blocked_approval_reader_cannot_be_starved_by_main_poll() -> None:
    """Regression pin for the starve: poll the main queue FIRST, then read
    the approval queue — the answer must still be there for the waiter."""
    mux = _mux()
    mux._route(_line({"approval_response": {"id": 9, "decision": "yes"}}))
    for _ in range(50):  # the busy OOB poll that used to eat the answer
        assert mux.get(timeout=0.001) is None
    out = mux.get_approval()
    assert out is not None and '"id": 9' in out
