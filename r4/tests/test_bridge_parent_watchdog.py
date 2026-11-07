"""Parent-death watchdog pins (2026-10-05 orphan-bridge class).

Two consecutive re-parented polls wake the mux with the EOF-shaped marker
(the main loop's exit path); a bridge started already re-parented gets no
watchdog at all (container/daemon context).
"""

from __future__ import annotations

import time

from r4.companion.ui.bridge_server import _start_parent_watchdog


class _FakeMux:
    def __init__(self):
        self.routed: list[str] = []

    def _route(self, line: str) -> None:
        self.routed.append(line)


def test_two_reparented_polls_route_the_eof_marker(monkeypatch):
    import os

    ppid_values = iter([100, 1, 1])
    monkeypatch.setattr(os, "getppid", lambda: next(ppid_values, 1))
    mux = _FakeMux()
    thread = _start_parent_watchdog(mux, interval=0.01, misses=2)
    assert thread is not None
    deadline = time.time() + 2
    while not mux.routed and time.time() < deadline:
        time.sleep(0.01)
    assert mux.routed == [""]


def test_living_parent_routes_nothing(monkeypatch):
    import os

    monkeypatch.setattr(os, "getppid", lambda: 4242)
    mux = _FakeMux()
    thread = _start_parent_watchdog(mux, interval=0.01, misses=2)
    assert thread is not None
    time.sleep(0.06)
    assert mux.routed == []


def test_daemon_start_gets_no_watchdog(monkeypatch):
    import os

    monkeypatch.setattr(os, "getppid", lambda: 1)
    mux = _FakeMux()
    assert _start_parent_watchdog(mux, interval=0.01, misses=1) is None
    assert mux.routed == []
