"""Where a workstation file comes from in git (ADR-108 D-6, W2a).

Ingest used to send only a file's basename, so nothing tied an artifact to
the commit a result came from. :class:`ProvenanceReader` reports, per file,
its path relative to the repository root, the repository's identity
(``origin``'s host and path, credentials stripped), the branch, the commit
and whether the file has uncommitted changes. UMRS keeps it as
``metadata.provenance`` (client-reported).

A file outside a git repository has none (``None``); git missing or failing
is the same, never a failed ingest. Repository facts are read once per
repository per reader (one reader per ingest call), so a directory of many
files costs one ``rev-parse`` set plus one ``status`` per file.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

_SCP_RE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


def scrub_remote(url: str) -> str:
    """``origin``'s URL as ``host/path``: scheme, user and token dropped,
    trailing ``.git`` removed ("" when there is none)."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" in text:
        rest = text.split("://", 1)[1]
        host_path = rest.split("@", 1)[1] if "@" in rest.split("/", 1)[0] else rest
    else:
        match = _SCP_RE.match(text)
        host_path = f"{match.group(1)}/{match.group(2)}" if match else text
    return host_path.rstrip("/").removesuffix(".git")


def _git(cwd: str, *args: str) -> Optional[str]:
    """git's stdout, or None when it failed (hooks off, no GIT_* leaking)."""
    from .teams.plan.workspace import WorkspaceError
    from .teams.plan.workspace import _git as run

    try:
        return run(_as_path(cwd), *args, code="provenance").strip()
    except WorkspaceError:
        return None


def _as_path(cwd: str) -> Any:
    from pathlib import Path

    return Path(cwd)


class ProvenanceReader:
    """Per-file git provenance, with repository facts cached per reader."""

    def __init__(self) -> None:
        self._repos: Dict[str, Dict[str, Any]] = {}

    def _repo(self, top: str) -> Dict[str, Any]:
        facts = self._repos.get(top)
        if facts is None:
            commit = _git(top, "rev-parse", "--verify", "HEAD^{commit}") or ""
            branch = _git(top, "rev-parse", "--abbrev-ref", "HEAD") or ""
            remote = scrub_remote(_git(top, "remote", "get-url", "origin") or "")
            facts = {"commit": commit, "branch": "" if branch == "HEAD" else branch, "repo": remote}
            self._repos[top] = facts
        return facts

    def of(self, path: str) -> Optional[Dict[str, Any]]:
        """The provenance block for ``path``, or None outside a repository."""
        real = os.path.realpath(path)
        top = _git(os.path.dirname(real) or ".", "rev-parse", "--show-toplevel")
        if not top:
            return None
        rel = os.path.relpath(real, top)
        status = _git(top, "status", "--porcelain", "--", rel)
        facts = self._repo(top)
        block = {"path": rel, **{k: v for k, v in facts.items() if v}}
        if status is not None:
            block["dirty"] = bool(status)
        return block
