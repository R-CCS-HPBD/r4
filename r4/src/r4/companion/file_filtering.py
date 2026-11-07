"""File filtering for companion filesystem tools (gitignore-aware listing).

Aligned with common agent products (Claude Code LS, Cursor glob/grep):
- respect .gitignore via ``git check-ignore`` when available
- optional per-call ignore globs
- skip hidden dotfiles by default
- non-recursive directory listing only
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from ..safe_git import git_argv, git_env

# Bulky or internal dirs — always skipped at list time (even outside git).
_BUILTIN_IGNORE_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        "vendor",
    }
)

MAX_LIST_DIR_ENTRIES = 200


def respect_gitignore() -> bool:
    raw = os.environ.get("R4_COMPANION_RESPECT_GITIGNORE", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def respect_hidden_files() -> bool:
    raw = os.environ.get("R4_COMPANION_RESPECT_HIDDEN_FILES", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _git_root(directory: Path) -> Path | None:
    for parent in [directory, *directory.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git_ignored_paths(paths: list[Path], *, cwd: Path) -> set[Path]:
    if not paths:
        return set()
    relative_paths: list[str] = []
    path_by_relative: dict[str, Path] = {}
    for path in paths:
        try:
            rel = str(path.relative_to(cwd))
        except ValueError:
            rel = str(path)
        relative_paths.append(rel)
        path_by_relative[rel] = path
        path_by_relative[rel.replace("\\", "/")] = path

    payload = "\0".join(relative_paths) + "\0"
    try:
        # Hardened (ADR-101, review ESC-1): ``cwd`` may be a repository a
        # sandboxed agent made; its core.fsmonitor must not run on the host.
        result = subprocess.run(
            git_argv("check-ignore", "-z", "--stdin"),
            input=payload.encode(),
            capture_output=True,
            cwd=cwd,
            timeout=10,
            env=git_env(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()

    ignored: set[Path] = set()
    for chunk in result.stdout.decode(errors="replace").split("\0"):
        rel = chunk.strip().strip('"')
        if not rel:
            continue
        resolved = path_by_relative.get(rel) or path_by_relative.get(rel.replace("\\", "/"))
        if resolved is not None:
            ignored.add(resolved)
    return ignored


def _matches_ignore_globs(name: str, *, is_dir: bool, patterns: list[str]) -> bool:
    label = f"{name}/" if is_dir else name
    for pattern in patterns:
        pat = pattern.strip()
        if not pat:
            continue
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(label, pat):
            return True
        if fnmatch.fnmatch(label, pat.rstrip("/")):
            return True
    return False


def _is_visible_entry(entry: os.DirEntry, *, show_hidden: bool, extra_ignore: list[str]) -> bool:
    """Return True if ``entry`` should appear in a directory listing."""
    name = entry.name
    if name in _BUILTIN_IGNORE_NAMES:
        return False
    if not show_hidden and name.startswith("."):
        return False
    if extra_ignore and _matches_ignore_globs(
        name, is_dir=entry.is_dir(follow_symlinks=False), patterns=extra_ignore
    ):
        return False
    return True


def _listing_notes(
    scanned: list[os.DirEntry], *, show_hidden: bool, use_gitignore: bool, git_root: Path | None
) -> list[str]:
    """Build explanatory notes about what was filtered from a listing."""
    notes: list[str] = []
    hidden_count = sum(
        1
        for entry in scanned
        if entry.name.startswith(".") and entry.name not in _BUILTIN_IGNORE_NAMES
    )
    if hidden_count and not show_hidden:
        notes.append(
            f"{hidden_count} hidden dotfile(s) omitted "
            "(set R4_COMPANION_RESPECT_HIDDEN_FILES=true to show)"
        )
    if use_gitignore and git_root is None:
        notes.append("not a git repository — .gitignore not applied")
    return notes


def list_directory_entries(
    directory: Path,
    *,
    ignore: list[str] | None = None,
) -> tuple[list[os.DirEntry], list[str]]:
    """Return visible immediate children of ``directory`` and any filter notes."""
    extra_ignore = list(ignore or [])
    show_hidden = respect_hidden_files()
    use_gitignore = respect_gitignore()

    try:
        with os.scandir(directory) as it:
            scanned = list(it)
    except OSError as exc:
        raise OSError(f"could not list directory {directory}: {exc}") from exc

    git_root = _git_root(directory) if use_gitignore else None
    visible = [
        entry
        for entry in scanned
        if _is_visible_entry(entry, show_hidden=show_hidden, extra_ignore=extra_ignore)
    ]

    if use_gitignore and visible and git_root is not None:
        ignored = _git_ignored_paths([Path(entry.path) for entry in visible], cwd=git_root)
        if ignored:
            visible = [entry for entry in visible if Path(entry.path) not in ignored]

    visible.sort(key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name.lower()))
    notes = _listing_notes(
        scanned, show_hidden=show_hidden, use_gitignore=use_gitignore, git_root=git_root
    )
    return visible, notes


def format_directory_listing(
    directory: Path,
    entries: list[os.DirEntry],
    *,
    notes: list[str] | None = None,
    max_entries: int = MAX_LIST_DIR_ENTRIES,
) -> str:
    listing_notes = list(notes or [])
    shown = entries
    if max_entries > 0 and len(entries) > max_entries:
        overflow = len(entries) - max_entries
        listing_notes.append(f"…and {overflow} more")
        shown = entries[:max_entries]

    if not shown:
        lines = ["(empty)"]
    else:
        lines = [
            f"{entry.name}/" if entry.is_dir(follow_symlinks=False) else entry.name
            for entry in shown
        ]
    if listing_notes:
        lines.extend(f"({note})" for note in listing_notes)
    return "\n".join(lines)
