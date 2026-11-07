"""H4 (TB 4.0 harness plan 2026-10-24): one completion review before an unattended task ends.

A reply without a tool call (or ``final_output``) ended the task with nothing
checking the work; terminus-2 asks twice and shows the terminal state. Unattended
runs (``R4_AUTONOMOUS=1``) now get ONE review nudge per task that restates the
instruction; ``R4_COMPANION_COMPLETION_REVIEW=0`` turns it off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.agent import ResearchCompanion

_TASK = "Write the solver to /app/solver.py and its output to /app/out.json."


@pytest.fixture(autouse=True)
def _unattended(monkeypatch):
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    monkeypatch.delenv("R4_COMPANION_COMPLETION_REVIEW", raising=False)
    monkeypatch.delenv("R4_COMPANION_TIME_BUDGET_SEC", raising=False)


def _text(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _final_output(answer: str, call_id: str = "f1") -> dict:
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": "final_output", "arguments": '{"content": "%s"}' % answer},
    }
    return {"choices": [{"message": {"content": "", "tool_calls": [call]}}]}


def _run(responses: list[dict], call_type: str = "turn") -> tuple[str, list[list[dict]]]:
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(
        SimpleNamespace(_config=SimpleNamespace(model=model)), model=model, call_type=call_type
    )
    agent._fit_agent_messages = lambda messages, tools=None: messages
    agent._emit_tool_selection = lambda *_a, **_k: None
    agent._execute_tool_calls = lambda calls, tools: [
        call["function"]["arguments"] for call in calls
    ]
    queue = list(responses)
    observed: list[list[dict]] = []

    def fake_round(messages, active_tools, stream):
        observed.append(list(messages))
        return None, queue.pop(0)

    agent._agentic_round_response = fake_round
    result = agent._run_agentic_turn(
        messages=[{"role": "user", "content": _TASK}],
        tool_schemas=[{"type": "function", "function": {"name": "final_output"}}],
        user_message=_TASK,
        tools=[{"name": "final_output"}],
    )
    return result, observed


def _review(messages: list[dict]) -> str:
    return str(messages[-1]["content"])


def test_unattended_text_reply_gets_one_review_with_the_instruction() -> None:
    result, observed = _run([_text("Done."), _text("Checked: both files match the spec.")])
    assert len(observed) == 2
    assert observed[1][-2] == {"role": "assistant", "content": "Done."}
    review = _review(observed[1])
    assert observed[1][-1]["role"] == "user"
    assert _TASK in review
    assert "programmatic tests" in review
    assert result == "Checked: both files match the spec."


def test_interactive_reply_ends_the_turn_without_review(monkeypatch) -> None:
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    result, observed = _run([_text("Done.")])
    assert (result, len(observed)) == ("Done.", 1)


def test_sub_agents_are_not_reviewed() -> None:
    result, observed = _run([_text("Done.")], call_type="subagent")
    assert (result, len(observed)) == ("Done.", 1)


def test_review_fires_once_per_task() -> None:
    result, observed = _run([_text("Done."), _text("Still done.")])
    assert (result, len(observed)) == ("Still done.", 2)


def test_final_output_is_reviewed_once() -> None:
    result, observed = _run([_final_output("first"), _final_output("second", "f2")])
    assert len(observed) == 2
    assert _TASK in _review(observed[1])
    assert "second" in result


def test_final_output_after_a_review_ends_the_task() -> None:
    result, observed = _run([_text("Done."), _final_output("verified")])
    assert len(observed) == 2
    assert "verified" in result


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_off_switch_keeps_the_old_ending(monkeypatch, value) -> None:
    """Every off word works (review 2026-10-24: ``=false`` left the review on)."""
    monkeypatch.setenv("R4_COMPANION_COMPLETION_REVIEW", value)
    result, observed = _run([_text("Done.")])
    assert (result, len(observed)) == ("Done.", 1)


def test_review_states_the_time_used_against_a_known_budget(monkeypatch) -> None:
    monkeypatch.setenv("R4_COMPANION_TIME_BUDGET_SEC", "28800")
    _, observed = _run([_text("Done."), _text("ok")])
    assert "of a 480 min budget" in _review(observed[1])
