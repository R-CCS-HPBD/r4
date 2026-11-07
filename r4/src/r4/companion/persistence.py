"""Persistence protocol for companion sessions (Plan 5).

Plan 5 decouples ``CompanionSession`` from its storage backend. The existing
:class:`CompanionSessionStore` (SQLite) remains the default; this module
introduces a minimal :class:`Persistence` protocol plus three concrete
implementations so tests, ephemeral runs, and UMRS-synced deployments can
each pick the appropriate backend without touching session.py internals.

Backends:

- :class:`NullPersistence` — no-op (in-memory sessions only).
- :class:`SQLitePersistence` — thin adapter over
  :class:`CompanionSessionStore`.
- :class:`UMRSSyncPersistence` — SQLite + best-effort UMRS mirror, gated by
  ``R4_COMPANION_UMRS_SYNC=1``. Falls back silently if UMRS is unavailable
  (honors the UMRS-first invariant from integration policy).

Select a backend via :func:`get_default_persistence`, which honors the
``R4_COMPANION_UMRS_SYNC`` environment variable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from .session_store import CompanionSessionStore

if TYPE_CHECKING:
    from r4.client import Client

    from .session import CompanionSession


__all__ = [
    "NullPersistence",
    "Persistence",
    "SQLitePersistence",
    "UMRSSyncPersistence",
    "get_default_persistence",
]


logger = logging.getLogger(__name__)


@runtime_checkable
class Persistence(Protocol):
    """Protocol for companion session persistence backends."""

    def save(self, session: "CompanionSession") -> None: ...

    def load(self, session_id: str, client: "Client") -> Optional["CompanionSession"]: ...


class NullPersistence:
    """No-op persistence — for tests or ephemeral sessions."""

    def save(self, session: "CompanionSession") -> None:  # noqa: D401
        """Drop the save on the floor."""
        return None

    def load(self, session_id: str, client: "Client") -> Optional["CompanionSession"]:
        return None


class SQLitePersistence:
    """Adapter over :class:`CompanionSessionStore`.

    This is the default persistence backend and is a pure wrapper — no
    behavior change from calling the store directly.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._store = CompanionSessionStore(db_path=db_path)

    @property
    def store(self) -> CompanionSessionStore:
        """Return the underlying SQLite store (for advanced ops like list/delete)."""
        return self._store

    def save(self, session: "CompanionSession") -> None:
        self._store.save(session)

    def load(self, session_id: str, client: "Client") -> Optional["CompanionSession"]:
        return self._store.load(session_id, client)


class UMRSSyncPersistence:
    """SQLite persistence with best-effort UMRS mirror.

    Enabled by setting ``R4_COMPANION_UMRS_SYNC=1``. The UMRS mirror is
    best-effort: if the UMRS sync endpoint is unreachable or returns an
    error, the local SQLite write still succeeds and a warning is logged.

    Honors the UMRS-first invariant: UMRS remains the source of truth for
    durable context (conversations, memory, summaries) in production
    deployments; this wrapper just keeps the local store in sync.
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        sync_callable=None,
    ) -> None:
        self._local = SQLitePersistence(db_path=db_path)
        self._sync_callable = sync_callable  # injected for tests

    @property
    def store(self) -> CompanionSessionStore:
        return self._local.store

    def save(self, session: "CompanionSession") -> None:
        self._local.save(session)
        self._try_mirror(session)

    def load(self, session_id: str, client: "Client") -> Optional["CompanionSession"]:
        return self._local.load(session_id, client)

    def _try_mirror(self, session: "CompanionSession") -> None:
        if self._sync_callable is None:
            return
        try:
            self._sync_callable(session)
        except Exception as exc:  # pragma: no cover - defensive best-effort
            logger.warning(
                "UMRS companion sync failed for session %s: %s",
                getattr(session, "session_id", "?"),
                exc,
            )


def get_default_persistence(db_path: Optional[Path] = None) -> Persistence:
    """Return the persistence backend selected via environment.

    - ``R4_COMPANION_UMRS_SYNC=1`` → :class:`UMRSSyncPersistence`
    - otherwise → :class:`SQLitePersistence`

    Callers that want an in-memory mode should instantiate
    :class:`NullPersistence` directly.
    """
    if os.environ.get("R4_COMPANION_UMRS_SYNC") == "1":
        return UMRSSyncPersistence(db_path=db_path)
    return SQLitePersistence(db_path=db_path)
