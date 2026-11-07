"""Bridge verbs for the local SearXNG (ADR-102 amendment, 2026-11-01).

The console's ``/search-server`` command and its offer after ``/web`` Public
drive ``r4.agent.searxng_local`` through these:

- ``search_server.status`` (read): state, URL, the engine ``up`` would use,
  whether a start is running.
- ``search_server.up``: answers at once and starts the container on a daemon
  thread, since a first start pulls the image (minutes). Each step and the end
  arrive as ``{"search_server_event": {phase, message, state, url, reason,
  hint}}`` lines (``phase``: progress, done or error), the ``teamup.start``
  pattern, so the stdin thread is never held.
- ``search_server.down``: removes the container; the settings stay.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

EVENT_CHANNEL = "search_server_event"
_DONE_STATES = frozenset({"running", "started"})


class SearchServerBridgeMixin:
    """Mixin expecting ``_emit_line`` on the host bridge server."""

    _search_server_starting: bool = False

    def _search_host(self) -> Any:
        """The bridge server this mixin is part of (its ``_emit_line``)."""
        return self

    def search_server_status(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        from r4.agent import searxng_local
        from r4.agent.web_search import SEARXNG_URL_ENV

        status = searxng_local.status()
        return {
            "state": str(status.get("state") or "absent"),
            "url": str(status.get("url") or ""),
            "engine": searxng_local.container_engine() or "",
            "starting": self._search_server_starting,
            # web_search prefers it over the local instance (web_search.base_url).
            "external_url": os.environ.get(SEARXNG_URL_ENV, "").strip(),
        }

    def search_server_up(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        if self._search_server_starting:
            return {"accepted": False, "state": "starting"}
        self._search_server_starting = True
        threading.Thread(target=self._search_server_up_thread, daemon=True).start()
        return {"accepted": True, "state": "starting"}

    def _search_server_up_thread(self) -> None:
        from r4.agent import searxng_local

        emit = self._search_host()._emit_line

        def say(message: str) -> None:
            emit({EVENT_CHANNEL: {"phase": "progress", "message": message}})

        try:
            result = searxng_local.up(progress=say)
        except Exception as exc:  # noqa: BLE001 — the console must hear the end
            logger.warning("search server start failed", exc_info=True)
            result = {"state": "failed", "reason": str(exc)}
        finally:
            self._search_server_starting = False
        emit({EVENT_CHANNEL: final_event(result)})

    def search_server_down(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        from r4.agent import searxng_local

        result = searxng_local.down()
        answer: dict[str, Any] = {"state": str(result.get("state") or "")}
        if result.get("reason"):
            answer["reason"] = str(result["reason"])
        return answer


def final_event(result: dict[str, Any]) -> dict[str, Any]:
    """The last ``search_server_event`` of a start: done or error."""
    state = str(result.get("state") or "failed")
    return {
        "phase": "done" if state in _DONE_STATES else "error",
        "state": state,
        "url": str(result.get("url") or ""),
        "reason": str(result.get("reason") or ""),
        "hint": str(result.get("hint") or ""),
    }
