"""Host-side git calls that run nothing of the repository's own (ADR-101, review ESC-1).

r4 runs git on the host, unconfined, in directories a sandboxed agent can
write: a nested repository the agent made (``git init`` in the workspace) or a
``.git`` the sandbox does not protect. Plain ``git check-ignore`` there runs
the repository's ``core.fsmonitor`` command with the user's full
permissions, a complete sandbox escape. Every such call goes through
:func:`git_argv` and :func:`git_env`: hooks, the fsmonitor, the untracked
cache and signature verification (``gpg.program``) are switched off on the
command line, which wins over any repository config, and a ``diff`` adds
:func:`diff_flags` (no external diff driver, no textconv); the system config
and ``GIT_*`` overrides are dropped.

Not covered: clean/smudge filter drivers a repository config names for paths
in its ``.gitattributes`` (``git diff`` of the work tree may run them). Only
the read-only commands below are used here, none of which runs filters
except a work-tree ``diff``; :func:`diff_flags` adds what git offers
(``--no-ext-diff --no-textconv``).

Stdlib only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: ``-c`` overrides: command-line config beats every config file.
GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "log.showSignature=false",
)


def git_argv(*args: str) -> list[str]:
    """``["git", <safe config>, *args]``."""
    return ["git", *GIT_SAFE_CONFIG, *args]


def diff_flags() -> list[str]:
    """Flags a ``git diff`` / ``git log -p`` adds: no external diff, no textconv."""
    return ["--no-ext-diff", "--no-textconv"]


def git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment without ``GIT_*`` overrides, no system config, no prompt."""
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env
