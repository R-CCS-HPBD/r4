"""Deterministic plumbing tests for the SWE-bench lane — no docker, no LLM.

FakeRuntime scripts the container lifecycle; a FakeAdapter exercises
run_instance end to end, including patch collection and resolution verdict.
"""
# ruff: noqa: ARG002 — fakes mirror the runtime/adapter protocol signatures

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.harness.adapters.base import (
    SWE_NO_IMPLEMENTATION,
    AdapterResult,
    HarnessAdapter,
    supports_swe,
)
from benchmarks.harness.adapters.echo import EchoAdapter
from benchmarks.harness.swe.containers import ExecResult
from benchmarks.harness.swe.dataset import SweInstance
from benchmarks.harness.swe.fairness import render_prompt, strip_future_commits_script
from benchmarks.harness.swe.patches import evaluate_resolved, strip_setup_churn
from benchmarks.harness.swe.runner import main, run_instance

BASE_COMMIT = "abc123def456"

INSTANCE = SweInstance(
    instance_id="django__django-11099",
    repo="django/django",
    base_commit=BASE_COMMIT,
    problem_statement="QuerySet.bulk_create() ignores specified fields.",
    fail_to_pass=("test_bulk_create.py::test_fields",),
    pass_to_pass=("test_bulk_create.py::test_existing",),
)

FAKE_PATCH = """\
diff --git a/django/db/models/query.py b/django/db/models/query.py
index 111..222 100644
--- a/django/db/models/query.py
+++ b/django/db/models/query.py
@@ -1,2 +1,3 @@
 def bulk_create(self, fields=None):
+    fields = fields or []
     return super().bulk_create(fields)
diff --git a/setup.py b/setup.py
index aaa..bbb 100644
--- a/setup.py
+++ b/setup.py
@@ -1,1 +1,2 @@
+name = "noise"
"""


class FakeRuntime:
    """Scripts exec results; records writes/prepare/destroy."""

    def __init__(self, *, diff: str = FAKE_PATCH, pytest_rc: int = 0) -> None:
        self.diff = diff
        self.pytest_rc = pytest_rc
        self.prepared: list[tuple[str, ...]] = []
        self.destroyed: list[str] = []
        self.writes: dict[str, str] = {}

    def prepare(self, instance, mounts=()):
        self.prepared.append(tuple(mounts))
        return "fake-container"

    def exec(self, container, argv, *, workdir="/testbed", env=None, timeout_sec=3600):
        text = " ".join(str(a) for a in argv)
        if "git" in text and "diff" in text:
            return ExecResult(rc=0, stdout=self.diff)
        if "pytest" in text:
            return ExecResult(rc=self.pytest_rc, stdout="collected", stderr="")
        if "echo stripped" in text:
            return ExecResult(rc=0, stdout="stripped")
        return ExecResult(rc=0, stdout="")

    def read_file(self, container, path):
        return self.writes.get(path, "")

    def write_file(self, container, path, content):
        self.writes[path] = content

    def destroy(self, container):
        self.destroyed.append(container)


class FakeAdapter(HarnessAdapter):
    name = "fake"

    def run_swe_case(self, instance, container, runtime):
        return AdapterResult(
            status="success",
            response_text="fixed it",
            metrics={"llm_calls": 4, "total_tokens": 1000, "wallclock_sec": 12.5},
        )


def test_dataset_parse_round_trip() -> None:
    row = {
        "instance_id": INSTANCE.instance_id,
        "repo": INSTANCE.repo,
        "base_commit": INSTANCE.base_commit,
        "problem_statement": INSTANCE.problem_statement,
        "FAIL_TO_PASS": json.dumps(list(INSTANCE.fail_to_pass)),
        "PASS_TO_PASS": json.dumps(list(INSTANCE.pass_to_pass)),
    }
    from benchmarks.harness.swe.dataset import _parse_instance

    parsed = _parse_instance(row)
    assert parsed.fail_to_pass == INSTANCE.fail_to_pass
    assert parsed.pass_to_pass == INSTANCE.pass_to_pass


def test_select_explicit_ids_fail_closed() -> None:
    from benchmarks.harness.swe.dataset import select

    with pytest.raises(KeyError, match="unknown instance ids"):
        select([INSTANCE], ids=["nonexistent-id"])


def test_select_random_is_deterministic() -> None:
    from benchmarks.harness.swe.dataset import select

    pool = [
        SweInstance(
            instance_id=f"repo/{i}",
            repo=f"org/repo{i}",
            base_commit=BASE_COMMIT,
            problem_statement="x",
        )
        for i in range(10)
    ]
    first = select(pool, count=3, seed=42)
    second = select(pool, count=3, seed=42)
    assert [i.instance_id for i in first] == [i.instance_id for i in second]


def test_prompt_is_identical_shape_across_instances() -> None:
    prompt_a = render_prompt(INSTANCE)
    assert INSTANCE.repo in prompt_a
    assert BASE_COMMIT in prompt_a
    assert INSTANCE.problem_statement in prompt_a
    assert "Do not access the network" in prompt_a
    assert "do NOT commit" in prompt_a
    other = SweInstance(
        instance_id="x/y-1", repo="x/y", base_commit="other", problem_statement="different"
    )
    prompt_b = render_prompt(other)
    # Fairness rule: the ONLY differences are the instance fields themselves.
    assert prompt_a.replace(INSTANCE.repo, "{R}").replace(BASE_COMMIT, "{C}").replace(
        INSTANCE.problem_statement, "{P}"
    ) == prompt_b.replace(other.repo, "{R}").replace(other.base_commit, "{C}").replace(
        other.problem_statement, "{P}"
    )


def test_future_commit_strip_script_targets_base() -> None:
    script = strip_future_commits_script(BASE_COMMIT)
    assert BASE_COMMIT in script
    assert "reflog expire" in script
    assert "--prune=now" in script


def test_strip_setup_churn_removes_noise() -> None:
    cleaned = strip_setup_churn(FAKE_PATCH)
    assert "django/db/models/query.py" in cleaned
    assert "setup.py" not in cleaned


def test_evaluate_resolved_readable_verdict() -> None:
    runtime = FakeRuntime(pytest_rc=0)
    verdict = evaluate_resolved(runtime, "fake-container", INSTANCE)
    assert verdict["resolved"] is True
    failing = FakeRuntime(pytest_rc=1)
    verdict = evaluate_resolved(failing, "fake-container", INSTANCE)
    assert verdict["resolved"] is False
    assert "rc=1" in verdict["reason"]


def test_run_instance_full_lifecycle(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    outcome = run_instance(FakeAdapter(), runtime, INSTANCE, tmp_path / "work")
    assert outcome.resolved is True
    assert outcome.status == "success"
    assert runtime.destroyed == ["fake-container"]
    work = tmp_path / "work"
    assert (work / "prompt.txt").read_text() == render_prompt(INSTANCE)
    assert (work / "git.patch").read_text() == FAKE_PATCH
    assert "setup.py" not in (work / "git.clean.patch").read_text()
    bench = json.loads((work / "bench.json").read_text())
    assert bench["passed"] is True
    assert bench["metrics"]["llm_calls"] == 4


def test_supports_swe_fail_closed_registry() -> None:
    reason = supports_swe(EchoAdapter())
    assert reason is not None and SWE_NO_IMPLEMENTATION in reason


def test_main_rejects_harness_without_swe_support(tmp_path: Path) -> None:
    rc = main(
        [
            "--harnesses",
            "echo",
            "--ids",
            INSTANCE.instance_id,
            "--out",
            str(tmp_path),
            "--cache",
            str(tmp_path / "cache.json"),
        ],
        runtime=FakeRuntime(),
    )
    assert rc == 2
