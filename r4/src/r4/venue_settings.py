"""Send the user's agent settings to a remote session (ADR-099 D-8).

The owner's client calls this before every remote start and whenever a
setting changes; the worker reads the document through its rail before the
next queued turn (``r4.venue_worker``). The document comes from
``r4.companion.agent_settings.effective_agent_settings``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def venue_settings_url(client: Any, session_id: str) -> str:
    from .companion.session_store import journal_prefix

    return f"{journal_prefix(client)}/companion/sessions/{session_id}/venue/settings"


_PUSH_TIMEOUT_SEC = 15.0


def push_venue_agent_settings(
    client: Any, session_id: str, settings: Mapping[str, Any], *, timeout: float | None = None
) -> int:
    """``PUT …/venue/settings``; the server's ``rev``. Raises on refusal."""
    payload = client.put(
        venue_settings_url(client, session_id),
        json=dict(settings),
        timeout=timeout or _PUSH_TIMEOUT_SEC,
    )
    rev = payload.get("rev") if isinstance(payload, dict) else None
    return int(rev) if isinstance(rev, int) else 0


def try_push_venue_agent_settings(
    client: Any, session_id: str, settings: Mapping[str, Any], *, timeout: float | None = None
) -> str:
    """Best-effort push: "" on success, else the reason (a server without the
    route, a network failure). The caller decides whether to say so."""
    try:
        push_venue_agent_settings(client, session_id, settings, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — named to the caller, never raised
        logger.debug("venue settings push failed for %s", session_id, exc_info=True)
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return ""


__all__ = ["push_venue_agent_settings", "try_push_venue_agent_settings", "venue_settings_url"]
