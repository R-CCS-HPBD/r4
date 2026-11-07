"""Tests for companion context-overflow recovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from r4.companion.agent import (
    ResearchCompanion,
    _context_overflow_retryable,
    _trim_messages_on_overflow,
)
from r4.errors import APIError


def test_context_overflow_retryable_detects_token_limit_errors():
    exc = APIError(
        "bad request",
        status_code=400,
        response_body='{"message":"maximum context length is 131072 tokens"}',
    )
    assert _context_overflow_retryable(exc)


def test_trim_messages_on_overflow_drops_oldest_non_system_rows():
    messages = [{"role": "system", "content": "sys"}]
    messages.extend({"role": "user", "content": f"msg-{index}"} for index in range(8))
    trimmed = _trim_messages_on_overflow(messages)
    assert trimmed[0]["role"] == "system"
    assert any("trimmed" in str(item.get("content", "")).lower() for item in trimmed)
    assert len(trimmed) < len(messages)


def test_chat_retries_after_context_overflow():
    client = MagicMock()
    client._config.model = "kimi-k2.6"
    overflow = APIError(
        "context length exceeded",
        status_code=400,
        response_body="maximum context length is 131072 tokens",
    )
    success = MagicMock()
    success.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    success.content = "done"
    success.model = "kimi-k2.6"
    success.raw_response = {"choices": [{"message": {"content": "done"}}]}
    client._request.side_effect = [overflow, success]

    companion = ResearchCompanion(client)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "again"},
    ]

    with patch.object(ResearchCompanion, "_extract_tool_calls", return_value=[]):
        with patch.object(ResearchCompanion, "_extract_content", return_value="done"):
            result = companion._chat(messages, None)

    assert result["choices"][0]["message"]["content"] == "done"
    assert client._request.call_count == 2


def test_tool_results_are_capped_before_return():
    client = MagicMock()
    companion = ResearchCompanion(client)
    huge = "x" * 50_000  # above the 40_000 per-result cap (W2 2026-10-05)
    tool_def = {"execute": lambda: huge}
    result = companion._invoke_tool_executor("grep_search", tool_def, {})
    assert len(result) < len(huge)
    assert "omitted" in result or "truncated" in result.lower() or "chars" in result


def _tool_round(index: int) -> list[dict]:
    call = {"id": f"c{index}", "type": "function", "function": {"name": "shell_exec"}}
    return [
        {"role": "assistant", "content": f"step-{index}", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{index}", "content": f"out-{index}"},
    ]


def _long_task_history() -> list[dict]:
    """An unattended task: the instruction, then ten tool rounds."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "TASK: build /app/out.json"},
    ]
    for index in range(10):
        messages += _tool_round(index)
    return messages


def test_trim_on_overflow_keeps_the_task_instruction():
    """H9 (TB 4.0 plan 2026-10-24): the oldest-25 % drop removed the first user
    message — in an unattended run, the task itself."""
    messages = _long_task_history()
    trimmed = _trim_messages_on_overflow(messages)
    assert len(trimmed) < len(messages)
    assert trimmed[2] == {"role": "user", "content": "TASK: build /app/out.json"}
    contents = [m["content"] for m in trimmed]
    assert "step-0" not in contents  # the drop starts after the instruction
    assert contents[-1] == "out-9"


def test_trim_keeps_the_current_turns_request_not_the_sessions_first():
    """Review 2026-10-24: in a multi-turn session the message to keep is the one
    that started the turn in flight; the session's first message (often a large
    pasted log) goes first, as before H9."""
    messages = [{"role": "system", "content": "sys"}]
    messages += [
        {"role": "user", "content": "U1 pasted log"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "U3 current"},
    ]
    for index in range(3):
        messages += _tool_round(index)
    contents = [m["content"] for m in _trim_messages_on_overflow(messages)]
    assert contents[2:5] == ["U2", "A2", "U3 current"]
    assert "U1 pasted log" not in contents and "A1" not in contents


def test_trim_notice_joins_the_leading_system_block():
    """Not a mid-conversation system message (GLM templates expect system first)."""
    trimmed = _trim_messages_on_overflow(_long_task_history())
    roles = [m["role"] for m in trimmed]
    assert roles[:3] == ["system", "system", "user"]
    assert "system" not in roles[2:]


def test_trim_leaves_a_mid_turn_system_note_in_place():
    """Review 2026-10-24: a later system message (the wrap-up note) was moved to the
    front, before the task; it keeps its place among the kept messages now."""
    messages = _long_task_history()
    messages.append({"role": "system", "content": "Tool budget exhausted"})
    trimmed = _trim_messages_on_overflow(messages)
    contents = [m["content"] for m in trimmed]
    assert contents[2] == "TASK: build /app/out.json"
    assert contents[-1] == "Tool budget exhausted"
    assert [m["role"] for m in trimmed[:2]] == ["system", "system"]
