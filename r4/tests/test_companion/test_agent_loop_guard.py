"""H5 (TB 4.0 harness plan 2026-10-24): same-call and A/B loops trip the guard.

Two loops ran undetected because their results changed: one timing benchmark
``shell_exec`` ran 181 times (biped ``ifD6J9q``, about 2 h), and two heredoc
scripts alternated about 53 times (layout ``HUWHQob``). The old guard needs
identical results too. Now 6 identical calls in a row, or an A/B pair over 6
steps, trip it whatever the results — except polls (``sleep``/``tail``/
``watch``/``wait``), scheduler and monitoring probes and read-only probes.
The guard runs in unattended runs (``R4_AUTONOMOUS=1``), where a trip is a
nudge; ``R4_COMPANION_SAME_CALL_GUARD=0`` is the off-switch there and ``=1``
opts an interactive session in (review 2026-10-24: an interactive user
watching a Slurm job with ``squeue`` was switched to answer mode).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from r4.companion.agent import ResearchCompanion, _should_switch_to_answer_mode


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    """An unattended run, the switch unset: the guard's default-on case."""
    monkeypatch.delenv("R4_COMPANION_SAME_CALL_GUARD", raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", "1")


def _shell(command: str, result: str) -> dict[str, str]:
    arguments = json.dumps({"command": command}, sort_keys=True, separators=(",", ":"))
    return {"name": "shell_exec", "arguments": arguments, "result": result}


def _timed(command: str, count: int) -> list[dict[str, str]]:
    return [_shell(command, f"elapsed {0.41 + i / 100:.2f}s") for i in range(count)]


_BENCH = "cd /app && python3 bench_timing.py --steps 400"
_SCRIPT_A = "python3 - <<'EOF'\nimport layout\nlayout.render(1)\nEOF"
_SCRIPT_B = "python3 - <<'EOF'\nimport layout\nlayout.render(2)\nEOF"


def test_six_identical_calls_with_changing_results_trip_the_guard() -> None:
    reason = _should_switch_to_answer_mode(_timed(_BENCH, 6))
    assert reason is not None
    assert "6 times in a row" in reason


def test_five_identical_calls_do_not() -> None:
    assert _should_switch_to_answer_mode(_timed(_BENCH, 5)) is None


def test_polling_a_growing_log_is_exempt() -> None:
    steps = [_shell("sleep 60; tail -n 50 job.log", f"epoch {i}") for i in range(8)]
    assert _should_switch_to_answer_mode(steps) is None


def test_read_only_probe_is_exempt() -> None:
    steps = [_shell("cat /app/progress.txt", f"{i}%") for i in range(8)]
    assert _should_switch_to_answer_mode(steps) is None


def test_a_heredoc_that_mentions_wait_is_not_a_poll() -> None:
    script = "python3 - <<'EOF'\nimport time\n# wait for the cache\nprint(time.time())\nEOF"
    assert _should_switch_to_answer_mode(_timed(script, 6)) is not None


def test_ab_alternation_trips_the_guard() -> None:
    steps = []
    for i in range(3):
        steps += [_shell(_SCRIPT_A, f"a {i}"), _shell(_SCRIPT_B, f"b {i}")]
    reason = _should_switch_to_answer_mode(steps)
    assert reason is not None
    assert "alternated" in reason


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_off_switch(monkeypatch, value) -> None:
    """Every off word works (review 2026-10-24: only ``0`` did)."""
    monkeypatch.setenv("R4_COMPANION_SAME_CALL_GUARD", value)
    assert _should_switch_to_answer_mode(_timed(_BENCH, 8)) is None


def test_interactive_sessions_keep_the_old_guard(monkeypatch) -> None:
    """Review 2026-10-24: off by default without R4_AUTONOMOUS (old code tripped)."""
    monkeypatch.delenv("R4_AUTONOMOUS")
    assert _should_switch_to_answer_mode(_timed(_BENCH, 8)) is None
    monkeypatch.setenv("R4_COMPANION_SAME_CALL_GUARD", "1")
    assert _should_switch_to_answer_mode(_timed(_BENCH, 8)) is not None


@pytest.mark.parametrize(
    "command",
    [
        "squeue -u $USER",
        "ssh login squeue -j 4242",
        "sacct -j 4242 --format=State",
        "pjstat",
        "qstat -u me",
        "nvidia-smi",
        "git status --short",
        "curl -s localhost:8000/health",
    ],
)
def test_scheduler_and_monitoring_probes_are_exempt(command) -> None:
    """Review 2026-10-24: watching a job repeats one command while its output changes."""
    assert _should_switch_to_answer_mode(_timed(command, 8)) is None


@pytest.mark.parametrize("tool", ["team_plan_result", "workflow_results", "platform_health"])
def test_status_like_tools_are_exempt(tool) -> None:
    steps = [{"name": tool, "arguments": '{"id":"r1"}', "result": f"{i}%"} for i in range(8)]
    assert _should_switch_to_answer_mode(steps) is None


def test_unattended_run_gets_the_change_approach_nudge(monkeypatch) -> None:
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    model = "glm-5.3@rikyu"
    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)
    messages: list[dict] = []
    force, cont, _ = agent._post_tool_round_mode_control(
        messages, _timed(_BENCH, 6), force_text_response=False, corrective_nudge_sent=False
    )
    assert (force, cont) == (False, True)
    assert messages[-1]["role"] == "user"
    assert "Change approach" in messages[-1]["content"]
