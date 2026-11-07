"""Run git in a test's scratch repository, never in the caller's repository.

A git hook exports ``GIT_DIR``, ``GIT_INDEX_FILE`` and (from a linked
worktree) ``GIT_COMMON_DIR``/``GIT_WORK_TREE``. A test that runs plain
``git init`` / ``git config`` / ``git commit`` under those variables writes
the *real* repository instead of its temp dir: on 2026-10-15 a pre-commit run
from a worktree left ``core.bare = true`` and ``user.name = test`` in the
shared ``.git/config`` (plan H-3.3). Every helper here strips every ``GIT_*``
variable first, the same rule ``r4.teams.plan.workspace`` applies to product
git calls.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


def hermetic_git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``base`` (default: ``os.environ``) without any ``GIT_*`` variable."""
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if not key.startswith("GIT_")}


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """``git -C <cwd> <args>`` with the hermetic env; output captured as text."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=hermetic_git_env(),
        capture_output=True,
        text=True,
        check=check,
    )


def git_init(path: Path) -> None:
    """A fresh repository at ``path`` with a local test identity."""
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "test")
    run_git(path, "config", "commit.gpgsign", "false")
