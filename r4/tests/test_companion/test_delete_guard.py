"""Delete recognition and backup, through ADR-104 amendment 3.

Autopilot asks before every recognised deletion (2026-11-09). Other automatic
allow paths retain amendment 2: a delete runs only once every target is
recoverable, committed unchanged in git, or copied to R4's trash first.
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

    def test_quoted_remote_control_operators_are_not_deleters(self):
        command = (
            'ssh -o BatchMode=yes -o ConnectTimeout=15 AI4S \'printf "%s\\n" '
            '"--- /etc/slurm ---"; ls -la /etc/slurm; for f in /etc/slurm/*; '
            'do [ -f "$f" ] && { echo "### $f"; sed -n "1,100p" "$f"; }; done; '
            'printf "%s\\n" "--- routes/controllers ---"; getent hosts sv08 sv09 '
            'slurmctld 2>&1 || true; printf "%s\\n" "--- processes ---"; '
            'ps -ef | grep -E "slurm|munge" | grep -v grep || true\''
        )
        found = scan("shell_exec", {"command": command})
        assert not found.deletes, found

    @pytest.mark.parametrize(
        ("command", "paths", "unresolved"),
        [
            ("echo before\nrm victim", ("victim",), ""),
            ("printf ok\nfind . -delete", (), "find may delete files it matches"),
            ("ssh host 'printf ok; ps -ef | grep x || true'\nrm victim", ("victim",), ""),
            ("printf 'before\nrm victim'", (), ""),
            ("printf before\\\nrm victim", (), ""),
            (r"echo \; rm victim", (), ""),
            ("echo ';' rm victim", (), ""),
            ("echo ok |& rm victim", ("victim",), ""),
            ("printf ok |& find . -delete", (), "find may delete files it matches"),
            ("echo before # user's\nrm victim\necho after # team's", ("victim",), ""),
            ("echo ok # rm not_a_command\nprintf fine", (), ""),
        ],
    )
    def test_real_newlines_split_commands_but_quoted_or_escaped_newlines_do_not(
        self, command, paths, unresolved
    ):
        found = scan("shell_exec", {"command": command})
        assert found.paths == paths
        assert found.unresolved == unresolved

    def test_pipe_ampersand_before_a_deletion_requires_approval(self):
        asked: list[str] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda tool, *_a: asked.append(tool) or False
        )
        assert manager.check("shell_exec", {"command": "echo ok |& rm victim"}) is False
        assert asked == ["shell_exec"]

    def test_newline_after_a_harmless_remote_script_still_requires_delete_approval(self):
        asked: list[str] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda tool, *_a: asked.append(tool) or False
        )
        command = "ssh host 'printf ok; ps -ef | grep x || true'\nrm victim"
        assert manager.check("shell_exec", {"command": command}) is False
        assert asked == ["shell_exec"]

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

    def test_apply_patch_rename_deletes_the_source_path(self):
        patch = (
            "*** Begin Patch\n*** Update File: old.py\n*** Move to: new.py\n"
            "@@\n-x\n+y\n*** End Patch\n"
        )
        assert scan("apply_patch", {"patch_text": patch}).paths == ("old.py",)

    def test_worktree_exit_remove_is_a_delete_but_keep_is_not(self):
        assert scan("worktree_exit", {"action": "remove"}).deletes
        assert scan("worktree_exit", {"action": "remove", "discard": True}).deletes
        assert not scan("worktree_exit", {"action": "keep"}).deletes

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
    def test_autopilot_asks_before_a_delete_even_when_a_backup_is_possible(self, workspace):
        (workspace / "scratch.txt").write_text("y")
        asked: list[tuple] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda *a: asked.append(a) or False
        )
        assert manager.check("shell_exec", {"command": "rm scratch.txt"}) is False
        assert len(asked) == 1 and "deletion" in asked[0][1]
        assert _trash(workspace) == []
        take_permission_notes()

    def test_autopilot_worktree_remove_asks_but_keep_runs(self):
        asked: list[str] = []
        manager = PermissionManager(
            PermissionMode.TRUST,
            approval_callback=lambda tool, *_a: asked.append(tool) or False,
        )
        assert manager.check("worktree_exit", {"action": "keep"}) is True
        assert manager.check("worktree_exit", {"action": "remove"}) is False
        assert asked == ["worktree_exit"]

    def test_autopilot_asks_for_a_delete_it_cannot_back_up(self, workspace):
        asked: list[tuple] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda *a: asked.append(a) or False
        )
        assert manager.check("shell_exec", {"command": "rm -rf *"}) is False
        assert asked and "deletion" in asked[0][1]

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

    def test_autopilot_auto_answers_credentials_and_knowledge_decisions(self, workspace):
        asked: list[str] = []
        manager = PermissionManager(
            PermissionMode.TRUST, approval_callback=lambda tool, *_a: asked.append(tool) or False
        )
        assert manager.check("shell_exec", {"command": "cat ~/.ssh/id_ed25519"}) is True
        assert manager.check("knowledge_graph_decide", {"proposal_id": "p"}) is True
        assert manager.check("team_plan_run", {"template": "t"}) is True
        assert asked == []


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
