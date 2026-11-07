"""Tool-call arguments budget (ADR-087 D-6, 2026-10-06): glm-5.3 moved its
deliberation into tool calls — heredoc scripts of 44k-268k chars that were 92-99%
comment lines, one taking 59 min — which the thinking budget cannot see. A round
whose tool-call arguments pass the budget, or a quarter of it while nearly every
line is a comment, is stopped before the call runs; the ladder asks for a step."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from r4.client import Client
from r4.companion.agent import (
    _LOOP_CONTINUE,
    _PLANNING_IN_ARGS_NUDGE,
    ResearchCompanion,
    _planning_cut,
)
from r4.stream_assembly import ARGS_BUDGET_FINISH, RoundLimits, planning_in_args

_PLAN = "# let me carefully plan the router: net_08 must wrap around the others\n"
_CODE = "x = compute(a, b)\n"


def _raw(text: str) -> str:
    return json.dumps({"command": "python3 - <<'EOF'\n" + text + "EOF"})


@pytest.mark.parametrize(
    ("text", "budget", "expected"),
    [
        (_PLAN * 450, 100_000, True),  # ~31k chars, all comments
        (_CODE * 1700, 100_000, False),  # ~31k chars of code
        (_CODE * 6000, 100_000, True),  # past the hard cap
        (_PLAN * 300, 100_000, False),  # under a quarter of the budget
        (_PLAN * 450, 0, False),  # off
    ],
)
def test_planning_in_args(text: str, budget: int, expected: bool) -> None:
    assert planning_in_args(_raw(text), budget) is expected


def _call_chunk(arguments: str, *, index: int = 0, first: bool = False) -> dict:
    call: dict[str, Any] = {"index": index, "function": {"arguments": arguments}}
    if first:
        call.update(id="c1", type="function", function={"name": "shell_exec", "arguments": ""})
    return {"choices": [{"delta": {"tool_calls": [call]}, "finish_reason": None}]}


def test_collector_stops_a_planning_tool_call() -> None:
    consumed: list[int] = []
    pieces = [json.dumps(_PLAN)[1:-1]] * 400  # JSON-escaped comment lines

    def chunks(*_a: Any, **_k: Any):
        yield _call_chunk("", first=True)
        for i, piece in enumerate(pieces):
            consumed.append(i)
            yield _call_chunk(piece)

    client = Client.__new__(Client)
    client._http_for_route = lambda _route: SimpleNamespace(iter_sse_chunks=chunks)
    client._record_stream_usage = lambda *a, **k: None
    acc: list[dict] = []
    list(
        client._collect_sse_deltas(
            "u", SimpleNamespace(kind="public"), {}, {}, acc, limits=RoundLimits(args_chars=40_000)
        )
    )
    assert client._last_stream_finish == ARGS_BUDGET_FINISH
    assert len(consumed) < len(pieces)


def _agent() -> ResearchCompanion:
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)
    agent._output_token_limit = lambda _messages: None
    return agent


def _args_cut_response() -> dict:
    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "shell_exec",
            "arguments": '{"command": "cat <<EOF\\n# plan A\\n# plan B',
        },
    }
    message = {"content": "", "reasoning_content": "short", "tool_calls": [call]}
    return {"choices": [{"message": message, "finish_reason": ARGS_BUDGET_FINISH}]}


def test_an_args_cut_loses_its_unfinished_call_and_the_ladder_asks_for_a_step() -> None:
    cut = _planning_cut(_args_cut_response())
    assert "tool_calls" not in cut["choices"][0]["message"]
    agent = _agent()
    messages: list[dict] = [{"role": "user", "content": "route the nets"}]
    assert agent._output_budget_ladder(cut, tool_call_count=0, messages=messages) is _LOOP_CONTINUE
    nudge = messages[-1]["content"]
    assert nudge.startswith(_PLANNING_IN_ARGS_NUDGE)
    assert "# plan A\n# plan B" in nudge  # the tail, readable
    assert agent._length_budget_nudges == 1


def test_the_round_response_applies_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    raw = _args_cut_response()
    seen: list[Any] = []

    def fake_request(payload: dict, **kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs.get("limits"))
        return SimpleNamespace(raw_response=raw, content="")

    agent.client = SimpleNamespace(_config=agent.client._config, _request=fake_request)
    monkeypatch.setenv("R4_COMPANION_TOOL_ARGS_BUDGET", "50000")
    _streamed, response = agent._agentic_round_response(
        [{"role": "user", "content": "go"}],
        [{"type": "function", "function": {"name": "x"}}],
        False,
    )
    assert seen[0].args_chars == 50_000
    assert response["choices"][0]["finish_reason"] == ARGS_BUDGET_FINISH
    assert "tool_calls" not in response["choices"][0]["message"]
