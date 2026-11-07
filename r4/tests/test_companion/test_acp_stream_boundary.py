"""ACP streamed-text round-boundary separator pins.

Text chunks from consecutive model rounds (separated by tool calls) used to
render glued together in the single assistant bubble the ACP chat path
builds ("…fits the existing patterns.Now let me look…"). The session adapter
must insert a paragraph break on the first chunk past a tool-round boundary.
"""

from __future__ import annotations

from typing import Any

from r4.companion.acp_session import CompanionAcpSession
from r4.companion.agent import CompanionEvent, CompanionEventType


class _FakeCompanionSession:
    """Minimum shape CompanionAcpSession needs (mirrors test_shell_output_acp)."""

    session_id = "sess-boundary"
    name = "boundary-test"
    agent = None
    messages: list[Any] = []


def _make_session(chunks: list[str]) -> CompanionAcpSession:
    session = object.__new__(CompanionAcpSession)  # bypass init machinery
    object.__setattr__(session, "_session", _FakeCompanionSession())
    object.__setattr__(session, "_store", None)
    object.__setattr__(session, "_on_event", None)
    object.__setattr__(session, "_stream_callback", chunks.append)
    object.__setattr__(session, "_event_callback", None)
    object.__setattr__(session, "_cancel", None)
    object.__setattr__(session, "_editor_tools", [])
    object.__setattr__(session, "_editor_tool_call", None)
    object.__setattr__(session, "_agent_listener_attached", False)
    return session


def _event(
    event_type: CompanionEventType, message: str = "", data: dict[str, Any] | None = None
) -> CompanionEvent:
    return CompanionEvent(event_type=event_type, message=message, data=data or {})


def test_text_after_tool_round_gets_separator() -> None:
    chunks: list[str] = []
    session = _make_session(chunks)

    session._handle_companion_event(
        _event(CompanionEventType.RESPONSE_CHUNK, "…fits the existing patterns.")
    )
    session._handle_companion_event(_event(CompanionEventType.TOOL_CALL, ""))
    session._handle_companion_event(_event(CompanionEventType.TOOL_RESULT, "ok"))
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "Now let me look…"))

    assert "".join(chunks) == "…fits the existing patterns.\n\nNow let me look…"


def test_first_round_chunk_has_no_leading_separator() -> None:
    chunks: list[str] = []
    session = _make_session(chunks)

    # A RESPONSE_DONE boundary before any streamed text must NOT produce a
    # leading separator on the turn's first chunk.
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_DONE, ""))
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "first text"))

    assert chunks == ["first text"]


def test_separator_inserted_once_per_boundary() -> None:
    chunks: list[str] = []
    session = _make_session(chunks)

    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "a"))
    session._handle_companion_event(_event(CompanionEventType.TOOL_CALL, ""))
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "b"))
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "c"))

    # Only the first chunk past the boundary carries the break.
    assert "".join(chunks) == "a\n\nbc"


def test_blocked_tool_also_opens_new_paragraph() -> None:
    chunks: list[str] = []
    session = _make_session(chunks)

    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "trying"))
    session._handle_companion_event(_event(CompanionEventType.TOOL_BLOCKED, "denied"))
    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "instead…"))

    assert "".join(chunks) == "trying\n\ninstead…"


def test_bypass_init_instance_survives_missing_boundary_attrs() -> None:
    """object.__new__ harnesses never run __init__ — the boundary fields are
    absent, so reads must go through getattr defaults (no AttributeError)."""
    chunks: list[str] = []
    session = _make_session(chunks)
    assert not hasattr(session, "_stream_round_boundary")

    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "x"))
    assert chunks == ["x"]


def test_failed_tool_round_gets_separator() -> None:
    """A tool that blows up emits ERROR (agent.py _invoke_tool_executor), not
    TOOL_RESULT — the round boundary must still hold so the post-failure
    narration opens a new paragraph instead of glueing onto the last one."""
    chunks: list[str] = []
    session = _make_session(chunks)

    session._handle_companion_event(_event(CompanionEventType.RESPONSE_CHUNK, "…storage-oriented."))
    session._handle_companion_event(_event(CompanionEventType.TOOL_CALL, ""))
    session._handle_companion_event(_event(CompanionEventType.ERROR, "HTTP 500"))
    session._handle_companion_event(
        _event(CompanionEventType.RESPONSE_CHUNK, "tool_search isn't available…")
    )

    assert "".join(chunks) == "…storage-oriented.\n\ntool_search isn't available…"


def test_integration_real_agent_failed_tool_keeps_separator() -> None:
    """Drive a real ResearchCompanion through a streamed two-round turn whose
    tool executor raises (platform 5xx), wired exactly like the ACP server
    wires CompanionAcpSession, and pin the separator in the accumulated
    stream — this is the 2026-09-05 webview transcript shape (glue at every
    failed-tool round boundary)."""
    from unittest.mock import MagicMock

    from r4.companion.agent import ResearchCompanion
    from r4.companion.context import ContextManager
    from r4.companion.permissions import PermissionManager, PermissionMode
    from r4.companion.skills import SkillRegistry

    mock_client = MagicMock()
    mock_client._config.model = "test-model"

    rounds = {"n": 0}

    def round_stream(payload: dict, acc: list, **kw: Any) -> Any:
        rounds["n"] += 1
        if rounds["n"] == 1:
            acc.extend(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "boom_tool", "arguments": "{}"},
                    }
                ]
            )
            return iter(["…my file tools are mostly platform-storage-oriented."])
        return iter(["tool_search isn't available, so I'll try …"])

    mock_client._stream_request_collect.side_effect = round_stream

    def _boom_execute(**kwargs: Any) -> str:
        raise RuntimeError("HTTP 500 from gateway")

    boom_tool = {
        "name": "boom_tool",
        "description": "fails like a platform 5xx",
        "parameters": {"type": "object", "properties": {}},
        "execute": _boom_execute,
    }

    agent = ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )

    chunks: list[str] = []
    acp = _make_session(chunks)
    agent.on_event(acp._handle_companion_event)

    response = agent.send("find validate_key", tools=[boom_tool], stream=True)

    accumulated = "".join(chunks)
    assert "platform-storage-oriented.\n\ntool_search" in accumulated
    # The turn still completes with the second round's text.
    assert "tool_search isn't available" in response


# ── B: tool-call-end envelopes carry the FULL (sanitized) result ────────────


def test_tool_call_end_envelope_is_not_preview_capped() -> None:
    """2000-char result must survive into result_preview — the card is the
    only surface long outputs (final_output, failures) get in the webview."""
    from r4.companion.tool_summaries import result_preview_full

    long_text = "x" * 2000
    preview = result_preview_full("final_output", long_text)
    assert preview == long_text
    capped_safe = result_preview_full("rag_query", "token: secret-abc123")
    assert "secret-abc123" not in (capped_safe or ""), "sanitization must still run"


def test_executor_error_event_completes_the_tool_card() -> None:
    """TOOL_CALL then ERROR (executor raised, 5xx) must produce a
    tool_call_end structured event with status 'error' and the tool's name —
    failed tools render as cards, not only through agent narration."""
    session = _make_session([])
    captured: list[dict[str, Any]] = []
    object.__setattr__(session, "_event_callback", captured.append)

    session._handle_companion_event(_event(CompanionEventType.TOOL_CALL, "", {"name": "rag_query"}))
    session._handle_companion_event(
        _event(CompanionEventType.ERROR, "Error executing rag_query: HTTP 503")
    )

    envelopes = [e for e in captured if e.get("type") == "tool_call_end"]
    assert envelopes, "ERROR must complete the open tool card"
    end = envelopes[-1]["payload"]
    assert end["name"] == "rag_query"
    assert end["status"] == "error"
    assert "HTTP 503" in (end.get("result_preview") or "")


def test_non_tool_error_stays_generic() -> None:
    """ERROR without a preceding TOOL_CALL falls back to the generic error
    channel (no phantom card)."""
    session = _make_session([])
    captured: list[dict[str, Any]] = []
    object.__setattr__(session, "_event_callback", captured.append)

    session._handle_companion_event(_event(CompanionEventType.ERROR, "boom"))

    assert captured, "generic error must still surface"
    kinds = {e.get("type") for e in captured}
    assert "tool_call_end" not in kinds
    assert "error" in kinds
