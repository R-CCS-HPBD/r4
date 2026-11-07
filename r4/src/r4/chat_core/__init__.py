"""Chat core — shared primitives for all R4 chat surfaces.

This package contains the building blocks used by:
- ``r4.tui`` (the unified TUI)
- ``r4._chat`` (legacy REPL, now a thin adapter)
- ``r4.companion`` (research companion session)

Importing from here is the canonical way to access session and tool
infrastructure. (Tool approval lives in ``r4.companion.permissions``,
ADR-104; the ``ToolApprover`` that was here had no caller.)
"""

from __future__ import annotations

from .fs_tools import build_local_fs_tools
from .scan import scan_content
from .session import Attachment, ChatSession, Turn


def __getattr__(name: str):
    if name == "SessionStore":
        from .session_store import SessionStore

        return SessionStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Attachment",
    "ChatSession",
    "SessionStore",
    "Turn",
    "build_local_fs_tools",
    "scan_content",
]
