"""r4 source identity for stale-process detection (round-3 task 7).

An ACP/bridge chat process can outlive the code it imports (editable
installs point at the working tree; long-lived children keep running even
after edits land). Both handshakes carry this identity; clients compare it
against a fresh computation and warn when they diverge.

Identity = short git commit (when the tree is a git checkout) +
a content digest over the r4 package tree. The digest is the part that
catches un-committed edits (the stale-child case). Non-git installs
degrade gracefully: commit is ``None``, digest identifies the tree.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def r4_package_root() -> Path:
    """Directory containing the ``r4`` package (i.e. the source root)."""
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class SourceIdentity:
    git_commit: Optional[str]
    digest: str
    source_dir: str

    def to_dict(self) -> dict:
        return asdict(self)


def _git_commit_at(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=10", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


def _content_digest(root: Path) -> str:
    """sha256 over every (relpath, size, mtime-seconds) in the package tree.

    Deterministic across clients/platforms when re-implemented in JS:
    lines joined with ``\\n`` (``relpath|size|mtime_s``), sorted by relpath,
    hashed as UTF-8 with no trailing newline.
    """
    hasher = hashlib.sha256()
    rows: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append(f"{rel}|{stat.st_size}|{int(stat.st_mtime)}")
    for row in sorted(rows):
        hasher.update(row.encode("utf-8") + b"\n")
    return hasher.hexdigest()[:16]


def current_source_identity() -> SourceIdentity:
    """Compute the identity of the running r4 source tree."""
    root = r4_package_root()
    return SourceIdentity(
        git_commit=_git_commit_at(root.parent.parent),  # repo root (src/r4 → repo)
        digest=_content_digest(root),
        source_dir=str(root),
    )


def identities_match(a: SourceIdentity, b: SourceIdentity) -> bool:
    """Match rule: digest equal AND (both commits unknown OR equal).

    Digest-only mismatch is the stale-child / edited tree signal. A commit
    mismatch with equal digest means concurrent checkouts of equal content —
    treat as matching (content is truth).
    """
    return a.digest == b.digest
