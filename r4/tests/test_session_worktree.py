"""ADR-108 W1c: the agent builds the worktree by itself when it is enabled.

Maintainer 2026-11-04: "worktree should be automatically build by agents on
cli, tui, vscode when it is enabled". One controller per session; auto mode
enters ``auto-<session>`` before the first writing tool call; afterwards the
workspace guard keeps every write inside the worktree.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from git_hermetic import git_init, run_git

from r4.companion import session_worktree as sw
from r4.companion.agent_workspace import WorkspaceEscape, resolve


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(sw.MODE_ENV, raising=False)
    monkeypatch.setattr(sw, "profile_mode", lambda profile=None: "")
    root = tmp_path / "repo"
    root.mkdir()
    git_init(root)
    (root / "a.txt").write_text("one\n")
    run_git(root, "add", "a.txt")
    run_git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def controller(repo: Path):
    rebinds: list[str] = []
    ctrl = sw.WorktreeController(
        owner="session:s-77", rebind=lambda: rebinds.append(os.getcwd()), session_key="s-77abcdef9"
    )
    ctrl.rebinds = rebinds  # type: ignore[attr-defined]
    sw.set_current_controller(ctrl)
    yield ctrl
    ctrl.worktree = None  # no exit-time cleanup in the test process
    sw.set_current_controller(None)


def test_mode_resolution_session_then_env_then_profile(controller, monkeypatch) -> None:
    assert controller.mode() == ("off", "default")
    monkeypatch.setattr(sw, "profile_mode", lambda profile=None: "auto")
    assert controller.mode() == ("auto", "profile")
    monkeypatch.setenv(sw.MODE_ENV, "off")
    assert controller.mode() == ("off", "env")
    controller.set_session_mode("auto")
    assert controller.mode() == ("auto", "session")
    with pytest.raises(ValueError):
        controller.set_session_mode("sometimes")


def test_off_never_enters(controller) -> None:
    assert controller.maybe_auto("file_write") == ""
    assert controller.worktree is None


def test_auto_enters_before_the_first_writer_only(controller, repo: Path) -> None:
    controller.set_session_mode("auto")
    assert controller.maybe_auto("file_read") == ""  # reads never trigger it
    note = controller.maybe_auto("file_write")
    expected = os.path.realpath(repo / ".r4" / "worktrees" / "auto-s-77abcd")
    assert ("auto worktree" in note, os.path.realpath(os.getcwd())) == (True, expected)
    assert controller.maybe_auto("shell_exec") == ""  # already in one
    assert controller.rebinds == [os.getcwd()]  # type: ignore[attr-defined]


def test_auto_outside_a_repository_does_nothing(controller, tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    controller.set_session_mode("auto")
    assert (controller.maybe_auto("file_write"), controller.worktree) == ("", None)


def test_the_guard_keeps_writes_in_the_worktree(controller, repo: Path) -> None:
    """The model may still name an absolute path in the user's checkout."""
    controller.enter("guarded")
    original = repo / "a.txt"
    with controller.guard():
        with pytest.raises(WorkspaceEscape):
            resolve(str(original), write=True)
        assert resolve(str(original), write=False) == Path(os.path.realpath(original))  # reads may
        inside = resolve("new.txt", write=True)
    assert os.path.realpath(inside).startswith(
        os.path.realpath(repo / ".r4" / "worktrees" / "guarded")
    )
    with controller.guard():  # outside a worktree the guard is a no-op
        pass


def test_no_guard_outside_a_worktree(controller, repo: Path) -> None:
    with controller.guard():
        assert resolve(str(repo / "a.txt"), write=True) == repo / "a.txt"


def test_listeners_hear_every_enter_and_exit(controller) -> None:
    events: list[dict] = []
    controller.add_listener(events.append)
    controller.enter("heard")
    controller.exit(action="remove")
    assert [(e["action"], e["source"], e["ok"]) for e in events] == [
        ("enter", "user", True),
        ("exit", "user", True),
    ]


def test_the_agent_tools_drive_the_same_controller(controller, repo: Path) -> None:
    from r4.companion.worktree_tools import build_worktree_tools

    tools = {t["name"]: t["execute"] for t in build_worktree_tools()}
    entered = json.loads(tools["worktree_enter"]())
    assert (entered["ok"], entered["worktree"]["id"]) == (True, "agent-s-77abcd")
    left = json.loads(tools["worktree_exit"](action="remove"))
    assert (left["ok"], left["removed"], os.path.realpath(os.getcwd())) == (
        True,
        True,
        os.path.realpath(repo),
    )


def test_agent_hook_and_guard_follow_the_current_controller(controller, repo: Path) -> None:
    from r4.companion.agent import _auto_worktree, _worktree_guard

    controller.set_session_mode("auto")
    assert "auto worktree" in _auto_worktree("file_edit")
    with _worktree_guard():
        with pytest.raises(WorkspaceEscape):
            resolve(str(repo / "a.txt"), write=True)


def test_permissions_enter_is_safe_exit_is_risky() -> None:
    from r4.companion.permissions import RISKY_TOOLS, SAFE_TOOLS

    assert ("worktree_enter" in SAFE_TOOLS, "worktree_exit" in RISKY_TOOLS) == (True, True)


def _agent() -> object:
    from unittest.mock import MagicMock

    from r4.companion.agent import ResearchCompanion
    from r4.companion.context import ContextManager
    from r4.companion.permissions import PermissionManager, PermissionMode
    from r4.companion.skills import SkillRegistry

    client = MagicMock()
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def test_end_to_end_the_agents_first_write_moves_it_and_stays_inside(
    controller, repo: Path
) -> None:
    """The real agent path: permission gate → auto enter → the real
    file_write under the guard → the note leads the result."""
    from r4.companion.tools import _core_tools

    agent = _agent()
    write = next(t for t in _core_tools() if t["name"] == "file_write")
    controller.set_session_mode("auto")

    def call(path: str, content: str) -> str:
        args = json.dumps({"path": path, "content": content})
        tool_call = {"id": "c1", "function": {"name": "file_write", "arguments": args}}
        return agent._execute_tool_call(tool_call, [write])  # type: ignore[attr-defined]

    first = call("out.txt", "made in the worktree\n")
    worktree = repo / ".r4" / "worktrees" / "auto-s-77abcd"
    assert first.startswith("[r4] auto worktree")
    made = ((worktree / "out.txt").read_text(), (repo / "out.txt").exists())
    assert made == ("made in the worktree\n", False)
    escaped = call(str(repo / "a.txt"), "overwritten\n")
    assert "refused" in escaped
    assert (repo / "a.txt").read_text() == "one\n"
    assert run_git(repo, "status", "--porcelain").stdout == ""


def test_close_removes_an_unchanged_worktree_and_keeps_a_changed_one(
    controller, repo: Path
) -> None:
    """Claude Code's end-of-session rule (no dialog on any surface)."""
    from r4 import worktrees as wt

    controller.enter("clean-one")
    assert "removed" in controller.close()
    controller.enter("busy-one")
    Path("a.txt").write_text("changed\n")
    line = controller.close()
    ids = [w.id for w in wt.list_worktrees(repo)]
    assert ("kept" in line, ids, controller.close()) == (True, ["busy-one"], "")
