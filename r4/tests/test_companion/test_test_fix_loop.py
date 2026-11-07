"""Unit pins for the test_fix_loop composite (companion W2/G4).

All deterministic: FakeClient chat stub + tmp_path workspaces; no LLM, no network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from r4.companion import test_fix_loop as tfl

PASS_SCRIPT = "true"
FAIL_SCRIPT = "echo BROKEN; exit 1"


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeClient:
    def __init__(self, proposals: list[str]) -> None:
        self.proposals = proposals
        self.calls: list[dict[str, Any]] = []

    def chat(self, prompt: str, **kwargs: Any) -> _Resp:
        self.calls.append({"prompt": prompt, **kwargs})
        body = (
            self.proposals[len(self.calls) - 1]
            if len(self.calls) <= len(self.proposals)
            else "CANNOT"
        )
        return _Resp(body)


def _seed(tmp_path: Path, name: str, content: str) -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _client(*, proposals: list[str]) -> Any:
    return FakeClient(proposals)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard guardrail: any stray subprocess.run internet attempt explodes the test."""
    orig = subprocess.run

    def safe_run(cmd: Any, **kw: Any) -> Any:
        return orig(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", safe_run)


def test_pass_on_first_run_never_calls_llm(tmp_path: Path) -> None:
    client = _client(proposals=[])
    report = tfl.run_test_fix_loop(client, PASS_SCRIPT, files=["x.py"], cwd=tmp_path)
    assert report.fixed
    assert len(report.iterations) == 1 and report.iterations[0].action == "pass"
    assert client.calls == []


def test_fail_patch_pass_second_run(tmp_path: Path) -> None:
    broken = tmp_path / "fixme.py"
    broken.write_text("alpha\nbeta\ngamma\n")
    proposal = """\
```diff
--- a/fixme.py
+++ b/fixme.py
@@ -1,3 +1,3 @@
 alpha
-beta
+bravo
 gamma
```
"""
    client = _client(proposals=[proposal, "CANNOT"])

    counter = tmp_path / "conftest_check.py"
    counter.write_text("content\n")
    run_counter = {"n": 0}

    orig_run = tfl._run

    def counting_run(command: str, cwd: Path, tb: int) -> tuple[int, str]:
        run_counter["n"] += 1
        if run_counter["n"] == 1:
            return 1, "BROKEN says beta\n"
        return 0, ""

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tfl, "_run", counting_run)
    try:
        report = tfl.run_test_fix_loop(client, FAIL_SCRIPT, files=["fixme.py"], cwd=tmp_path)
    finally:
        monkey.undo()
    assert report.fixed
    actions = [it.action for it in report.iterations]
    assert actions[0] == "patched" and actions[-1] == "pass"
    assert broken.read_text() == "alpha\nbravo\ngamma\n"
    assert len(client.calls) == 1  # one proposal consumed
    _ = orig_run  # silence lint


def test_cannot_proposal_stops_immediately(tmp_path: Path) -> None:
    broken = tmp_path / "stuck.py"
    broken.write_text("bad\n")
    client = _client(proposals=["CANNOT"])
    report = tfl.run_test_fix_loop(client, FAIL_SCRIPT, files=["stuck.py"], cwd=tmp_path)
    assert not report.fixed
    assert report.iterations[-1].action == "cannot_fix"
    assert broken.read_text() == "bad\n"  # unchanged


def test_stop_identical_output_hash(tmp_path: Path) -> None:
    # The loop's guard: same output hash twice in a row → stop_identical.
    # Sequence: run fails → patch applies → re-run SAME tail → stop.
    (tmp_path / "same.py").write_text("v1\n")
    run_sequence = {"n": 0}
    monkey = pytest.MonkeyPatch()
    orig_run = tfl._run

    def always_fail(command: str, cwd: Path, tb: int) -> tuple[int, str]:
        run_sequence["n"] += 1
        return 1, "same tail always\n"

    monkey.setattr(tfl, "_run", always_fail)
    try:
        client2 = _client(
            proposals=[
                "--- a/same.py\n+++ b/same.py\n@@ -1,1 +1,1 @@\n-v1\n+v2\n",
                "CANNOT",
            ]
        )
        report = tfl.run_test_fix_loop(client2, FAIL_SCRIPT, files=["same.py"], cwd=tmp_path)
    finally:
        monkey.undo()
        del orig_run
    assert run_sequence["n"] == 2  # fail+patch → fail(same hash) → stop
    assert [it.action for it in report.iterations] == ["patched", "stop_identical"]


def test_conflict_proposal_stops_with_conflict(tmp_path: Path) -> None:
    target = tmp_path / "real.py"
    target.write_text("different content\n")
    proposal = """\
--- a/real.py
+++ b/real.py
@@ -1,1 +1,1 @@
-nope_matches_nothing
+anything
"""
    client = _client(proposals=[proposal])
    report = tfl.run_test_fix_loop(client, FAIL_SCRIPT, files=["real.py"], cwd=tmp_path)
    assert not report.fixed
    last = report.iterations[-1]
    assert last.action == "stop_conflict"
    assert "could not be located" in last.fix_note or "conflict" in last.fix_note
    assert target.read_text() == "different content\n"  # untouched


def test_max_iterations_respected(tmp_path: Path) -> None:
    target = tmp_path / "loop.py"
    target.write_text("v1\n")
    proposals = ["--- a/loop.py\n+++ b/loop.py\n@@ -1,1 +1,1 @@\n-v1\n+v2\n"]
    client = _client(proposals=proposals)
    monkey = pytest.MonkeyPatch()
    seq = {"n": 0}

    def always_fail(command: str, cwd: Path, tb: int) -> tuple[int, str]:
        seq["n"] += 1
        return 1, f"tail-{seq['n']}\n"

    monkey.setattr(tfl, "_run", always_fail)
    try:
        report = tfl.run_test_fix_loop(
            client, FAIL_SCRIPT, files=["loop.py"], cwd=tmp_path, max_iterations=2
        )
    finally:
        monkey.undo()
    assert seq["n"] == 2
    assert report.iterations[-1].action == "cannot_fix"


def test_no_files_yields_cannot_fix(tmp_path: Path) -> None:
    client = _client(proposals=["CANNOT"])
    report = tfl.run_test_fix_loop(client, FAIL_SCRIPT, files=[], cwd=tmp_path)
    assert not report.fixed
    assert report.iterations[0].action == "cannot_fix"


def test_files_outside_workdir_rejected_mkdocs(tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET\n")
    work = tmp_path / "work"
    work.mkdir()
    _seed(work, "in.py", "x\n")
    client = _client(proposals=["CANNOT"])
    report = tfl.run_test_fix_loop(client, FAIL_SCRIPT, files=["../outside.txt"], cwd=work)
    assert not report.fixed
    assert report.iterations[0].action == "cannot_fix"


def test_extract_patch_rejects_prose() -> None:
    assert tfl._extract_patch("just talk, no diff") is None
    assert tfl._extract_patch("CANNOT") is None
    patch = tfl._extract_patch("```diff\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n```")
    assert patch is not None and patch.startswith("--- a/x")


def test_llm_failure_stops_cleanly(tmp_path: Path) -> None:
    class ErrorClient:
        def chat(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("upstream 502")

    _seed(tmp_path, "ok.py", "x\n")
    report = tfl.run_test_fix_loop(ErrorClient(), FAIL_SCRIPT, files=["ok.py"], cwd=tmp_path)
    assert not report.fixed
    assert report.iterations[-1].action == "cannot_fix"
