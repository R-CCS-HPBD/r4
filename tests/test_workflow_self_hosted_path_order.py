"""Self-hosted PATH must come before the first ``gh`` call (#834, 2026-11-11).

The self-hosted runner (data0) keeps ``gh`` in ``~/.local/bin``, which is not
on the runner service's PATH. Jobs add it with a step named
``Self-hosted PATH …``. On 2026-11-09 the monthly dependency refresh called
``gh`` in an earlier step and died with "gh: command not found" (run
42613868281). This pins the order in every workflow job that has the step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
GH_CALL = re.compile(r"(^|[\s;&|(`$])gh\s+[a-z]", re.MULTILINE)


def _jobs_with_path_step() -> list[tuple[str, str, list[dict]]]:
    found = []
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any(str(s.get("name", "")).startswith("Self-hosted PATH") for s in steps):
                found.append((wf.name, job_id, steps))
    return found


def _first_gh_violation(steps: list[dict]) -> str | None:
    for step in steps:
        if str(step.get("name", "")).startswith("Self-hosted PATH"):
            return None
        if GH_CALL.search(str(step.get("run", ""))):
            return str(step.get("name", "<unnamed step>"))
    return None


JOBS = _jobs_with_path_step()


def test_some_jobs_use_the_self_hosted_path_step() -> None:
    assert JOBS, "no workflow job has a 'Self-hosted PATH' step; the pin below would be vacuous"


@pytest.mark.parametrize(
    ("workflow", "job_id", "steps"), JOBS, ids=[f"{w}:{j}" for w, j, _ in JOBS]
)
def test_no_gh_call_before_self_hosted_path(workflow: str, job_id: str, steps: list[dict]) -> None:
    offender = _first_gh_violation(steps)
    assert offender is None, (
        f"{workflow} job {job_id!r}: step {offender!r} calls gh before the "
        "'Self-hosted PATH' step, so gh is not on PATH on the self-hosted runner (#834)"
    )


def test_detector_flags_the_834_order() -> None:
    steps = [
        {"name": "Name the branch", "run": 'prs=$(gh pr list --repo "$R")'},
        {
            "name": "Self-hosted PATH (skip when hosted)",
            "run": 'echo "$HOME/.local/bin" >> "$GITHUB_PATH"',
        },
    ]
    assert _first_gh_violation(steps) == "Name the branch"
    assert _first_gh_violation(list(reversed(steps))) is None
