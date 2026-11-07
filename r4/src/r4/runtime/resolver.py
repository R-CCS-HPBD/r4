"""Runtime resolution: pick the ResearchRuntime for a project.

Selection precedence (highest first):

1. Explicit ``mode`` argument (e.g. CLI ``--runtime``).
2. ``R4_RUNTIME_MODE`` environment variable.
3. Project metadata ``runtime.mode`` (per-project durable choice).
4. Default ``umrs`` — preserves every existing behavior.

Modes:

- ``umrs``: authoritative UMRS substrate (requires a configured Client).
- ``local``: SQLite local runtime (works fully offline; no Client needed).
- ``hybrid``: UMRS when a client is available, local otherwise. The mode is
  a client-side execution choice about *which runtime is authoritative for
  new loops*; it never dual-writes one loop to both stores.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from .api import ResearchRuntime
from .local import LocalRuntime, _default_db_path

VALID_MODES = ("umrs", "local", "hybrid")
ENV_MODE = "R4_RUNTIME_MODE"


class RuntimeResolutionError(ValueError):
    """Raised for unknown runtime modes or unavailable runtime requirements."""


def _normalize_mode(raw: str) -> str:
    """Validate a mode token; empty string means 'not specified'."""
    mode = raw.strip().lower()
    if not mode:
        return ""
    if mode not in VALID_MODES:
        raise RuntimeResolutionError(
            f"unknown runtime mode {mode!r}; expected one of {', '.join(VALID_MODES)}"
        )
    return mode


def resolve_mode(explicit: Optional[str] = None, project_metadata: Optional[dict] = None) -> str:
    """Resolve the effective runtime mode; returns one of VALID_MODES."""
    for candidate in (
        explicit or "",
        os.environ.get(ENV_MODE, ""),
        _metadata_mode(project_metadata),
    ):
        mode = _normalize_mode(candidate)
        if mode:
            return mode
    return "umrs"


def _metadata_mode(project_metadata: Optional[dict]) -> str:
    """Extract ``runtime.mode`` from project metadata; '' when absent."""
    if not isinstance(project_metadata, dict):
        return ""
    runtime_meta = project_metadata.get("runtime") or {}
    if not isinstance(runtime_meta, dict):
        return ""
    return str(runtime_meta.get("mode") or "")


def _project_metadata(project_id: Optional[str]) -> Optional[dict]:
    if not project_id:
        return None
    try:
        from ..project import get_project_manager

        config = get_project_manager().get(project_id)
    except Exception:  # noqa: BLE001 - metadata lookup must never break resolution
        return None
    return getattr(config, "metadata", None)


def get_runtime(
    mode: Optional[str] = None,
    project_id: Optional[str] = None,
    client=None,
    client_factory: Optional[Callable[[], object]] = None,
    local_db_path: Optional[Path] = None,
) -> ResearchRuntime:
    """Build the ResearchRuntime for the resolved mode.

    ``local`` never constructs a Client (offline-safe). ``umrs`` requires a
    client or client_factory. ``hybrid`` always builds the LocalRuntime and
    wraps an UMRS substrate when a client is available; venue is then
    decided per-loop by ``spec.requires``.
    """
    resolved = resolve_mode(mode, _project_metadata(project_id))
    if resolved == "local":
        return LocalRuntime(db_path=local_db_path)
    hybrid = resolved == "hybrid"
    if client is None and client_factory is not None:
        try:
            client = client_factory()
        except Exception:  # noqa: BLE001
            if hybrid:
                return _hybrid_runtime(local_db_path, None)
            raise
    if client is None:
        if hybrid:
            return _hybrid_runtime(local_db_path, None)
        raise RuntimeResolutionError(f"runtime mode {resolved!r} requires a configured R4 client")
    from .umrs import UmrsRuntime

    umrs = UmrsRuntime(client)
    return _hybrid_runtime(local_db_path, umrs) if hybrid else umrs


def _hybrid_runtime(
    local_db_path: Optional[Path], umrs: Optional[ResearchRuntime]
) -> ResearchRuntime:
    """Local runtime always exists; remote is best-effort."""
    from .hybrid import HybridRuntime

    return HybridRuntime(LocalRuntime(db_path=local_db_path), umrs)


__all__ = [
    "VALID_MODES",
    "RuntimeResolutionError",
    "get_runtime",
    "resolve_mode",
    "_default_db_path",
]
