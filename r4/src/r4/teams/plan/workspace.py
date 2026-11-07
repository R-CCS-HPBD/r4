"""Per-agent workspaces of a team plan run (ADR-092 slice 5).

An ``agent`` or ``map`` step with ``isolation: worktree`` or ``copy`` runs
each of its agents in a workspace of its own, under the run directory
(``<run_dir>/workspaces/<id>``):

- ``worktree``: ``git worktree add`` of the project's repository at its
  ``HEAD``, on a throwaway branch ``r4-plan/<run>/<id>``. The project root
  must be inside a git repository; otherwise the run is refused by name
  before any agent starts. The agent works in the worktree's copy of the
  project root (the same sub-directory when the root is not the top level).
- ``copy``: a copy of the project tree, bounded by a byte and a file cap
  (``R4_PLAN_COPY_MAX_BYTES``, ``R4_PLAN_COPY_MAX_FILES``); a tree beyond
  either is refused by name. ``.git`` and caches (``SKIP_NAMES``) are not
  copied, and symlinks are copied as links.

Each workspace's file state is recorded when it is made (a manifest of
content digests). What its agents changed is the difference to that
manifest. A ``merge_back`` step turns those changes into text diffs, and
applies them to the project root only after a human approved them and only
where the root still holds exactly what the workspace started from: any
other state is a conflict, refused by name, never resolved. Symlinks,
binary files, files over ``MAX_MERGE_FILE_BYTES``, paths into ``.git`` and
paths whose parent leaves the project root through a symlink are refused by
name too. Applying is file writes only: no git command, hook or
agent-authored code runs (D-11).

Every git call disables hooks and the fsmonitor (``_GIT_SAFE``), so making
or removing a worktree runs no script from the repository.

Stdlib only: the plan package's import fence (ADR-092 D-2).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT_KEY = "project_root"  # in spec.context and resolution.json
WORKSPACES_DIR = "workspaces"
MERGE_DIR = "merge"
BRANCH_PREFIX = "r4-plan"

COPY_MAX_BYTES_ENV = "R4_PLAN_COPY_MAX_BYTES"
COPY_MAX_FILES_ENV = "R4_PLAN_COPY_MAX_FILES"
DEFAULT_COPY_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_COPY_MAX_FILES = 20_000
MAX_MERGE_FILE_BYTES = 2 * 1024 * 1024
MAX_MERGE_FILES = 500
GIT_TIMEOUT_SEC = 120

# Never copied, never diffed: version control and caches a tool run makes.
SKIP_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "node_modules",
        ".DS_Store",
    }
)
# No hook, no fsmonitor command, no untracked cache: git runs nothing of the
# repository's own when the engine calls it.
_GIT_SAFE = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
)
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_REF_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


class WorkspaceError(Exception):
    """A workspace that cannot be made, read or merged; ``code`` names why."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def reason(self) -> str:
        return f"workspace_refused:{self.code}:{self.detail}"


@dataclass(frozen=True)
class Workspace:
    """One agent workspace. ``path`` is its directory under the run
    directory; ``root`` is where the agent works (``path``, or the same
    sub-directory of a worktree the project root is of its repository)."""

    id: str
    mode: str
    path: str
    root: str
    project_root: str
    step: str
    node: str
    branch: str | None = None
    repo: str | None = None
    base: str | None = None

    def record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Workspace | None:
        try:
            fields = {name: record.get(name) for name in cls.__dataclass_fields__}
            if not all(isinstance(fields[k], str) for k in ("id", "mode", "path", "root")):
                return None
            return cls(**fields)
        except TypeError:
            return None


@dataclass(frozen=True)
class Change:
    """One file a workspace changed: ``base`` and ``new`` are digests
    (None: absent), ``kind`` add, modify or delete."""

    rel: str
    kind: str
    base: str | None
    new: str | None


# ── ids, caps ───────────────────────────────────────────────────────────────


def workspace_id(key: str) -> str:
    """A path- and ref-safe id for a workspace key (``<node>[<item>]#<n>``)."""
    slug = _SLUG_RE.sub("-", key.lower()).strip("-")[:40] or "ws"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def copy_caps() -> tuple[int, int]:
    """The copy mode's byte and file caps, from the environment (§3.4)."""
    return (
        _env_int(COPY_MAX_BYTES_ENV, DEFAULT_COPY_MAX_BYTES),
        _env_int(COPY_MAX_FILES_ENV, DEFAULT_COPY_MAX_FILES),
    )


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


# ── preflight and creation ──────────────────────────────────────────────────


def preflight(mode: str, project_root: str | None, run_dir: str | Path) -> None:
    """Refuse, by name, a mode this project cannot have, before any spend."""
    root = _project_dir(project_root)
    if mode == "worktree":
        _git_toplevel(root)
    elif mode == "copy":
        _copy_plan(root, Path(run_dir))
    else:
        raise WorkspaceError("bad_mode", mode)


def create(
    ws_id: str,
    mode: str,
    project_root: str | None,
    run_dir: str | Path,
    *,
    run_id: str,
    step: str,
    node: str,
) -> Workspace:
    """Make the workspace and record its manifest; WorkspaceError if not."""
    root = _project_dir(project_root)
    home = Path(run_dir) / WORKSPACES_DIR
    home.mkdir(parents=True, exist_ok=True)
    path = home / ws_id
    if mode == "worktree":
        ws = _create_worktree(ws_id, root, path, run_id=run_id, step=step, node=node)
    elif mode == "copy":
        ws = _create_copy(ws_id, root, path, Path(run_dir), step=step, node=node)
    else:
        raise WorkspaceError("bad_mode", mode)
    _write_json(_manifest_path(run_dir, ws_id), tree_digests(ws))
    return ws


def _project_dir(project_root: str | None) -> Path:
    if not project_root:
        raise WorkspaceError("no_project_root", "the run names no project root")
    root = Path(os.path.realpath(project_root))
    if not root.is_dir():
        raise WorkspaceError("no_project_root", f"{root} is not a directory")
    return root


def _create_worktree(
    ws_id: str, root: Path, path: Path, *, run_id: str, step: str, node: str
) -> Workspace:
    top = _git_toplevel(root)
    base = _git(top, "rev-parse", "--verify", "HEAD^{commit}", code="no_commit").strip()
    branch = f"{branch_prefix(run_id)}/{ws_id}"
    _discard_leftover(top, path)
    _git(top, "worktree", "add", "-B", branch, str(path), base, code="worktree_failed")
    prefix = os.path.relpath(root, top)
    ws_root = path if prefix == "." else path / prefix
    return Workspace(
        id=ws_id,
        mode="worktree",
        path=str(path),
        root=str(ws_root),
        project_root=str(root),
        step=step,
        node=node,
        branch=branch,
        repo=str(top),
        base=base,
    )


def _discard_leftover(top: Path, path: Path) -> None:
    """A directory left by a crashed attempt that never recorded it."""
    if not path.exists():
        return
    _git_quiet(top, "worktree", "remove", "--force", str(path))
    shutil.rmtree(path, ignore_errors=True)
    _git_quiet(top, "worktree", "prune")


def _create_copy(
    ws_id: str, root: Path, path: Path, run_dir: Path, *, step: str, node: str
) -> Workspace:
    entries = _copy_plan(root, run_dir)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    try:
        for rel, kind in entries:
            _copy_entry(root / rel, path / rel, kind)
    except OSError as exc:
        shutil.rmtree(path, ignore_errors=True)
        raise WorkspaceError("copy_failed", f"{type(exc).__name__}: {exc}") from exc
    return Workspace(
        id=ws_id,
        mode="copy",
        path=str(path),
        root=str(path),
        project_root=str(root),
        step=step,
        node=node,
    )


def _copy_entry(src: Path, dst: Path, kind: str) -> None:
    if kind == "dir":
        dst.mkdir(parents=True, exist_ok=True)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if kind == "link":
        os.symlink(os.readlink(src), dst)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def _copy_plan(root: Path, run_dir: Path) -> list[tuple[str, str]]:
    """``(rel, dir|file|link)`` of what a copy holds, refused past the caps.

    The run directory is never copied into itself (a run root inside the
    project would otherwise recurse).
    """
    budget = _CopyBudget(root, *copy_caps())
    skip = Path(os.path.realpath(run_dir))
    entries: list[tuple[str, str]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(current)
        rel_dir = here.relative_to(root)
        dirnames[:], links = _copy_dirs(here, dirnames, skip)
        entries += [(str(rel_dir / name), "dir") for name in dirnames]
        for name in sorted([*filenames, *links]):
            kind = _copy_kind(here / name, name)
            if kind is not None:
                budget.take(here / name, kind)
                entries.append((str(rel_dir / name), kind))
    return entries


class _CopyBudget:
    """The copy caps, counted as the walk goes; past either is refused."""

    def __init__(self, root: Path, max_bytes: int, max_files: int) -> None:
        self.root, self.max_bytes, self.max_files = root, max_bytes, max_files
        self.size = self.files = 0

    def take(self, path: Path, kind: str) -> None:
        self.files += 1
        self.size += path.lstat().st_size if kind == "file" else 0
        if self.files > self.max_files or self.size > self.max_bytes:
            raise WorkspaceError(
                "copy_too_large",
                f"{self.root} is over the copy cap ({self.max_bytes} bytes, "
                f"{self.max_files} files; {COPY_MAX_BYTES_ENV}, {COPY_MAX_FILES_ENV}); "
                "use isolation: worktree",
            )


def _copy_dirs(here: Path, dirnames: list[str], skip: Path) -> tuple[list[str], list[str]]:
    """The sub-directories to walk into, and the links to directories (copied
    as links). Skipped names and the run directory itself are left out."""
    kept: list[str] = []
    links: list[str] = []
    for name in sorted(dirnames):
        full = here / name
        if name in SKIP_NAMES or Path(os.path.realpath(full)) == skip:
            continue
        (links if full.is_symlink() else kept).append(name)
    return kept, links


def _copy_kind(path: Path, name: str) -> str | None:
    """``link``, ``file``, or None for a skipped name, socket or fifo."""
    if name in SKIP_NAMES:
        return None
    if path.is_symlink():
        return "link"
    return "file" if path.is_file() else None


# ── the workspace's files ───────────────────────────────────────────────────


def tree_digests(ws: Workspace) -> dict[str, str]:
    """``{relative path: digest}`` of every file the workspace holds now."""
    root = Path(ws.root)
    names = _git_files(root) if ws.mode == "worktree" else _walk_files(root)
    out: dict[str, str] = {}
    for rel in names:
        if set(PurePosixPath(rel).parts) & SKIP_NAMES:
            continue
        digest = file_digest(root / rel)
        if digest is not None and digest != "dir":
            out[rel] = digest
    return out


def _git_files(root: Path) -> list[str]:
    """Tracked and untracked-but-not-ignored files under ``root``."""
    text = _git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        code="git_failed",
    )
    return sorted({name for name in text.split("\0") if name})


def _walk_files(root: Path) -> list[str]:
    names: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(current)
        links = [d for d in dirnames if (here / d).is_symlink()]
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES and d not in links]
        for name in [*filenames, *links]:
            names.append(str((here / name).relative_to(root)).replace(os.sep, "/"))
    return sorted(names)


def file_digest(path: Path) -> str | None:
    """``sha256:…`` of a regular file, ``link:<target>`` of a symlink,
    ``big:<size>:<mtime>`` past ``MAX_MERGE_FILE_BYTES``, ``dir`` for a
    directory, None when absent."""
    try:
        st = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    if path.is_symlink():
        return f"link:{os.readlink(path)}"
    if path.is_dir():
        return "dir"
    if st.st_size > MAX_MERGE_FILE_BYTES:
        return f"big:{st.st_size}:{st.st_mtime_ns}"
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(run_dir: str | Path, ws_id: str) -> dict[str, str] | None:
    try:
        doc = json.loads(_manifest_path(run_dir, ws_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def changes(ws: Workspace, run_dir: str | Path) -> list[Change]:
    """What the workspace's agents changed since it was made."""
    base = manifest(run_dir, ws.id)
    if base is None or not Path(ws.root).is_dir():
        raise WorkspaceError("workspace_missing", f"{ws.id} ({ws.path})")
    now = tree_digests(ws)
    out = []
    for rel in sorted(set(base) | set(now)):
        before, after = base.get(rel), now.get(rel)
        if before == after:
            continue
        kind = "add" if before is None else "delete" if after is None else "modify"
        out.append(Change(rel=rel, kind=kind, base=before, new=after))
    return out


# ── merge-back ──────────────────────────────────────────────────────────────


def merge_problems(ws: Workspace, found: list[Change]) -> list[str]:
    """Why these changes may not be applied to the project root, each
    ``<code>:<path>``; empty when every one may be. Never resolves anything."""
    if len(found) > MAX_MERGE_FILES:
        return [f"too_many_files:{len(found)}>{MAX_MERGE_FILES}"]
    project = Path(ws.project_root)
    problems: list[str] = []
    for change in found:
        why = _change_problem(ws, project, change)
        if why:
            problems.append(f"{why}:{change.rel}")
    return problems


def _change_problem(ws: Workspace, project: Path, change: Change) -> str | None:
    unsupported = _unsupported(project, change)
    if unsupported:
        return unsupported
    current = file_digest(project / change.rel)
    if current == change.new:
        return None  # already in place (a resumed apply)
    if current != change.base:
        return "conflict"  # the project root moved on since the workspace was made
    if change.new is not None and _text_of(Path(ws.root) / change.rel) is None:
        return "binary"
    if change.kind == "modify" and _text_of(project / change.rel) is None:
        return "binary"
    return None


def _unsupported(project: Path, change: Change) -> str | None:
    """A path that may not be written, a symlink, or a file too large to diff."""
    unsafe = _unsafe_path(project, change.rel)
    if unsafe:
        return unsafe
    kinds = {digest.split(":", 1)[0] for digest in (change.base, change.new) if digest}
    if "link" in kinds:
        return "symlink"
    return "too_large" if "big" in kinds else None


def _unsafe_path(project: Path, rel: str) -> str | None:
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or ".." in parts or ".git" in parts:
        return "unsafe_path"
    target = project.joinpath(*parts)
    parent = Path(os.path.realpath(target.parent))
    if parent != project and project not in parent.parents:
        return "unsafe_path"  # a symlinked directory leads out of the project
    return None


def _text_of(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def render_diff(ws: Workspace, found: list[Change]) -> str:
    """A unified diff of the changes against the project root, per file."""
    chunks = [_file_diff(Path(ws.project_root), Path(ws.root), change) for change in found]
    return "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks)


def _file_diff(project: Path, root: Path, change: Change) -> str:
    before = _text_of(project / change.rel) if change.kind != "add" else ""
    after = _text_of(root / change.rel) if change.kind != "delete" else ""
    if before is None or after is None:
        return f"# {change.rel}: not a text change ({change.kind})\n"
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="/dev/null" if change.kind == "add" else f"a/{change.rel}",
            tofile="/dev/null" if change.kind == "delete" else f"b/{change.rel}",
        )
    )


def apply(ws: Workspace, found: list[Change]) -> list[str]:
    """Write the changes into the project root; the paths written.

    Call only after :func:`merge_problems` returned nothing. A file already
    holding its new content is skipped, so a resumed apply finishes the rest.
    """
    project = Path(ws.project_root)
    written: list[str] = []
    for change in found:
        target = project.joinpath(*PurePosixPath(change.rel).parts)
        if file_digest(target) == change.new:
            continue
        if change.new is None:
            target.unlink()
        else:
            _replace_file(Path(ws.root) / change.rel, target)
        written.append(change.rel)
    return written


def _replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = (target if target.is_file() else source).stat().st_mode & 0o7777
    tmp = target.with_name(f".{target.name}.r4-merge.tmp")
    tmp.write_bytes(source.read_bytes())
    os.chmod(tmp, mode)
    os.replace(tmp, target)


def diff_path(run_dir: str | Path, step: str, ws_id: str) -> Path:
    return Path(run_dir) / MERGE_DIR / step / f"{ws_id}.diff"


# ── removal ─────────────────────────────────────────────────────────────────


def remove(ws: Workspace, run_dir: str | Path) -> str | None:
    """Remove the workspace (worktree and branch, or the copy); an error
    text when something stayed behind, else None."""
    errors: list[str] = []
    path = Path(ws.path)
    if ws.mode == "worktree" and ws.repo:
        repo = Path(ws.repo)
        if not _git_quiet(repo, "worktree", "remove", "--force", str(path)):
            shutil.rmtree(path, ignore_errors=True)
            _git_quiet(repo, "worktree", "prune")
        if ws.branch and not _git_quiet(repo, "branch", "-D", ws.branch):
            errors.append(f"branch {ws.branch} not deleted")
    else:
        shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        errors.append(f"{path} not removed")
    _manifest_path(run_dir, ws.id).unlink(missing_ok=True)
    return "; ".join(errors) or None


# ── git ─────────────────────────────────────────────────────────────────────


def branch_prefix(run_id: str) -> str:
    """``r4-plan/<run>``: every throwaway branch of one run lives under it."""
    return f"{BRANCH_PREFIX}/{_REF_UNSAFE_RE.sub('-', run_id).strip('-') or 'run'}"


def repo_of(path: str | Path | None) -> Path | None:
    """The git top level ``path`` lies in, or None (not a repository, gone)."""
    if not path or not Path(path).is_dir():
        return None
    try:
        return _git_toplevel(Path(path))
    except WorkspaceError:
        return None


def run_branches(repo: Path, run_id: str) -> list[str]:
    """The throwaway branches one run left in ``repo`` (never another run's)."""
    prefix = branch_prefix(run_id)
    try:
        text = _git(
            repo, "branch", "--list", "--format=%(refname:short)", f"{prefix}/*", code="git_failed"
        )
    except WorkspaceError:
        return []
    return sorted(b.strip() for b in text.splitlines() if b.strip().startswith(prefix + "/"))


def delete_branch(repo: Path, branch: str) -> bool:
    return _git_quiet(repo, "branch", "-D", branch)


def prune_worktrees(repo: Path) -> bool:
    """Drop the records of worktrees whose directory is gone."""
    return _git_quiet(repo, "worktree", "prune")


def _git_toplevel(root: Path) -> Path:
    text = _git(root, "rev-parse", "--show-toplevel", code="not_a_git_repo")
    return Path(os.path.realpath(text.strip()))


def _git(cwd: Path, *args: str, code: str) -> str:
    try:
        done = subprocess.run(
            ["git", *_GIT_SAFE, "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            stdin=subprocess.DEVNULL,
            env=_git_env(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("git_unavailable", "git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(code, f"git {args[0]} timed out") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise WorkspaceError(code, (detail[-1] if detail else f"git {args[0]} failed")[:200])
    return done.stdout


def _git_quiet(cwd: Path, *args: str) -> bool:
    try:
        _git(cwd, *args, code="git_failed")
    except WorkspaceError:
        return False
    return True


def _git_env() -> dict[str, str]:
    """The process's env without GIT_* overrides (a GIT_DIR would point every
    call at another repository), and no prompt."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


# ── files ───────────────────────────────────────────────────────────────────


def _manifest_path(run_dir: str | Path, ws_id: str) -> Path:
    return Path(run_dir) / WORKSPACES_DIR / f"{ws_id}.manifest.json"


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
