"""ADR-093 D-8 (C12): the JSONL bridge carries the router's announcement.

The pre-retrieval router announces "Retrieved n passages (status)" as a
THINKING event with ``data.kind == "context_pack"``. The bridge drops THINKING
events as noise, so the Bun TUI never saw the line (r4-harness-9,
2026-10-10). Only the context-pack kind is surfaced; the agent's own
"Processing your message…" stays off the wire.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.agent import CompanionEvent, CompanionEventType
from r4.companion.ui.bridge_server import CompanionBridgeServer


def _bridge() -> tuple[CompanionBridgeServer, list[dict]]:
    bridge = CompanionBridgeServer.__new__(CompanionBridgeServer)
    bridge._session = SimpleNamespace(session_id="s-1")
    bridge._event_log = MagicMock()
    lines: list[dict] = []
    bridge._emit_line = lines.append
    return bridge, lines


def test_the_retrieval_announcement_reaches_the_tui():
    bridge, lines = _bridge()
    data = {"kind": "context_pack", "action": "pack", "n": 3, "status": "sufficient"}
    bridge._forward_event(
        CompanionEvent(CompanionEventType.THINKING, "Retrieved 3 passages (sufficient)", data)
    )
    events = [line["event"] for line in lines if "event" in line]
    assert len(events) == 1
    assert events[0]["type"] == "thinking"
    assert events[0]["message"] == "Retrieved 3 passages (sufficient)"
    assert events[0]["data"]["kind"] == "context_pack"


def test_the_unknown_model_scale_notice_reaches_the_tui():
    """ADR-093 D-14 (C22, wave D 2026-10-10): the router's once-per-session
    notice that a model's scale is unknown (and how to set it)."""
    bridge, lines = _bridge()
    notice = "model scale unknown for house@lab; set it with `r4 model scale set house ...`"
    data = {"kind": "model_scale", "size_class": "unknown", "profile": "medium"}
    bridge._forward_event(CompanionEvent(CompanionEventType.THINKING, notice, data))
    events = [line["event"] for line in lines if "event" in line]
    assert [(e["type"], e["message"], e["data"]["kind"]) for e in events] == [
        ("thinking", notice, "model_scale")
    ]


def test_other_thinking_events_stay_off_the_wire():
    bridge, lines = _bridge()
    bridge._forward_event(CompanionEvent(CompanionEventType.THINKING, "Processing your message…"))
    bridge._forward_event(
        CompanionEvent(CompanionEventType.THINKING, "Planning", {"kind": "something_else"})
    )
    assert lines == []
