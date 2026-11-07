"""Thinking budget (ADR-087 amendment, 2026-10-06): a round that deliberates past
the budget with no answer or tool call is stopped and closed with an assistant
prefill (``continue_final_message``) so the model acts on its plan; a backend that
refuses the prefill turns the budget off and the round becomes an output-limit cut.
A streamed round that produced only reasoning is no longer re-sent blocking."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from r4.client import Client
from r4.companion.agent import (
    _THINK_CLOSE_LINE,
    ResearchCompanion,
    _think_budget_chars,
)
from r4.errors import APIError
from r4.stream_assembly import THINK_BUDGET_FINISH, ChatStreamAssembler, RoundLimits


def _chunk(reasoning: str = "", content: str = "", finish: str | None = None) -> dict:
    delta = {k: v for k, v in (("reasoning_content", reasoning), ("content", content)) if v}
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def test_budget_env_default_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_THINK_BUDGET", raising=False)
    assert _think_budget_chars() == 64_000
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET", "0")
    assert _think_budget_chars() == 0
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET", "2000")
    assert _think_budget_chars() == 8_000


@pytest.mark.parametrize(
    ("chunks", "budget", "expected"),
    [
        ([_chunk("x" * 50)], 40, True),
        ([_chunk("x" * 30)], 40, False),
        ([_chunk(content="Hi"), _chunk("x" * 50)], 40, False),
        ([_chunk("x" * 50)], 0, False),
    ],
)
def test_assembler_budget(chunks: list[dict], budget: int, expected: bool) -> None:
    assembler = ChatStreamAssembler(RoundLimits(think_chars=budget))
    for chunk in chunks:
        assembler.feed(chunk)
    assert (assembler.stop_reason() == THINK_BUDGET_FINISH) is expected


def _bare_client(http: Any) -> Client:
    client = Client.__new__(Client)
    client._http_for_route = lambda _route: http
    client._record_stream_usage = lambda *a, **k: None
    return client


def test_collector_stops_at_budget_and_records_finish() -> None:
    consumed: list[int] = []

    def chunks(*_a: Any, **_k: Any):
        for i in range(10):
            consumed.append(i)
            yield _chunk("y" * 20)

    client = _bare_client(SimpleNamespace(iter_sse_chunks=chunks))
    route = SimpleNamespace(kind="public")
    reasoning: list[str] = []
    out = list(
        client._collect_sse_deltas(
            "u", route, {}, {}, [], reasoning_acc=reasoning, limits=RoundLimits(think_chars=50)
        )
    )
    assert out == [] and len(consumed) == 3
    assert client._last_stream_finish == THINK_BUDGET_FINISH


def test_collector_keeps_the_server_finish_without_budget() -> None:
    def chunks(*_a: Any, **_k: Any):
        yield _chunk("y" * 200)
        yield _chunk(finish="length")

    client = _bare_client(SimpleNamespace(iter_sse_chunks=chunks))
    list(client._collect_sse_deltas("u", SimpleNamespace(kind="public"), {}, {}, []))
    assert client._last_stream_finish == "length"


def test_post_streamed_stops_reading_at_budget() -> None:
    lines = [f'data: {{"choices":[{{"delta":{{"reasoning_content":"{"z" * 30}"}}}}]}}'] * 6
    read: list[str] = []

    class _Response:
        headers = {"content-type": "text/event-stream"}

        def iter_lines(self):
            for line in lines:
                read.append(line)
                yield line

    @contextmanager
    def stream(*_a: Any, **_k: Any):
        yield _Response()

    client = _bare_client(SimpleNamespace(stream=stream))
    data = client._post_streamed(
        SimpleNamespace(kind="public"),
        "u",
        {},
        {},
        (None, None),
        limits=RoundLimits(think_chars=70),
    )
    assert data["choices"][0]["finish_reason"] == THINK_BUDGET_FINISH
    assert len(read) == 3
    assert data["choices"][0]["message"]["reasoning_content"] == "z" * 90


def _response(message: dict, finish: str) -> SimpleNamespace:
    raw = {"choices": [{"message": message, "finish_reason": finish}]}
    return SimpleNamespace(raw_response=raw, content=message.get("content"))


def _agent(replies: list[Any]) -> tuple[ResearchCompanion, list[tuple[dict, dict]]]:
    calls: list[tuple[dict, dict]] = []

    def fake_request(payload: dict, **kwargs: Any) -> SimpleNamespace:
        calls.append((payload, kwargs))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    model = "glm-5.3@rikyu"
    client = SimpleNamespace(_config=SimpleNamespace(model=model), _request=fake_request)
    agent = ResearchCompanion(client, model=model)
    agent._output_token_limit = lambda _messages: None
    return agent, calls


_TOOL_CALL = {
    "id": "c1",
    "type": "function",
    "function": {"name": "shell_exec", "arguments": '{"command": "python -c 1"}'},
}
_MESSAGES = [{"role": "user", "content": "build the solver"}]
_TOOLS = [{"type": "function", "function": {"name": "shell_exec"}}]


def test_deliberation_is_closed_with_a_prefill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET", "100")
    thought = "design " * 80
    agent, calls = _agent(
        [
            _response({"content": None, "reasoning_content": thought}, THINK_BUDGET_FINISH),
            _response({"content": None, "tool_calls": [_TOOL_CALL]}, "tool_calls"),
        ]
    )
    streamed, response = agent._agentic_round_response(list(_MESSAGES), _TOOLS, False)
    assert streamed is None
    assert calls[0][1]["limits"].think_chars == 400
    forced = calls[1][0]
    assert forced["continue_final_message"] is True
    assert forced["add_generation_prompt"] is False
    assert forced["messages"][-1] == {
        "role": "assistant",
        "content": "",
        "reasoning_content": thought.rstrip() + _THINK_CLOSE_LINE,
    }
    message = response["choices"][0]["message"]
    assert message["tool_calls"] == [_TOOL_CALL]
    assert message["reasoning_content"] == thought.rstrip()
    assert response["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.parametrize(
    "forced",
    [
        APIError("Unknown parameter: continue_final_message", status_code=400),
        _response({"content": None, "reasoning_content": "again " * 90}, THINK_BUDGET_FINISH),
    ],
)
def test_refused_close_turns_the_budget_off(monkeypatch: pytest.MonkeyPatch, forced: Any) -> None:
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET", "100")
    cut = {"content": None, "reasoning_content": "design " * 80}
    agent, calls = _agent([_response(cut, THINK_BUDGET_FINISH), forced])
    _streamed, response = agent._agentic_round_response(list(_MESSAGES), _TOOLS, False)
    assert response["choices"][0]["finish_reason"] == "length"
    assert response["choices"][0]["message"] == cut
    assert agent._round_limits().think_chars == 0
    assert len(calls) == 2


def test_unbounded_round_sends_no_budget_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET", "0")
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET_SEC", "0")
    monkeypatch.setenv("R4_COMPANION_TOOL_ARGS_BUDGET", "0")
    agent, calls = _agent([_response({"content": "done"}, "stop")])
    agent._agentic_round_response(list(_MESSAGES), _TOOLS, False)
    assert "limits" not in calls[0][1]


def test_reasoning_only_stream_is_not_resent_blocking() -> None:
    def collect(payload, acc, *, reasoning_acc=None, turn_cancel=None, **_kw):
        client._last_stream_finish = "length"
        reasoning_acc.append("long design")
        return iter(())

    model = "glm-5.3@rikyu"
    client = SimpleNamespace(
        _config=SimpleNamespace(model=model),
        _stream_request_collect=collect,
        _request=lambda *_a, **_k: pytest.fail("re-sent the round blocking"),
    )
    agent = ResearchCompanion(client, model=model)
    agent._output_token_limit = lambda _messages: None
    streamed, response = agent._agentic_round_response(list(_MESSAGES), _TOOLS, True)
    assert streamed is response
    assert response["choices"][0]["finish_reason"] == "length"
    assert response["choices"][0]["message"]["reasoning_content"] == "long design"


def test_wall_clock_budget_closes_a_slow_deliberation(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-5b (2026-10-07): at ~14 tokens/s, 16k tokens took 18-20 min per step."""
    import r4.stream_assembly as sa

    clock = [1000.0]
    monkeypatch.setattr(sa.time, "monotonic", lambda: clock[0])
    guard = sa.RoundGuard(RoundLimits(think_chars=64_000, think_seconds=600))
    guard.add(reasoning=500)
    assert guard.stop_reason(lambda: "") is None
    clock[0] += 601
    assert guard.stop_reason(lambda: "") == THINK_BUDGET_FINISH
    acted = sa.RoundGuard(RoundLimits(think_seconds=600))
    acted.add(reasoning=500, called=True)
    clock[0] += 601
    assert acted.stop_reason(lambda: "") is None  # a tool call is under way


def test_wall_clock_budget_default_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_THINK_BUDGET_SEC", raising=False)
    agent, _calls = _agent([])
    assert agent._round_limits().think_seconds == 600
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET_SEC", "0")
    assert agent._round_limits().think_seconds == 0
    monkeypatch.setenv("R4_COMPANION_THINK_BUDGET_SEC", "300")
    agent._think_close_unsupported = True
    assert agent._round_limits().think_seconds == 0
