"""ADR-108 D-2 (W1b): the work-level worktree module on a real git repo.

Every git call in the scratch repository goes through ``git_hermetic``, so
the caller's repository (and the shared .git/config of a linked worktree)
is never touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from git_hermetic import git_init, run_git

from r4 import worktrees as wt


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    git_init(root)
    (root / "a.txt").write_text("one\n")
    run_git(root, "add", "a.txt")
    run_git(root, "commit", "-q", "-m", "init")
    return root


def _status(path: Path) -> str:
    return run_git(path, "status", "--porcelain").stdout


def test_create_from_head_makes_a_branch_and_registers_it(repo: Path) -> None:
    made, warning = wt.create(repo, "feat-x", owner="session:s1")
    assert (warning, made.branch, made.parent) == ("", "r4/feat-x", "")
    path = Path(made.path)
    assert (path, (path / "a.txt").read_text()) == (repo / ".r4" / "worktrees" / "feat-x", "one\n")
    here = wt.current(made.path)
    listed = [w.id for w in wt.list_worktrees(repo)]
    assert (listed, getattr(here, "id", None), wt.current(repo)) == (["feat-x"], "feat-x", None)


def test_the_users_checkout_is_never_touched(repo: Path) -> None:
    (repo / "a.txt").write_text("edited, not committed\n")
    (repo / "new.txt").write_text("untracked\n")
    before = _status(repo)
    head = run_git(repo, "rev-parse", "HEAD").stdout
    made, _ = wt.create(repo, "snap")
    assert _status(repo) == before  # .r4/ is excluded, the index is unchanged
    assert run_git(repo, "rev-parse", "HEAD").stdout == head
    # The worktree starts from the snapshot: uncommitted and untracked work.
    assert (Path(made.path) / "a.txt").read_text() == "edited, not committed\n"
    assert (Path(made.path) / "new.txt").read_text() == "untracked\n"
    assert made.snapshot_ref == "refs/r4/snap/snap"


def test_a_child_starts_from_its_parents_state(repo: Path) -> None:
    parent, _ = wt.create(repo, "parent")
    (Path(parent.path) / "b.txt").write_text("parent work\n")
    child, _ = wt.create(parent.path, "child")
    assert child.parent == "parent"
    assert (Path(child.path) / "b.txt").read_text() == "parent work\n"
    # Flat directories; the hierarchy is in the registry.
    assert Path(child.path).parent == Path(parent.path).parent


def test_changed_means_edits_or_commits_beyond_the_base(repo: Path) -> None:
    made, _ = wt.create(repo, "c")
    assert not wt.is_changed(made)
    (Path(made.path) / "a.txt").write_text("two\n")
    assert wt.is_changed(made)
    run_git(Path(made.path), "commit", "-q", "-am", "work")
    assert wt.is_changed(made)


def test_remove_refuses_changes_and_children(repo: Path) -> None:
    parent, _ = wt.create(repo, "p")
    child, _ = wt.create(parent.path, "k")
    with pytest.raises(wt.WorkspaceError) as refused:
        wt.remove(repo, "p")
    assert refused.value.code == "has_children"
    (Path(child.path) / "a.txt").write_text("changed\n")
    with pytest.raises(wt.WorkspaceError) as changed:
        wt.remove(repo, "k")
    assert changed.value.code == "changed"
    wt.remove(repo, "k", discard=True)
    wt.remove(repo, "p")
    assert wt.list_worktrees(repo) == []
    branches = run_git(repo, "branch", "--list", "r4/*").stdout
    assert branches.strip() == ""


def test_clean_removes_unchanged_leaves_first_and_keeps_changed(repo: Path) -> None:
    parent, _ = wt.create(repo, "top")
    wt.create(parent.path, "leaf")
    kept, _ = wt.create(repo, "busy")
    (Path(kept.path) / "a.txt").write_text("changed\n")
    assert sorted(wt.clean(repo)) == ["leaf", "top"]
    assert [w.id for w in wt.list_worktrees(repo)] == ["busy"]


def test_names_and_duplicates_are_refused_by_name(repo: Path) -> None:
    with pytest.raises(wt.WorkspaceError) as bad:
        wt.create(repo, "../escape")
    assert bad.value.code == "bad_name"
    wt.create(repo, "once")
    with pytest.raises(wt.WorkspaceError) as dup:
        wt.create(repo, "once")
    assert dup.value.code == "exists"


def test_not_a_git_repo_is_refused_by_name(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(wt.WorkspaceError) as refused:
        wt.create(plain, "x")
    assert refused.value.code == "not_a_git_repo"


# ── the bridge verbs (worktree_bridge) ───────────────────────────────────


class _Host:
    """A bridge host whose session owns a real controller; it records the
    re-roots and the console lines the bridge emits."""

    def __init__(self) -> None:
        from r4.companion.session_worktree import WorktreeController
        from r4.companion.ui.worktree_bridge import WorktreeBridgeMixin

        self.rebinds: list[str] = []
        self.lines: list[dict] = []
        controller = WorktreeController(
            owner="session:s-1", rebind=lambda: self.rebinds.append(os.getcwd()), session_key="s-1"
        )
        session = type("S", (), {"session_id": "s-1", "worktree": controller})()
        self.bridge = type(
            "B", (WorktreeBridgeMixin,), {"_emit_line": lambda _s, line: self.lines.append(line)}
        )()
        self.bridge.session = session
        self.bridge._attach_worktree_events(session)
        self.controller = controller


def test_enter_and_exit_move_the_bridge_and_reroot_the_session(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    host = _Host()
    entered = host.bridge.worktree_enter({"name": "work"})
    inside = os.path.realpath(os.getcwd())
    target = os.path.realpath(repo / ".r4" / "worktrees" / "work")
    assert (entered["ok"], entered["created"], inside, host.rebinds) == (
        True,
        True,
        target,
        [os.getcwd()],
    )
    listed = host.bridge.worktree_list({})
    assert (listed["current"], listed["worktrees"][0]["owner"]) == ("work", "session:s-1")
    again = host.bridge.worktree_enter({"name": "other"})  # exit first
    left = host.bridge.worktree_exit({"action": "remove"})
    back = os.path.realpath(os.getcwd())
    summary = (again["ok"], left["ok"], left["removed"], back, len(host.rebinds))
    assert summary == (False, True, True, os.path.realpath(repo), 2)


def test_a_changed_worktree_is_kept_unless_discarded(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    host = _Host()
    host.bridge.worktree_enter({"name": "busy"})
    Path("a.txt").write_text("changed in the worktree\n")
    left = host.bridge.worktree_exit({"action": "remove"})
    assert left["ok"] and not left["removed"] and left["changed"]
    assert "kept" in left["message"]
    assert [w.id for w in wt.list_worktrees(repo)] == ["busy"]
    host.bridge.worktree_enter({"name": "busy"})  # re-enter the kept one
    gone = host.bridge.worktree_exit({"action": "remove", "discard": True})
    assert gone["removed"] and wt.list_worktrees(repo) == []


# ── the CLI (`r4 worktree`, `r4 --worktree NAME`) ────────────────────────


def _cli(*argv: str) -> int:
    from argparse import ArgumentParser

    from r4._cli.worktree import WorktreeCommand

    command = WorktreeCommand()
    parser = ArgumentParser()
    command.register(parser.add_subparsers(dest="cmd"))
    return command.handle(parser.parse_args(["worktree", *argv]))


def test_cli_create_list_remove_clean(repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(repo)
    assert _cli("create", "cli-a") == 0
    assert "✓ worktree cli-a" in capsys.readouterr().out
    assert _cli("list") == 0
    assert "cli-a  r4/cli-a  clean" in capsys.readouterr().out
    assert _cli("remove", "nope") == 1
    assert "no worktree nope" in capsys.readouterr().err
    assert _cli("clean") == 0
    assert "removed 1 unchanged worktree(s): cli-a" in capsys.readouterr().out


def test_launch_flag_makes_and_enters_the_worktree(repo: Path, monkeypatch, capsys) -> None:
    from argparse import Namespace

    from r4.cli import _apply_worktree_flag

    monkeypatch.chdir(repo)
    monkeypatch.delenv("R4_TUI_WORKSPACE", raising=False)
    _apply_worktree_flag(Namespace(root_worktree="launch"))
    target = os.path.realpath(repo / ".r4" / "worktrees" / "launch")
    assert os.path.realpath(os.getcwd()) == target
    assert os.path.realpath(os.environ["R4_TUI_WORKSPACE"]) == target
    assert "worktree launch:" in capsys.readouterr().err
    monkeypatch.chdir(repo)
    _apply_worktree_flag(Namespace(root_worktree="launch"))  # reused, not refused
    assert [w.id for w in wt.list_worktrees(repo)] == ["launch"]
    _apply_worktree_flag(Namespace(root_worktree=None))  # no flag: nothing
