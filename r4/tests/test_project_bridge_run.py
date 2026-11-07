"""Bun/console ``/project run`` folds onto the canonical UMRS loop (WP-07)."""

from __future__ import annotations

from r4.companion.ui.project_bridge import ProjectBridgeMixin, _loop_start_args


class _Host(ProjectBridgeMixin):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def _start_project_cli_job(self, operation: str, cli_args: list[str]) -> dict:
        self.calls.append((operation, cli_args))
        return {"jobId": "job-1", "operation": operation}


def test_run_starts_the_umrs_loop_through_the_cli_seam():
    host = _Host()
    out = host.project_run(
        {"project_id": "demo", "max_iterations": 3, "approve": True, "workflow_name": "wf-a"}
    )
    assert out == {"jobId": "job-1", "operation": "run"}
    assert host.calls == [
        (
            "run",
            [
                "loop",
                "start",
                "demo",
                "--max-iterations",
                "3",
                "--approve",
                "--workflow",
                "wf-a",
                "--json",
            ],
        )
    ]


def test_run_defaults_and_optional_goal():
    args = _loop_start_args("demo", {"research_goal": "Does X raise yield?"})
    assert args == [
        "loop",
        "start",
        "demo",
        "Does X raise yield?",
        "--max-iterations",
        "10",
        "--json",
    ]


def test_legacy_simulate_flag_is_ignored_loudly():
    host = _Host()
    out = host.project_run({"project_id": "demo", "simulate": True})
    assert out["jobId"] == "job-1"
    assert "simulate ignored" in out["note"]
    assert "--simulate" not in host.calls[0][1]


def test_run_requires_project_id():
    import pytest

    with pytest.raises(ValueError, match="project_id"):
        _Host().project_run({})
