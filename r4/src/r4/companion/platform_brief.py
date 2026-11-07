"""The UMRS-rendered situational brief (ADR-105 D-8, slice S4).

The per-turn ``<project_state>`` block (D-28) used to be built here, in the
client, from about twelve HTTP reads on every send — sub-agents and team
roles included, nothing cached. UMRS now renders the same facts (plus what
the client could not see: the site's partitions, the service tier, quota
left, this session's own running jobs) at
``GET /umrs/projects/{id}/agent-brief`` in one read, cached on both sides.

The brief is DATA, never instructions: UMRS prefixes it so, and it rides
the volatile ``<turn_context>`` (never the stable prefix, wave 80). An empty
brief (UMRS could build no section) means no block, as D-28 requires.

Fallbacks, so a staged rollout never regresses:
- a 404/405 without a typed code is a UMRS without the route: the client
  falls back to its own builder for a while;
- ``projects.not_found`` is this project only (no registry row, e.g. a
  pre-registry id): that project falls back, others still use the brief;
- any other failure falls back and is retried soon.

The cache follows UMRS's ``ttl_s`` and is dropped when the agent submits or
cancels work, so the next send sees the change UMRS already knows about.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 15.0
_MAX_TTL_SEC = 60.0
_UNSUPPORTED_RETRY_SEC = 600.0
_PROJECT_RETRY_SEC = 60.0
_FAILURE_RETRY_SEC = 30.0
_TIMEOUT_SEC = 5.0
_ATTR = "_r4_platform_brief"
# Tools after which the brief is stale at once (ADR-105 S4 review).
INVALIDATING_TOOLS = frozenset(
    {
        "workflow_run",
        "workflow_trigger",
        "workflow_replay",
        "workflow_cancel",
        "job_commit",
        "job_cancel",
        "job_retry",
        "hpc_submit",
        "endpoint_provision",
        "endpoint_stop",
    }
)


class _BriefCache:
    """Per-client cache: one brief for every companion on that client."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key: tuple[str, str] | None = None
        self.text: str | None = None
        self.expires_at = float("-inf")
        self.unsupported_until = float("-inf")
        self.project_skip: dict[str, float] = {}


def _cache_for(client: Any) -> _BriefCache | None:
    cache = getattr(client, _ATTR, None)
    if isinstance(cache, _BriefCache):  # a mock client's auto-attribute is not one
        return cache
    cache = _BriefCache()
    try:
        setattr(client, _ATTR, cache)
    except AttributeError:
        return None
    return cache


def _status_code(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = (getattr(exc, "details", None) or {}).get("status_code")
    return code if isinstance(code, int) else None


def _project_id(client: Any) -> str:
    return str(getattr(getattr(client, "config", None), "project_id", "") or "")


def _fetch(client: Any, project_id: str, session_id: str) -> tuple[str, float]:
    params = {"session_id": session_id} if session_id else None
    prefix = getattr(client, "_api_prefix", "/gw")
    data = client.get(
        f"{prefix}/projects/{project_id}/agent-brief", params=params, timeout=_TIMEOUT_SEC
    )
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError("agent brief: unexpected answer")  # e.g. a proxy page
    ttl = data.get("ttl_s")
    ttl_sec = float(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else DEFAULT_TTL_SEC
    text: str = data["text"]
    return text, min(ttl_sec, _MAX_TTL_SEC)


def invalidate(client: Any) -> None:
    """Drop the cached brief (work was submitted or cancelled)."""
    cache = getattr(client, _ATTR, None)
    if isinstance(cache, _BriefCache):
        with cache.lock:
            cache.expires_at = float("-inf")


def _skipped(cache: _BriefCache, project_id: str, now: float) -> bool:
    return now < cache.unsupported_until or now < cache.project_skip.get(project_id, float("-inf"))


def load_brief(client: Any, *, clock: Callable[[], float] = time.monotonic) -> str | None:
    """The brief text ('' = no block), or None when the caller must fall back
    (no project, an older UMRS, an unknown project, a transport error)."""
    project_id = _project_id(client)
    cache = _cache_for(client) if project_id else None
    if cache is None:
        return None
    key = (project_id, str(getattr(client, "companion_session_id", "") or ""))
    with cache.lock:
        now = clock()
        if _skipped(cache, project_id, now):
            return None
        if cache.key == key and now < cache.expires_at:
            return cache.text
        return _refresh(client, cache, key, now)


def _note_failure(cache: _BriefCache, project_id: str, exc: Exception, now: float) -> None:
    status = _status_code(exc)
    # The typed platform code (x-platform-error-code) rides APIError.details.
    code = (getattr(exc, "details", None) or {}).get("code")
    if status in (404, 405) and code == "projects.not_found":
        cache.project_skip[project_id] = now + _PROJECT_RETRY_SEC
    elif status in (404, 405):  # no such route: an older UMRS
        cache.unsupported_until = now + _UNSUPPORTED_RETRY_SEC
    else:
        cache.project_skip[project_id] = now + _FAILURE_RETRY_SEC


def _refresh(client: Any, cache: _BriefCache, key: tuple[str, str], now: float) -> str | None:
    try:
        text, ttl = _fetch(client, *key)
    except Exception as exc:  # fall back to the client builder
        _note_failure(cache, key[0], exc, now)
        logger.debug("agent brief unavailable (%s); using the client builder", exc)
        return None
    cache.key, cache.text, cache.expires_at = key, text, now + ttl
    return text
