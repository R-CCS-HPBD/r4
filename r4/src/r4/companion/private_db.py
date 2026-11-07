"""Owner-only files for the companion's local journal (security sweep 2026-10-21).

``~/.config/r4/companion/sessions.db`` (and its ``-wal`` / ``-shm``) holds
raw user messages and tool results: a pasted token, a file the agent read.
sqlite created it 0644 inside a 0755 directory, readable by every account on
a shared login node. SQLite gives ``-wal`` / ``-shm`` the mode of the
database file, so an owner-only database keeps its journals owner-only too.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_SIDE_FILES = ("", "-wal", "-shm", "-journal")


def ensure_private_db(db_path: str | os.PathLike[str]) -> None:
    """Create ``db_path``'s directory 0700 and the file 0600 before sqlite
    opens it; narrow an existing wider directory / file / journal."""
    path = Path(db_path)
    _private_dir(path.parent)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, _FILE_MODE))
    for suffix in _SIDE_FILES:
        _narrow(Path(f"{path}{suffix}"), _FILE_MODE)


def _private_dir(directory: Path) -> None:
    if not directory.exists():
        directory.mkdir(parents=True, mode=_DIR_MODE, exist_ok=True)
    _narrow(directory, _DIR_MODE)


def _narrow(path: Path, mode: int) -> None:
    """Drop group/other bits on a path this user owns (no-op otherwise)."""
    try:
        info = path.stat()
    except OSError:
        return
    if info.st_uid != os.getuid() or not stat.S_IMODE(info.st_mode) & 0o077:
        return
    try:
        path.chmod(mode)
    except OSError:
        return
