"""Reply-text budget (ADR-087 D-7, 2026-10-06): a TB 4.0 round wrote 131,072
tokens of plain reply text with no tool call (89 min), and the cut text ended the
task as its final answer. In tool mode a cut text reply goes back to the ladder
with its tail kept; unattended runs stop text past the budget early."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.agent import (
    _LOOP_CONTINUE,
    _TEXT_CUT_NUDGE,
    ResearchCompanion,
    _text_budget_chars,
)
from r4.stream_assembly import (
    TEXT_BUDGET_FINISH,
    ChatStreamAssembler,
    RoundGuard,
    RoundLimits,
)


def test_text_past_the_budget_stops_before_any_tool_call() -> None:
    guard = RoundGuard(RoundLimits(text_chars=100))
    guard.add(text=150)
    assert guard.stop_reason(lambda: "") == TEXT_BUDGET_FINISH
    started = RoundGuard(RoundLimits(text_chars=100))
    started.add(args=5, called=True)
    started.add(text=150)
    assert started.stop_reason(lambda: "") is None  # a call is under way: args rules apply


def test_assembler_counts_reply_text() -> None:
    assembler = ChatStreamAssembler(RoundLimits(text_chars=10))
    assembler.feed({"choices": [{"delta": {"content": "x" * 11}}]})
    assert assembler.stop_reason() == TEXT_BUDGET_FINISH


def test_budget_is_on_for_unattended_runs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_TEXT_BUDGET", raising=False)
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    assert _text_budget_chars() == 0
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    assert _text_budget_chars() == 64_000
    monkeypatch.setenv("R4_COMPANION_TEXT_BUDGET", "5000")
    assert _text_budget_chars() == 5000


def _agent() -> ResearchCompanion:
    model = "glm-5.3@rikyu"
    return ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)


@pytest.mark.parametrize("finish", ["length", TEXT_BUDGET_FINISH])
def test_a_cut_text_reply_is_not_an_answer_in_tool_mode(finish: str) -> None:
    text = "net_08 must go over O7 … " * 2000
    response = {"choices": [{"message": {"content": text}, "finish_reason": finish}]}
    agent = _agent()
    messages: list[dict] = [{"role": "user", "content": "route the nets"}]
    out = agent._output_budget_ladder(
        response, tool_call_count=0, messages=messages, tool_mode=True
    )
    assert out is _LOOP_CONTINUE
    assert messages[-2]["role"] == "assistant" and len(messages[-2]["content"]) < 8_200
    assert messages[-2]["content"].endswith(text.strip()[-100:])
    assert messages[-1] == {"role": "user", "content": _TEXT_CUT_NUDGE}


def test_outside_tool_mode_a_long_answer_is_left_alone() -> None:
    response = {"choices": [{"message": {"content": "report " * 100}, "finish_reason": "length"}]}
    agent = _agent()
    assert agent._output_budget_ladder(response, tool_call_count=0, messages=[]) is None
