"""Tests for companion endpoint expiry guard policy."""

from __future__ import annotations

from r4.companion.endpoint_policy import evaluate_endpoint_guard, match_tools


def _format_duration(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs:02d}s"


def test_match_tools_distinguishes_request_and_long_running_keywords():
    tools = [{"name": "shell_exec"}, {"name": "python_exec"}]

    request_matches = match_tools(
        "run pwd in the project directory",
        tools,
        {"shell_exec": ("run",), "python_exec": ("dataset",)},
    )
    long_running_matches = match_tools(
        "run pwd in the project directory",
        tools,
        {"shell_exec": ("workflow",), "python_exec": ("dataset",)},
    )

    assert request_matches == ["shell_exec"]
    assert long_running_matches == []


def test_evaluate_endpoint_guard_blocks_long_running_request():
    decision = evaluate_endpoint_guard(
        user_message="run a full workflow across the dataset",
        tools=[{"name": "shell_exec"}, {"name": "python_exec"}],
        active_endpoints=[{"instance_id": "ep-1", "backend": "slurm", "remaining_seconds": 120}],
        warning_threshold_sec=900,
        block_threshold_sec=300,
        format_duration=_format_duration,
    )

    assert decision["state"] == "blocked"
    assert "shell_exec" in decision["message"] or "python_exec" in decision["message"]


def test_evaluate_endpoint_guard_allows_short_shell_request():
    decision = evaluate_endpoint_guard(
        user_message="run pwd in the project directory",
        tools=[{"name": "shell_exec"}],
        active_endpoints=[{"instance_id": "ep-1", "backend": "slurm", "remaining_seconds": 120}],
        warning_threshold_sec=900,
        block_threshold_sec=300,
        format_duration=_format_duration,
    )

    assert decision == {"state": "clear"}
