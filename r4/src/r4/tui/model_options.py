"""Cross-source model option collector for companion model pickers.

`r4.tui.commands._collect_endpoint_model_options` only lists models served
by mini-RiVault endpoints on the *active* platform gateway. Public sources
(OpenAI-compatible providers, Ollama, etc.) and peer platform deployments
would stay invisible to pickers even though the agent can already route to
them via ``model@source`` notation (`r4.model_routing.resolve_model_route`).

This module augments the endpoint options with those extra-source models so
every picker surface (JS Ink TUI via the bridge, Bun TUI, Python UnifiedTUI
via ``r4.tui.commands.collect_model_options``) can detect and select them.
Selecting a ``model@source`` option works end-to-end without frontend
changes: pickers submit ``option["name"]`` and the agent resolves the route
client-side.
"""

from __future__ import annotations

import time
from typing import Any

# 5s TTL mirrors model_resolution._ENDPOINT_CACHE_TTL_SEC — picker opens and
# session startup can call this back-to-back, while probes block up to
# `timeout` seconds when a source is slow.
_CACHE_TTL_SEC = 5.0
# (monotonic stamp, options, problems) — options and probe problems come from
# ONE network pass so asking both never doubles the per-source probes.
_CACHE: tuple[float, list[dict[str, str]], list[dict[str, str]]] | None = None


def reset_extra_source_model_cache() -> None:
    """Drop the cached extra-source options (test isolation)."""
    global _CACHE
    _CACHE = None


def _active_source_name() -> str | None:
    from r4.source import get_source_store

    try:
        return get_source_store().get_active()
    except Exception:  # noqa: BLE001 - store unreadable; treat as no active source.
        return None


def _extra_source_snapshot(timeout: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(options, problems) for non-active sources from ONE probe pass.

    A source that answers contributes ``model@source`` options (the active
    platform source stays skipped — endpoint options already cover it); a
    source whose probe FAILED contributes a ``{"source", "error"}`` problem,
    so pickers can say "rikyu: API key rejected …" instead of the misleading
    bare "no provisioned models" (field report 2026-10-20: a dropped probe
    error reads exactly like an empty endpoint).
    """
    from r4.model_routing import probe_sources_detailed

    active = _active_source_name()
    options: list[dict[str, str]] = []
    problems: list[dict[str, str]] = []
    for probe in probe_sources_detailed(timeout=timeout):
        skipped = probe.kind == "platform" and probe.source_name == active
        if probe.error is not None:
            if not skipped:
                problems.append({"source": probe.source_name, "error": probe.error})
            continue
        if skipped:
            continue  # active gateway already covered by endpoint options
        for model in probe.models:
            qualified = f"{model}@{probe.source_name}"
            options.append(
                {
                    "name": qualified,
                    "source": probe.kind,
                    "status": "available",
                    "label": f"{qualified} ({probe.kind})",
                }
            )
    return options, problems


def _snapshot_cached(timeout: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """The probe snapshot, fresh or from ``_CACHE``; failures cache to empty."""
    global _CACHE
    now = time.monotonic()
    if _CACHE is not None and now - _CACHE[0] < _CACHE_TTL_SEC:
        return [dict(option) for option in _CACHE[1]], [dict(p) for p in _CACHE[2]]
    try:
        options, problems = _extra_source_snapshot(timeout)
    except Exception:  # noqa: BLE001 - picker must survive probe failures.
        options, problems = [], []
    _CACHE = (now, list(options), list(problems))
    return options, problems


def list_extra_source_model_options(timeout: float = 5.0) -> list[dict[str, str]]:
    """Models served by non-active sources, as ``model@source`` options.

    Best-effort: probe failures contribute no options (but are reported by
    :func:`list_extra_source_probe_problems`). Results are cached for
    ``_CACHE_TTL_SEC`` because probes are network-bound.
    """
    return _snapshot_cached(timeout)[0]


def list_extra_source_probe_problems(timeout: float = 5.0) -> list[dict[str, str]]:
    """Why probed sources contributed no models, as ``{"source", "error"}``.

    Shares :func:`list_extra_source_model_options`' probe pass and cache — a
    picker that asks both pays one network round, not two.
    """
    return _snapshot_cached(timeout)[1]


def collect_bridge_model_options(client: Any) -> list[dict[str, str]]:
    """Active-gateway endpoint options plus extra-source (public/peer) models.

    Drop-in replacement for the platform-only collector used by bridge
    consumers; extra-source entries keep qualified ``model@source`` names so
    selection routes to the right source without ambiguity.
    """
    from r4.tui.commands import _collect_endpoint_model_options

    options = _collect_endpoint_model_options(client)
    seen = {str(option.get("name") or "") for option in options}
    for extra in list_extra_source_model_options():
        if extra["name"] in seen:
            continue
        seen.add(extra["name"])
        options.append(extra)
    return options
