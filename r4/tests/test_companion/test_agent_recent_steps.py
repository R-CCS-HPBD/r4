"""H7 (TB 4.0 harness plan 2026-10-24): the recent-step window is truncated in place.

``_record_tool_round`` rebound ``recent_tool_steps = recent_tool_steps[-8:]``: the
caller's list kept growing past 8, and once it had, only the FIRST call of each
parallel round reached it. The loop guards and the round-exhausted digest then
read the task's opening steps (TB 4.0 mp ``fifERQx``: a digest of the opening
``list_dir`` calls).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from r4.companion.agent import ResearchCompanion


def _agent() -> ResearchCompanion:
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)
    agent._execute_tool_calls = lambda calls, tools: [  # type: ignore[method-assign]
        f"result of {call['id']}" for call in calls
    ]
    return agent


def _call(round_index: int, slot: int) -> dict:
    return {
        "id": f"r{round_index}c{slot}",
        "type": "function",
        "function": {"name": "shell_exec", "arguments": json.dumps({"command": f"step {slot}"})},
    }


def test_window_keeps_the_last_eight_calls_of_parallel_rounds() -> None:
    agent = _agent()
    steps: list[dict[str, str]] = []
    messages: list[dict] = []
    for round_index in range(12):
        calls = [_call(round_index, 0), _call(round_index, 1)]
        agent._record_tool_round(messages, calls, [], steps)
    assert len(steps) == 8
    assert steps[-1]["result"] == "result of r11c1"
    assert steps[-2]["result"] == "result of r11c0"
    assert [s["result"] for s in steps[:2]] == ["result of r8c0", "result of r8c1"]


def test_round_exhausted_digest_shows_the_latest_steps() -> None:
    agent = _agent()
    steps: list[dict[str, str]] = []
    for round_index in range(12):
        agent._record_tool_round([], [_call(round_index, 0)], [], steps)
    digest = agent._round_exhausted_fallback(steps, 12)
    assert "result of r11c0" in digest
    assert "result of r0c0" not in digest
