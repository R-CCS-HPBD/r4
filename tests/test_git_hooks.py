"""Tracked git hooks and their installer (plan H-3.3/H-3.4, 2026-10-18).

Each test builds a scratch repository, copies the tracked hook (and a stub
gate that records its arguments and environment) into it, and commits there.
Every git call strips the caller's GIT_* variables: this file may itself run
under a hook, and a GIT_DIR left in place would point these commands at the
real repository.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "scripts" / "git-hooks"
INSTALLER = REPO / "scripts" / "install-hooks.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

_STUB_GATE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$(dirname "$0")/gate-args.log"
compgen -e | grep '^GIT_' > "$(dirname "$0")/gate-git-env.log" || true
exit "$(cat "$(dirname "$0")/gate-exit")"
"""

_STUB_BUN = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG_DIR/bun.log"
exit "$(cat "$STUB_LOG_DIR/bun-exit")"
"""


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, **extra)
    return env


def _git(cwd: Path, *args: str, check: bool = True, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=_env(**extra),
        capture_output=True,
        text=True,
        check=check,
    )


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _scratch_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README").write_text("scratch\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init", "--no-verify")
    return repo


@pytest.fixture
def hooked_repo(tmp_path: Path) -> Path:
    """A scratch repo running the tracked pre-commit against a stub gate."""
    repo = _scratch_repo(tmp_path)
    _executable(repo / "scripts" / "git-hooks" / "pre-commit", (HOOKS / "pre-commit").read_text())
    _executable(repo / "scripts" / "ci-lint-local.sh", _STUB_GATE)
    (repo / "scripts" / "gate-exit").write_text("0")
    _git(repo, "config", "core.hooksPath", "scripts/git-hooks")
    return repo


def _stage(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", rel)


def _commit(repo: Path, **extra: str) -> subprocess.CompletedProcess:
    return _git(repo, "commit", "-q", "-m", "change", check=False, **extra)


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ── pre-commit ──────────────────────────────────────────────────────────────


def test_a_failing_gate_blocks_an_r4_commit(hooked_repo: Path) -> None:
    """The old hook ran the r4 gate informationally and committed anyway."""
    (hooked_repo / "scripts" / "gate-exit").write_text("1")
    before = _head(hooked_repo)
    _stage(hooked_repo, "r4/src/r4/mod.py")

    result = _commit(hooked_repo)

    assert result.returncode != 0
    assert "BLOCKED" in result.stderr
    assert _head(hooked_repo) == before


def test_the_gate_gets_the_staged_paths(hooked_repo: Path) -> None:
    _stage(hooked_repo, "r4/src/r4/mod.py")
    _stage(hooked_repo, "docs/note.md", "note\n")
    (hooked_repo / "unstaged.py").write_text("y = 2\n")

    result = _commit(hooked_repo)

    assert result.returncode == 0, result.stderr
    args = (hooked_repo / "scripts" / "gate-args.log").read_text().split()
    assert args == ["--files", "docs/note.md", "r4/src/r4/mod.py"]


def test_the_gate_runs_without_the_hooks_git_variables(hooked_repo: Path) -> None:
    """H-3.3: GIT_INDEX_FILE/GIT_DIR from the hook never reach the gate's tests."""
    _stage(hooked_repo, "r4/src/r4/mod.py")

    result = _commit(hooked_repo, GIT_AUTHOR_NAME="hook-env-probe")

    assert result.returncode == 0, result.stderr
    leaked = (hooked_repo / "scripts" / "gate-git-env.log").read_text().split()
    assert leaked == []


def test_a_failing_tui_check_blocks_a_v3_commit(hooked_repo: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "bun", _STUB_BUN)
    (tmp_path / "bun-exit").write_text("1")
    _stage(hooked_repo, "packages/r4-tui-v3/src/a.ts", "export {}\n")
    before = _head(hooked_repo)

    result = _commit(
        hooked_repo, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", STUB_LOG_DIR=str(tmp_path)
    )

    assert result.returncode != 0
    assert (tmp_path / "bun.log").read_text().split() == ["run", "check"]
    assert _head(hooked_repo) == before


def test_a_commit_with_only_deletions_skips_the_gate(hooked_repo: Path) -> None:
    _git(hooked_repo, "rm", "-q", "README")

    result = _commit(hooked_repo)

    assert result.returncode == 0, result.stderr
    assert not (hooked_repo / "scripts" / "gate-args.log").exists()


# ── install-hooks.sh ────────────────────────────────────────────────────────


@pytest.fixture
def install_repo(tmp_path: Path) -> Path:
    """A scratch repo carrying the tracked hooks and the installer."""
    repo = _scratch_repo(tmp_path)
    for hook in HOOKS.iterdir():
        _executable(repo / "scripts" / "git-hooks" / hook.name, hook.read_text())
    _executable(repo / "scripts" / "install-hooks.sh", INSTALLER.read_text())
    return repo


def _install(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/install-hooks.sh", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _tracked_hook_names() -> list[str]:
    return sorted(p.name for p in HOOKS.iterdir() if os.access(p, os.X_OK))


def test_install_links_every_tracked_hook(install_repo: Path) -> None:
    result = _install(install_repo)

    assert result.returncode == 0, result.stderr
    for name in _tracked_hook_names():
        hook = install_repo / ".git" / "hooks" / name
        assert hook.is_symlink()
        assert hook.resolve() == (install_repo / "scripts" / "git-hooks" / name).resolve()
        assert f"linked    {name}" in result.stdout


def test_install_is_idempotent(install_repo: Path) -> None:
    _install(install_repo)

    again = _install(install_repo)

    assert again.returncode == 0, again.stderr
    assert "linked    " not in again.stdout
    assert again.stdout.count("ok        ") == len(_tracked_hook_names())


def test_install_refuses_a_regular_hook_file_without_force(install_repo: Path) -> None:
    local = install_repo / ".git" / "hooks" / "pre-commit"
    _executable(local, "#!/bin/sh\necho mine\n")

    result = _install(install_repo)

    assert result.returncode != 0
    assert "--force" in result.stderr
    assert not local.is_symlink()
    assert local.read_text() == "#!/bin/sh\necho mine\n"


def test_install_force_keeps_a_backup(install_repo: Path) -> None:
    local = install_repo / ".git" / "hooks" / "pre-commit"
    _executable(local, "#!/bin/sh\necho mine\n")

    result = _install(install_repo, "--force")

    assert result.returncode == 0, result.stderr
    assert local.is_symlink()
    backup = install_repo / ".git" / "hooks" / "pre-commit.local-backup"
    assert backup.read_text() == "#!/bin/sh\necho mine\n"


def test_install_from_a_worktree_links_the_shared_hooks(install_repo: Path, tmp_path: Path) -> None:
    _git(install_repo, "add", "scripts")
    _git(install_repo, "commit", "-q", "-m", "hooks", "--no-verify")
    worktree = tmp_path / "wt"
    _git(install_repo, "worktree", "add", "-q", str(worktree), "-b", "wave/x")

    result = _install(worktree)

    assert result.returncode == 0, result.stderr
    hook = install_repo / ".git" / "hooks" / "pre-commit"
    assert hook.resolve() == (install_repo / "scripts" / "git-hooks" / "pre-commit").resolve()
    assert not (worktree / ".git").is_dir()  # a worktree's .git is a file


def test_install_from_a_worktree_refuses_a_hook_missing_in_the_main_checkout(
    install_repo: Path, tmp_path: Path
) -> None:
    """The links point at the main checkout; a hook only the worktree has is named."""
    (install_repo / "scripts" / "git-hooks" / "pre-commit").unlink()
    _git(install_repo, "add", "scripts")
    _git(install_repo, "commit", "-q", "-m", "hooks", "--no-verify")
    worktree = tmp_path / "wt"
    _git(install_repo, "worktree", "add", "-q", str(worktree), "-b", "wave/x")
    _executable(
        worktree / "scripts" / "git-hooks" / "pre-commit", (HOOKS / "pre-commit").read_text()
    )

    result = _install(worktree)

    assert result.returncode != 0
    assert "pre-commit" in result.stderr
    assert "main checkout" in result.stderr
    assert not (install_repo / ".git" / "hooks" / "pre-commit").exists()
