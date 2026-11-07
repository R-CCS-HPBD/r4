"""``search_server.*`` bridge verbs (ADR-102 amendment, 2026-11-01).

The console starts the local SearXNG through these: ``up`` answers at once
and reports each step and the end as ``search_server_event`` lines.
Hermetic: ``searxng_local`` is stubbed and the worker thread runs inline.
"""

from __future__ import annotations

from typing import Any

import pytest

from r4.agent import searxng_local
from r4.companion.ui import search_server_bridge
from r4.companion.ui.search_server_bridge import EVENT_CHANNEL, SearchServerBridgeMixin


class _Host(SearchServerBridgeMixin):
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def _emit_line(self, payload: dict[str, Any]) -> None:
        self.lines.append(payload)

    def events(self) -> list[dict[str, Any]]:
        return [line[EVENT_CHANNEL] for line in self.lines]


class _InlineThread:
    """Runs the target on ``start`` so the test sees every event."""

    def __init__(self, target: Any, daemon: bool = False) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


@pytest.fixture(autouse=True)
def _inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_server_bridge.threading, "Thread", _InlineThread)


def test_up_reports_each_step_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    def up(progress: Any = None) -> dict[str, Any]:
        progress("downloading img (first start only, about 200 MB)")
        progress("starting the container with podman")
        return {"state": "started", "url": "http://127.0.0.1:8888"}

    monkeypatch.setattr(searxng_local, "up", up)
    host = _Host()
    assert host.search_server_up({}) == {"accepted": True, "state": "starting"}
    events = host.events()
    assert [e["phase"] for e in events] == ["progress", "progress", "done"]
    assert events[-1]["url"] == "http://127.0.0.1:8888"
    assert host._search_server_starting is False


def test_a_failed_start_ends_with_an_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = {"state": "failed", "reason": "pull x: unauthorized", "hint": "podman logout"}
    monkeypatch.setattr(searxng_local, "up", lambda progress=None: failed)
    host = _Host()
    host.search_server_up({})
    assert host.events()[-1] == {
        "phase": "error",
        "state": "failed",
        "url": "",
        "reason": "pull x: unauthorized",
        "hint": "podman logout",
    }


def test_an_exception_still_ends_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(progress: Any = None) -> dict[str, Any]:
        raise OSError("engine vanished")

    monkeypatch.setattr(searxng_local, "up", boom)
    host = _Host()
    host.search_server_up({})
    assert host.events()[-1]["phase"] == "error"
    assert "engine vanished" in host.events()[-1]["reason"]
    assert host._search_server_starting is False


def test_a_second_up_while_one_runs_is_not_accepted() -> None:
    host = _Host()
    host._search_server_starting = True
    assert host.search_server_up({}) == {"accepted": False, "state": "starting"}
    assert host.lines == []


def test_status_and_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(searxng_local, "status", lambda: {"state": "down", "url": "http://x"})
    monkeypatch.setattr(searxng_local, "container_engine", lambda: "podman")
    monkeypatch.delenv("R4_WEB_SEARCH_URL", raising=False)
    host = _Host()
    assert host.search_server_status({}) == {
        "state": "down",
        "url": "http://x",
        "engine": "podman",
        "starting": False,
        "external_url": "",
    }
    monkeypatch.setattr(searxng_local, "down", lambda: {"state": "failed", "reason": "busy"})
    assert host.search_server_down({}) == {"state": "failed", "reason": "busy"}
    monkeypatch.setattr(searxng_local, "down", lambda: {"state": "stopped"})
    assert host.search_server_down({}) == {"state": "stopped"}


def test_status_names_the_external_search_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4_WEB_SEARCH_URL is what web_search uses; the status said "absent"
    while web_search worked (2026-11-03, the console's web chip reads it)."""
    monkeypatch.setattr(searxng_local, "status", lambda: {"state": "absent"})
    monkeypatch.setattr(searxng_local, "container_engine", lambda: "")
    monkeypatch.setenv("R4_WEB_SEARCH_URL", " https://searx.example.org ")
    status = _Host().search_server_status({})
    assert status["state"] == "absent"
    assert status["external_url"] == "https://searx.example.org"
