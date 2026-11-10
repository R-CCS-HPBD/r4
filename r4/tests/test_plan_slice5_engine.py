"""ADR-092 slice 5 in the engine: isolated workspaces, merge-back, cleanup.

Every run is a real ``PlanRun`` over scripted turns (``MemoryAgentBroker``):
a scripted agent "writes" by writing into the workspace the engine named in
its request (``metadata["workspace"]``), as the broker's tools would. The
project roots are a temporary git repository and a temporary plain
directory; ``git`` is the real CLI. No model, no network.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from git_hermetic import hermetic_git_env
from r4_contracts.team_engine.events import EventKind
from r4_contracts.team_engine.services import AgentTurnRequest, GateDecision, GateRequest
from r4_teamengine_spi.testing import MemoryAgentBroker, make_engine_context, make_services

from r4.teams.plan import PlanEngine, spec_from_plan
from r4.teams.plan import workspace as ws_mod
from r4.teams.plan.journal import PlanJournal

GIT = ["git", "-c", "user.email=t@example.org", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


# ── fixtures ────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [*GIT, "-C", str(cwd), *args], capture_output=True, text=True, env=hermetic_git_env()
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("print('v1')\n")
    (root / "keep.txt").write_text("keep\n")
    (root / ".gitignore").write_text("*.log\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "app.py").write_text("print('v1')\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-310.pyc").write_bytes(b"\0\1binary")
    return root


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs" / "run-test-1"
    path.mkdir(parents=True)
    return path


def _plan(*steps: dict[str, Any], **top: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "plan_version": 1,
        "name": "iso",
        "description": "slice 5",
        "budget": {"max_tokens": 100_000},
        "agents": {"w": {"tools": ["file_read", "file_write", "shell_exec"]}},
        "phases": [{"id": "p1", "title": "One", "steps": list(steps)}],
    }
    plan.update(top)
    return plan


def _fan(mode: str = "worktree", count: int = 3, step_id: str = "impl") -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": "agent",
        "use": "w",
        "prompt": "change it",
        "fanout": {"count": count},
        "isolation": mode,
    }


def _merge(*sources: str) -> dict[str, Any]:
    return {"id": "merge", "kind": "merge_back", "from": list(sources or ("impl",))}


def _compile(raw: dict[str, Any], root: Path | None):
    spec, problems = spec_from_plan(raw, {}, project="p")
    assert not problems, problems
    if root is not None:
        spec = dataclasses.replace(spec, context={**spec.context, "project_root": str(root)})
    return PlanEngine().compile(spec, make_engine_context())


def _services(run_dir: Path, broker: Any, attempt: int = 1):
    services = make_services(agents=broker, attempt_id=f"attempt-{attempt}")
    services.run = replace(services.run, working_dir=str(run_dir))
    return services


async def _run(raw, root, run_dir, broker, attempt: int = 1):
    run = await PlanEngine().start(_compile(raw, root), _services(run_dir, broker, attempt))
    events = [event async for event in run.events()]
    return run, events


def _result(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "result.json").read_text())


def _writes(files: dict[str, str]):
    """A scripted agent that writes ``files`` into its workspace."""

    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        return f"wrote {sorted(files)} in {root}"

    return script


def _per_replica(request: AgentTurnRequest) -> str:
    root = Path(request.metadata["workspace"])
    replica = request.metadata["replica"]
    (root / f"part{replica}.txt").write_text(f"part {replica}\n")
    return f"part {replica}"


def _branches(repo: Path) -> list[str]:
    listed = _git(repo, "branch", "--list", "--format=%(refname:short)", "r4-plan/*")
    return [b.strip() for b in listed.splitlines() if b.strip()]


def _worktrees(repo: Path) -> int:
    return _git(repo, "worktree", "list", "--porcelain").count("worktree ")


def _ws_records(run_dir: Path) -> list[dict[str, Any]]:
    return [r for r in PlanJournal(run_dir).records() if r.get("kind") == "workspace"]


# ── preflight: refused by name, before any agent ────────────────────────────


async def test_worktree_on_a_directory_that_is_not_a_git_repo_is_refused_before_any_agent(
    plain, run_dir
) -> None:
    """Refused at the start, so not even an earlier, non-isolated agent spends."""
    broker = MemoryAgentBroker({"first": "read", "impl": _per_replica})
    first = {"id": "first", "kind": "agent", "prompt": "read"}
    run, _events = await _run(_plan(first, _fan("worktree")), plain, run_dir, broker)
    result = await run.result()
    assert result.status == "failed"
    assert "workspace_refused:not_a_git_repo" in (result.error or "")
    assert broker.calls == []


async def test_a_copy_over_the_cap_is_refused_before_any_agent(plain, run_dir, monkeypatch) -> None:
    monkeypatch.setenv(ws_mod.COPY_MAX_BYTES_ENV, "4")
    broker = MemoryAgentBroker({"impl": _per_replica})
    run, _events = await _run(_plan(_fan("copy")), plain, run_dir, broker)
    result = await run.result()
    assert result.status == "failed" and "workspace_refused:copy_too_large" in result.error
    assert broker.calls == []


async def test_a_copy_over_the_file_cap_is_refused(plain, run_dir, monkeypatch) -> None:
    monkeypatch.setenv(ws_mod.COPY_MAX_FILES_ENV, "1")
    (plain / "two.txt").write_text("2\n")
    run, _ = await _run(_plan(_fan("copy")), plain, run_dir, MemoryAgentBroker({}))
    assert "copy_too_large" in ((await run.result()).error or "")


async def test_isolation_without_a_project_root_is_refused(run_dir) -> None:
    broker = MemoryAgentBroker({"impl": _per_replica})
    run, _ = await _run(_plan(_fan("copy")), None, run_dir, broker)
    assert "workspace_refused:no_project_root" in ((await run.result()).error or "")
    assert broker.calls == []


# ── worktree mode on a git repository ───────────────────────────────────────


async def test_each_replica_gets_its_own_worktree_on_a_throwaway_branch(repo, run_dir) -> None:
    seen: list[tuple[str, str, str]] = []

    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        seen.append((str(root), branch, (root / "app.py").read_text()))
        return _per_replica(request)

    run, _ = await _run(_plan(_fan("worktree")), repo, run_dir, MemoryAgentBroker({"impl": script}))

    status = (await run.result()).status
    roots = {root for root, _b, _t in seen}
    parents = {Path(root).parent for root in roots}
    prefixed = {branch.startswith("r4-plan/run-test-1/impl") for _r, branch, _t in seen}
    texts = {text for _r, _b, text in seen}
    # three trees (parallel writers never share one), under the run directory,
    # each on its own throwaway branch at HEAD
    assert (status, len(roots), parents, prefixed, texts) == (
        "success",
        3,
        {run_dir / ws_mod.WORKSPACES_DIR},
        {True},
        {"print('v1')\n"},
    )
    _assert_all_removed(repo, run_dir, roots)


def _assert_all_removed(repo: Path, run_dir: Path, roots: set[str]) -> None:
    """No merge-back: every worktree removed and its branch deleted at run end."""
    leftovers = [root for root in roots if Path(root).exists()]
    states = {r["id"]: r["status"] for r in _ws_records(run_dir)}
    report = {row["state"] for row in _result(run_dir)["workspaces"].values()}
    names = sorted(p.name for p in repo.iterdir())
    assert (_branches(repo), _worktrees(repo), leftovers) == ([], 1, [])
    assert names == [".git", ".gitignore", "app.py", "keep.txt"]
    assert (len(states), set(states.values()), report) == (3, {"removed"}, {"removed"})


async def test_a_worktree_of_a_sub_directory_works_in_the_same_sub_directory(repo, run_dir) -> None:
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pkg")
    seen: list[Path] = []

    def script(request: AgentTurnRequest) -> str:
        seen.append(Path(request.metadata["workspace"]))
        return "ok"

    raw = _plan(_fan("worktree", count=1))
    run, _ = await _run(raw, repo / "pkg", run_dir, MemoryAgentBroker({"impl": script}))
    assert (await run.result()).status == "success"
    assert seen[0].name == "pkg" and seen[0].parent.parent.name == ws_mod.WORKSPACES_DIR


async def test_making_a_worktree_runs_no_repository_hook(repo, run_dir) -> None:
    """D-11: no script of the repository runs when the engine calls git."""
    marker = repo.parent / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    _git(repo, "checkout", "-q", "-b", "probe")  # the hook is live for plain git
    assert marker.exists()
    marker.unlink()

    run, _ = await _run(
        _plan(_fan("worktree", count=1)), repo, run_dir, MemoryAgentBroker({"impl": _per_replica})
    )
    assert (await run.result()).status == "success"
    assert not marker.exists()


# ── copy mode on a plain directory ──────────────────────────────────────────


async def test_a_map_item_keeps_one_copy_across_its_stages(plain, run_dir) -> None:
    seen: dict[str, list[str]] = {}

    def first(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        assert not (root / "__pycache__").exists()  # caches are not copied
        (root / "stage0.txt").write_text(f"item {request.metadata['item_index']}\n")
        seen.setdefault("first", []).append(str(root))
        return "stage 0"

    def second(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        seen.setdefault("second", []).append(str(root))
        return (root / "stage0.txt").read_text()  # the item's own first stage

    raw = _plan(
        {"id": "a", "kind": "agent", "prompt": "list"},
        {
            "id": "m",
            "kind": "map",
            "over": "steps.a.output",
            "max_items": 3,
            "isolation": "copy",
            "stages": [
                {"kind": "agent", "use": "w", "prompt": "one {{item}}", "label": "one"},
                {"kind": "agent", "use": "w", "prompt": "two {{prev}}", "label": "two"},
            ],
        },
    )
    broker = MemoryAgentBroker({"a": ["x", "y"], "one": first, "two": second})
    run, _ = await _run(raw, plain, run_dir, broker)

    assert (await run.result()).status == "success"
    assert sorted(seen["first"]) == sorted(seen["second"])
    assert len(set(seen["first"])) == 2
    assert _result(run_dir)["output"]["m"] == ["item 0\n", "item 1\n"]
    assert not any(Path(p).exists() for p in seen["first"])  # removed at run end
    assert sorted(p.name for p in plain.iterdir()) == ["__pycache__", "app.py"]


async def test_two_equal_map_items_never_share_a_workspace(plain, run_dir) -> None:
    roots: list[str] = []

    def stage(request: AgentTurnRequest) -> str:
        roots.append(request.metadata["workspace"])
        return "ok"

    raw = _plan(
        {"id": "a", "kind": "agent", "prompt": "list"},
        {
            "id": "m",
            "kind": "map",
            "over": "steps.a.output",
            "max_items": 2,
            "isolation": "copy",
            "stages": [{"kind": "agent", "use": "w", "prompt": "{{item}}", "label": "one"}],
        },
    )
    await _run(raw, plain, run_dir, MemoryAgentBroker({"a": ["same", "same"], "one": stage}))
    assert len(roots) == 2 and len(set(roots)) == 2


# ── merge-back ──────────────────────────────────────────────────────────────


class _AutopilotGates(MemoryAgentBroker):
    """Automatic non-delete gates, with deletion answers coming from a person."""

    def __init__(self, script: dict[str, Any], *, approve_deletes: bool = False) -> None:
        super().__init__(script)
        self.approve_deletes = approve_deletes

    def gate_is_automatic(self, request: GateRequest) -> bool:
        return not bool(request.metadata.get("deletions"))

    async def ask_gate(self, request: GateRequest) -> GateDecision:
        self.gate_calls.append(request)
        if request.metadata.get("deletions"):
            return GateDecision(approved=self.approve_deletes, answered=True, decided_by="human")
        return GateDecision(
            approved=True,
            answered=True,
            decided_by="autopilot",
            notes=("never_auto ask auto-answered in autopilot",),
        )


def _is_autopilot_log(event: Any) -> bool:
    if event.kind is not EventKind.AGENT_MESSAGE:
        return False
    if event.payload.get("type") != "log":
        return False
    return "[autopilot] never_auto ask auto-answered" in event.payload.get("text", "")


async def _autopilot_merge(repo: Path, run_dir: Path):
    broker = _AutopilotGates({"impl": _per_replica})
    run, events = await _run(_plan(_fan("worktree", count=1), _merge()), repo, run_dir, broker)
    return run, broker, events


async def test_non_delete_merge_back_autopilot_runs_without_human_provenance(repo, run_dir) -> None:
    run, broker, events = await _autopilot_merge(repo, run_dir)

    result = await run.result()
    output = _result(run_dir)["output"]["merge"]
    assert (result.status, result.metrics.human_interventions) == ("success", 0)
    assert broker.gate_calls[0].metadata["deletions"] == []
    assert output["decided_by"] == "autopilot"
    assert [r for r in events if r.kind is EventKind.HUMAN_INPUT_REQUESTED] == []


async def test_autopilot_merge_back_logs_its_decision_without_a_gate_replay(repo, run_dir) -> None:
    _run_, _broker, events = await _autopilot_merge(repo, run_dir)

    assert any(_is_autopilot_log(event) for event in events)
    records = PlanJournal(run_dir).records()
    assert [r for r in records if r.get("kind") == "gate"] == []


async def test_merge_back_deletion_carries_only_exact_deleted_paths(plain, run_dir) -> None:
    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        (root / "app.py").write_text("print('v2')\n")
        (root / "new.txt").write_text("new\n")
        (root / "gone.txt").unlink()
        return "changed"

    (plain / "gone.txt").write_text("bye\n")
    broker = _AutopilotGates({"impl": script})
    raw = _plan(_fan("copy", count=1), _merge())
    run, _ = await _run(raw, plain, run_dir, broker)

    assert (await run.result()).status == "cancelled"
    assert broker.gate_calls[0].metadata["deletions"] == ["gone.txt"]
    assert set(broker.gate_calls[0].metadata["files"]) == {"app.py", "new.txt", "gone.txt"}
    assert (plain / "app.py").read_text() == "print('v1')\n"
    assert (plain / "gone.txt").exists() and not (plain / "new.txt").exists()


async def test_an_approved_merge_back_applies_the_diffs_and_asks_once(repo, run_dir) -> None:
    raw = _plan(_fan("worktree", count=2), _merge())
    broker = MemoryAgentBroker({"impl": _per_replica}, gate_answers={"merge": True})
    run, events = await _run(raw, repo, run_dir, broker)

    result = await run.result()
    (asked,) = broker.gate_calls
    applied = ((repo / "part0.txt").read_text(), (repo / "part1.txt").read_text())
    assert (result.status, applied) == ("success", ("part 0\n", "part 1\n"))
    assert (asked.trigger_class, asked.gate) == ("merge_back", "merge")
    assert "part0.txt" in asked.prompt
    assert "+part 0" in asked.prompt  # the human reads the diff itself
    assert any(e.kind is EventKind.HUMAN_INPUT_REQUESTED for e in events)
    _assert_merged_and_journalled(repo, run_dir)


def _assert_merged_and_journalled(repo: Path, run_dir: Path) -> None:
    output = _result(run_dir)["output"]["merge"]
    diffs = [Path(row["diff"]).read_text()[:13] for row in output["workspaces"]]
    done = [r.get("ok") for r in PlanJournal(run_dir).records() if r.get("kind") == "merge_back"]
    assert (output["applied"], output["files"]) == (True, ["part0.txt", "part1.txt"])
    assert diffs == ["--- /dev/null"] * 2
    assert done == [True]  # journalled once: a resume replays it
    assert _branches(repo) == []  # merged: no longer pending, cleaned up


async def test_merge_back_modifies_and_deletes_files(plain, run_dir) -> None:
    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        (root / "app.py").write_text("print('v2')\n")
        (root / "new" / "deep.txt").parent.mkdir()
        (root / "new" / "deep.txt").write_text("deep\n")
        return "done"

    (plain / "gone.txt").write_text("bye\n")

    def remover(request: AgentTurnRequest) -> str:
        (Path(request.metadata["workspace"]) / "gone.txt").unlink()
        return "removed"

    raw = _plan(
        {**_fan("copy", count=1), "id": "edit"},
        {**_fan("copy", count=1), "id": "prune"},
        _merge("edit", "prune"),
    )
    broker = MemoryAgentBroker({"edit": script, "prune": remover}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    assert (await run.result()).status == "success"
    assert (plain / "app.py").read_text() == "print('v2')\n"
    assert (plain / "new" / "deep.txt").read_text() == "deep\n"
    assert not (plain / "gone.txt").exists()


async def test_a_rejected_merge_back_writes_nothing_stops_the_run_and_cleans_up(
    repo, run_dir
) -> None:
    raw = _plan(
        _fan("worktree", count=2), _merge(), {"id": "after", "kind": "agent", "prompt": "x"}
    )
    broker = MemoryAgentBroker(
        {"impl": _per_replica, "after": "late"}, gate_answers={"merge": False}
    )
    run, _ = await _run(raw, repo, run_dir, broker)
    result = await run.result()
    assert result.status == "cancelled"
    doc = _result(run_dir)
    assert doc["cancel_reason"] == "merge_back_rejected:merge"
    assert doc["steps"]["after"]["null_reasons"] == ["halted_by_gate"]
    assert not (repo / "part0.txt").exists()
    assert _branches(repo) == []  # a human no is final: nothing pending


async def test_a_merge_back_with_no_human_refuses_and_keeps_the_workspaces(repo, run_dir) -> None:
    raw = _plan(_fan("worktree", count=1), _merge())
    run, _ = await _run(raw, repo, run_dir, MemoryAgentBroker({"impl": _per_replica}))
    result = await run.result()
    assert result.status == "cancelled"
    assert _result(run_dir)["cancel_reason"] == "merge_back_refused:merge:no_human"
    assert not (repo / "part0.txt").exists()
    assert len(_branches(repo)) == 1  # pending: kept for a resume
    assert {row["state"] for row in _result(run_dir)["workspaces"].values()} == {"kept"}


async def test_a_conflict_is_refused_by_name_and_nothing_is_written(repo, run_dir) -> None:
    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        (root / "app.py").write_text("print('agent')\n")
        (root / "other.txt").write_text("other\n")
        (repo / "app.py").write_text("print('human')\n")  # the root moves on meanwhile
        return "edited"

    raw = _plan(_fan("worktree", count=1), _merge())
    broker = MemoryAgentBroker({"impl": script}, gate_answers={"merge": True})
    run, _ = await _run(raw, repo, run_dir, broker)

    assert (await run.result()).status == "partial"
    reasons = _result(run_dir)["steps"]["merge"]["null_reasons"]
    assert reasons and reasons[0].startswith("merge_back_conflict:merge:conflict:app.py@")
    assert broker.gate_calls == []  # nothing to approve: refused before asking
    assert (repo / "app.py").read_text() == "print('human')\n"
    assert not (repo / "other.txt").exists()  # all or nothing
    assert len(_branches(repo)) == 1  # pending: kept


async def test_two_workspaces_changing_one_file_differently_is_an_overlap(plain, run_dir) -> None:
    def script(request: AgentTurnRequest) -> str:
        (Path(request.metadata["workspace"]) / "app.py").write_text(
            f"print({request.metadata['replica']})\n"
        )
        return "x"

    raw = _plan(_fan("copy", count=2), _merge())
    broker = MemoryAgentBroker({"impl": script}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    await run.result()
    reasons = _result(run_dir)["steps"]["merge"]["null_reasons"]
    assert "overlap:app.py" in reasons[0]
    assert (plain / "app.py").read_text() == "print('v1')\n"


@pytest.mark.parametrize("kind", ["symlink", "binary", "dotgit", "symlinked_parent"])
async def test_unsupported_or_unsafe_changes_are_refused_by_name(
    plain, run_dir, tmp_path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "symlinked_parent":
        (plain / "linkdir").symlink_to(outside)

    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        if kind == "symlink":
            (root / "evil").symlink_to(outside)
        elif kind == "binary":
            (root / "blob.bin").write_bytes(b"\0\1\2")
        elif kind == "dotgit":
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("[core]\n\tfsmonitor = touch pwned\n")
        else:
            (root / "linkdir").unlink()
            (root / "linkdir").mkdir()
            (root / "linkdir" / "planted.txt").write_text("planted\n")
        return "x"

    raw = _plan(_fan("copy", count=1), _merge())
    broker = MemoryAgentBroker({"impl": script}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    await run.result()
    doc = _result(run_dir)
    if kind == "dotgit":  # .git is never diffed, so never merged
        assert doc["output"]["merge"]["files"] == []
        assert not (plain / ".git").exists()
        return
    reason = doc["steps"]["merge"]["null_reasons"][0]
    expected = {"symlink": "symlink:evil", "binary": "binary:blob.bin"}
    assert expected.get(kind, "unsafe_path:linkdir") in reason
    assert not (outside / "planted.txt").exists()
    assert not (plain / "evil").exists() and not (plain / "blob.bin").exists()


async def test_a_failed_agents_workspace_is_skipped_by_name(plain, run_dir) -> None:
    def script(request: AgentTurnRequest) -> Any:
        _per_replica(request)
        return None if request.metadata["replica"] == 1 else "ok"

    raw = _plan(_fan("copy", count=2), _merge())
    broker = MemoryAgentBroker({"impl": script}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    await run.result()
    output = _result(run_dir)["output"]["merge"]
    assert output["files"] == ["part0.txt"]
    assert [row["why"] for row in output["skipped"]] == ["agent_failed"]
    assert not (plain / "part1.txt").exists()


async def test_nothing_changed_asks_nobody(plain, run_dir) -> None:
    raw = _plan(_fan("copy", count=1), _merge())
    broker = MemoryAgentBroker({"impl": "read only"}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    assert (await run.result()).status == "success"
    assert broker.gate_calls == []
    assert _result(run_dir)["output"]["merge"]["applied"] is False


async def test_a_merge_back_is_still_asked_after_the_token_budget_is_spent(plain, run_dir) -> None:
    """A merge-back spends no tokens: a spent budget refuses agents, not the
    human's decision on work already paid for."""
    raw = _plan(_fan("copy", count=1), _merge(), budget={"max_tokens": 100})
    broker = MemoryAgentBroker({"impl": _per_replica}, gate_answers={"merge": True})
    run, _ = await _run(raw, plain, run_dir, broker)
    await run.result()
    assert len(broker.gate_calls) == 1
    assert (plain / "part0.txt").exists()


async def test_isolation_is_part_of_the_journal_key(plain, run_dir) -> None:
    keys = []
    for mode in ("none", "copy"):
        broker = MemoryAgentBroker({"impl": "ok"})
        step = {**_fan(mode, count=1), "use": None}
        step.pop("use")
        await _run(_plan(step), plain, run_dir, broker)
        keys.append(broker.calls[0].call_key)
    assert keys[0] != keys[1]  # effects that land elsewhere never replay as the same call


async def test_a_copy_never_holds_the_run_directory_inside_the_project(plain) -> None:
    run_dir = plain / ".runs" / "run-test-1"
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text("")
    seen: dict[str, bool] = {}

    def script(request: AgentTurnRequest) -> str:
        root = Path(request.metadata["workspace"])
        seen["parent"] = (root / ".runs").is_dir()  # the parent is copied ...
        seen["run"] = (root / ".runs" / "run-test-1").exists()  # ... never the run itself
        return "ok"

    run, _ = await _run(
        _plan(_fan("copy", count=1)), plain, run_dir, MemoryAgentBroker({"impl": script})
    )
    assert (await run.result()).status == "success"
    assert seen == {"parent": True, "run": False}


# ── resume: journalled, never asked twice, never applied twice ──────────────


async def test_resume_after_a_refusal_replays_the_agents_and_asks_once(repo, run_dir) -> None:
    raw = _plan(_fan("worktree", count=2), _merge())
    first = MemoryAgentBroker({"impl": _per_replica})  # no human: refused, kept
    await _run(raw, repo, run_dir, first)
    assert (len(_branches(repo)), (repo / "part0.txt").exists()) == (2, False)

    second = MemoryAgentBroker({}, gate_answers={"merge": True})  # unscripted: must replay
    run, _ = await _run(raw, repo, run_dir, second, attempt=2)
    status = (await run.result()).status
    made = ((repo / "part0.txt").exists(), (repo / "part1.txt").exists())
    # the agents replayed from the journal, the human was asked once, applied, cleaned up
    assert (status, second.calls, len(second.gate_calls), made, _branches(repo)) == (
        "success",
        [],
        1,
        (True, True),
        [],
    )

    third = MemoryAgentBroker({}, gate_answers={"merge": False})
    run, _ = await _run(raw, repo, run_dir, third, attempt=3)
    status = (await run.result()).status
    applied = _result(run_dir)["output"]["merge"]["applied"]
    # the merge replays: not asked, not applied again
    assert (status, third.gate_calls, applied) == ("success", [], True)


class _MovingRoot(MemoryAgentBroker):
    """A human who edits the project while the card is open, then says yes."""

    def __init__(self, *args: Any, edit: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.edit = edit

    async def ask_gate(self, request: GateRequest) -> GateDecision:
        self.edit.write_text("print('moved')\n")
        return await super().ask_gate(request)


async def test_an_approval_is_journalled_so_a_resume_after_a_conflict_does_not_ask_again(
    repo, run_dir
) -> None:
    def script(request: AgentTurnRequest) -> str:
        (Path(request.metadata["workspace"]) / "app.py").write_text("print('agent')\n")
        return "edited"

    raw = _plan(_fan("worktree", count=1), _merge())
    moving = _MovingRoot({"impl": script}, gate_answers={"merge": True}, edit=repo / "app.py")
    await _run(raw, repo, run_dir, moving)
    reason = _result(run_dir)["steps"]["merge"]["null_reasons"][0]
    assert "conflict:app.py" in reason  # re-checked after the yes: nothing written
    assert (repo / "app.py").read_text() == "print('moved')\n"

    _git(repo, "checkout", "--", "app.py")  # the human puts the root back
    again = MemoryAgentBroker({})  # no lane: must not be asked
    run, _ = await _run(raw, repo, run_dir, again, attempt=2)
    assert (await run.result()).status == "success"
    assert again.gate_calls == [] and again.calls == []
    assert (repo / "app.py").read_text() == "print('agent')\n"


async def test_from_phase_keeps_the_workspace_records(repo, run_dir) -> None:
    raw = _plan(_fan("worktree", count=1), _merge())
    await _run(raw, repo, run_dir, MemoryAgentBroker({"impl": _per_replica}))
    before = len(_ws_records(run_dir))
    program = json.loads((run_dir / "plan.json").read_text())
    PlanJournal(run_dir).invalidate_from_phase(program, "p1")
    assert len(_ws_records(run_dir)) == before > 0


# ── cleanup on success, failure and cancel ──────────────────────────────────


async def test_cleanup_on_failure(repo, run_dir) -> None:
    def script(request: AgentTurnRequest) -> None:
        _per_replica(request)
        return None

    run, _ = await _run(_plan(_fan("worktree")), repo, run_dir, MemoryAgentBroker({"impl": script}))
    assert (await run.result()).status == "failed"
    assert _branches(repo) == [] and _worktrees(repo) == 1
    assert not any((run_dir / ws_mod.WORKSPACES_DIR).glob("*-*/"))


@pytest.mark.parametrize("with_merge", [False, True])
async def test_cleanup_on_cancel(repo, run_dir, with_merge: bool) -> None:
    hold = asyncio.Event()
    raw = _plan(_fan("worktree", count=2), *([_merge()] if with_merge else []))
    broker = MemoryAgentBroker({"impl": _per_replica}, gates={"impl#1": hold})
    run = await PlanEngine().start(_compile(raw, repo), _services(run_dir, broker))

    async def cancel_when_started() -> None:
        while len(broker.calls) < 2:
            await asyncio.sleep(0.01)
        await run.cancel("stop")
        hold.set()

    canceller = asyncio.ensure_future(cancel_when_started())
    _events = [event async for event in run.events()]
    await canceller
    assert (await run.result()).status == "cancelled"
    if with_merge:
        # the merge-back never ran: pending, so the workspaces are kept
        assert len(_branches(repo)) == 2
    else:
        assert _branches(repo) == [] and _worktrees(repo) == 1


def test_remove_deletes_the_worktree_and_its_branch(repo, run_dir) -> None:
    ws = ws_mod.create(
        "solo-1", "worktree", str(repo), run_dir, run_id="r", step="impl", node="impl#0"
    )
    (Path(ws.root) / "dirty.txt").write_text("untracked\n")
    assert _branches(repo) == [ws.branch]
    assert ws_mod.remove(ws, run_dir) is None
    assert _branches(repo) == [] and not Path(ws.path).exists()
    assert not ws_mod._manifest_path(run_dir, "solo-1").exists()


@pytest.mark.parametrize(
    "rel", ["../escape.txt", ".git/hooks/pre-commit", "pkg/.git/config", "/etc/passwd"]
)
def test_a_path_into_git_or_out_of_the_root_is_never_merged(plain, rel: str) -> None:
    """Defence in depth: the diff walk never lists .git, and the merge check
    refuses it (and any path leaving the root) by name all the same."""
    ws = ws_mod.Workspace(
        id="w",
        mode="copy",
        path=str(plain),
        root=str(plain),
        project_root=str(plain),
        step="impl",
        node="impl#0",
    )
    change = ws_mod.Change(rel=rel, kind="add", base=None, new="sha256:x")
    assert ws_mod.merge_problems(ws, [change]) == [f"unsafe_path:{rel}"]


def test_workspace_ids_are_path_and_ref_safe() -> None:
    ws_id = ws_mod.workspace_id("loop.iterations[0].impl[ab/../c]#2")
    assert "/" not in ws_id and ".." not in ws_id and " " not in ws_id
    assert ws_mod.workspace_id("a#1") != ws_mod.workspace_id("a#2")
    assert os.sep not in ws_id
