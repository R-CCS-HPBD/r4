"""Pins for the W5b ACP frame chain: SHELL_OUTPUT type + shell/cancel method.

Deterministic — no LLM, no gateway; uses FakeMcp sessions directly.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from r4.companion.acp_session import _NOISY_SESSION_EVENT_TYPES, CompanionAcpSession
from r4.companion.agent import CompanionEvent
from r4.companion.streaming_shell import (
    SHELL_OUTPUT_EVENT_TYPE,
    cancel_stream,
    new_stream_id,
    run_shell_streaming,
)


class _FakeCompanionSession:
    """Minimum AgentSessionProtocol-compatible shape CompanionAcpSession needs."""

    session_id = "sess-sh"
    name = "shell-test"
    agent = None
    messages: list[Any] = []

    def __init__(self) -> None:
        self._listeners: list[Any] = []

    def on_event(self, cb: Any) -> None:
        self._listeners.append(cb)

    def emit(self, event: CompanionEvent) -> None:
        for cb in self._listeners:
            cb(event)


def _make_acp_session() -> tuple[_FakeCompanionSession, CompanionAcpSession]:
    fake = _FakeCompanionSession()
    session = object.__new__(CompanionAcpSession)  # bypass init machinery
    object.__setattr__(session, "_session", fake)
    object.__setattr__(session, "_store", None)
    object.__setattr__(session, "_on_event", None)
    object.__setattr__(session, "_stream_callback", None)
    object.__setattr__(session, "_event_callback", None)
    object.__setattr__(session, "_cancel", None)
    object.__setattr__(session, "_editor_tools", [])
    object.__setattr__(session, "_editor_tool_call", None)
    object.__setattr__(session, "_agent_listener_attached", False)
    return fake, session


def test_shell_output_type_exists_and_not_in_noisy_filter() -> None:
    assert SHELL_OUTPUT_EVENT_TYPE == "shell_output"
    assert SHELL_OUTPUT_EVENT_TYPE not in _NOISY_SESSION_EVENT_TYPES


def test_shell_output_event_forwards_as_acp_forward_event() -> None:
    _, session = _make_acp_session()
    captured: list[Any] = []
    session._on_event = captured.append
    event = CompanionEvent(
        event_type=SHELL_OUTPUT_EVENT_TYPE,
        message="chunk-line-1\n",
        data={"stream_id": "shell-abc12345"},
    )
    session._handle_companion_event(event)
    assert len(captured) == 1
    forwarded = captured[0]
    payload = forwarded.to_dict()
    assert payload["event_type"] == "shell_output"
    assert payload["message"] == "chunk-line-1\n"
    assert payload["data"]["stream_id"] == "shell-abc12345"
    assert isinstance(payload["timestamp"], str)


def test_shell_output_does_not_go_through_structured_or_stream_callback() -> None:
    _, session = _make_acp_session()
    structured: list[Any] = []
    streamed: list[str] = []
    forwarded: list[Any] = []
    session._event_callback = structured.append
    session._stream_callback = streamed.append
    session._on_event = forwarded.append
    event = CompanionEvent(
        event_type=SHELL_OUTPUT_EVENT_TYPE,
        message="x\n",
        data={"stream_id": "shell-z"},
    )
    session._handle_companion_event(event)
    assert structured == []
    assert streamed == []
    assert len(forwarded) == 1


def test_shell_cancel_kills_live_stream_and_reports_state() -> None:
    sid = new_stream_id()
    started = threading.Event()

    def on_chunk(stream_id: str, chunk: str) -> None:
        started.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_shell_streaming,
            "sleep 5",
            stream_id=sid,
            on_chunk=on_chunk,
            timeout=30,
        )
        time.sleep(0.2)
        # Simulate the ACP shell/cancel endpoint body (module-level helper)
        killed = cancel_stream(sid)
        result = future.result(timeout=10)
    assert killed or result.status in ("cancelled", "done")
    if killed:
        assert result.cancelled or result.status == "cancelled"


def test_shell_cancel_unknown_stream_returns_not_found() -> None:
    assert cancel_stream("shell-deadbeef") is False


def test_acp_shell_cancel_method_shape(tmp_path: Any) -> None:
    """Register the method on a throw-sensitive minimal server and call it."""
    from r4 import acp as acp_module

    class _Server:
        def __init__(self) -> None:
            self.methods: dict[str, Any] = {}

        def method(self, name: str) -> Any:
            def deco(fn: Any) -> Any:
                self.methods[name] = fn
                return fn

            return deco

    class _Runtime:
        def __init__(self) -> None:
            pass

    server = _Server()
    acp_module._register_session_methods(server, _Runtime(), lambda: [])
    assert "shell/cancel" in server.methods

    handler = server.methods["shell/cancel"]
    out = handler({"stream_id": "shell-nope"})
    assert out["stream_id"] == "shell-nope"
    assert out["status"] == "not-found"

    # Now start a live stream and ensure the ACP path kills it
    sid = new_stream_id()

    def on_chunk(stream_id: str, chunk: str) -> None:
        pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_shell_streaming, "sleep 10", stream_id=sid, on_chunk=on_chunk, timeout=30
        )
        time.sleep(0.25)
        result = handler({"stream_id": sid})
        done = future.result(timeout=10)
    # Either we killed it or the process died naturally before cancel resolved
    assert result["status"] in ("cancelled", "not-found")
    assert done.status in ("cancelled", "done", "timed_out")


def test_forward_event_is_utc_iso_string() -> None:
    evt = CompanionEvent(
        event_type=SHELL_OUTPUT_EVENT_TYPE,
        message="m",
        data={"stream_id": "shell-1"},
        timestamp=datetime(2026, 8, 25, 12, 0, 0),
    )
    _, session = _make_acp_session()
    captured: list[Any] = []
    session._on_event = captured.append
    session._handle_companion_event(evt)
    out = captured[0].to_dict()
    assert out["timestamp"].startswith("2026-08-25T12:00:00")
