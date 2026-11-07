"""ADR-092 slice 5 follow-ups: the discarded-changes warning and
``r4 team plan clean``.

- An isolated step that may write while no ``merge_back`` names it is warned
  about on the approval card, in the dry run and in the compiled plan's
  warnings (``run_started``): its changes are discarded at run end.
- ``clean`` removes the worktrees, ``r4-plan/<run>/*`` branches and copies a
  run kept, only for a run whose last attempt is terminal or which has been
  idle longer than ``--older-than``, and never for a run whose journal holds
  a human's yes to a merge-back that was not applied.

Runs are real ``PlanRun``s over scripted turns, a real temporary git
repository and the real ``git`` CLI. No model.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time
from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from git_hermetic import hermetic_git_env
from r4_contracts.team_engine.services import AgentTurnRequest, GateDecision, GateRequest
from r4_teamengine_spi.testing import MemoryAgentBroker, make_engine_context, make_services

from r4._cli import teamup as teamup_cli
from r4.companion.team_plan_tools import describe_run_call
from r4.teams.plan import PlanEngine, spec_from_plan
from r4.teams.plan.clean import clean_all, clean_run
from r4.teams.plan.journal import PlanJournal

GIT = ["git", "-c", "user.email=t@example.org", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
DISCARDED = "changes made in impl's isolated workspace are discarded at run end"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [*GIT, "-C", str(cwd), *args], capture_output=True, text=True, env=hermetic_git_env()
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _branches(repo: Path, prefix: str = "r4-plan/") -> list[str]:
    out = _git(repo, "branch", "--list", "--format=%(refname:short)", f"{prefix}*")
    return sorted(b.strip() for b in out.splitlines() if b.strip())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("print('v1')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def runs(tmp_path: Path, monkeypatch) -> Path:
    from r4.teams import service

    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(service, "TEAM_RUNS_DIR", root)
    return root


def _plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_version": 1,
        "name": "iso",
        "description": "follow-ups",
        "budget": {"max_tokens": 100_000},
        "agents": {"w": {"tools": ["file_read", "file_write"]}},
        "phases": [{"id": "p1", "title": "One", "steps": list(steps)}],
    }


def _fan(mode: str = "worktree", count: int = 1) -> dict[str, Any]:
    return {
        "id": "impl",
        "kind": "agent",
        "use": "w",
        "prompt": "change it",
        "fanout": {"count": count},
        "isolation": mode,
    }


MERGE = {"id": "merge", "kind": "merge_back", "from": ["impl"]}


def _writes(request: AgentTurnRequest) -> str:
    (Path(request.metadata["workspace"]) / "app.py").write_text("print('agent')\n")
    return "edited"


async def _run(raw, root: Path, runs: Path, run_id: str, broker: Any, attempt: int = 1) -> Path:
    spec, problems = spec_from_plan(raw, {}, project="p")
    assert not problems, problems
    spec = dataclasses.replace(spec, context={**spec.context, "project_root": str(root)})
    run_dir = runs / run_id
    run_dir.mkdir(exist_ok=True)
    services = make_services(agents=broker, run_id=run_id, attempt_id=f"attempt-{attempt}")
    services.run = replace(services.run, working_dir=str(run_dir))
    run = await PlanEngine().start(PlanEngine().compile(spec, make_engine_context()), services)
    _ = [event async for event in run.events()]
    manifest = {"run_id": run_id, "attempt": attempt, "project_root": str(root)}
    (run_dir / "resolution.json").write_text(json.dumps(manifest))
    return run_dir


async def _kept_run(repo: Path, runs: Path, run_id: str = "run-a", mode: str = "worktree") -> Path:
    """A finished run (cancelled: no human to approve) that kept its workspace."""
    raw = _plan(_fan(mode), MERGE)
    return await _run(raw, repo, runs, run_id, MemoryAgentBroker({"impl": _writes}))


class _MovingRoot(MemoryAgentBroker):
    """A human who edits the project while the card is open, then says yes."""

    def __init__(self, *args: Any, edit: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.edit = edit

    async def ask_gate(self, request: GateRequest) -> GateDecision:
        self.edit.write_text("print('moved')\n")
        return await super().ask_gate(request)


async def _approved_not_applied(repo: Path, runs: Path, run_id: str = "run-p") -> Path:
    broker = _MovingRoot({"impl": _writes}, gate_answers={"merge": True}, edit=repo / "app.py")
    return await _run(_plan(_fan(), MERGE), repo, runs, run_id, broker)


def _workspace_dirs(run_dir: Path) -> list[Path]:
    home = run_dir / "workspaces"
    return [d for d in home.iterdir() if d.is_dir()] if home.is_dir() else []


# ── the discarded-changes warning ───────────────────────────────────────────


def test_the_approval_card_warns_that_unmerged_isolated_changes_are_discarded(runs) -> None:
    card = describe_run_call({"plan": _plan(_fan("copy"))})
    assert f"warning: {DISCARDED}" in card
    covered = describe_run_call({"plan": _plan(_fan("copy"), MERGE)})
    assert "discarded" not in covered


def test_the_compiled_plan_carries_the_warning_into_run_started(repo) -> None:
    spec, problems = spec_from_plan(_plan(_fan("copy")), {}, project="p")
    assert problems == []
    compiled = PlanEngine().compile(spec, make_engine_context())
    assert [(w.field, w.severity) for w in compiled.warnings] == [("isolation", "warning")]
    assert compiled.warnings[0].message.startswith(DISCARDED)


def test_the_dry_run_prints_the_warning(runs, tmp_path, monkeypatch, capsys) -> None:
    import r4.client
    import r4.project

    monkeypatch.setattr(
        r4.project, "get_project_manager", lambda: SimpleNamespace(get_current=lambda: "p")
    )
    client = SimpleNamespace(config=SimpleNamespace(project_id=""))
    monkeypatch.setattr(r4.client, "Client", lambda *a, **k: client)
    plan_file = tmp_path / "iso.json"
    plan_file.write_text(json.dumps(_plan(_fan("copy"))))
    code, out, _err = _r4(capsys, "run", str(plan_file), "--dry-run")
    assert code == 0
    assert f"warning:  {DISCARDED}" in out


# ── clean: what qualifies ───────────────────────────────────────────────────


async def test_dry_run_lists_and_touches_nothing(repo, runs) -> None:
    run_dir = await _kept_run(repo, runs)
    report = clean_run(run_dir, dry_run=True)
    assert (report.eligible, report.reason) == (True, "finished (cancelled)")
    assert (len(report.workspaces), report.branches) == (1, _branches(repo))
    assert len(_branches(repo)) == 1 and len(_workspace_dirs(run_dir)) == 1


async def test_a_finished_run_loses_its_worktree_branch_and_records_it(repo, runs) -> None:
    run_dir = await _kept_run(repo, runs)
    report = clean_run(run_dir)
    removed = [r for r in PlanJournal(run_dir).records() if r.get("attempt") == "clean"]
    assert (report.eligible, report.errors) == (True, [])
    assert (_branches(repo), _workspace_dirs(run_dir)) == ([], [])
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert [r["status"] for r in removed] == ["removed"]
    assert not clean_run(run_dir).has_work  # a second clean finds nothing


async def test_a_kept_copy_is_removed(repo, runs) -> None:
    run_dir = await _kept_run(repo, runs, mode="copy")
    assert len(_workspace_dirs(run_dir)) == 1
    assert clean_run(run_dir).eligible
    assert _workspace_dirs(run_dir) == []


async def test_a_pending_merge_back_answer_is_never_touched(repo, runs) -> None:
    run_dir = await _approved_not_applied(repo, runs)
    for older_than in (None, 0.0):
        report = clean_run(run_dir, older_than_days=older_than, now=time.time() + 10**7)
        assert not report.eligible
        assert "approved merge-back merge" in report.reason
    assert len(_branches(repo)) == 1 and len(_workspace_dirs(run_dir)) == 1


async def test_an_unfinished_attempt_needs_older_than_and_real_idleness(repo, runs) -> None:
    run_dir = await _kept_run(repo, runs)
    manifest = json.loads((run_dir / "resolution.json").read_text())
    manifest["attempt"] = 2  # attempt 2 started; result.json is attempt 1's
    (run_dir / "resolution.json").write_text(json.dumps(manifest))
    assert "running or unfinished" in clean_run(run_dir).reason
    assert not clean_run(run_dir, older_than_days=1).eligible  # written just now
    week_ago = time.time() - 7 * 86400
    os.utime(run_dir / "journal.jsonl", (week_ago, week_ago))
    assert not clean_run(run_dir, older_than_days=10).eligible
    report = clean_run(run_dir, older_than_days=1)
    assert report.eligible and report.reason.startswith("idle for 7.0 days")
    assert _branches(repo) == []


async def test_stray_branches_and_directories_of_this_run_go_and_no_other_runs(repo, runs) -> None:
    run_dir = await _kept_run(repo, runs)
    _git(repo, "branch", "r4-plan/run-a/stray")
    _git(repo, "branch", "r4-plan/run-ab/other")  # a prefix-sharing other run
    _git(repo, "branch", "r4-plan/other-run/keep")
    (run_dir / "workspaces" / "crashed-1").mkdir()
    report = clean_run(run_dir)
    assert "r4-plan/run-a/stray" in report.branches
    assert _branches(repo) == ["r4-plan/other-run/keep", "r4-plan/run-ab/other"]
    assert _workspace_dirs(run_dir) == []


async def test_clean_all_cleans_only_what_qualifies(repo, runs) -> None:
    await _kept_run(repo, runs, run_id="run-a")
    await _approved_not_applied(repo, runs, run_id="run-p")
    reports = {r.run_id: r for r in clean_all(runs)}
    assert (reports["run-a"].eligible, reports["run-p"].eligible) == (True, False)
    assert _branches(repo) == [_branches(repo, "r4-plan/run-p/")[0]]


# ── the CLI verb ────────────────────────────────────────────────────────────


def _r4(capsys, *argv: str) -> tuple[int, str, str]:
    parser = ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    teamup_cli.build_teamup_parser(sub, name="team")
    args = parser.parse_args(["team", "plan", *argv])
    try:
        teamup_cli.cmd_teamup(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def cli(monkeypatch):
    import r4.client

    client = SimpleNamespace(config=SimpleNamespace(project_id=""))
    monkeypatch.setattr(r4.client, "Client", lambda *a, **k: client)


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("run-a", "--all-finished"),
        ("../x",),
        ("nope",),
        ("--all-finished", "--older-than", "-1"),
    ],
)
def test_clean_refuses_bad_input(runs, cli, capsys, argv) -> None:
    code, _out, _err = _r4(capsys, "clean", *argv)
    assert code == 2


async def test_clean_one_run_from_the_cli(repo, runs, cli, capsys) -> None:
    await _kept_run(repo, runs)
    code, out, _ = _r4(capsys, "clean", "run-a", "--dry-run")
    assert code == 0 and "would remove workspace" in out and "would delete branch" in out
    assert len(_branches(repo)) == 1
    code, out, _ = _r4(capsys, "clean", "run-a")
    assert code == 0 and "removed workspace" in out and _branches(repo) == []


async def test_clean_all_finished_from_the_cli(repo, runs, cli, capsys) -> None:
    await _kept_run(repo, runs, run_id="run-a")
    await _approved_not_applied(repo, runs, run_id="run-p")
    code, out, _ = _r4(capsys, "clean", "--all-finished")
    assert code == 0
    assert "cleaned 1 run(s); left 1 run(s) untouched" in out
    assert "run-p: a human approved merge-back merge" in out
    code, _out, _ = _r4(capsys, "clean", "run-p")
    assert code == 1  # that run may not be cleaned
    assert _branches(repo) == _branches(repo, "r4-plan/run-p/")
