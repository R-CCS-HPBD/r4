"""ADR-104 amendment 2: true autopilot, except a delete without a full backup.

Maintainer, 2026-11-05: "we need true autopilot mode (except file delete if
no full backup exists)". A delete nobody approved runs only once every target
is recoverable: committed unchanged in git, or copied to R4's trash first.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from r4.companion import delete_guard
from r4.companion.delete_guard import DeleteScan, protect, scan
from r4.companion.permissions import PermissionManager, PermissionMode, take_permission_notes


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _trash(workspace: Path) -> list[Path]:
    trash = Path(os.path.expanduser(delete_guard.TRASH_ROOT))
    return sorted(p for p in trash.rglob("*") if p.is_file()) if trash.exists() else []


class TestScan:
    @pytest.mark.parametrize(
        ("command", "paths"),
        [
            ("rm a.txt", ("a.txt",)),
            ("rm -rf build dist", ("build", "dist")),
            ("cd x && rm -f -- a.txt", ("a.txt",)),
            ("sudo rm b.log", ("b.log",)),
            ("bash -c 'rm c.txt'", ("c.txt",)),
            ("git rm notes.md", ("notes.md",)),
            ("unlink link", ("link",)),
            ("ls -la; cat a.txt", ()),
            ("git clean -n", ()),
        ],
    )
    def test_shell_known_targets(self, command, paths):
        assert scan("shell_exec", {"command": command}).paths == paths
        assert scan("shell_exec", {"command": command}).unresolved == ""

    @pytest.mark.parametrize(
        "command",
        [
            "rm *.txt",
            "rm $TARGET",
            "rm $(cat list)",
            "git clean -fd",
            "find . -name '*.o' -delete",
            "ls | xargs rm",
        ],
    )
    def test_shell_unknown_targets(self, command):
        assert scan("shell_exec", {"command": command}).unresolved

    def test_python_literal_and_computed(self):
        assert scan("python_exec", {"code": "import os\nos.remove('a.txt')"}).paths == ("a.txt",)
        assert scan("python_exec", {"code": "Path('d/x.bin').unlink()"}).paths == ("d/x.bin",)
        assert scan("python_exec", {"code": "import shutil; shutil.rmtree(out)"}).unresolved
        assert not scan("python_exec", {"code": "print('remove me')"}).deletes

    def test_apply_patch_delete_hunk(self):
        patch = "*** Begin Patch\n*** Delete File: old.py\n*** End Patch\n"
        assert scan("apply_patch", {"patch_text": patch}).paths == ("old.py",)
        update = "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch\n"
        assert not scan("apply_patch", {"patch_text": update}).deletes

    def test_other_tools_delete_nothing(self):
        assert not scan("file_write", {"path": "a", "content": ""}).deletes


class TestProtect:
    def test_an_uncommitted_file_is_copied_to_the_trash(self, workspace):
        (workspace / "data.csv").write_text("1,2,3\n")
        result = protect(DeleteScan(("data.csv",)))
        assert result.ok and "backed up 1 path" in result.note
        copies = _trash(workspace)
        assert [p.name for p in copies] == ["data.csv"] and copies[0].read_text() == "1,2,3\n"

    def test_a_directory_tree_is_copied(self, workspace):
        (workspace / "out" / "sub").mkdir(parents=True)
        (workspace / "out" / "sub" / "r.txt").write_text("r")
        assert protect(DeleteScan(("out",))).ok
        assert [p.name for p in _trash(workspace)] == ["r.txt"]

    def test_a_missing_target_needs_nothing(self, workspace):
        result = protect(DeleteScan(("nope.txt",)))
        assert result.ok and result.note == "" and _trash(workspace) == []

    def test_a_committed_clean_file_is_not_copied(self, workspace):
        _git(workspace, "init", "-q")
        (workspace / "tracked.py").write_text("x = 1\n")
        _git(workspace, "add", "tracked.py")
        _git(workspace, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
        result = protect(DeleteScan(("tracked.py",)))
        assert result.ok and "git can restore" in result.note
        assert _trash(workspace) == []
        (workspace / "tracked.py").write_text("x = 2\n")  # changed: copy it
        assert "backed up" in protect(DeleteScan(("tracked.py",))).note

    def test_outside_the_workspace_asks(self, workspace, tmp_path):
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("keep")
        result = protect(DeleteScan((str(outside),)))
        assert not result.ok and "outside the workspace" in result.reason

    def test_the_workspace_itself_asks(self, workspace):
        assert not protect(DeleteScan((".",))).ok

    def test_unknown_targets_ask(self, workspace):
        result = protect(DeleteScan((), "the targets use a glob or a variable"))
        assert not result.ok and "glob" in result.reason

    def test_too_large_asks(self, workspace, monkeypatch):
        monkeypatch.setattr(delete_guard, "MAX_BACKUP_BYTES", 3)
        (workspace / "big.bin").write_bytes(b"12345")
        assert "too much" in protect(DeleteScan(("big.bin",))).reason

    def test_a_symlink_is_backed_up_as_the_link(self, workspace):
        (workspace / "real.txt").write_text("r")
        (workspace / "link").symlink_to(workspace / "real.txt")
        assert protect(DeleteScan(("link",))).ok
        trash = Path(os.path.expanduser(delete_guard.TRASH_ROOT))
        assert any(p.is_symlink() and p.name == "link" for p in trash.rglob("*"))


class TestLadder:
    def test_autopilot_deletes_after_the_backup_and_notes_it(self, workspace):
        (workspace / "scratch.txt").write_text("y")
        manager = PermissionManager(PermissionMode.TRUST, approval_callback=lambda *_a: False)
        assert manager.check("shell_exec", {"command": "rm scratch.txt"}) is True
        notes = take_permission_notes()
        assert any("backed up 1 path" in note for note in notes)
        assert [p.name for p in _trash(workspace)] == ["scratch.txt"]

    def test_autopilot_asks_for_a_delete_it_cannot_back_up(self, workspace):
        asked: list[tuple] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda *a: asked.append(a) or False
        )
        assert manager.check("shell_exec", {"command": "rm -rf *"}) is False
        assert asked and "cannot back up" in asked[0][1]

    def test_a_session_always_is_guarded_too(self, workspace):
        (workspace / "a.log").write_text("a")
        manager = PermissionManager(PermissionMode.AUTO_SAFE, approval_callback=lambda *_a: False)
        manager.approve_for_session("shell_exec")
        assert manager.check("shell_exec", {"command": "rm a.log"}) is True
        assert [p.name for p in _trash(workspace)] == ["a.log"]

    def test_a_person_approving_the_delete_makes_no_copy(self, workspace):
        (workspace / "b.log").write_text("b")
        manager = PermissionManager(PermissionMode.AUTO_SAFE, approval_callback=lambda *_a: True)
        assert manager.check("shell_exec", {"command": "rm b.log"}) is True
        assert _trash(workspace) == []

    def test_autopilot_still_asks_for_credentials_and_knowledge_decisions(self, workspace):
        asked: list[str] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda tool, *_a: asked.append(tool) or False
        )
        assert manager.check("shell_exec", {"command": "cat ~/.ssh/id_ed25519"}) is False
        assert manager.check("knowledge_graph_decide", {"proposal_id": "p"}) is False
        assert manager.check("team_plan_run", {"template": "t"}) is True
        assert asked == ["shell_exec", "knowledge_graph_decide"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
