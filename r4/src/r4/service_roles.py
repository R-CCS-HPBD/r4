"""Implicit service-model roles (ADR-106 D-10, 2026-11-03).

Users never pick a service model. They type natural language in the TUI or
VS Code; R4 resolves each auxiliary role it needs to the platform's live role
alias and routes it to the platform source as ``svc-<role>@<source>``,
whatever source the chat model is on (a user-registered public endpoint
included). An explicit choice wins: ``R4_<ROLE>_MODEL`` (e.g.
``R4_FAST_MODEL``), and for decisions the existing ``decision_model``
settings.

Liveness comes from ``connect-info.default_models``, which advertises a role
only while one of its models has a READY/DEGRADED replica (ADR-106 D-9
refinement). The map is read live (unauthenticated GET) and cached for
``LIVE_TTL_SEC``; when the front cannot be reached, the copy stored on the
source at login is used. A role nobody serves resolves to ``None`` and the
caller keeps its old behaviour (the chat model, or the feature off).

Roles resolved here are the client-side ones. ``embed``, ``rerank`` and
``ocr`` run inside UMRS jobs, which use the front's own configuration.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any

#: Roles a front can advertise (ADR-106 D-1), in advertisement order.
ROLES: tuple[str, ...] = ("chat", "fast", "decision", "vision", "ocr", "embed", "rerank")

#: How long a live ``default_models`` answer is reused (seconds).
LIVE_TTL_SEC = 300.0

_live_cache: dict[str, tuple[float, dict[str, Any]]] = {}

Fetch = Callable[[str], dict[str, Any]]


def _fetch_connect_info(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - platform URL
        data = json.loads(resp.read().decode())
    return data if isinstance(data, dict) else {}


def _connect_info_url(source: Any) -> str:
    from ._http import probe_api_prefix

    base = str(source.url).rstrip("/")
    return f"{base}{probe_api_prefix(base)}/ops/connect-info"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _live_info(
    source: Any, fetch: Fetch, now: Callable[[], float] = time.monotonic
) -> dict[str, Any]:
    """``default_models`` and ``role_models`` from connect-info, cached per URL;
    the role map stored at login (and no names) when the front is unreachable."""
    key = str(source.url)
    hit = _live_cache.get(key)
    if hit is not None and now() - hit[0] < LIVE_TTL_SEC:
        return hit[1]
    try:
        data = fetch(_connect_info_url(source))
    except Exception:  # noqa: BLE001 - liveness is advisory; fall back to the stored map
        return {"default_models": dict(getattr(source, "default_models", None) or {})}
    info = {
        "default_models": _mapping(data.get("default_models")),
        "role_models": _mapping(data.get("role_models")),
    }
    _live_cache[key] = (now(), info)
    return info


def live_default_models(
    source: Any, *, fetch: Fetch = _fetch_connect_info, now: Callable[[], float] = time.monotonic
) -> dict[str, Any]:
    """The source's role map, live (cached) or, if unreachable, as stored at login."""
    return _mapping(_live_info(source, fetch, now)["default_models"])


def live_role_models(source: Any, *, fetch: Fetch = _fetch_connect_info) -> dict[str, Any]:
    """Role -> served model names behind the live alias ({} when unknown)."""
    return _mapping(_live_info(source, fetch).get("role_models"))


def _platform_sources() -> tuple[list[Any], str | None]:
    from .source import get_source_store

    store = get_source_store()
    return [s for s in store.list() if s.kind == "platform"], store.get_active()


def platform_source(config: Any) -> Any | None:
    """The platform source whose roles serve this client.

    Order: the active source when it is a platform source; else the platform
    source on the deployment ``config.gateway_url`` points at (a public
    source keeps the gateway on the platform); else the only platform source.
    """
    from .source_context import same_deployment

    sources, active = _platform_sources()
    for source in sources:
        if source.name == active:
            return source
    gateway = getattr(config, "gateway_url", None)
    if gateway:
        for source in sources:
            if same_deployment(source.url, gateway):
                return source
    return sources[0] if len(sources) == 1 else None


def explicit_role_model(role: str) -> str:
    """``R4_<ROLE>_MODEL`` when set (an explicit choice outranks the platform)."""
    return os.getenv(f"R4_{role.upper()}_MODEL", "").strip()


def resolve_role_model(config: Any, role: str, *, fetch: Fetch = _fetch_connect_info) -> str | None:
    """``svc-<role>@<platform source>`` for a live role, an explicit choice, or None."""
    explicit = explicit_role_model(role)
    if explicit:
        return explicit
    source = platform_source(config)
    if source is None:
        return None
    alias = live_default_models(source, fetch=fetch).get(role)
    if not isinstance(alias, str) or not alias.strip():
        return None
    return f"{alias.strip()}@{source.name}"


def is_role_alias(model: str) -> bool:
    """True for a ``svc-<role>`` alias, bare or ``@source``-qualified."""
    from .model_routing import parse_model_ref

    bare = parse_model_ref(str(model or "")).model.strip().lower()
    return bare.startswith("svc-") and bare[4:] in ROLES


def _source_named(name: str) -> Any | None:
    sources, _active = _platform_sources()
    return next((source for source in sources if source.name == name), None)


def model_label(model: str, *, fetch: Fetch = _fetch_connect_info) -> str:
    """How a model is named to the user (ADR-106 D-10: say which model did
    the work). A role alias names its live backing model, e.g.
    ``Qwen3.8-27B-NVFP4 (svc-fast, rccs-cloud)``; anything else is shown as is.
    """
    from .model_routing import parse_model_ref

    if not is_role_alias(model):
        return model
    ref = parse_model_ref(model)
    source = _source_named(ref.source) if ref.source else None
    names = live_role_models(source, fetch=fetch).get(ref.model.lower()[4:]) if source else None
    where = f", {ref.source}" if ref.source else ""
    if isinstance(names, list) and names:
        return f"{', '.join(str(n) for n in names)} ({ref.model}{where})"
    return model


def safe_model_label(model: str) -> str:
    """``model_label`` that never raises (a label must not break a turn)."""
    try:
        return model_label(model)
    except Exception:  # noqa: BLE001 - display only
        return model


def clear_cache() -> None:
    """Forget live answers (tests, and after a login refreshes the source)."""
    _live_cache.clear()
