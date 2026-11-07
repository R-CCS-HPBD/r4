"""Pin for the bridge's request counters under parallel agents (ADR-092 D-6).

``self._approval_seq += 1`` then a separate read is two steps: two agents
asking at once could both read the second value and share one request id,
so one approval reply would answer both. The counters are now locked.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from r4.companion.ui.bridge_server import CompanionBridgeServer


class _SlowCounterServer(CompanionBridgeServer):
    """Widens the window between the increment and the read, deterministically."""

    def _get(self, name: str) -> int:
        return self.__dict__.get(f"_v{name}", 0)

    def _set(self, name: str, value: int) -> None:
        self.__dict__[f"_v{name}"] = value
        time.sleep(0.05)  # the other agent gets the GIL here

    _approval_seq = property(
        lambda self: self._get("approval"), lambda self, v: self._set("approval", v)
    )
    _ask_user_seq = property(lambda self: self._get("ask"), lambda self, v: self._set("ask", v))


def _server() -> _SlowCounterServer:
    server = _SlowCounterServer.__new__(_SlowCounterServer)
    server._emitted = []
    server._emit_line = server._emitted.append
    server._register_approval_intent = lambda *a: None
    server._decide_approval_intent = lambda *a: None
    server._await_approval = lambda request_id, _q: "no"
    server._read_ask_user = lambda request_id: str(request_id)
    server._session_id_str = lambda: "s-1"
    return server


def _run_concurrently(*calls: Any) -> list[Any]:
    results: list[Any] = [None] * len(calls)
    start = threading.Barrier(len(calls))

    def _run(index: int) -> None:
        start.wait(5)
        results[index] = calls[index]()

    threads = [threading.Thread(target=_run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    return results


def test_concurrent_approvals_get_distinct_ids() -> None:
    server = _server()
    _run_concurrently(
        lambda: server._on_tool_approval("file_write", "[agent a] w", {"path": "a"}),
        lambda: server._on_tool_approval("file_write", "[agent b] w", {"path": "b"}),
    )
    ids = [line["approval_request"]["id"] for line in server._emitted if "approval_request" in line]
    assert sorted(ids) == [1, 2]


def test_concurrent_ask_user_get_distinct_ids() -> None:
    server = _server()
    answers = _run_concurrently(
        lambda: server._on_ask_user("which?", ["x"]),
        lambda: server._on_ask_user("which?", ["y"]),
    )
    assert sorted(answers) == ["1", "2"]


def test_init_creates_the_counter_lock() -> None:
    from unittest.mock import MagicMock

    server = CompanionBridgeServer(client=MagicMock(), store=MagicMock(db_path=":memory:"))
    assert isinstance(server._seq_lock, type(threading.Lock()))
