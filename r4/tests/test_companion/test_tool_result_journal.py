"""Bounded tool_call_end envelope + full-result journal (round-3 task 2).

A 5 MB tool result must not flow uncapped into the webview store: the wire
preview is bounded (16 KiB), the full text stays in the session's bounded
journal, and `sessions/tool_result` serves it on demand by tool_call_id.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import (
    _JOURNAL_MAX_ENTRIES,
    _JOURNAL_MAX_ENTRY_CHARS,
    CompanionEvent,
    CompanionEventType,
    ResearchCompanion,
)
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.companion.tool_summaries import (
    MAX_ENVELOPE_RESULT_CHARS,
    TRUNCATED_MARKER,
    result_preview_full,
)


def _make_agent() -> ResearchCompanion:
    client = MagicMock()
    client._config.model = "probe-model"
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def test_envelope_bounded_and_truncated_flag() -> None:
    big = "x" * (MAX_ENVELOPE_RESULT_CHARS * 2)
    preview = result_preview_full("job_logs", big)
    assert len(preview) <= MAX_ENVELOPE_RESULT_CHARS + len(TRUNCATED_MARKER)
    assert preview.endswith(TRUNCATED_MARKER)


def test_envelope_short_result_passes_through() -> None:
    small = "all good"
    assert result_preview_full("job_list", small) == small


def test_2000_char_final_output_preview_preserved() -> None:
    """Round-2 pin: final_output card carries full content up to the bound."""
    text = "y" * 2000
    assert result_preview_full("final_output", text) == text


def test_5mb_result_bounded_on_wire_full_in_journal() -> None:
    agent = _make_agent()
    big = "z" * (5 * 1024 * 1024)
    agent._record_tool_result("call_1", "job_logs", big)
    name, content = agent.tool_result_record("call_1")
    assert name == "job_logs"
    # Journal entry itself is bounded per-entry (4 MiB)
    assert len(content) == _JOURNAL_MAX_ENTRY_CHARS
    # The wire preview is bounded independently.
    preview = result_preview_full(name, big)
    assert len(preview) <= MAX_ENVELOPE_RESULT_CHARS + len(TRUNCATED_MARKER)


def test_journal_fifo_eviction() -> None:
    agent = _make_agent()
    for i in range(_JOURNAL_MAX_ENTRIES + 5):
        agent._record_tool_result(f"call_{i}", "t", f"text-{i}")
    assert len(agent._tool_result_journal) == _JOURNAL_MAX_ENTRIES
    assert agent.tool_result_record("call_0") is None
    assert agent.tool_result_record(f"call_{_JOURNAL_MAX_ENTRIES + 4}") is not None


def test_repeated_call_id_updates_in_place() -> None:
    agent = _make_agent()
    agent._record_tool_result("call_1", "t", "first")
    agent._record_tool_result("call_1", "t", "second")
    assert len(agent._tool_result_journal) == 1
    assert agent.tool_result_record("call_1") == ("t", "second")


_BOOM_TOOL = {
    "name": "boom_tool",
    "description": "fails",
    "parameters": {"type": "object"},
    "execute": None,  # replaced at call site
}


def _failing_tool():
    def _boom(**kwargs):
        raise RuntimeError("HTTP 503")

    return {**_BOOM_TOOL, "execute": _boom}


def _rounds_with_call():
    def round_stream_then_finish(payload, acc, **kw):
        st = round_stream_then_finish
        st.n = getattr(st, "n", 0) + 1
        if st.n == 1:
            acc.extend(
                [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "boom_tool", "arguments": "{}"},
                    }
                ]
            )
            return iter(["checking. "])
        return iter(["failed, closing."])

    return round_stream_then_finish


def _drive_decoded_turn(agent):
    agent.client._stream_request_collect.side_effect = _rounds_with_call()
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    agent.send("x", tools=[_failing_tool()], stream=True)
    return events


def test_tool_call_id_appears_on_call_and_error_events() -> None:
    agent = _make_agent()
    events = _drive_decoded_turn(agent)
    tool_call = next(e for e in events if e.event_type == CompanionEventType.TOOL_CALL)
    assert tool_call.data.get("tool_call_id") == "call_xyz"
    error_events = [e for e in events if e.event_type == CompanionEventType.ERROR]
    assert error_events, "ERROR event must fire on executor failure"


def test_journal_records_the_failing_call_by_id() -> None:
    agent = _make_agent()
    _drive_decoded_turn(agent)
    record = agent.tool_result_record("call_xyz")
    assert record is not None
    name, content = record
    assert name == "boom_tool"
    assert "HTTP 503" in content


def test_sessions_tool_result_verb() -> None:
    """ACP verb: fetch the full result by session + tool_call_id."""
    import r4.acp as acp

    agent = _make_agent()
    agent._record_tool_result("call_9", "rag_query", "FULL TEXT")

    session = MagicMock()
    session.companion = MagicMock()
    session.companion.agent = agent

    runtime = object.__new__(acp._AgentSessionRuntime)
    runtime.sessions = {"s1": session}

    server = acp.ACPServer()
    acp._register_session_methods(server, runtime, lambda: [])
    handler = server._methods["sessions/tool_result"]
    out = handler({"session_id": "s1", "tool_call_id": "call_9"})
    assert out["found"] is True
    assert out["content"] == "FULL TEXT"
    assert out["name"] == "rag_query"

    assert handler({"session_id": "s1", "tool_call_id": "missing"})["found"] is False
