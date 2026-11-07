"""Tests for gitignore-aware directory listing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git_hermetic import git_init, run_git

from r4.companion import tools as t
from r4.companion.file_filtering import list_directory_entries

# A hook's GIT_* variables point at the real repository (plan H-3.3).
_HOOK_GIT_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)


def _git_init(path: Path) -> None:
    git_init(path)


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
def test_list_dir_respects_gitignore(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "tracked.txt").write_text("x")
    run_git(tmp_path, "add", ".gitignore", "tracked.txt")
    run_git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "ignored.log").write_text("x")

    out = t._list_dir(str(tmp_path))
    assert "tracked.txt" in out
    assert "ignored.log" not in out


def test_list_dir_skips_builtin_heavy_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg").write_text("x")
    (tmp_path / "src").mkdir()

    out = t._list_dir(str(tmp_path))
    assert "src/" in out
    assert "node_modules" not in out


def test_list_dir_hides_dotfiles_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("R4_COMPANION_RESPECT_HIDDEN_FILES", raising=False)
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "visible.txt").write_text("ok")

    out = t._list_dir(str(tmp_path))
    assert "visible.txt" in out
    assert ".env" not in out


def test_list_dir_ignore_parameter(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x")
    (tmp_path / "noise.log").write_text("x")

    out = t._list_dir(str(tmp_path), ignore=["*.log"])
    assert "keep.py" in out
    assert "noise.log" not in out


def test_list_dir_sorts_dirs_first(tmp_path: Path) -> None:
    (tmp_path / "zebra.txt").write_text("x")
    (tmp_path / "alpha_dir").mkdir()
    out = t._list_dir(str(tmp_path))
    lines = [line for line in out.splitlines() if line and not line.startswith("(")]
    assert lines[0] == "alpha_dir/"
    assert "zebra.txt" in lines


def test_list_directory_entries_non_git_note(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    entries, notes = list_directory_entries(tmp_path)
    assert len(entries) == 1
    assert any("not a git repository" in note for note in notes)


def test_list_dir_truncates_large_directories(tmp_path: Path) -> None:
    for i in range(205):
        (tmp_path / f"file_{i:03d}.txt").write_text("x")

    out = t._list_dir(str(tmp_path))
    lines = [line for line in out.splitlines() if line and not line.startswith("(")]
    assert len(lines) == 200
    assert "(…and 5 more)" in out


def test_hidden_dotfile_note_excludes_builtin_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "visible.txt").write_text("ok")

    out = t._list_dir(str(tmp_path))
    assert "1 hidden dotfile(s) omitted" in out


def _sentinel_repo(root: Path) -> Path:
    sentinel = root / "sentinel"
    sentinel.mkdir()
    git_init(sentinel)
    run_git(sentinel, "config", "user.name", "sentinel-owner")
    (sentinel / "keep.txt").write_text("sentinel")
    run_git(sentinel, "add", "keep.txt")
    run_git(sentinel, "commit", "-q", "-m", "sentinel")
    return sentinel


def _point_hook_vars_at(monkeypatch: pytest.MonkeyPatch, sentinel: Path) -> None:
    git_dir = sentinel / ".git"
    targets = {
        "GIT_DIR": git_dir,
        "GIT_INDEX_FILE": git_dir / "index",
        "GIT_WORK_TREE": sentinel,
        "GIT_COMMON_DIR": git_dir,
        "GIT_OBJECT_DIRECTORY": git_dir / "objects",
    }
    for name in _HOOK_GIT_VARS:
        monkeypatch.setenv(name, str(targets[name]))


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)
def test_git_init_under_hook_env_leaves_the_real_repo_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-3.3: a commit hook's GIT_* variables must not redirect the helper."""
    sentinel = _sentinel_repo(tmp_path)
    config_before = (sentinel / ".git" / "config").read_bytes()
    index_before = (sentinel / ".git" / "index").read_bytes()
    head_before = run_git(sentinel, "rev-parse", "HEAD").stdout
    target = tmp_path / "target"
    target.mkdir()
    _point_hook_vars_at(monkeypatch, sentinel)

    _git_init(target)
    assert (sentinel / ".git" / "config").read_bytes() == config_before
    (target / "a.txt").write_text("a")
    run_git(target, "add", "a.txt")
    run_git(target, "commit", "-q", "-m", "a")

    assert (sentinel / ".git" / "config").read_bytes() == config_before
    assert (sentinel / ".git" / "index").read_bytes() == index_before
    assert run_git(sentinel, "rev-parse", "HEAD").stdout == head_before
    assert (target / ".git" / "config").is_file()
    assert run_git(target, "config", "user.name").stdout.strip() == "test"
