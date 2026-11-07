"""Dashboard API client — overview aggregation from the UMRS web dashboard.

Thin read accessor over ``{prefix}/web/dashboard/overview`` used by
``r4 data intake`` (U3) and the future ``r4 dashboard`` TUI (U1).

Route reachability note: the UMRS HTTP sidecar serves the dashboard at
``/umrs/web/dashboard/*``; the Python Gateway has no /gw proxy route for it,
so the client probes both prefixes — inferred prefix first, fallback second.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import R4Error


class DashboardClient:
    """Read-only accessor for the UMRS web dashboard API."""

    def __init__(self, parent) -> None:
        self._parent = parent
        self._config = parent._config

    def overview(self) -> Dict[str, Any]:
        """Aggregate system overview (jobs, models, health, workflows, connectors)."""
        return self._get("web/dashboard/overview")

    def _get(self, path: str) -> Dict[str, Any]:
        prefixes = self._prefix_candidates()
        last_error: Optional[Exception] = None
        for prefix in prefixes:
            url = f"{self._config.gateway_url}{prefix}/{path}"
            try:
                return self._parent.http.get(url, timeout=self._config.timeout)
            except Exception as exc:  # noqa: BLE001 — try next prefix shape
                last_error = exc
        raise R4Error(f"Dashboard unreachable at {path}: {last_error}")

    def _prefix_candidates(self) -> list[str]:
        """Inferred prefix first, then the alternate (never duplicates)."""
        inferred = self._parent._api_prefix
        other = "/umrs" if inferred == "/gw" else "/gw"
        return [inferred, other]
