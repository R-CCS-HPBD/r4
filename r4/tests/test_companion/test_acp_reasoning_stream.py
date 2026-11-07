"""A model's live reasoning on the ACP rail (maintainer report 2026-11-01).

Models that stream reasoning in ``reasoning_content`` make the companion
emit one ``reasoning_delta`` event per token. The ACP session adapter
forwarded each as a generic ``session/event``, and the VS Code agent view
printed every token as its own "event:" row. Reasoning must go out as the
canonical thinking envelope (the shape the JSONL bridge sends) and never as
a generic event.
"""

from __future__ import annotations

from typing import Any

from r4.companion.acp_session import CompanionAcpSession
from r4.companion.agent import CompanionEvent, CompanionEventType


class _FakeCompanionSession:
    session_id = "sess-reasoning"
    name = "reasoning-test"
    agent = None
    messages: list[Any] = []


def _make_session(generic: list[Any], structured: list[dict[str, Any]]) -> CompanionAcpSession:
    session = object.__new__(CompanionAcpSession)  # bypass init machinery
    object.__setattr__(session, "_session", _FakeCompanionSession())
    object.__setattr__(session, "_store", None)
    object.__setattr__(session, "_on_event", generic.append)
    object.__setattr__(session, "_stream_callback", None)
    object.__setattr__(session, "_event_callback", structured.append)
    object.__setattr__(session, "_cancel", None)
    object.__setattr__(session, "_editor_tools", [])
    object.__setattr__(session, "_editor_tool_call", None)
    object.__setattr__(session, "_agent_listener_attached", False)
    return session


def _event(event_type: CompanionEventType, message: str = "") -> CompanionEvent:
    return CompanionEvent(event_type=event_type, message=message, data={})


def test_reasoning_tokens_never_become_generic_events() -> None:
    generic: list[Any] = []
    structured: list[dict[str, Any]] = []
    session = _make_session(generic, structured)
    for token in ("The", " user", " just", " typed"):
        session._handle_companion_event(_event(CompanionEventType.REASONING_DELTA, token))
    session._handle_companion_event(_event(CompanionEventType.REASONING_DONE))

    assert [e.to_dict()["event_type"] for e in generic] == []


def test_reasoning_goes_out_as_canonical_thinking_with_a_sequence() -> None:
    generic: list[Any] = []
    structured: list[dict[str, Any]] = []
    session = _make_session(generic, structured)
    session._handle_companion_event(_event(CompanionEventType.REASONING_DELTA, "The"))
    session._handle_companion_event(_event(CompanionEventType.REASONING_DELTA, " user"))
    session._handle_companion_event(_event(CompanionEventType.REASONING_DELTA, ""))
    session._handle_companion_event(_event(CompanionEventType.REASONING_DONE))
    session._handle_companion_event(_event(CompanionEventType.REASONING_DELTA, "Next"))

    assert [e["type"] for e in structured] == [
        "thinking_delta",
        "thinking_delta",
        "thinking_done",
        "thinking_delta",
    ]
    deltas = [e["payload"] for e in structured if e["type"] == "thinking_delta"]
    assert [(d["seq"], d["text"], d["partial"]) for d in deltas] == [
        (0, "The", True),
        (1, " user", True),
        (0, "Next", True),
    ], "an empty delta is skipped and the block end resets the sequence"
    assert all(e["payload"]["session_id"] == "sess-reasoning" for e in structured)
