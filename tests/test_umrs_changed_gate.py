"""Hermetic pointer-selection tests for ``ci-lint-local.sh --umrs-changed``.

A parent merge can have a resolved, staged umrs gitlink newer than ``HEAD``.
The gate must inspect that final index pointer, not approve only the older
committed delta. Scratch repositories, a scratch HOME, and stripped ``GIT_*``
variables keep these tests away from the caller's repository and hooks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Scratch:
    parent: Path
    source: Path
    env: dict[str, str]
    old: str
    new: str


def _git(
    repo: Path, env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo: Path, env: dict[str, str], message: str, *paths: str) -> str:
    _git(repo, env, "add", "--", *paths)
    _git(repo, env, "commit", "-q", "--no-verify", "-m", message)
    return _git(repo, env, "rev-parse", "HEAD").stdout.strip()


def _install_gate(parent: Path) -> None:
    gate = parent / "scripts" / "ci-lint-local.sh"
    gate.parent.mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "ci-lint-local.sh", gate)
    shutil.copy2(REPO / "pyproject.toml", parent / "pyproject.toml")
    (parent / "tools").mkdir()
    for name in ("lint_baseline.py", "complexity_baseline.py", "complexity_audit.py"):
        shutil.copy2(REPO / "tools" / name, parent / "tools" / name)
    _write(parent, "tools/lint_baseline_umrs.json", '{"entries": []}\n')
    _write(
        parent,
        "tools/complexity_baseline_umrs.json",
        '{"threshold": 9, "entries": []}\n',
    )


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        PYTHON=sys.executable,
    )
    return env


@pytest.fixture
def scratch(tmp_path: Path) -> Scratch:
    env = _hermetic_env(tmp_path)
    source = tmp_path / "source"
    parent = tmp_path / "parent"
    source.mkdir()
    parent.mkdir()
    for repo in (source, parent):
        _git(repo, env, "init", "-q", "-b", "main")
    _write(source, "src/checks.py", "def clean():\n    return 1\n")
    old = _commit(source, env, "old umrs", "src/checks.py")
    _git(
        parent,
        env,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(source),
        "umrs",
    )
    _commit(parent, env, "old parent", ".gitmodules", "umrs")
    _git(parent, env, "tag", "base")
    _install_gate(parent)
    _write(source, "src/final_pointer.py", "def broken():\n    return missing_final_name\n")
    new = _commit(source, env, "new umrs", "src/final_pointer.py")
    return Scratch(parent, source, env, old, new)


def _merge_new_pointer(scratch: Scratch, main_pointer: str | None = None) -> None:
    parent, source, env = scratch.parent, scratch.source, scratch.env
    umrs = parent / "umrs"
    _git(parent, env, "checkout", "-q", "-b", "incoming")
    _git(umrs, env, "fetch", "-q", str(source), "main")
    _git(umrs, env, "checkout", "-q", scratch.new)
    _commit(parent, env, "incoming pointer", "umrs")
    _git(parent, env, "checkout", "-q", "main")
    if main_pointer is None:
        _write(parent, "main-note", "diverge\n")
        _commit(parent, env, "main divergence", "main-note")
    else:
        _git(umrs, env, "fetch", "-q", str(source), "HEAD")
        _git(umrs, env, "checkout", "-q", main_pointer)
        _commit(parent, env, "main pointer", "umrs")
    result = _git(parent, env, "merge", "--no-commit", "--no-ff", "incoming", check=False)
    assert result.returncode == (1 if main_pointer else 0), result.stderr


def _run_gate(scratch: Scratch) -> subprocess.CompletedProcess[str]:
    script = scratch.parent / "scripts" / "ci-lint-local.sh"
    return subprocess.run(
        ["bash", str(script), "--umrs-changed", "--base", "base"],
        cwd=scratch.parent,
        env=scratch.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize("commit_merge", [False, True], ids=["staged-merge", "committed-ci"])
def test_gate_checks_the_final_umrs_pointer(scratch: Scratch, commit_merge: bool) -> None:
    _merge_new_pointer(scratch)
    parent, env = scratch.parent, scratch.env
    assert _git(parent, env, "rev-parse", "HEAD:umrs").stdout.strip() == scratch.old
    assert _git(parent, env, "rev-parse", ":umrs").stdout.strip() == scratch.new
    if commit_merge:
        _commit(parent, env, "merge incoming")

    result = _run_gate(scratch)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "umrs/src/final_pointer.py" in result.stdout
    assert "F821" in result.stdout


def test_unstaged_umrs_python_is_still_checked(scratch: Scratch) -> None:
    with (scratch.parent / "umrs" / "src" / "checks.py").open("a") as source:
        source.write("\ndef broken_worktree():\n    return missing_worktree_name\n")

    result = _run_gate(scratch)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "umrs/src/checks.py" in result.stdout
    assert "F821" in result.stdout


def test_a_staged_pointer_deletion_keeps_the_missing_pointer_fallback(
    scratch: Scratch,
) -> None:
    _git(scratch.parent, scratch.env, "rm", "-q", "-f", "umrs")

    result = _run_gate(scratch)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no changed .py under umrs/" in result.stdout


def test_an_unmerged_umrs_gitlink_fails_closed(scratch: Scratch) -> None:
    source, env = scratch.source, scratch.env
    _git(source, env, "checkout", "-q", "--detach", scratch.old)
    _write(source, "src/main_only.py", "MAIN = 1\n")
    main_pointer = _commit(source, env, "main-only umrs", "src/main_only.py")
    _merge_new_pointer(scratch, main_pointer=main_pointer)
    assert _git(scratch.parent, env, "ls-files", "-u", "--", "umrs").stdout

    result = _run_gate(scratch)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "unmerged umrs gitlink" in result.stderr
