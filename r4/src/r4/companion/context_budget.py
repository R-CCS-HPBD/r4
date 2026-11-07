"""Resolve and sync companion context budgets from the active model."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from r4_contracts.models import RetrievalProfile as ReaderProfile
from r4_contracts.models import model_size_class, profile_for_class

from r4.model_context import known_context_length

from .context import DEFAULT_MAX_TOKENS

# Leave headroom below the model hard limit (tokenizer drift + completion reserve).
CONTEXT_LIMIT_HEADROOM_RATIO = 0.90
EXTRA_HEADROOM_TOKENS = 4_096

# ADR-093 D-8/D-14: the pre-retrieval pack is sized by the reading model's
# D-14 profile (``r4_contracts.models.RETRIEVAL_PROFILES``: ``pack_passages``
# and ``pack_tokens``), the one table R4 and UMRS share. The profile sets
# nothing else here: the cut stays UMRS's.
RAG_PROFILE_ENV = "R4_RAG_PROFILE"
RAG_BUDGET_SHARE = 0.12  # the pack never takes more of the context budget


@dataclass(frozen=True)
class RetrievalProfile:
    """How much retrieved evidence one turn may carry (ADR-093 D-8, D-14).

    ``size_class`` is the reader's own class (``small``, ``medium``, ``large``
    or ``unknown``: detected, D-14), which ``R4_RAG_ROUTER=auto`` gates on
    (D-8) and ``R4_RAG_PROFILE`` never changes. ``profile`` is the D-14
    profile the pack is sized by (``""``: the class's own, ``unknown`` reading
    as medium): ``R4_RAG_PROFILE`` overrides the profile only, so an eval arm
    that only resizes the pack neither gains nor loses routing.
    """

    size_class: str
    k_max: int
    max_tokens: int
    profile: str = ""

    @property
    def reader(self) -> tuple[str, ReaderProfile]:
        """The D-14 ``(name, profile)`` this pack reads with."""
        return profile_for_class(self.profile or self.size_class)


def retrieval_profile(
    model: str,
    context_budget: int,
    *,
    override: str | None = None,
    size_class: str | None = None,
) -> RetrievalProfile:
    """The pack's k_max and token cap for ``model`` within ``context_budget``.

    ``override`` (else ``R4_RAG_PROFILE``) names the profile outright (the
    class stays the reader's). Otherwise ``size_class`` (a scale
    ``r4.model_scale`` detected) or, without one, the shared name rule
    (``r4_contracts.models.model_size_class``: the effective size) picks it;
    ``unknown`` reads with medium (lead decision, 2026-10-10). The token cap
    is the profile's ``pack_tokens``, never more than 12% of the context
    budget.
    """
    chosen = override if override is not None else os.environ.get(RAG_PROFILE_ENV)
    size = size_class or model_size_class(model)
    name, profile = profile_for_class(size, chosen)
    share = int(max(0, int(context_budget)) * RAG_BUDGET_SHARE)
    return RetrievalProfile(
        size_class=size,
        k_max=profile.pack_passages,
        max_tokens=min(profile.pack_tokens, share),
        profile=name,
    )


def effective_context_budget(raw_limit: int) -> int:
    """Convert a model's max context into a safe companion budget."""
    if raw_limit <= 0:
        return DEFAULT_MAX_TOKENS
    scaled = int(raw_limit * CONTEXT_LIMIT_HEADROOM_RATIO)
    return max(4_096, scaled - EXTRA_HEADROOM_TOKENS)


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _endpoint_names(endpoint: dict[str, Any]) -> list[str]:
    names = [str(endpoint["model"])] if endpoint.get("model") else []
    models = endpoint.get("models")
    if isinstance(models, list):
        names.extend(str(item) for item in models if item)
    return names


def _entry_limits(entry: dict[str, Any]) -> dict[str, int | None]:
    return {
        "context_window": _positive_int(entry.get("context_window"))
        or _positive_int(entry.get("max_model_len")),
        "max_output_tokens": _positive_int(entry.get("max_output_tokens")),
    }


def _listing_limits(client: Any, model: str) -> dict[str, int | None]:
    """Limits from the platform endpoint listing (``max_model_len`` there)."""
    try:
        response = client.list_endpoints(include_stopped=False)
    except Exception:
        return {}
    if not isinstance(response, dict):
        return {}
    for endpoint in response.get("endpoints", []):
        if isinstance(endpoint, dict) and model in _endpoint_names(endpoint):
            return _entry_limits(endpoint)
    return {}


def _models_route_limits(client: Any, model: str) -> dict[str, int | None]:
    """Limits from the route's ``/v1/models`` entry: the UMRS LLM proxy publishes
    ``context_window`` and ``max_output_tokens`` there (W5/U3); vLLM-style sources
    publish ``max_model_len``."""
    fetch = getattr(client, "model_entries", None)
    if not callable(fetch):
        return {}
    try:
        entries = fetch()
    except Exception:
        return {}
    if not isinstance(entries, list):
        return {}
    bare = model.split("@", 1)[0]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") in (model, bare):
            return _entry_limits(entry)
    return {}


#: Key in :func:`endpoint_model_limits` set (to the window) when the window is
#: the Ollama fallback, not a value the server reported.
OLLAMA_DEFAULT_KEY = "ollama_default"


def _ollama_limits(client: Any, model: str) -> dict[str, int | None]:
    """The window an Ollama server serves ``model`` with, when the chat route
    goes straight to one (its ``/v1/models`` publishes no window; 2026-10-30)."""
    from .ollama_window import SERVED_BASIS, ollama_served_window

    found = ollama_served_window(client, model)
    if found is None:
        return {}
    guessed = found.window if found.basis != SERVED_BASIS else None
    return {"context_window": found.window, OLLAMA_DEFAULT_KEY: guessed}


CONTEXT_WINDOW_ENV = "R4_MODEL_CONTEXT_WINDOW"


def _env_window() -> int | None:
    """``R4_MODEL_CONTEXT_WINDOW``: the user's window, over every published one."""
    raw = os.environ.get(CONTEXT_WINDOW_ENV, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def endpoint_model_limits(client: Any, model: str) -> dict[str, int | None]:
    """Published limits for ``model`` (W4/W5, plan rows H15/U3).

    The endpoint listing first, then the route's ``/v1/models`` entry, then
    (window only) the Ollama server the route goes to, for any value still
    unknown. ``context_window`` from ``context_window`` or ``max_model_len``
    (``R4_MODEL_CONTEXT_WINDOW`` wins); ``max_output_tokens`` when published.
    Unknown → ``None``.
    """
    limits: dict[str, int | None] = {"context_window": _env_window(), "max_output_tokens": None}
    for source in (_listing_limits, _models_route_limits):
        if all(limits.values()):
            break
        for key, value in source(client, model).items():
            limits[key] = limits[key] or value
    if not limits["context_window"]:
        limits.update(_ollama_limits(client, model))
    return limits


def context_window_for(model: str, limits: dict[str, int | None]) -> tuple[int | None, str]:
    """``(window, basis)`` the budget is computed from: the published or served
    window, else the name table; ``(None, "default")`` when neither knows."""
    pinned = _env_window()
    if pinned:
        return pinned, CONTEXT_WINDOW_ENV
    if limits.get(OLLAMA_DEFAULT_KEY):
        from .ollama_window import DEFAULT_BASIS

        return limits[OLLAMA_DEFAULT_KEY], DEFAULT_BASIS
    if limits.get("context_window"):
        return limits["context_window"], "endpoint"
    known = known_context_length(model)
    return (known, "model table") if known else (None, "default")


def resolve_model_context_limit(
    client: Any, model: str, *, published: dict[str, int | None] | None = None
) -> int:
    """Best-effort context budget for ``model``."""
    if not model:
        return DEFAULT_MAX_TOKENS
    limits = published if published is not None else endpoint_model_limits(client, model)
    window, _basis = context_window_for(model, limits)
    return effective_context_budget(window) if window else DEFAULT_MAX_TOKENS


def sync_session_context_budget(session: Any) -> int:
    """Align ``session.context`` with the active model's served context window."""
    from .model_resolution import session_model_info

    model = str(session_model_info(session).get("model") or "")
    published = endpoint_model_limits(session.client, model) if model else None
    limit = resolve_model_context_limit(session.client, model, published=published)
    session.context.max_context_tokens = limit
    # /context shows the window the budget came from, and why.
    window, basis = context_window_for(model, published or {}) if model else (None, "default")
    session.context.model_context_window, session.context.context_window_basis = window, basis
    # W4: hand the published limits to the agent (output limit and history
    # budget); a model without metadata keeps the name-table defaults.
    agent = getattr(session, "agent", None)
    if agent is not None and published is not None:
        agent.model_context_window = published["context_window"]
        agent.model_output_limit = published["max_output_tokens"]
    return limit
