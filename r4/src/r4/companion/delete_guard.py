"""A delete nobody approved must be recoverable (ADR-104 amendment 2).

Autopilot runs tool calls without asking. The one exception the maintainer
kept (2026-11-05: "true autopilot mode (except file delete if no full backup
exists)") is a delete R4 cannot undo. Before a call that no person approved
deletes files, :func:`protect` makes sure each target has a full backup:

- the file is tracked by git and committed unchanged (git can restore it), or
- R4 copies it (file or directory tree) into a per-call folder under
  ``~/.local/share/r4/trash/`` first.

When R4 cannot know or cannot copy the targets — a glob or a variable in the
command, ``git clean``, ``find -delete``, a computed path in Python, a target
outside the workspace, more than :data:`MAX_BACKUP_BYTES` — the call asks.

Detection reads the call's own text: ``apply_patch`` ``Delete File`` hunks,
``rm`` / ``rmdir`` / ``unlink`` / ``shred`` / ``git rm`` (also inside
``bash -c``), ``os.remove`` / ``os.unlink`` / ``os.rmdir`` /
``shutil.rmtree`` / ``Path(...).unlink()``. It is a guard against an agent's
mistakes, not a sandbox: a program that deletes files some other way is not
seen (the local tool sandbox, ADR-101, confines what a process can reach).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import agent_workspace

logger = logging.getLogger(__name__)

TRASH_ROOT = "~/.local/share/r4/trash"
MAX_BACKUP_BYTES = 512 * 1024 * 1024

_SHELL_DELETERS = frozenset({"rm", "rmdir", "unlink", "shred"})
_SHELL_PREFIXES = frozenset({"sudo", "command", "nohup", "time", "env", "exec"})
_SHELL_WRAPPERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_UNRESOLVED_CHARS = re.compile(r"[*?\[\]$`{}]")

_PY_LITERAL_DELETE = re.compile(
    r"\b(?:os\.(?:remove|unlink|rmdir|removedirs)|shutil\.rmtree)\(\s*(['\"])([^'\"]+)\1"
)
_PY_PATH_DELETE = re.compile(r"\bPath\(\s*(['\"])([^'\"]+)\1\s*\)\.(?:unlink|rmdir)\(")
_PY_ANY_DELETE = re.compile(
    r"\b(?:os\.(?:remove|unlink|rmdir|removedirs)|shutil\.rmtree)\(|\.(?:unlink|rmdir)\("
)


@dataclass(frozen=True)
class DeleteScan:
    """What a call deletes: known paths, or why the targets are unknown."""

    paths: tuple[str, ...] = ()
    unresolved: str = ""
    cwd: str = ""

    @property
    def deletes(self) -> bool:
        return bool(self.paths or self.unresolved)


@dataclass(frozen=True)
class Protection:
    """``ok``: every target is backed up (``note`` says where); else
    ``reason`` says why the call must ask."""

    ok: bool
    note: str = ""
    reason: str = ""


# ── Detection ───────────────────────────────────────────────────────────────


def scan(tool_name: str, arguments: dict[str, Any]) -> DeleteScan:
    """The files this call would delete (best effort, from its text)."""
    cwd = str(arguments.get("cwd") or arguments.get("workdir") or "")
    if tool_name == "apply_patch":
        return _scan_patch(arguments, cwd)
    if tool_name == "shell_exec":
        return _with_cwd(_scan_shell(str(arguments.get("command") or "")), cwd)
    if tool_name == "python_exec":
        return _with_cwd(_scan_python(str(arguments.get("code") or "")), cwd)
    return DeleteScan()


def _with_cwd(found: DeleteScan, cwd: str) -> DeleteScan:
    return DeleteScan(found.paths, found.unresolved, cwd)


def _scan_patch(arguments: dict[str, Any], cwd: str) -> DeleteScan:
    from .apply_patch import parse_patch

    text = arguments.get("patch_text")
    if not isinstance(text, str) or not text:
        return DeleteScan()
    try:
        patches = parse_patch(text)
    except Exception:  # noqa: BLE001 - a malformed patch fails in the tool
        return DeleteScan()
    deleted = tuple(fp.old_path for fp in patches if fp.action == "delete" and fp.old_path)
    return DeleteScan(deleted, "", cwd)


def _scan_shell(command: str) -> DeleteScan:
    paths: list[str] = []
    for segment in _SEGMENT_SPLIT.split(command):
        found = _scan_segment(segment)
        if found.unresolved:
            return found
        paths.extend(found.paths)
    return DeleteScan(tuple(paths))


def _scan_segment(segment: str) -> DeleteScan:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return DeleteScan((), "the command cannot be parsed")
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return DeleteScan()
    return _scan_tokens(tokens)


def _strip_prefixes(tokens: list[str]) -> list[str]:
    """Drop ``sudo``-style prefixes and ``VAR=value`` assignments."""
    while tokens and (tokens[0] in _SHELL_PREFIXES or re.match(r"^\w+=", tokens[0])):
        tokens = tokens[1:]
    return tokens


def _scan_tokens(tokens: list[str]) -> DeleteScan:
    verb = os.path.basename(tokens[0])
    if verb in _SHELL_DELETERS:
        return _operands(tokens[1:])
    if verb in _SHELL_WRAPPERS:
        return _scan_wrapper(tokens[1:])
    scanner = _VERB_SCANNERS.get(verb)
    return scanner(tokens[1:]) if scanner else DeleteScan()


def _scan_wrapper(args: list[str]) -> DeleteScan:
    """``bash -c '<command>'``: scan the inner command."""
    if len(args) > 1 and args[0] == "-c":
        return _scan_shell(args[1])
    return DeleteScan()


def _scan_find(args: list[str]) -> DeleteScan:
    if any(a in ("-delete", "-exec", "-execdir") for a in args):
        return DeleteScan((), "find may delete files it matches")
    return DeleteScan()


def _scan_xargs(args: list[str]) -> DeleteScan:
    if args and os.path.basename(args[0]) in _SHELL_DELETERS:
        return DeleteScan((), "xargs deletes paths it reads at run time")
    return DeleteScan()


def _scan_git(args: list[str]) -> DeleteScan:
    if not args:
        return DeleteScan()
    if args[0] == "clean" and not any(a in ("-n", "--dry-run") for a in args):
        return DeleteScan((), "git clean deletes untracked files")
    if args[0] == "rm":
        return _operands(args[1:])
    return DeleteScan()


_VERB_SCANNERS: dict[str, Callable[[list[str]], DeleteScan]] = {
    "git": _scan_git,
    "find": _scan_find,
    "xargs": _scan_xargs,
}


def _operands(args: list[str]) -> DeleteScan:
    """The non-option operands of a delete command; unknown when any is a
    glob, a variable or a substitution."""
    operands = [a for a in args if a != "--" and not a.startswith("-")]
    if any(_UNRESOLVED_CHARS.search(a) for a in operands):
        return DeleteScan((), "the targets use a glob or a variable")
    return DeleteScan(tuple(operands))


def _scan_python(code: str) -> DeleteScan:
    if not _PY_ANY_DELETE.search(code):
        return DeleteScan()
    literal = [m.group(2) for m in _PY_LITERAL_DELETE.finditer(code)]
    literal += [m.group(2) for m in _PY_PATH_DELETE.finditer(code)]
    calls = len(_PY_ANY_DELETE.findall(code))
    if len(literal) < calls:
        return DeleteScan((), "the code deletes a path computed at run time")
    return DeleteScan(tuple(literal))


# ── Backup ──────────────────────────────────────────────────────────────────


def protect(found: DeleteScan) -> Protection:
    """Back up every target, or say why the call must ask."""
    if found.unresolved:
        return Protection(
            False, reason=f"deletes files R4 cannot back up first ({found.unresolved})"
        )
    targets: list[Path] = []
    for raw in found.paths:
        target, why = _target(raw, found.cwd)
        if why:
            return Protection(False, reason=why)
        if target is not None:
            targets.append(target)
    return _backup(targets)


def _target(raw: str, cwd: str) -> tuple[Path | None, str]:
    """The real path of one target (None when it does not exist), or why
    it cannot be backed up."""
    joined = os.path.join(cwd, os.path.expanduser(raw)) if cwd else os.path.expanduser(raw)
    try:
        named = Path(agent_workspace.resolve(joined, write=False)).absolute()
    except (OSError, ValueError, RuntimeError):
        return None, f"deletes {raw}, outside the workspace"
    # ``rm link`` deletes the link, not what it points at.
    real = named if named.is_symlink() else Path(os.path.realpath(named))
    if not real.exists() and not real.is_symlink():
        return None, ""
    root = Path(os.path.realpath(os.getcwd()))
    if real != root and root not in real.parents:
        return None, f"deletes {raw}, outside the workspace"
    if real == root:
        return None, "deletes the whole workspace"
    return real, ""


def _backup(targets: list[Path]) -> Protection:
    to_copy = [t for t in targets if not _committed_clean(t)]
    if not to_copy:
        return Protection(True, note=_note(len(targets), "") if targets else "")
    size = sum(_size(t) for t in to_copy)
    if size > MAX_BACKUP_BYTES:
        return Protection(
            False, reason=f"deletes {size // (1024 * 1024)} MiB, too much to back up first"
        )
    folder = _trash_folder()
    try:
        for target in to_copy:
            _copy(target, folder)
    except OSError as exc:
        logger.warning("delete backup into %s failed: %s", folder, exc)
        return Protection(False, reason=f"the backup before the delete failed ({exc})")
    return Protection(True, note=_note(len(to_copy), str(folder)))


def _note(count: int, folder: str) -> str:
    if not folder:
        return f"delete of {count} git-committed path(s): git can restore them"
    return f"backed up {count} path(s) to {folder} before the delete"


def _committed_clean(target: Path) -> bool:
    """True when git tracks ``target`` and it has no change since the last
    commit (for a directory: no untracked or changed file inside)."""
    from r4.safe_git import git_argv, git_env

    folder = target if target.is_dir() else target.parent
    status = _git(
        git_argv("-C", str(folder), "status", "--porcelain", "--ignored", "--", str(target)),
        git_env(),
    )
    tracked = _git(git_argv("-C", str(folder), "ls-files", "--", str(target)), git_env())
    return status == "" and bool(tracked)


def _git(argv: list[str], env: dict[str, str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _size(target: Path) -> int:
    if target.is_file() or target.is_symlink():
        return target.lstat().st_size
    return sum(p.lstat().st_size for p in target.rglob("*") if p.is_file() or p.is_symlink())


def _trash_folder() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(os.path.expanduser(TRASH_ROOT)) / f"{stamp}-{uuid.uuid4().hex[:8]}"


def _copy(target: Path, folder: Path) -> None:
    root = Path(os.path.realpath(os.getcwd()))
    dest = folder / root.name / target.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir() and not target.is_symlink():
        shutil.copytree(target, dest, symlinks=True)
    else:
        shutil.copy2(target, dest, follow_symlinks=False)


__all__ = ["MAX_BACKUP_BYTES", "TRASH_ROOT", "DeleteScan", "Protection", "protect", "scan"]
