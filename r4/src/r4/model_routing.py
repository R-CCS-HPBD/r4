"""Client-side model routing for platform and public endpoint traffic.

Supports cross-source routing: models can be bound to a named source
(from ``r4 source list``) via ``model_routes``, or addressed inline
with the ``model@source`` notation (e.g. ``Qwen/Qwen2.5-7B@rccs-cloud``).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from .errors import R4Error

RouteKind = Literal["platform", "public"]


@dataclass(frozen=True)
class ModelRoute:
    """Resolved destination for a model request."""

    kind: RouteKind
    base_url: str
    source_name: Optional[str] = None


@dataclass(frozen=True)
class ModelRef:
    """A parsed model reference, optionally qualified with a source name.

    ``parse_model_ref("Qwen/Qwen2.5-7B@rccs-cloud")``
    → ``ModelRef(model="Qwen/Qwen2.5-7B", source="rccs-cloud")``
    """

    model: str
    source: Optional[str] = None


def parse_model_ref(raw: str) -> ModelRef:
    """Parse a potentially source-qualified model string.

    The ``@`` character separates model name from source name.  Models
    already use ``/`` in their names (e.g. ``Qwen/Qwen2.5-7B``), so ``@``
    is the unambiguous delimiter.  If multiple ``@`` appear, the *last*
    occurrence is treated as the separator.
    """
    if "@" in raw:
        idx = raw.rfind("@")
        model_part = raw[:idx]
        source_part = raw[idx + 1 :]
        if model_part and source_part:
            return ModelRef(model=model_part, source=source_part)
    return ModelRef(model=raw, source=None)


def has_public_endpoint(config) -> bool:
    """Return whether the active profile has a public endpoint configured."""
    return bool(getattr(config, "endpoint_url", None) or getattr(config, "endpoint_provider", None))


def gateway_llm_endpoint(config) -> str:
    """Return the platform LLM base endpoint — ``/gw/llm`` behind the edge,
    ``/umrs/llm`` on a direct-UMRS base (2026-10-20, plan H-10.2)."""
    from ._http import probe_api_prefix

    return f"{str(config.gateway_url).rstrip('/')}{probe_api_prefix(config)}/llm"


def public_endpoint(config) -> str:
    """Return the public provider base endpoint, or raise when unavailable."""
    endpoint_url = getattr(config, "endpoint_url", None)
    if endpoint_url:
        url = str(endpoint_url).rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        return url
    if getattr(config, "endpoint_provider", None) == "bedrock":
        return "https://bedrock.placeholder.internal"
    raise R4Error(
        "This model is routed to a public endpoint, but no public endpoint is configured. "
        "Run `r4 up --public <provider-or-url> --model <model>` or choose a platform model."
    )


def normalize_route_kind(value: object) -> RouteKind | None:
    """Normalize a persisted route kind."""
    if value in ("platform", "gateway"):
        return "platform"
    if value in ("public", "provider"):
        return "public"
    return None


def _route_for_source(source) -> ModelRoute:
    """Build a ``ModelRoute`` from a ``Source`` object."""
    if source.kind == "platform":
        from ._http import probe_api_prefix

        url = str(source.url).rstrip("/")
        base = f"{url}{probe_api_prefix(url)}/llm"
        return ModelRoute(kind="platform", base_url=base, source_name=source.name)
    # public source
    url = str(source.url).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return ModelRoute(kind="public", base_url=url, source_name=source.name)


def resolve_model_route(config, model: str) -> ModelRoute:
    """Resolve the endpoint route for a model.

    Resolution order:

    1. ``model@source`` notation — inline source wins unconditionally.
    2. Stored ``model_routes[model]`` — either a legacy kind
       (``"platform"``/``"public"``) or a registered source name.
    3. Legacy configured public endpoint.
    4. Public-source auto-fallback for bare models (:func:`discover_public_source`;
       platform sources are never auto-fallback targets), else the default
       platform gateway.
    """
    ref = parse_model_ref(model)

    # 1. Inline @source notation
    if ref.source is not None:
        from .source import get_source_store

        src = get_source_store().get(ref.source)
        if src is None:
            raise R4Error(
                f"Source '{ref.source}' not found. Run `r4 source list` to see registered sources."
            )
        return _route_for_source(src)

    # 2. Stored route map
    route_map = getattr(config, "model_routes", None) or {}
    route_value = route_map.get(ref.model)
    if route_value is not None:
        # Legacy kind values
        route_kind = normalize_route_kind(route_value)
        if route_kind == "platform":
            return ModelRoute(kind="platform", base_url=gateway_llm_endpoint(config))
        if route_kind == "public":
            return ModelRoute(kind="public", base_url=public_endpoint(config))

        # Source-name binding
        from .source import get_source_store

        src = get_source_store().get(route_value)
        if src is not None:
            return _route_for_source(src)
        # Source was deleted; fall through to default behavior

    # 3. Legacy fallback
    if has_public_endpoint(config):
        return ModelRoute(kind="public", base_url=public_endpoint(config))
    # 4. Bare-model default: public-source auto-fallback, else platform gateway
    return _bare_model_default_route(config, ref.model)


def _bare_model_default_route(config, model: str) -> ModelRoute:
    """Default route for a bare model: public-source fallback or platform.

    The ADR-062 sensitive-project refusal fires in ``r4.client`` on the
    resolved public route exactly as it does for an explicit
    ``model@public-source`` — auto-fallback never bypasses the gate.
    """
    fallback = _public_fallback_route(model)
    if fallback is not None:
        return fallback
    return ModelRoute(kind="platform", base_url=gateway_llm_endpoint(config))


# ------------------------------------------------------------------
# Bare-model public-source fallback
# ------------------------------------------------------------------

_PUBLIC_FALLBACK_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_PUBLIC_FALLBACK_TTL_SEC = 5.0


def _public_sources() -> list:
    """Registered public-kind sources ([] when the store is unreadable)."""
    from .source import get_source_store

    try:
        sources = get_source_store().list()
    except Exception:  # noqa: BLE001 - fallback must never break routing
        return []
    return [s for s in sources if getattr(s, "kind", "public") == "public"]


def _public_probe_hit(source, model: str, timeout: float) -> bool:
    models, error = probe_source_models(source, timeout=timeout)
    return error is None and model in models


def discover_public_source(model: str, *, timeout: float = 5) -> Optional[str]:
    """Probe configured *public* sources for *model* and return a source name.

    Companion to :func:`discover_model_source`, restricted to public
    sources: platform sources are never auto-fallback targets (they stay
    governed). Hits and misses are cached for ``_PUBLIC_FALLBACK_TTL_SEC``
    so agentic loops do not re-probe on every iteration.
    """
    now = time.monotonic()
    cached = _PUBLIC_FALLBACK_CACHE.get(model)
    if cached is not None and now - cached[0] < _PUBLIC_FALLBACK_TTL_SEC:
        return cached[1]
    found = _probe_public_sources(model, timeout=timeout)
    _PUBLIC_FALLBACK_CACHE[model] = (time.monotonic(), found)
    return found


def _probe_public_sources(model: str, *, timeout: float) -> Optional[str]:
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial

    sources = _public_sources()
    if not sources:
        return None
    probe = partial(_public_probe_hit, model=model, timeout=timeout)
    with ThreadPoolExecutor(max_workers=min(len(sources), 8)) as pool:
        hits = list(pool.map(probe, sources))
    for src, hit in zip(sources, hits):
        if hit:
            return str(getattr(src, "name", "") or "") or None
    return None


def _public_fallback_route(model: str) -> Optional[ModelRoute]:
    """Resolve the public-source route for a bare model, or ``None``."""
    name = discover_public_source(model)
    if not name:
        return None
    from .source import get_source_store

    src = get_source_store().get(name)
    if src is None:
        return None
    return _route_for_source(src)


def active_model_route_label(config) -> str:
    """Return the active model route label for UI display."""
    try:
        route = resolve_model_route(config, getattr(config, "model", ""))
        return route.source_name or route.kind
    except Exception:
        return "unresolved"


def update_model_route_map(config, model: str, route: str) -> dict[str, str]:
    """Return a copy of config.model_routes with one model route updated.

    ``route`` may be ``"platform"``, ``"public"``, or a registered source
    name (e.g. ``"rccs-cloud"``).
    """
    routes = dict(getattr(config, "model_routes", None) or {})
    routes[model] = route
    return routes


def clear_model_route(config, model: str) -> dict[str, str]:
    """Return a copy of config.model_routes with *model* removed."""
    routes = dict(getattr(config, "model_routes", None) or {})
    routes.pop(model, None)
    return routes


# ------------------------------------------------------------------
# Cross-source model discovery
# ------------------------------------------------------------------


def _source_probe_config(source):
    """Build a per-source :class:`Config` for model probing.

    The probe carries only the source's own credential, never the active
    deployment's (onboarding plan S1, :func:`r4.source_context.config_for_source`).
    """
    from .config import Config
    from .source_context import config_for_source

    return config_for_source(source, Config.from_env())


def _probe_models_url(source) -> str:
    """Return the ``/v1/models`` URL that a probe of *source* would call."""
    url = str(getattr(source, "url", "") or "").rstrip("/")
    if getattr(source, "kind", None) == "platform":
        from ._http import probe_api_prefix

        return f"{url}{probe_api_prefix(url)}/llm/v1/models"
    if url.endswith("/v1"):
        url = url[:-3]
    return f"{url}/v1/models"


def _probe_error_message(source, exc: Exception) -> str:
    """Render an actionable probe failure message for *source*.

    Bare transport/HTTP errors (``API error: 503``) do not tell the user which
    URL failed or how to recover, so the source URL and a concrete remediation
    command are appended.
    """
    from .errors import AuthenticationError

    name = getattr(source, "name", "?")
    kind = getattr(source, "kind", "public")
    detail = f"{exc} ({_probe_models_url(source)})"

    if isinstance(exc, AuthenticationError):
        if kind == "platform":
            from .source_context import login_hint

            # A key-less source on another deployment is sent no credential
            # (onboarding plan S1): say how to give it one (2026-10-15).
            hint = login_hint(source)
        else:
            hint = (
                "API key rejected; store a valid key (other fields are kept): "
                f"printf '%s' \"$KEY\" | r4 source set {name} --api-key-stdin"
            )
        return f"{detail} — {hint}"

    status = getattr(exc, "status_code", None)
    if status == 503 and kind == "platform":
        return f"{detail} — no ready endpoint; run: r4 endpoint up --model <model> --source {name}"
    return detail


def probe_source_models(source, *, timeout: float = 5) -> tuple[list[str], Optional[str]]:
    """Fetch model IDs from a single source, returning ``(models, error)``.

    ``error`` is ``None`` on success or a human-readable message when the
    source could not be reached (e.g. gateway not ready). This lets callers
    surface *why* a source contributed no models instead of showing it as
    silently empty.
    """
    from .client import Client

    try:
        cfg = _source_probe_config(source)
    except Exception as exc:  # noqa: BLE001 - report config failures as probe errors.
        return [], _probe_error_message(source, exc)
    try:
        c = Client(config=cfg)
        if source.kind == "platform":
            return c.list_gateway_models(timeout=timeout), None
        return c.list_public_models(timeout=timeout), None
    except Exception as exc:  # noqa: BLE001 - best-effort probe across sources.
        return [], _probe_error_message(source, exc)


def _probe_source_models(source, *, timeout: float = 5) -> list[str]:
    """Fetch model IDs from a single source (best-effort, errors ignored)."""
    models, _ = probe_source_models(source, timeout=timeout)
    return models


def _ordered_sources(sources: list, active_source_name: Optional[str]) -> list:
    """Return sources with *active_source_name* first, others in order."""
    ordered: list = []
    rest: list = []
    for s in sources:
        if active_source_name and s.name == active_source_name:
            ordered.insert(0, s)
        else:
            rest.append(s)
    ordered.extend(rest)
    return ordered


def discover_model_source(
    model: str,
    *,
    active_source_name: Optional[str] = None,
    timeout: float = 5,
) -> Optional[str]:
    """Probe all registered sources for *model* and return the source name.

    Tries *active_source_name* first (if given), then all others in order.
    Returns ``None`` when no source serves the model.
    """
    from .source import get_source_store

    sources = get_source_store().list()
    if not sources:
        return None

    found: list[str] = []
    for src in _ordered_sources(sources, active_source_name):
        if model in _probe_source_models(src, timeout=timeout):
            found.append(src.name)
            if len(found) >= 2:
                break  # enough to warn

    if not found:
        return None
    if len(found) > 1:
        print(
            f"info: model '{model}' found on multiple sources: {', '.join(found)}; "
            f"using '{found[0]}'. Use model@source to disambiguate.",
            file=sys.stderr,
        )
    return found[0]


# ------------------------------------------------------------------
# Cross-source model aggregation
# ------------------------------------------------------------------


@dataclass(frozen=True)
class SourceModel:
    """A model served by a specific registered source."""

    model: str
    source_name: str
    kind: RouteKind
    status: str = "available"

    @property
    def qualified(self) -> str:
        """The ``model@source`` identifier that routes to this source."""
        return f"{self.model}@{self.source_name}"


def count_registered_sources() -> int:
    """Return the number of registered sources (0 when unavailable)."""
    try:
        from .source import get_source_store

        return len(get_source_store().list())
    except Exception:
        return 0


@dataclass(frozen=True)
class SourceProbe:
    """One source's ``/models`` probe: what it serves, or why nothing came back."""

    source_name: str
    kind: RouteKind
    models: tuple[str, ...] = ()
    error: Optional[str] = None


def probe_sources_detailed(*, timeout: float = 5) -> list[SourceProbe]:
    """Probe every registered source concurrently, keeping failures.

    Same best-effort pass as :func:`list_models_across_sources`, but a source
    that errors contributes its error text instead of vanishing — pickers can
    then say "rikyu: API key rejected …" instead of the bare "no models" that
    hid a rejected key on 2026-10-20 (a probe failure reads exactly like an
    empty endpoint when the error is dropped).
    """
    from .source import get_source_store

    try:
        sources = list(get_source_store().list())
    except Exception:
        return []
    if not sources:
        return []

    from concurrent.futures import ThreadPoolExecutor

    def _probe(src: Any) -> SourceProbe:
        models, error = probe_source_models(src, timeout=timeout)
        return SourceProbe(
            source_name=src.name,
            kind=src.kind,
            models=tuple(str(m) for m in models),
            error=error,
        )

    with ThreadPoolExecutor(max_workers=min(len(sources), 8)) as pool:
        return list(pool.map(_probe, sources))


def list_models_across_sources(*, timeout: float = 5) -> list[SourceModel]:
    """Probe every registered source concurrently for the models it serves.

    Best-effort: a source that errors or times out contributes no models.
    Results preserve source registration order; duplicates of the same
    ``(model, source)`` pair are collapsed, but the same model served by
    different sources is intentionally kept (one entry per source).
    """
    results: list[SourceModel] = []
    seen: set[tuple[str, str]] = set()
    for probe in probe_sources_detailed(timeout=timeout):
        for m in probe.models:
            key = (m, probe.source_name)
            if key in seen:
                continue
            seen.add(key)
            results.append(SourceModel(model=m, source_name=probe.source_name, kind=probe.kind))
    return results
