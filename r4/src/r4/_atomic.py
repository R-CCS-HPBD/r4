"""Atomic file writes (write-to-temp + rename) for JSON state files.

Every writer of a shared state file goes through this so a concurrent reader
never sees a torn/truncated JSON document: the temp file is written and
closed fully, then `os.rename` swaps it in atomically (POSIX). Pattern
matches `auth.TokenStore.save` and `curve_session.CurveSessionManager.
_save_session` — those two keep their inline secret-class 0o600 paths.

Chat tabs (2026-09-30): companion bridge parallelism made torn-write
silent-loss real (memory.json/lessons.json loaders tolerate decode errors
and "recover" to empty state).
"""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

__all__ = ["atomic_write_text"]


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    """Write `text` to `path` atomically (temp + rename), with `mode` perms.

    Creates the parent directory. On failure the temp file is removed and
    the exception propagates — the destination is never left half-written.

    A symlinked destination (a dotfiles-managed config.json, token or s3cfg)
    is resolved first, so the rename replaces the link's target and the link
    survives; renaming over the link itself would turn it into a plain local
    file and leave the target stale (stab cycle 3, data-integrity#2).
    """
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
    try:
        os.fchmod(fd, stat.S_IMODE(mode))
        content = text.encode("utf-8")
        # Large payloads may not fit in one os.write — loop until done.
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.close(fd)
        fd = -1
        os.rename(tmp, str(path))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise
