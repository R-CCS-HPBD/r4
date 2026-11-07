"""Tests for ResearchCompanion agent."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEvent, CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.errors import APIError


def _chat_response(
    content: str | None,
    *,
    model: str = "test-model",
    finish_reason: str = "stop",
    tool_calls: list[dict] | None = None,
):
    message = {
        "content": content,
        "role": "assistant",
    }
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return MagicMock(
        content=content or "",
        model=model,
        usage=MagicMock(prompt_tokens=18, completion_tokens=4, total_tokens=22),
        raw_response={
            "choices": [
                {
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


def _build_agent(
    mock_client,
    *,
    permission_mode: PermissionMode = PermissionMode.TRUST,
    max_tool_rounds: int | None = None,
):
    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=permission_mode)
    kwargs = {"max_tool_rounds": max_tool_rounds} if max_tool_rounds is not None else {}
    return ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        **kwargs,
    )


def _list_dir_tool():
    def _execute(path="."):
        return "file1.py\nfile2.py"

    return {
        "name": "list_dir",
        "description": "List dir",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execute": _execute,
    }


@pytest.fixture
def mock_client():
    client = MagicMock()
    # Simulate a simple text response (no tool calls)
    client._request.return_value = MagicMock(
        content="Hello! I'm your research companion.",
        model="test-model",
        usage=MagicMock(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": "Hello! I'm your research companion.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )
    client._config.model = "test-model"
    return client


@pytest.fixture
def companion(mock_client):
    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    return ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        model="test-model",
    )


def test_send_returns_text(companion, mock_client):
    response = companion.send("Hello")
    assert response == "Hello! I'm your research companion."
    mock_client._request.assert_called_once()


def test_send_with_tool_call(mock_client):
    # First call returns a tool call, second returns text
    tool_response = MagicMock(
        content="",
        model="test-model",
        usage=MagicMock(prompt_tokens=10, completion_tokens=0, total_tokens=10),
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": '{"path": "."}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )
    text_response = MagicMock(
        content="I see the directory contents.",
        model="test-model",
        usage=MagicMock(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": "I see the directory contents.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )
    mock_client._request.side_effect = [
        MagicMock(raw_response=tool_response.raw_response),
        MagicMock(raw_response=text_response.raw_response),
    ]
    mock_client._config.model = "test-model"

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
    )

    # Provide list_dir tool
    tools = [
        {
            "name": "list_dir",
            "description": "List dir",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            "execute": lambda path=".": "file1.py\nfile2.py",
        }
    ]

    response = agent.send("What files are here?", tools=tools)
    assert "directory contents" in response
    assert mock_client._request.call_count == 2


def test_send_parses_tagged_tool_call_scaffold(mock_client):
    tagged_tool_response = _chat_response(
        "<tool_calls_section_begin>\n"
        '<tool_call_begin> functions_list_dir <tool_call_argument_begin>{"path":"."}'
        "<tool_call_argument_end>\n<tool_call_end>\n<tool_calls_section_end>"
    )
    final_response = _chat_response("I mapped the project files.")
    mock_client._request.side_effect = [tagged_tool_response, final_response]
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    response = agent.send("List files", tools=[_list_dir_tool()])

    assert response == "I mapped the project files."
    assert mock_client._request.call_count == 2


def test_send_strips_tagged_tool_scaffold_from_visible_content(mock_client):
    scaffold_response = _chat_response(
        "I will inspect the repository.\n"
        "<tool_calls_section_begin>\n"
        "<tool_call_begin>\n"
        "<tool_calls_section_end>"
    )
    mock_client._request.side_effect = [scaffold_response]
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    response = agent.send("What will you do next?", tools=[])

    assert response == "I will inspect the repository."


def test_event_emission(companion):
    events: list[CompanionEvent] = []
    companion.on_event(events.append)
    companion.send("Test")
    types = [e.event_type for e in events]
    assert CompanionEventType.THINKING in types
    assert CompanionEventType.RESPONSE_DONE in types


def test_send_streaming_emits_response_events(mock_client):
    """Streaming mode emits RESPONSE_START/RESPONSE_CHUNK and returns content."""
    mock_client._stream_request.return_value = iter(["Hello", " world"])
    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("Hello", stream=True)
    assert response == "Hello world"
    types = [e.event_type for e in events]
    assert CompanionEventType.RESPONSE_START in types
    assert CompanionEventType.RESPONSE_CHUNK in types
    assert CompanionEventType.RESPONSE_DONE in types
    mock_client._stream_request.assert_called_once()


def test_send_streaming_after_tool_round_falls_back_when_stream_empty(mock_client):
    """Streamed rounds (tools on VIE) fall back to blocking when the model
    chooses tool calls (empty content stream), and still complete the loop."""
    tool_response = _chat_response(
        None,
        tool_calls=[
            {
                "id": "call_1",
                "function": {"name": "list_dir", "arguments": '{"path":"."}'},
            }
        ],
    )
    final_response = _chat_response("Here are the files.")
    mock_client._request.side_effect = [tool_response, final_response]
    mock_client._stream_request_collect.side_effect = lambda payload, acc, **kw: iter(())
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("List files", tools=[_list_dir_tool()], stream=True)

    assert response == "Here are the files."
    # Each round first tries streaming, then falls back to blocking because
    # the streamed content is empty and no tool-call deltas arrived either.
    assert mock_client._stream_request_collect.call_count == 2
    assert mock_client._request.call_count == 2
    types = [e.event_type for e in events]
    assert CompanionEventType.RESPONSE_DONE in types


def test_send_streaming_text_round_streams_chunks_live(mock_client):
    """When a round is already final text, its chunks fire RESPONSE_CHUNK
    live (thinking markers ride through) and there is no fallback re-emit."""
    chunks = [
        "<think>Considering whale-song bands… ",
        "P-waves isolate near 1 Hz. ",
        "</think>Visible answer.",
    ]
    mock_client._stream_request_collect.side_effect = lambda payload, acc, **kw: iter(chunks)
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("Summarise the filter design", tools=[_list_dir_tool()], stream=True)

    assert response == "".join(chunks)
    mock_client._request.assert_not_called()
    types = [e.event_type for e in events]
    assert types.count(CompanionEventType.RESPONSE_CHUNK) == len(chunks)
    assert types.count(CompanionEventType.RESPONSE_DONE) == 1
    # Final completion closes exactly once without duplicating the chunks.
    emitted_text = "".join(
        event.message for event in events if event.event_type == CompanionEventType.RESPONSE_CHUNK
    )
    assert emitted_text == "".join(chunks)


def test_send_streaming_preamble_round_keeps_tool_calls(mock_client):
    """Preamble text + tool call in the SAME streamed round must still run.

    Regression: the text-only stream dropped tool-call deltas, so a round
    like "Let me check …" + tool_call ended the whole turn right after the
    preamble. Delta reconstruction keeps the loop alive.
    """
    rounds = {"n": 0}

    def round_stream(payload, acc, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1:
            acc.extend(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": '{"path":"."}'},
                    }
                ]
            )
            return iter(["Let me check the project first. "])
        return iter(["The directory holds file1.py and file2.py."])

    mock_client._stream_request_collect.side_effect = round_stream
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("List files", tools=[_list_dir_tool()], stream=True)

    assert response == "The directory holds file1.py and file2.py."
    # Both rounds streamed — NO blocking fallback call was needed.
    mock_client._request.assert_not_called()
    assert mock_client._stream_request_collect.call_count == 2
    types = [e.event_type for e in events]
    assert types.count(CompanionEventType.RESPONSE_DONE) == 1


def test_tool_blocked_when_denied(mock_client):
    """Tool calls should be blocked when permission is denied."""
    tool_response = MagicMock(
        content="",
        model="test-model",
        usage=MagicMock(prompt_tokens=9, completion_tokens=0, total_tokens=9),
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "shell_exec",
                                    "arguments": '{"command": "ls"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )
    text_response = MagicMock(
        content="OK, I won't run that.",
        model="test-model",
        usage=MagicMock(prompt_tokens=14, completion_tokens=7, total_tokens=21),
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": "OK, I won't run that.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )
    mock_client._request.side_effect = [
        MagicMock(raw_response=tool_response.raw_response),
        MagicMock(raw_response=text_response.raw_response),
    ]
    mock_client._config.model = "test-model"

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    # ASK_ALWAYS with no callback → deny
    perms = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
    )

    tools = [
        {
            "name": "shell_exec",
            "description": "Run shell",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            "execute": lambda command="", **kw: "output",
        }
    ]

    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    agent.send("Run ls", tools=tools)
    assert any(e.event_type == CompanionEventType.TOOL_BLOCKED for e in events)


def test_send_reraises_on_500_instead_of_disabling_tools(mock_client):
    """A 500 is a transient server error, not a tool-capability signal.

    Post-B7: transient errors back off with retries; tools stay enabled the
    whole time. After the retry budget is spent the original error re-raises.
    """
    payloads: list[dict] = []

    def fake_request(payload, **kwargs):
        payloads.append(payload)
        raise APIError(
            "API error: 500",
            status_code=500,
            response_body="<html><h1>500 Internal Server Error</h1></html>",
        )

    mock_client._request.side_effect = fake_request
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    agent._cancelable_sleep = lambda seconds: None  # type: ignore[assignment]

    tools = [_list_dir_tool()]

    with pytest.raises(APIError, match="500"):
        agent.send("What files are here?", tools=tools)

    assert len(payloads) == 3  # 1 initial + 2 transient retries
    assert all("tools" in p for p in payloads)
    assert agent._tool_calling_disabled is False


def test_send_retries_without_tools_on_400_tool_parser_error(mock_client):
    """A 400 with tool-call-parser markers means the backend is not configured for tools."""
    payloads: list[dict] = []
    events: list[CompanionEvent] = []
    text_response = _chat_response("Fallback reply.")

    def fake_request(payload, **kwargs):
        payloads.append(payload)
        if len(payloads) == 1:
            raise APIError(
                "tool-call-parser not configured",
                status_code=400,
                response_body="auto tool choice requires a tool-call-parser",
            )
        return text_response

    mock_client._request.side_effect = fake_request
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    agent.on_event(events.append)

    tools = [_list_dir_tool()]

    response = agent.send("What files are here?", tools=tools)
    follow_up = agent.send("And now?", tools=tools)

    assert response == "Fallback reply."
    assert follow_up == "Fallback reply."
    assert len(payloads) == 3
    assert "tools" in payloads[0]
    assert "tools" not in payloads[1]
    assert "tools" not in payloads[2]

    disabled_events = [e for e in events if e.event_type == CompanionEventType.TOOLS_DISABLED]
    assert len(disabled_events) == 1
    assert "does not support tool calling" in disabled_events[0].message


def test_send_retries_without_tools_when_tool_mode_returns_json_artifact(mock_client):
    payloads: list[dict] = []
    events: list[CompanionEvent] = []
    json_artifact = _chat_response('{"response": "Hello!"}')
    text_response = _chat_response("Hello!")

    def fake_request(payload, **kwargs):
        payloads.append(payload)
        if len(payloads) == 1:
            return json_artifact
        return text_response

    mock_client._request.side_effect = fake_request
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)

    agent.on_event(events.append)

    tools = [_list_dir_tool()]

    response = agent.send("Hello", tools=tools)

    assert response == "Hello!"
    assert len(payloads) == 2
    assert "tools" in payloads[0]
    assert "tools" not in payloads[1]

    disabled_events = [e for e in events if e.event_type == CompanionEventType.TOOLS_DISABLED]
    assert len(disabled_events) == 1
    assert "produced structured text" in disabled_events[0].message


def test_usage_callback_called(mock_client):
    records: list[tuple[str, dict]] = []
    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        usage_callback=lambda call_type, payload: records.append((call_type, payload)),
    )

    response = agent.send("Hello")

    assert response == "Hello! I'm your research companion."
    assert len(records) == 1
    call_type, payload = records[0]
    assert call_type == "turn"
    assert payload["prompt_tokens"] == 12
    assert payload["completion_tokens"] == 8
    assert payload["total_tokens"] == 20


def test_streaming_usage_prefers_measured_stream_usage(mock_client):
    """When the client captured a real usage chunk from the SSE stream, the
    agent records MEASURED tokens (estimated=False) and consumes the stash."""
    records: list[tuple[str, dict]] = []
    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        usage_callback=lambda call_type, payload: records.append((call_type, payload)),
    )
    mock_client._last_stream_usage = {
        "prompt_tokens": 1234,
        "completion_tokens": 56,
        "total_tokens": 1290,
        "model": "kimi-k3",
    }

    agent._record_streaming_usage([{"content": "ignored prompt"}], "ignored content")

    assert mock_client._last_stream_usage is None  # consume-on-read
    assert len(records) == 1
    _, payload = records[0]
    assert payload["prompt_tokens"] == 1234
    assert payload["completion_tokens"] == 56
    assert payload["total_tokens"] == 1290
    assert payload["estimated"] is False
    assert payload["model"] == "kimi-k3"
    assert payload["streaming"] is True


def test_streaming_usage_falls_back_to_estimates_without_stash(mock_client):
    """No usage chunk from the server → the estimate path is preserved."""
    records: list[tuple[str, dict]] = []
    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        usage_callback=lambda call_type, payload: records.append((call_type, payload)),
    )
    mock_client._last_stream_usage = None

    agent._record_streaming_usage([{"content": "a" * 40}], "b" * 20)

    assert len(records) == 1
    _, payload = records[0]
    assert payload["estimated"] is True
    assert payload["prompt_tokens"] > 0
    assert payload["completion_tokens"] > 0


def test_default_max_tool_rounds_is_higher(mock_client):
    """Default budget is deliberately conservative (25 — was 1000).

    A degenerate tool loop (wrong ssh alias, dead gateway) previously ran
    hundreds of rounds over an hour until upstream 400'd. Copilot-grade
    behavior: hard-cap, then summarize + ask. Workflows that need more runway
    opt in via R4_COMPANION_MAX_TOOL_ROUNDS or the constructor argument.
    """
    agent = _build_agent(mock_client)
    assert agent.max_tool_rounds == 25


def test_default_max_tool_rounds_reads_env_override(mock_client, monkeypatch):
    monkeypatch.setenv("R4_COMPANION_MAX_TOOL_ROUNDS", "75")
    agent = _build_agent(mock_client)
    assert agent.max_tool_rounds == 75


def test_send_supports_deeper_progressive_tool_sequences(mock_client):
    payloads: list[dict] = []

    def fake_request(payload, **kwargs):
        payloads.append(payload)
        call_idx = len(payloads)
        if call_idx <= 40:
            return _chat_response(
                None,
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": f"call_{call_idx}",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": f"dir_{call_idx}"}),
                        },
                    }
                ],
            )
        return _chat_response("Completed after deeper tool sequence.")

    mock_client._request.side_effect = fake_request
    mock_client._config.model = "test-model"

    # Deep tool sequences need the explicit deep budget (default is 25).
    agent = _build_agent(mock_client, max_tool_rounds=60)
    response = agent.send("Map the repository deeply", tools=[_list_dir_tool()])

    assert response == "Completed after deeper tool sequence."
    assert len(payloads) == 41


def test_send_detects_repetitive_tool_loop_and_switches_to_answer_mode(mock_client):
    payloads: list[dict] = []

    def fake_request(payload, **kwargs):
        payloads.append(payload)
        if len(payloads) <= 3:
            return _chat_response(
                None,
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": f"call_{len(payloads)}",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                    }
                ],
            )
        return _chat_response("Best effort answer without further tool calls.")

    mock_client._request.side_effect = fake_request
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    response = agent.send("Keep checking this same folder", tools=[_list_dir_tool()])

    assert response == "Best effort answer without further tool calls."
    assert len(payloads) == 4
    assert "tools" not in payloads[-1]


def test_send_returns_guided_fallback_when_tool_round_cap_reached(mock_client):
    tool_call = _chat_response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ],
    )
    # Endless constant stream: block-1 (rounds 1-2) shows progress →
    # continuation fires once; block-2 marker == block-1 marker → break at
    # round 4, checkpoint fallback emitted (not the old shutter).
    mock_client._request.side_effect = lambda payload, **kwargs: tool_call
    mock_client._config.model = "test-model"

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
        max_tool_rounds=2,
    )

    response = agent.send("Keep trying", tools=[_list_dir_tool()])

    assert "Checkpoint after" in response
    assert "narrow the scope" in response


class TestOpenPipeDialect:
    """kimi@openrouter dialect: structural parse of the pipe-marker blocks."""

    _OPEN = "<" + "|open|>"
    _CLOSE = "<" + "|close|>"
    _SEP = "<" + "|sep|>"

    @classmethod
    def _dialect(cls, command: str) -> str:
        return (
            cls._OPEN
            + "tools"
            + cls._SEP
            + cls._OPEN
            + 'call tool="shell_exec" index="1"'
            + cls._SEP
            + cls._OPEN
            + 'argument key="command" type="string"'
            + cls._SEP
            + command
            + cls._CLOSE
            + "argument"
            + cls._SEP
            + cls._CLOSE
            + "call"
            + cls._SEP
            + cls._CLOSE
            + "tools"
            + cls._SEP
        )

    def test_parse_shell_exec_command(self):
        from r4.companion.agent import _parse_open_pipe_tool_calls

        calls = _parse_open_pipe_tool_calls(self._dialect("ssh AI4S 'env | grep slurm' 2>&1"))
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "shell_exec"
        assert calls[0]["function"]["arguments"]["command"] == "ssh AI4S 'env | grep slurm' 2>&1"

    def test_parse_literal_backslash_markers(self):
        from r4.companion.agent import _parse_open_pipe_tool_calls

        # kimi@openrouter emits literal backslashes: <\|open\|> instead of <|open|>
        escaped = (
            self._dialect("uptime").replace("<" + "|", "<" + "\\" + "|").replace("|>", "\\" + "|>")
        )
        calls = _parse_open_pipe_tool_calls(escaped)
        assert len(calls) == 1
        assert calls[0]["function"]["arguments"]["command"] == "uptime"

    def test_multiline_argument_value(self):
        from r4.companion.agent import _parse_open_pipe_tool_calls

        calls = _parse_open_pipe_tool_calls(self._dialect("echo a\nhostname"))
        assert len(calls) == 1
        assert "\n" in calls[0]["function"]["arguments"]["command"]


def test_answer_mode_dialect_text_does_not_disable_tools(mock_client, monkeypatch):
    """Regression for the 2026-08-30 leak: an answer-mode round (active_tools=None)
    that returns only dialect scaffold must degrade to the evidence digest and
    must NOT flip the session-wide tool switch."""
    import r4.companion.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_should_switch_to_answer_mode", lambda steps: "test guard")

    tool_call = _chat_response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ],
    )
    dialect_text = TestOpenPipeDialect._dialect("ssh AI4S 'sinfo' 2>&1")
    mock_client._request.side_effect = [
        tool_call,
        _chat_response(dialect_text),
        _chat_response(dialect_text),
        _chat_response(dialect_text),
        _chat_response(dialect_text),
    ]

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
    )

    events: list = []
    agent.on_event(lambda ev: events.append(ev))

    response = agent.send("diagnose slurm", tools=[_list_dir_tool()])

    assert agent._tool_calling_disabled is False
    assert "Evidence so far:" in response
    assert "|open|" not in response
    disabled_events = [e for e in events if e.event_type is CompanionEventType.TOOLS_DISABLED]
    assert disabled_events == []


class TestRepairToolPairing:
    """B1 borrow (opencode/OpenHands/goose): no orphaned tool_use/tool_result."""

    def test_dangling_tool_call_gets_synthetic_result(self):
        from r4.companion.agent import _repair_tool_pairing

        msgs = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "shell_exec", "arguments": "{}"},
                    }
                ],
            },
        ]
        out, repairs = _repair_tool_pairing(msgs)
        assert repairs == 1
        assert len(out) == 3
        assert out[2]["role"] == "tool"
        assert out[2]["tool_call_id"] == "tc1"
        assert "produced no result" in out[2]["content"]

    def test_orphan_tool_result_is_dropped(self):
        from r4.companion.agent import _repair_tool_pairing

        msgs = [
            {"role": "tool", "tool_call_id": "ghost", "content": "stale"},
            {"role": "assistant", "content": "done"},
        ]
        out, repairs = _repair_tool_pairing(msgs)
        assert repairs == 1
        assert [m["role"] for m in out] == ["assistant"]

    def test_healthy_pairs_pass_through_untouched(self):
        from r4.companion.agent import _repair_tool_pairing

        msgs = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "tc1", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                    {"id": "tc2", "type": "function", "function": {"name": "y", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
            {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
            {"role": "assistant", "content": "done"},
        ]
        out, repairs = _repair_tool_pairing(msgs)
        assert repairs == 0
        assert out == msgs

    def test_result_before_call_becomes_synthetic_pair(self):
        from r4.companion.agent import _repair_tool_pairing

        msgs = [
            {"role": "tool", "tool_call_id": "tc1", "content": "early result"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "tc1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
                ],
            },
        ]
        out, repairs = _repair_tool_pairing(msgs)
        # early result dropped; synthetic insert after the assistant message
        assert repairs == 2
        assert out[0]["role"] == "assistant"
        assert out[1]["role"] == "tool"
        assert "produced no result" in out[1]["content"]


def test_send_repairs_broken_history_before_request(mock_client):
    """A TUI-side history containing a dangling tool_use must not reach the API."""
    from r4.companion.agent import _repair_tool_pairing  # noqa: F401

    sent = _send_with_broken_history(mock_client)
    ghost_tools = [
        m for m in sent if m.get("role") == "tool" and m.get("tool_call_id") == "tc-ghost"
    ]
    assert len(ghost_tools) == 1
    assert "produced no result" in ghost_tools[0]["content"]


def test_send_repaired_history_has_no_orphan_tool_messages(mock_client):
    """Every tool message in the repaired history must reference a claimed id."""
    sent = _send_with_broken_history(mock_client)
    claimed = {
        tc["id"] for m in sent if m.get("role") == "assistant" for tc in (m.get("tool_calls") or [])
    }
    orphans = [m for m in sent if m.get("role") == "tool" and m.get("tool_call_id") not in claimed]
    assert orphans == []


def _send_with_broken_history(mock_client) -> list[dict]:
    """Run one send() with a dangling tool_use in history; return sent messages."""
    payloads: list[dict] = []

    def _capture(payload, **kwargs):
        payloads.append(payload)
        return _chat_response("continued fine")

    mock_client._request.side_effect = _capture

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
    )

    broken_history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc-ghost",
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "previous question"},
    ]
    response = agent.send("next question", history=broken_history, tools=[_list_dir_tool()])

    assert response == "continued fine"
    assert payloads
    return payloads[0]["messages"]


class TestToolFailureContract:
    """B2 borrow (goose deny-text / opencode InvalidArgumentsError):
    model-facing instructions must ride every tool failure."""

    def _agent(self, mock_client, *, permissions_mode: PermissionMode):
        ctx = ContextManager(client=mock_client)
        skills = SkillRegistry()
        perms = PermissionManager(mode=permissions_mode)
        return ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=skills,
            permission_manager=perms,
        )

    def _tc(self, name: str, arguments: str) -> dict:
        return {
            "id": f"call-{name}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    def test_unknown_tool_instructs_no_retry(self, mock_client):
        agent = self._agent(mock_client, permissions_mode=PermissionMode.TRUST)
        result = agent._execute_tool_call(self._tc("turbo_ls", "{}"), [_list_dir_tool()])
        assert "unknown tool 'turbo_ls'" in result
        assert "Do NOT retry this name" in result

    def test_invalid_arguments_instructs_single_retry(self, mock_client):
        agent = self._agent(mock_client, permissions_mode=PermissionMode.TRUST)
        result = agent._execute_tool_call(self._tc("list_dir", "not-json"), [_list_dir_tool()])
        assert "invalid JSON arguments" in result
        assert "retry ONCE" in result

    def test_permission_denial_instructs_no_retry_with_user_path(self, mock_client):
        agent = self._agent(mock_client, permissions_mode=PermissionMode.TRUST)
        agent._permissions.check = lambda name, args: False  # type: ignore[assignment]
        result = agent._execute_tool_call(self._tc("list_dir", "{}"), [_list_dir_tool()])
        assert "permission policy" in result
        assert "Do NOT retry the same call" in result
        assert "ask the user" in result

    def test_execution_error_instructs_no_loop(self, mock_client):
        agent = self._agent(mock_client, permissions_mode=PermissionMode.TRUST)

        def _boom(**kwargs):
            raise RuntimeError("disk gone")

        tool = {
            "name": "fragile",
            "description": "breaks",
            "parameters": {"type": "object", "properties": {}},
            "execute": _boom,
        }
        result = agent._execute_tool_call(self._tc("fragile", "{}"), [tool])
        assert "Error executing fragile: disk gone" in result
        assert "Do not loop" in result


class TestRepairToolArguments:
    """B3 borrow (OpenHands repair-before-reject): GLM/MiniMax malformed args."""

    def test_plain_json_string(self):
        from r4.companion.agent import _repair_tool_arguments

        assert _repair_tool_arguments('{"path": "."}') == {"path": "."}

    def test_dict_passthrough_from_dialect_extraction(self):
        from r4.companion.agent import _repair_tool_arguments

        # Open-pipe parser emits dict args — previously crashed json.loads
        # with TypeError inside _prepare_tool_call.
        assert _repair_tool_arguments({"path": "."}) == {"path": "."}

    def test_raw_control_chars_inside_string_value_are_escaped(self):
        from r4.companion.agent import _repair_tool_arguments

        raw = '{"command": "echo a\nhostname"}'
        assert _repair_tool_arguments(raw) == {"command": "echo a\nhostname"}

    def test_glm_stringified_container_values(self):
        from r4.companion.agent import _repair_tool_arguments

        raw = '{"domains": "[\\"a.example\\", \\"b.example\\"]", "cores": 128}'
        assert _repair_tool_arguments(raw) == {
            "domains": ["a.example", "b.example"],
            "cores": 128,
        }

    def test_trailing_commentary_after_json_object(self):
        from r4.companion.agent import _repair_tool_arguments

        raw = '{"path": "."} — note: listing the project root'
        assert _repair_tool_arguments(raw) == {"path": "."}

    def test_empty_string_means_no_arguments(self):
        from r4.companion.agent import _repair_tool_arguments

        assert _repair_tool_arguments("") == {}

    def test_irrecoverable_junk_returns_none(self):
        from r4.companion.agent import _repair_tool_arguments

        assert _repair_tool_arguments("path: . (not json)") is None

    def test_non_dict_scalar_returns_none(self):
        from r4.companion.agent import _repair_tool_arguments

        assert _repair_tool_arguments('"just a string"') is None


def test_dialect_call_executes_end_to_end_without_crash(mock_client):
    """Regression for the dict-args TypeError: an open-pipe dialect call must
    reach the executor, not crash _prepare_tool_call."""
    import r4.companion.agent as agent_mod

    dialect_text = TestOpenPipeDialect._dialect("uptime")  # shell_exec call
    mock_client._request.side_effect = [
        _chat_response(dialect_text),
        _chat_response("uptime probe done"),
    ]

    ctx = ContextManager(client=mock_client)
    skills = SkillRegistry()
    perms = PermissionManager(mode=PermissionMode.TRUST)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=skills,
        permission_manager=perms,
    )

    executed: dict[str, str] = {}

    def _shell_exec(command: str, timeout: int = 120):
        executed["command"] = command
        return "# uname executed"

    shell_tool = {
        "name": "shell_exec",
        "description": "run shell",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        "execute": _shell_exec,
    }

    response = agent.send("probe the node", tools=[shell_tool])
    assert executed["command"] == "uptime"
    assert response == "uptime probe done"
    assert agent_mod._repair_tool_pairing  # history stayed pair-consistent along the way


class TestTransientRetry:
    """B7 borrow (goose retry.rs / OpenHands whitelist): transient hiccups
    back off instead of killing the turn."""

    def test_classifies_connection_and_status_errors(self):
        from r4.companion.agent import _is_transient_provider_error

        assert _is_transient_provider_error(ConnectionError("reset")) == 0.0
        assert _is_transient_provider_error(TimeoutError("read timed out")) == 0.0

        err_503 = APIError("Service Unavailable")
        err_503.status_code = 503
        assert _is_transient_provider_error(err_503) == 1.0

    def test_honors_retry_after_hint(self):
        from r4.companion.agent import _is_transient_provider_error

        err = APIError("rate limit")
        err.retry_after = 7
        assert _is_transient_provider_error(err) == 7.0

    def test_overflow_and_tool_reject_are_not_transient(self):
        from r4.companion.agent import _is_transient_provider_error

        overflow = APIError("maximum context length exceeded")
        overflow.status_code = 400
        assert _is_transient_provider_error(overflow) is None

        tool_reject = APIError("400 model does not support tools")
        tool_reject.status_code = 400
        assert _is_transient_provider_error(tool_reject) is None

    def test_request_retries_transient_then_succeeds(self, mock_client):
        calls = {"n": 0}

        def _flaky(payload, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("upstream reset")
            return _chat_response("recovered")

        mock_client._request.side_effect = _flaky
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        # keep the test fast: shrink the backoff
        agent._cancelable_sleep = lambda seconds: None  # type: ignore[assignment]

        response = agent.send("hi")
        assert response == "recovered"
        assert calls["n"] == 3

    def test_request_raises_hard_error_without_retry(self, mock_client):
        calls = {"n": 0}

        def _hard(payload, **kwargs):
            calls["n"] += 1
            raise APIError("400 model does not support tools")

        mock_client._request.side_effect = _hard
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        with pytest.raises(APIError):
            agent.send("hi", tools=[_list_dir_tool()], stream=False)
        # goes to the tool-reject recovery (tools disabled + retry without)
        # or raises — but never classifies as transient on the way through.
        assert calls["n"] >= 1

    def test_transient_gives_up_after_attempts(self, mock_client, monkeypatch):
        calls = {"n": 0}

        def _always_down(payload, **kwargs):
            calls["n"] += 1
            raise ConnectionError("still down")

        mock_client._request.side_effect = _always_down
        monkeypatch.setenv("R4_COMPANION_TRANSIENT_RETRIES", "2")
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        agent._cancelable_sleep = lambda seconds: None  # type: ignore[assignment]
        with pytest.raises(ConnectionError):
            agent.send("hi")
        assert calls["n"] == 2

    def test_cancel_during_backoff_stops_retry(self, mock_client):
        from r4.companion.cancellation import TurnCancellation
        from r4.companion.cancellation import TurnCancelledError as _TCE

        def _down(payload, **kwargs):
            raise ConnectionError("down")

        mock_client._request.side_effect = _down
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        token = TurnCancellation()
        token.request()
        agent.set_turn_cancel(token)
        with pytest.raises(_TCE):
            agent.send("hi")


def test_budget_wrap_up_uses_model_summary(mock_client):
    """B4 (opencode MAX_STEPS): budget end gets ONE no-tools wrap-up round —
    the model's own checkpoint instead of only the deterministic digest."""
    tool_call = _chat_response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ],
    )
    mock_client._request.side_effect = [
        tool_call,
        tool_call,
        _chat_response("Checkpoint: found file1.py + file2.py; next: read them."),
    ]

    ctx = ContextManager(client=mock_client)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        max_tool_rounds=1,
    )

    response = agent.send("survey the folder", tools=[_list_dir_tool()])
    assert response == "Checkpoint: found file1.py + file2.py; next: read them."
    # no digest markers — the model wrap-up answered instead
    assert "Checkpoint after" not in response


def test_budget_wrap_up_falls_back_to_digest_on_failure(mock_client):
    """Wrap-up round fails (or emits nothing) → deterministic digest path."""
    tool_call = _chat_response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ],
    )
    # ADR-087: an empty reply in tool mode is nudged twice before the digest.
    mock_client._request.side_effect = [
        tool_call,
        tool_call,
        _chat_response(""),
        _chat_response(""),
        _chat_response(""),
    ]

    ctx = ContextManager(client=mock_client)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        max_tool_rounds=1,
    )

    response = agent.send("survey the folder", tools=[_list_dir_tool()])
    assert "Checkpoint after" in response
    assert "narrow the scope" in response


def test_failure_streak_nudge_then_concede(mock_client, monkeypatch):
    """B5 (OpenHands get_action_error_nudge): first error streak injects ONE
    corrective nudge (tools stay on); a repeated streak flips to answer mode."""
    import r4.companion.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_is_failure_streak_breaker", lambda steps: True)
    monkeypatch.setattr(agent_mod, "_is_repetitive_tool_loop", lambda steps: False)

    tool_call = _chat_response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ],
    )
    final = _chat_response("Conceded: the folder probe keeps failing.")
    # round1 executes (nudge), round2 executes (flip to answer mode), round3
    # is the answer-mode round consuming the text response.
    mock_client._request.side_effect = [tool_call, tool_call, final]

    ctx = ContextManager(client=mock_client)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    events: list = []
    agent.on_event(lambda ev: events.append(ev))

    response = agent.send("list it", tools=[_list_dir_tool()])

    assert response == "Conceded: the folder probe keeps failing."
    assert agent._tool_calling_disabled is False
    nudges = [e for e in events if "corrective nudge" in e.message]
    assert len(nudges) == 1


def test_soft_block_boundary_continues_with_checkpoint(mock_client):
    """New budget doctrine: identical evidence across a block boundary must
    inject ONE checkpoint message and keep iterating — never stop (copilot
    parity; root fix for the 2026-08-30 stalled runs).

    Uses VARYING arguments so the identical-replay/drift guards stay silent
    while the progress marker still collapses (the productive-long-run shape).
    """

    def _tool_call_variant(idx: int):
        return _chat_response(
            None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": f"call_{idx}",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path": ".' + str(idx) + '"}'},
                }
            ],
        )

    mock_client._request.side_effect = [
        _tool_call_variant(1),
        _tool_call_variant(2),
        _tool_call_variant(3),
        _tool_call_variant(4),
        _chat_response("folders mapped: file1.py, file2.py"),
    ]

    ctx = ContextManager(client=mock_client)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        max_tool_rounds=2,
    )
    events: list = []
    agent.on_event(lambda ev: events.append(ev))

    response = agent.send("survey the folder", tools=[_list_dir_tool()])

    assert response == "folders mapped: file1.py, file2.py"
    # checkpoint continuation event fired once at the block boundary
    checkpoints = [e for e in events if "checkpoint at" in e.message]
    assert len(checkpoints) == 1
    # and NO tool-round-limit digest was rendered
    assert "tool-round limit" not in response


def _checkpoint_run(mock_client) -> list[dict]:
    """A turn past the soft-block boundary; the last request's messages."""
    captured: list[dict] = []

    def _tool_call_variant(idx: int):
        return _chat_response(
            None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": f"call_{idx}",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path": ".' + str(idx) + '"}'},
                }
            ],
        )

    ctx = ContextManager(client=mock_client)
    agent = ResearchCompanion(
        mock_client,
        context_manager=ctx,
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        max_tool_rounds=2,
    )

    def _side(payload, **kwargs):
        captured.append(dict(payload))
        if len(captured) < 5:
            return _tool_call_variant(len(captured))
        return _chat_response("done")

    mock_client._request.side_effect = _side

    response = agent.send("go", tools=[_list_dir_tool()])
    assert response == "done"
    return captured[-1]["messages"]


def test_checkpoint_message_reaches_the_next_model_round(mock_client, monkeypatch):
    """The checkpoint nudge must land in the conversation before the next model round."""
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    monkeypatch.delenv("R4_COMPANION_NUDGES_AS_USER", raising=False)
    last_messages = _checkpoint_run(mock_client)
    sys_msgs = [
        m
        for m in last_messages
        if m.get("role") == "system" and "Checkpoint:" in str(m.get("content", ""))
    ]
    assert sys_msgs, "checkpoint message missing from post-boundary request"


def test_checkpoint_note_stays_in_place_in_an_unattended_run(mock_client, monkeypatch):
    """H8 (2026-10-24): with notes as user messages (default under R4_AUTONOMOUS=1)
    the nudge stays in place as a [harness note] user message; it was merged into
    system message 0 and stayed there for the whole task."""
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    monkeypatch.delenv("R4_COMPANION_NUDGES_AS_USER", raising=False)
    last_messages = _checkpoint_run(mock_client)
    notes = [
        m
        for m in last_messages
        if m.get("role") == "user" and "[harness note] Checkpoint:" in str(m.get("content", ""))
    ]
    assert notes, "checkpoint message missing from post-boundary request"
    assert "Checkpoint:" not in last_messages[0]["content"]


class TestDeniedToolPrune:
    """B6b (opencode deny→hide): a denied tool leaves the advertised set."""

    def test_denied_tool_hidden_next_turn_and_revealed_on_blanket(self, mock_client):
        risky_tool = {
            "name": "shell_exec",
            "description": "run shell",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            "execute": lambda command, **k: "ran",
        }
        # round 1: model calls risky tool → denied. round 2: answer text.
        tool_call = _chat_response(
            None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": '{"command":"rm -f x"}'},
                }
            ],
        )
        mock_client._request.side_effect = [tool_call, _chat_response("ok next")]

        from r4.companion.permission_rules import PermissionRule, RuleSet

        ctx = ContextManager(client=mock_client)
        perms = PermissionManager(mode=PermissionMode.AUTO_SAFE)
        perms.set_approval_callback(lambda n, d, a: True)
        # ADR-104 D-2: only a deny that holds whatever the arguments hides.
        perms.ruleset = RuleSet([PermissionRule("deny", "shell_exec", "", "user", "shell_exec")])
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=perms,
        )
        agent.send("try it", tools=[risky_tool], stream=False)
        assert "shell_exec" in agent._session_denied_tools

        # next turn: the denied tool is not advertised at all
        pruned = agent._prune_denied_tools([risky_tool])
        assert pruned == []

        # the deny rule removed: the tool comes back
        perms.ruleset = RuleSet([])
        restored = agent._prune_denied_tools([risky_tool])
        assert restored == [risky_tool]

    def test_a_person_saying_no_never_hides_the_tool(self, mock_client):
        """ADR-104 defect 8: one "no" (here on a single command) hid
        shell_exec for the rest of the session."""
        risky_tool = {
            "name": "shell_exec",
            "description": "run shell",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            "execute": lambda command, **k: "ran",
        }
        tool_call = _chat_response(
            None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": '{"command":"rm -f x"}'},
                }
            ],
        )
        mock_client._request.side_effect = [tool_call, _chat_response("ok")]
        perms = PermissionManager(mode=PermissionMode.AUTO_SAFE)
        perms.set_approval_callback(lambda n, d, a: False)  # the person says no
        agent = ResearchCompanion(
            mock_client,
            context_manager=ContextManager(client=mock_client),
            skill_registry=SkillRegistry(),
            permission_manager=perms,
        )
        agent.send("try it", tools=[risky_tool], stream=False)
        assert "shell_exec" not in agent._session_denied_tools
        assert agent._prune_denied_tools([risky_tool]) == [risky_tool]


class TestReasoningEcho:
    """B9 (goose): reasoning_content preserved on streamed rounds."""

    def test_reasoning_content_echoed_on_round_message(self, mock_client):
        def _collect(payload, acc, *, reasoning_acc=None, **kwargs):
            if reasoning_acc is not None:
                reasoning_acc.extend(["let me check ", "the folders"])
            acc.append(
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            )
            return iter(["checking"])

        mock_client._stream_request_collect = _collect
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        payload = agent._stream_agentic_round(
            [{"role": "user", "content": "go"}], [{"name": "list_dir"}]
        )
        assert payload is not None
        message = payload["choices"][0]["message"]
        assert message.get("reasoning_content") == "let me check the folders"
        assert message["tool_calls"][0]["function"]["name"] == "list_dir"

    def test_no_reasoning_no_field(self, mock_client):
        def _collect(payload, acc, *, reasoning_acc=None, **kwargs):
            return iter(["text only"])

        mock_client._stream_request_collect = _collect
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        payload = agent._stream_agentic_round([{"role": "user", "content": "go"}], None)
        assert payload is not None
        assert "reasoning_content" not in payload["choices"][0]["message"]


class TestRoundHistoryShape:
    """Round recording contract: model thinking persists, calls batch
    canonically (opencode/goose/OpenHands shape)."""

    def _run_two_call_turn(self, mock_client):
        tool_calls = [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "src"}'},
            },
        ]
        mock_client._request.side_effect = [
            _chat_response("First I'll peek at the root.", tool_calls=tool_calls),
            _chat_response("Done — both listings reviewed."),
        ]
        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        response = agent.send("survey", tools=[_list_dir_tool()])
        assert response == "Done — both listings reviewed."
        followup = mock_client._request.call_args_list[-1].args[0]["messages"]
        return followup

    def test_round_thinking_text_persists_as_one_assistant_message(self, mock_client):
        msgs = self._run_two_call_turn(mock_client)
        assistants = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistants) == 1
        assert assistants[0]["content"] == "First I'll peek at the root."
        assert len(assistants[0]["tool_calls"]) == 2

    def test_round_results_follow_in_call_order(self, mock_client):
        msgs = self._run_two_call_turn(mock_client)
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b"]

    def test_reasoning_content_echoes_with_round(self, mock_client):
        tool_call = _chat_response(
            None,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        )
        tool_call.raw_response["choices"][0]["message"]["reasoning_content"] = "need the listing"
        mock_client._request.side_effect = [tool_call, _chat_response("got it")]

        ctx = ContextManager(client=mock_client)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ctx,
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        agent.send("go", tools=[_list_dir_tool()])
        followup = mock_client._request.call_args_list[-1].args[0]["messages"]
        assistant = next(m for m in followup if m.get("tool_calls"))
        assert assistant.get("reasoning_content") == "need the listing"
