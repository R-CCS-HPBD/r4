"""Session model selection and provision checks for the research companion."""

from __future__ import annotations

import time
from typing import Any

from r4.errors import R4Error
from r4.model_routing import has_public_endpoint, resolve_model_route

# Matches UMRS routing: ready endpoints plus degraded (may still serve).
USABLE_ENDPOINT_STATUSES = frozenset({"ready", "running", "active", "degraded"})

# TTL cache for list_endpoints results. Keyed by id(client) to avoid
# cross-client contamination. 5s TTL is safe — endpoint readiness changes
# slowly and ensure_model_ready is called on every turn.
_ENDPOINT_CACHE: dict[int, tuple[float, list[str]]] = {}
_ENDPOINT_CACHE_TTL_SEC = 5.0


def endpoint_status_usable(status: str | None) -> bool:
    """True when an endpoint may serve chat for /models selection."""
    return str(status or "").strip().lower() in USABLE_ENDPOINT_STATUSES


def _endpoint_model_names(endpoint: dict[str, Any]) -> list[str]:
    names: list[str] = []
    models = endpoint.get("models")
    if isinstance(models, list):
        names.extend(str(model) for model in models if model)
    model = endpoint.get("model")
    if model:
        names.append(str(model))
    return names


# TTL cache for the cross-source aggregation (independent of any client).
_AGG_CACHE: tuple[float, list[str]] | None = None


def _aggregated_ready_models() -> list[str] | None:
    """Ready models across ALL sources, qualified as ``model@source``.

    Returns ``None`` when 0 or 1 source is registered so the caller falls back
    to the single-client path. Cached for ``_ENDPOINT_CACHE_TTL_SEC`` because
    it is consulted on every turn via ``model_is_provisioned``.
    """
    global _AGG_CACHE
    from r4.model_routing import count_registered_sources, list_models_across_sources

    if count_registered_sources() <= 1:
        return None
    now = time.monotonic()
    if _AGG_CACHE is not None and now - _AGG_CACHE[0] < _ENDPOINT_CACHE_TTL_SEC:
        return list(_AGG_CACHE[1])
    try:
        source_models = list_models_across_sources()
    except Exception:
        return None
    if not source_models:
        return None
    seen: set[str] = set()
    ready: list[str] = []
    for sm in source_models:
        qualified = sm.qualified
        if qualified not in seen:
            seen.add(qualified)
            ready.append(qualified)
    _AGG_CACHE = (now, list(ready))
    return ready


def _scan_ready_endpoint_models(response: Any) -> list[str]:
    """Collect deduped model names from usable endpoints in a list response."""
    if not isinstance(response, dict):
        return []
    seen: set[str] = set()
    ready: list[str] = []
    for endpoint in response.get("endpoints", []):
        if not isinstance(endpoint, dict):
            continue
        if not endpoint_status_usable(endpoint.get("status")):
            continue
        for name in _endpoint_model_names(endpoint):
            if name not in seen:
                seen.add(name)
                ready.append(name)
    return ready


def list_ready_endpoint_models(client: Any) -> list[str]:
    """Return model names served by active (ready) mini-RiVault endpoints.

    With multiple registered sources the models are aggregated across every
    source and qualified as ``model@source``; with a single source the legacy
    single-client behavior (bare names from the active gateway) is preserved.
    """
    aggregated = _aggregated_ready_models()
    if aggregated is not None:
        return aggregated
    cache_key = id(client)
    now = time.monotonic()
    cached = _ENDPOINT_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _ENDPOINT_CACHE_TTL_SEC:
        return list(cached[1])
    try:
        response = client.list_endpoints(include_stopped=False)
    except Exception:
        return []
    ready = _scan_ready_endpoint_models(response)
    _ENDPOINT_CACHE[cache_key] = (now, list(ready))
    return ready


def model_uses_public_route(client: Any, model: str) -> bool:
    """True when the model routes to a configured public/provider endpoint.

    A ``model@<public-source>`` reference (or a ``model_routes`` binding to a
    public source) is provisioned by definition — the source already serves
    it — so this must hold even when no legacy profile ``endpoint_url`` /
    ``endpoint_provider`` is configured. Only fall back to the legacy gate
    for unqualified models.
    """
    config = getattr(client, "config", None)
    if config is None:
        return False
    try:
        route = resolve_model_route(config, model)
    except Exception:
        return False
    if route.kind != "public":
        return False
    # Qualified/source-bound public routes are self-provisioning; bare models
    # still require a configured legacy public endpoint.
    if route.source_name is not None:
        return True
    return has_public_endpoint(config)


def model_is_provisioned(client: Any, model: str) -> bool:
    """True when the model can be invoked without provisioning a new endpoint."""
    if not model:
        return False
    if model_uses_public_route(client, model):
        return True
    from r4.model_routing import parse_model_ref

    ready = set(list_ready_endpoint_models(client))
    if model in ready:
        return True
    # Tolerate bare-vs-qualified mismatch: a bare profile default matches a
    # qualified ready model (and vice versa) on the same bare model name.
    bare = parse_model_ref(model).model
    for entry in ready:
        entry_bare = parse_model_ref(entry).model
        if entry_bare == bare:
            return True
        # Tolerate redundant compound ids: a clean base name (e.g.
        # ``Qwen3.6-35B-A3B-NVFP4``) matches a ``<base>-<variant>`` ready entry
        # (e.g. ``Qwen3.6-35B-A3B-NVFP4-qwen3.6-35b-a3b-nvfp4``) and vice versa.
        # The trailing hyphen keeps the prefix match on a name-segment boundary.
        if entry_bare.startswith(f"{bare}-") or bare.startswith(f"{entry_bare}-"):
            return True
    return False


def _chat_candidates(client: Any) -> list[str]:
    """Ready models a session may auto-select: a decision model (ADR-076)
    scores choices and never chats, so it is left out — an explicit or saved
    choice still wins."""
    from r4.decisions.settings import is_decision_model

    return [m for m in list_ready_endpoint_models(client) if not is_decision_model(m, client)]


def resolve_initial_model(
    client: Any,
    *,
    explicit: str | None,
    saved: str | None,
) -> str | None:
    """Pick a session model at startup.

    Explicit CLI flags win unconditionally. A resumed session model (*saved*)
    is honored only when it is still provisioned on a reachable source. This
    prevents pinning a stale ``model@source`` that targets a retired
    deployment (e.g. an AI4S endpoint after migrating to RCCS), which would
    otherwise make every turn fail with a gateway 502 while ``r4 chat`` — which
    always targets the active source — keeps working. Otherwise auto-select
    when a ready endpoint exists, or when the profile default is provisioned.
    """
    if explicit:
        return explicit

    ready_models = _chat_candidates(client)

    if saved:
        if model_is_provisioned(client, saved):
            return saved
        # Saved model is unreachable (its source may have been retired during a
        # deployment migration). Fall through to live auto-selection only when
        # we have positive evidence of other ready models; otherwise keep the
        # saved name so the user still gets an actionable provision hint
        # instead of a silent model switch.
        if not ready_models:
            return saved

    if len(ready_models) == 1:
        return ready_models[0]
    if len(ready_models) > 1:
        return _select_from_ready(ready_models, _profile_default_model(client))
    return _provisioned_default_model(client)


def _provisioned_default_model(client: Any) -> str | None:
    """The active public source's default model, else the profile default,
    whichever is provisioned first.

    A public source serves no mini-RiVault endpoint, so ``ready_models`` is
    empty for it, and the profile default (the built-in
    ``rivault-gpt-oss-static`` unless the profile names one) is not served
    there either: the TUI's first turn said "No model selected" while
    ``r4 chat`` — which takes the active source's ``default_model`` — worked
    (stabilization 2026-10-21, chat#6).
    """
    for candidate in (_active_public_source_default(), _profile_default_model(client)):
        if candidate and model_is_provisioned(client, candidate):
            return candidate
    return None


def _active_public_source_default() -> str | None:
    """``model@source`` for an active public source that names a default model."""
    try:
        from r4.source import resolve_active_source
        from r4.source_context import source_model

        source = resolve_active_source()
    except Exception:  # noqa: BLE001 - unreadable source store: no source default.
        return None
    if source is None or getattr(source, "kind", None) == "platform":
        return None
    model = source_model(source, None)
    return f"{model}@{source.name}" if model else None


def _active_source_name() -> str | None:
    """Return the CLI active source name (the shared deployment pointer)."""
    try:
        from r4.source import get_source_store

        return get_source_store().get_active()
    except Exception:
        return None


def _select_from_ready(ready_models: list[str], profile_default: str | None) -> str:
    """Pick from several ready models, preferring the profile default.

    Preference order: exact profile default, bare-name match of the profile
    default, a model served by the active source (so the companion aligns with
    ``r4 chat``, which always targets the active source), then the first ready
    model. Tolerates a bare profile default matching a qualified
    ``model@source`` ready entry.
    """
    if profile_default in ready_models:
        return profile_default  # type: ignore[return-value]

    from r4.model_routing import parse_model_ref

    if profile_default:
        bare_default = parse_model_ref(profile_default).model
        for entry in ready_models:
            if parse_model_ref(entry).model == bare_default:
                return entry

    # Prefer a model served by the active source so the companion targets the
    # same deployment as `r4 chat`, instead of an arbitrary first entry across
    # all aggregated sources.
    active = _active_source_name()
    if active:
        for entry in ready_models:
            if parse_model_ref(entry).source == active:
                return entry

    return ready_models[0]


def _normalize_provider_prefixed_model(model: str | None) -> str | None:
    """Translate a ``source/model`` ref (e.g. ``ollama/llama3.2:latest``) to
    the canonical ``model@source`` form, but only when the prefix matches a
    registered source. Leaves ``author/model`` HF-style names alone.
    """
    if not model or "/" not in model or "@" in model:
        return model
    prefix, _, rest = model.partition("/")
    if not prefix or not rest:
        return model
    try:
        from r4.source import get_source_store

        if get_source_store().get(prefix) is None:
            return model
    except Exception:
        return model
    return f"{rest}@{prefix}"


def session_model_info(session: Any) -> dict[str, Any]:
    """Describe the active session model for UI and guards."""
    agent_model = getattr(getattr(session, "agent", None), "model", None)
    model = agent_model if isinstance(agent_model, str) and agent_model.strip() else None
    if model:
        model = _normalize_provider_prefixed_model(model)
    profile_default = _profile_default_model(session.client)
    provisioned = bool(model and model_is_provisioned(session.client, model))
    return {
        "model": model,
        "profile_default": profile_default,
        "provisioned": provisioned,
        "needs_selection": model is None,
    }


def ensure_model_ready(session: Any) -> str:
    """Require a provisioned model before an LLM turn."""
    from .context_budget import sync_session_context_budget

    info = session_model_info(session)
    model = info["model"]
    if not model:
        profile_default = info["profile_default"]
        if profile_default and model_is_provisioned(session.client, profile_default):
            return profile_default
        raise R4Error(
            "No model selected for this session.",
            {
                # Shared by the console and the terminal entries (r4 -p, the
                # REPL): name both ways to pick one (cli lane R2, 2026-10-21).
                "hint": (
                    "Pick a model with /models in the console or --model <name> on "
                    "the command line; `r4 endpoint up <model>` provisions an endpoint."
                ),
            },
        )
    if not info["provisioned"]:
        raise R4Error(
            f"Model '{model}' is not provisioned.",
            {
                "hint": (
                    f"Run /endpoint up {model} to start an endpoint, "
                    "or /models to choose another model."
                ),
            },
        )
    # Persist the normalized form back onto the agent so the LLM call itself
    # uses ``model@source`` (session_model_info may have translated a legacy
    # ``source/model`` ref that the router cannot parse otherwise).
    agent = getattr(session, "agent", None)
    if agent is not None and getattr(agent, "model", None) != model:
        agent.model = model
    sync_session_context_budget(session)
    return model


def _profile_default_model(client: Any) -> str:
    for attr in ("_config", "config"):
        config = getattr(client, attr, None)
        if config is None:
            continue
        value = getattr(config, "model", None)
        if isinstance(value, str) and value.strip():
            return value
    return ""
