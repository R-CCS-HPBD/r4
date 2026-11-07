"""Harness-level graders.

Grade the *workspace state* a harness leaves behind plus the adapter result —
never internal harness logs (per ADR-020 section 13: metrics come from
authoritative surfaces only). A grader answers a boolean plus a
human-readable note. LLM-judge rubric graders come later and route through
the ModelBroker, never a provider SDK directly.
"""

from __future__ import annotations

import re
import shlex
import subprocess  # nosec B404 — bench runs trusted test_command from versioned suites
import sys
from pathlib import Path
from typing import Any

from benchmarks.team_engines.graders import Grade

_TEST_COMMAND_TIMEOUT_SEC = 300


class WorkspaceGrader:
    """Base grader: evaluates (case, workdir, adapter result)."""

    name = "base"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:
        raise NotImplementedError


class StatusSuccessGrader(WorkspaceGrader):
    """Harness must complete without error — the baseline success-rate signal."""

    name = "status_success"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:  # noqa: ARG002 — uniform interface
        ok = result.status == "success"
        return Grade(self.name, ok, "" if ok else f"status={result.status}")


class MetricsSanityGrader(WorkspaceGrader):
    """Result metrics must be present and non-degenerate.

    Harnesses that do not report llm_calls (e.g. black-box CLIs) get a pass
    with a note — absence of telemetry is not a task failure, just a blind
    spot recorded in the results.
    """

    name = "metrics_sanity"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:  # noqa: ARG002 — uniform interface
        if result.status != "success":
            return Grade(self.name, False, f"status={result.status}")
        llm_calls = result.metrics.get("llm_calls")
        if llm_calls is None:
            return Grade(self.name, True, "no llm metrics reported")
        if llm_calls < 1:
            return Grade(self.name, False, "llm_calls == 0")
        if result.metrics.get("total_tokens", 0) < 0:
            return Grade(self.name, False, "negative token count")
        return Grade(self.name, True)


class ArtifactPresenceGrader(WorkspaceGrader):
    """All expected output files exist in the case workdir."""

    name = "artifact_present"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:  # noqa: ARG002 — uniform interface
        if not case.expected_outputs:
            return Grade(self.name, True, "no expected outputs")
        missing = [rel for rel in case.expected_outputs if not (workdir / rel).is_file()]
        if missing:
            return Grade(self.name, False, f"missing: {missing}")
        return Grade(self.name, True)


class TestPassGrader(WorkspaceGrader):
    """The case's test command exits 0 in the workdir."""

    name = "test_pass"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:
        if not case.test_command:
            return Grade(self.name, True, "no test_command")
        if result.status != "success":
            return Grade(self.name, False, "harness failed; tests not run")
        argv = shlex.split(case.test_command)
        # Suites say "python ..." for portability; resolve to the bench interpreter
        # so hosts without a bare `python` alias still grade correctly.
        if argv and argv[0] in {"python", "python3"}:
            argv[0] = sys.executable
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=_TEST_COMMAND_TIMEOUT_SEC,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return Grade(self.name, False, f"timeout after {_TEST_COMMAND_TIMEOUT_SEC}s")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-200:].strip()
            return Grade(self.name, False, f"rc={proc.returncode}: {tail}")
        return Grade(self.name, True)


class ContentMatchGrader(WorkspaceGrader):
    """Expected files contain every regex in case.expect['content_match'].

    Shape: ``{"content_match": {"relative/path.py": ["regex1", "regex2"]}}``.
    """

    name = "content_match"

    def grade(self, case: Any, workdir: Path, result: Any) -> Grade:  # noqa: ARG002 — uniform interface
        spec = case.expect.get("content_match")
        if not spec:
            return Grade(self.name, True, "no content_match expectations")
        misses: list[str] = []
        for rel, patterns in spec.items():
            target = workdir / rel
            if not target.is_file():
                misses.append(f"{rel}: not created")
                continue
            body = target.read_text(errors="replace")
            for pattern in patterns:
                if not re.search(pattern, body):
                    misses.append(f"{rel}: missing /{pattern}/")
        if misses:
            return Grade(self.name, False, "; ".join(misses))
        return Grade(self.name, True)


GRADER_REGISTRY: dict[str, type[WorkspaceGrader]] = {
    StatusSuccessGrader.name: StatusSuccessGrader,
    MetricsSanityGrader.name: MetricsSanityGrader,
    ArtifactPresenceGrader.name: ArtifactPresenceGrader,
    TestPassGrader.name: TestPassGrader,
    ContentMatchGrader.name: ContentMatchGrader,
}


def load_graders(names: list[str]) -> list[WorkspaceGrader]:
    """Instantiate graders; unknown names raise — fail closed, per bench
    reproducibility rules (never grade with an unspecified metric)."""
    graders: list[WorkspaceGrader] = []
    for name in names:
        cls = GRADER_REGISTRY.get(name)
        if cls is None:
            raise KeyError(f"unknown grader {name!r} (known: {sorted(GRADER_REGISTRY)})")
        graders.append(cls())
    return graders
