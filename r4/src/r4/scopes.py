"""Work-level scope tokens on the client (ADR-108 D-3, W2b).

UMRS issues a token for a work level (a loop, a teamup run) on the caller's
verified identity (``POST /gw/scopes``). The client keeps it opaque: inside
:func:`acting_in_scope` every platform request carries it as ``X-R4-Scope``,
so UMRS checks the level's grants and stamps its path on what it writes.
Stopping the level revokes it and everything beneath it (``revoke``).

Loops and teamups start using this in W4; a request outside any scope is
unchanged.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Dict, Iterator, List, Optional

HEADER = "X-R4-Scope"

_CURRENT: contextvars.ContextVar[str] = contextvars.ContextVar("r4_scope_token", default="")


def current_scope_token() -> str:
    """The scope token the requests in this context carry ("" = none)."""
    return _CURRENT.get()


@contextlib.contextmanager
def acting_in_scope(token: str) -> Iterator[None]:
    """Make every platform request inside carry ``token``."""
    reset = _CURRENT.set(str(token or "").strip())
    try:
        yield
    finally:
        _CURRENT.reset(reset)


def scope_headers() -> Dict[str, str]:
    """``{X-R4-Scope: token}`` inside a scope, else ``{}``."""
    token = _CURRENT.get()
    return {HEADER: token} if token else {}


class ScopeClient:
    """``/gw/scopes``: issue and revoke work-level tokens."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def issue(
        self,
        path: str,
        grants: List[str],
        *,
        parent_token: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """``{"token", "scope"}`` for a level at ``path`` (a child when a
        ``parent_token`` is given: beneath it, never wider)."""
        body: Dict[str, Any] = {"path": path, "grants": list(grants)}
        if parent_token:
            body["parent_token"] = parent_token
        if ttl_seconds:
            body["ttl_seconds"] = int(ttl_seconds)
        for key in ("repo", "branch", "commit"):
            if provenance and provenance.get(key):
                body[key] = provenance[key]
        reply: Dict[str, Any] = self._client.post(
            f"{self._client._api_prefix}/scopes", json=body, timeout=15
        )
        return reply

    def revoke(self, path: str) -> Dict[str, Any]:
        """Revoke ``path`` and every level beneath it."""
        reply: Dict[str, Any] = self._client.post(
            f"{self._client._api_prefix}/scopes/revoke", json={"path": path}, timeout=15
        )
        return reply
