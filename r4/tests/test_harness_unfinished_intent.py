"""H17 (TB 4.0 re-run 2026-10-05): a tool-mode reply that announces a step but
calls no tool is nudged to act instead of ending the turn (ks-solver-cpp ended on
"Let me isolate it with a random-matrix test:")."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.agent import (
    _UNFINISHED_INTENT_MAX_NUDGES,
    _UNFINISHED_INTENT_NUDGE,
    ResearchCompanion,
    _announces_unfinished_step,
)


@pytest.mark.parametrize(
    "text",
    [
        "The reference solver works but my `LUFact` fails. Let me isolate it with a test:",
        "Now let me run the tests.",
        "Next, I'll rebuild the extension and rerun the benchmark.",
        "Okay, I need to check the build log first.",
    ],
)
def test_announcements_are_detected(text: str) -> None:
    assert _announces_unfinished_step(text)


@pytest.mark.parametrize(
    "text",
    [
        "All tests pass. The solver is in /app/solver.cpp.",
        "Done. Let me know if you need anything else.",
        "Here is the summary:\n- fixed X\n- fixed Y",
        "I'll summarize: everything passes and the output is in /app/out.json.",
        "",
    ],
)
def test_answers_are_not_announcements(text: str) -> None:
    assert not _announces_unfinished_step(text)


def _text(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _run(responses):
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)
    agent._fit_agent_messages = lambda messages, tools=None: messages
    agent._emit_tool_selection = lambda *_a, **_k: None
    queue = list(responses)
    observed: list[list[dict]] = []

    def fake_round(messages, active_tools, stream):
        observed.append(list(messages))
        return None, queue.pop(0)

    agent._agentic_round_response = fake_round
    result = agent._run_agentic_turn(
        messages=[{"role": "user", "content": "work"}],
        tool_schemas=[{"type": "function", "function": {"name": "noop"}}],
        user_message="work",
        tools=[{"name": "noop"}],
    )
    return result, observed


def test_announcement_is_kept_and_nudged_then_the_answer_ends_the_turn() -> None:
    announce = "My `LUFact` fails. Let me isolate it with a random-matrix test:"
    result, observed = _run([_text(announce), _text("Fixed: LUFact pivots now; all tests pass.")])
    assert result == "Fixed: LUFact pivots now; all tests pass."
    assert observed[1][-2:] == [
        {"role": "assistant", "content": announce},
        {"role": "user", "content": _UNFINISHED_INTENT_NUDGE},
    ]


def test_nudges_are_bounded_per_turn() -> None:
    announce = "Let me check the build log next."
    replies = [_text(announce)] * (_UNFINISHED_INTENT_MAX_NUDGES + 1)
    result, observed = _run(replies)
    assert len(observed) == _UNFINISHED_INTENT_MAX_NUDGES + 1
    assert result  # the last announcement is returned as-is once the bound is hit


def test_streamed_final_text_records_no_phantom_usage() -> None:
    """The blocking round was accounted by _chat; emitting its text must not add a call."""
    calls: list[tuple] = []
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(
        SimpleNamespace(_config=SimpleNamespace(model=model)),
        model=model,
        usage_callback=lambda *args: calls.append(args),
    )
    events: list = []
    agent.on_event(events.append)
    agent._emit_streamed_text("the answer", messages=[{"role": "user", "content": "q"}])
    assert calls == []
    assert [e.event_type.value for e in events] == [
        "response_start",
        "response_chunk",
        "response_done",
    ]


_POLL = {
    "name": "shell_exec",
    "arguments": '{"command": "sleep 700; tail log"}',
    "result": "best 8.39",
}


def _guard_agent() -> ResearchCompanion:
    model = "glm-5.3@rikyu"
    return ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)


def test_unattended_guard_nudges_before_answer_mode(monkeypatch) -> None:
    """TB 4.0 mp-checkpoint: three identical polls of a stalled job ended the task
    on the interactive digest; unattended runs get bounded change-approach nudges."""
    from r4.companion.agent import _AUTONOMOUS_GUARD_NUDGES

    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    agent = _guard_agent()
    steps = [dict(_POLL) for _ in range(3)]
    for i in range(_AUTONOMOUS_GUARD_NUDGES):
        messages: list[dict] = []
        force, cont, _ = agent._post_tool_round_mode_control(
            messages, steps, force_text_response=False, corrective_nudge_sent=False
        )
        assert (force, cont) == (False, True), i
        assert messages[-1]["role"] == "user"
        assert "Change approach" in messages[-1]["content"]
    force, cont, _ = agent._post_tool_round_mode_control(
        [], steps, force_text_response=False, corrective_nudge_sent=False
    )
    assert (force, cont) == (True, False)  # bound reached: the old answer mode


def test_interactive_guard_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    agent = _guard_agent()
    messages: list[dict] = []
    force, cont, _ = agent._post_tool_round_mode_control(
        messages,
        [dict(_POLL) for _ in range(3)],
        force_text_response=False,
        corrective_nudge_sent=False,
    )
    assert (force, cont) == (True, False)
    assert messages[-1]["role"] == "system"


def test_unattended_runs_get_more_recoveries(monkeypatch) -> None:
    """A ladder stop always failed an unattended task (TB 4.0 photonic)."""
    from r4.companion.agent import _AUTONOMOUS_LENGTH_RECOVERIES, _length_recoveries

    monkeypatch.delenv("R4_COMPANION_LENGTH_RECOVERIES", raising=False)
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    assert _length_recoveries() == 4
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    assert _length_recoveries() == _AUTONOMOUS_LENGTH_RECOVERIES
    monkeypatch.setenv("R4_COMPANION_LENGTH_RECOVERIES", "2")
    assert _length_recoveries() == 2  # an explicit setting still wins


def test_project_state_drops_unreachable_sections() -> None:
    """Failure = absent (D-28): offline status sections are dropped, not shown as
    "unavailable … check your SSH tunnel" hints (TB 4.0, 2026-10-06)."""
    from r4.companion.agent import _reachable_sections

    offline = (
        "## Source & Health\n"
        "unavailable: Failed to reach gateway: refused. Hint: tunnel\n(none)\n\n"
        "## Endpoints\nunavailable: Connection failed\n(none)\n"
    )
    assert _reachable_sections(offline) == ""
    mixed = offline + "## Workspace\n3 files under /app\n"
    assert _reachable_sections(mixed) == "## Workspace\n3 files under /app"
    assert _reachable_sections("## Endpoints\nqwen ready\n") == "## Endpoints\nqwen ready"
