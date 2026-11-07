"""Work-level git worktrees, modeled on Claude Code's (ADR-108 D-2, W1b).

One mechanism for every level. W1b serves the session; teamup and loops come
in W4, and the ADR-092 plan workspaces fold in after that.

- **Where.** A worktree lives at ``<main repo>/.r4/worktrees/<id>`` on
  branch ``r4/<id>``. The directories are flat: no worktree sits inside
  another's. The hierarchy is in the registry (``parent``) and the branch path.
- **Base.**
  - A worktree made from inside another one is based on that one's HEAD.
    Its uncommitted work (tracked and untracked, ``.gitignore`` honoured) is
    first saved as a snapshot commit on ``refs/r4/snap/<id>``, built with a
    temporary index, so the user's index and branch never move. A child sees
    what its parent sees.
  - At the top level the base is HEAD, or the default branch with
    ``base="default"``.
- **Safety.**
  - The user's checkout is never stashed, reset or written.
  - git runs with hooks off and without inherited ``GIT_*`` variables (the
    plan workspace runner).
  - Submodules are initialised in the new worktree, best effort: a failure is
    reported, not fatal.
- **Lifecycle.**
  - :func:`remove` refuses a worktree that has children, and one with changes
    unless ``discard=True``.
  - :func:`clean` removes unchanged, childless ones.
  - "Changed" means uncommitted edits, or commits beyond the base.

The registry is ``.r4/worktrees/registry.json`` in the main repository. That
directory is git-ignored through ``.git/info/exclude``, so nothing is added
to the user's tracked files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .teams.plan.workspace import (
    _GIT_SAFE,
    GIT_TIMEOUT_SEC,
    WorkspaceError,
    _git,
    _git_env,
    _git_quiet,
    _git_toplevel,
)

WORKTREES_DIR = Path(".r4") / "worktrees"
REGISTRY_NAME = "registry.json"
BRANCH_PREFIX = "r4"
SNAPSHOT_REF = "refs/r4/snap"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

__all__ = [
    "Worktree",
    "WorkspaceError",
    "clean",
    "create",
    "current",
    "find",
    "is_changed",
    "list_worktrees",
    "main_repo",
    "remove",
]


@dataclass
class Worktree:
    """One registered worktree."""

    id: str
    path: str
    branch: str
    base: str
    parent: str = ""
    owner: str = ""
    level: str = "session"
    snapshot_ref: str = ""
    created_at: float = field(default_factory=time.time)

    def record(self) -> dict[str, Any]:
        return asdict(self)


# ── the repository ─────────────────────────────────────────────────────────


def main_repo(start: str | os.PathLike[str]) -> Path:
    """The main working tree of the repository ``start`` is in (also when
    ``start`` is inside one of its linked worktrees)."""
    top = _git_toplevel(Path(start))
    common = _git(
        top, "rev-parse", "--path-format=absolute", "--git-common-dir", code="not_a_git_repo"
    )
    common_dir = Path(common.strip())
    return common_dir.parent if common_dir.name == ".git" else top


def _home(repo: Path) -> Path:
    return repo / WORKTREES_DIR


def _registry_path(repo: Path) -> Path:
    return _home(repo) / REGISTRY_NAME


def _load(repo: Path) -> list[Worktree]:
    try:
        rows = json.loads(_registry_path(repo).read_text())
    except (OSError, ValueError):
        return []
    fields = Worktree.__dataclass_fields__
    return [
        Worktree(**{k: v for k, v in r.items() if k in fields}) for r in rows if isinstance(r, dict)
    ]


def _save(repo: Path, rows: list[Worktree]) -> None:
    home = _home(repo)
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f".{REGISTRY_NAME}.tmp"
    tmp.write_text(json.dumps([r.record() for r in rows], indent=2))
    tmp.replace(_registry_path(repo))


def _ignore_home(repo: Path) -> None:
    """Keep ``.r4/`` out of ``git status`` via ``info/exclude`` (untracked)."""
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude", code="git_failed").strip())
    exclude = exclude if exclude.is_absolute() else repo / exclude
    line = "/.r4/"
    try:
        text = exclude.read_text() if exclude.exists() else ""
    except OSError:
        return
    if line not in text.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n")


# ── queries ────────────────────────────────────────────────────────────────


def list_worktrees(start: str | os.PathLike[str]) -> list[Worktree]:
    """Every registered worktree of ``start``'s repository."""
    return _load(main_repo(start))


def find(start: str | os.PathLike[str], wt_id: str) -> Optional[Worktree]:
    return next((w for w in list_worktrees(start) if w.id == wt_id), None)


def current(start: str | os.PathLike[str]) -> Optional[Worktree]:
    """The registered worktree ``start`` is inside, if any."""
    here = Path(os.path.realpath(start))
    for wt in list_worktrees(start):
        root = Path(os.path.realpath(wt.path))
        if here == root or root in here.parents:
            return wt
    return None


def is_changed(wt: Worktree) -> bool:
    """Uncommitted edits, or commits beyond its base."""
    path = Path(wt.path)
    if not path.is_dir():
        return False
    if _git(path, "status", "--porcelain", code="git_failed").strip():
        return True
    ahead = _git(path, "rev-list", "--count", f"{wt.base}..HEAD", code="git_failed").strip()
    return ahead not in ("", "0")


def _children(rows: list[Worktree], wt_id: str) -> list[Worktree]:
    return [r for r in rows if r.parent == wt_id]


# ── create ─────────────────────────────────────────────────────────────────


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise WorkspaceError("bad_name", "use 1-64 letters, digits, '.', '_' or '-'")
    return name


def _default_branch(repo: Path) -> str:
    """``origin/HEAD``'s branch when known, else HEAD."""
    try:
        ref = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", code="no_default")
    except WorkspaceError:
        return "HEAD"
    return ref.strip() or "HEAD"


def _snapshot(top: Path, wt_id: str) -> tuple[str, str]:
    """``(commit, ref)`` of ``top``'s working state on top of HEAD, made
    with a temporary index; ``(HEAD, "")`` when nothing is uncommitted."""
    head = _git(top, "rev-parse", "--verify", "HEAD^{commit}", code="no_commit").strip()
    if not _git(top, "status", "--porcelain", code="git_failed").strip():
        return head, ""
    with tempfile.TemporaryDirectory(prefix="r4-snap-") as tmp:
        env = {**_git_env(), "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _run(top, env, "read-tree", "HEAD")
        _run(top, env, "add", "-A")
        tree = _run(top, env, "write-tree").strip()
    message = f"r4 snapshot for worktree {wt_id}"
    commit = _git(
        top, "commit-tree", tree, "-p", head, "-m", message, code="snapshot_failed"
    ).strip()
    ref = f"{SNAPSHOT_REF}/{wt_id}"
    _git(top, "update-ref", ref, commit, code="snapshot_failed")
    return commit, ref


def _run(cwd: Path, env: dict[str, str], *args: str) -> str:
    """git with a custom environment (the temporary index)."""
    done = subprocess.run(
        ["git", *_GIT_SAFE, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SEC,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise WorkspaceError("snapshot_failed", (detail[-1] if detail else "git failed")[:200])
    return done.stdout


def _base_of(start: Path, repo: Path, wt_id: str, base: str) -> tuple[str, str, str]:
    """``(commit, snapshot ref, parent id)`` for a new worktree."""
    parent = current(start)
    if parent is not None:
        commit, ref = _snapshot(Path(parent.path), wt_id)
        return commit, ref, parent.id
    if base == "default":
        ref_name = _default_branch(repo)
        commit = _git(repo, "rev-parse", "--verify", f"{ref_name}^{{commit}}", code="no_commit")
        return commit.strip(), "", ""
    commit, ref = _snapshot(_git_toplevel(start), wt_id)
    return commit, ref, ""


def _init_submodules(path: Path) -> str:
    """Best effort; the warning text when it failed, else ""."""
    if not (path / ".gitmodules").exists():
        return ""
    if _git_quiet(path, "submodule", "update", "--init", "--recursive"):
        return ""
    return "submodules were not initialised (run `git submodule update --init` in the worktree)"


def create(
    start: str | os.PathLike[str],
    name: str,
    *,
    owner: str = "",
    level: str = "session",
    base: str = "head",
) -> tuple[Worktree, str]:
    """Make and register worktree ``name``; ``(worktree, warning)``."""
    wt_id = _check_name(name)
    start_path = Path(os.path.realpath(start))
    repo = main_repo(start_path)
    rows = _load(repo)
    if any(r.id == wt_id for r in rows):
        raise WorkspaceError("exists", f"worktree {wt_id} exists (/worktree {wt_id} enters it)")
    commit, snap_ref, parent_id = _base_of(start_path, repo, wt_id, base)
    path = _home(repo) / wt_id
    branch = f"{BRANCH_PREFIX}/{wt_id}"
    _ignore_home(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), commit, code="worktree_failed")
    wt = Worktree(wt_id, str(path), branch, commit, parent_id, owner, level, snap_ref)
    _save(repo, [*rows, wt])
    return wt, _init_submodules(path)


# ── remove / clean ─────────────────────────────────────────────────────────


def _removable(rows: list[Worktree], wt_id: str, *, discard: bool) -> Worktree:
    """The worktree ``wt_id`` when it may go; WorkspaceError naming why not."""
    wt = next((r for r in rows if r.id == wt_id), None)
    if wt is None:
        raise WorkspaceError("unknown", f"no worktree {wt_id}")
    if _children(rows, wt_id):
        raise WorkspaceError("has_children", f"worktree {wt_id} has worktrees made from it")
    if not discard and is_changed(wt):
        raise WorkspaceError("changed", f"worktree {wt_id} has changes (keep it, or discard)")
    return wt


def remove(start: str | os.PathLike[str], wt_id: str, *, discard: bool = False) -> Worktree:
    """Remove worktree ``wt_id``, its branch and its snapshot ref.

    Refused while it has children, or when it has changes and not
    ``discard`` (keep it, merge it, or discard it explicitly)."""
    repo = main_repo(start)
    rows = _load(repo)
    wt = _removable(rows, wt_id, discard=discard)
    _git_quiet(repo, "worktree", "remove", "--force", wt.path)
    _git_quiet(repo, "worktree", "prune")
    _git_quiet(repo, "branch", "-D", wt.branch)
    if wt.snapshot_ref:
        _git_quiet(repo, "update-ref", "-d", wt.snapshot_ref)
    _save(repo, [r for r in rows if r.id != wt_id])
    return wt


def clean(start: str | os.PathLike[str], *, keep: frozenset[str] = frozenset()) -> list[str]:
    """Remove every unchanged, childless worktree not in ``keep``; their ids.

    Leaves come first, so a parent whose only children were unchanged goes
    in the same call."""
    removed: list[str] = []
    while True:
        rows = list_worktrees(start)
        leaf = next(
            (
                r
                for r in rows
                if r.id not in keep and not _children(rows, r.id) and not is_changed(r)
            ),
            None,
        )
        if leaf is None:
            return removed
        remove(start, leaf.id)
        removed.append(leaf.id)
