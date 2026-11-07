"""Turning the decision model on and off for a user (``/decision-model``).

The choice lives in the profile (``~/.config/r4/config.json`` key
``decision_model``), so every new session of that profile follows it; the
running session is updated in place (config + ``decision_evaluate`` tool).
``off`` is stored as the literal ``"off"`` so it outranks nothing but still
reads back as disabled (``Config`` normalizes the off-words to ``""``).
Higher-precedence sources — ``R4_DECISION_MODEL`` and the ``.r4/*.toml``
layers — still win over the profile; the status line names them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

#: The site-wide always-on decision endpoint (ADR-076, rccs).
DEFAULT_DECISION_MODEL = "Bespoke-Nimble-9B"

#: Values that mean "no decision model" wherever a model name is read.
OFF_WORDS = frozenset({"", "off", "none", "false", "0", "disabled"})

#: Models that score choices and never chat (lowercase bare names). The
#: platform's decision role alias is one (ADR-106 D-10).
KNOWN_DECISION_MODELS = frozenset({DEFAULT_DECISION_MODEL.lower(), "svc-decision"})


def normalize_decision_model(value: Any) -> str:
    """Model name, or ``""`` for any of the off-words."""
    text = str(value or "").strip()
    return "" if text.lower() in OFF_WORDS else text


def _bare(model: str) -> str:
    from r4.model_routing import parse_model_ref

    return parse_model_ref(model).model.lower()


def is_decision_model(model: str, client: Any = None) -> bool:
    """True for a model that only scores decisions (never a chat model):
    the known ones, and the decision model this client is configured with.

    A decision model answers one constrained token per question; chatting
    with it fails or returns noise, so session auto-selection skips it
    (2026-10-02: rccs serves only Nimble, which made it every new
    session's chat model).
    """
    if not model:
        return False
    bare = _bare(model)
    if bare in KNOWN_DECISION_MODELS:
        return True
    configured = normalize_decision_model(
        getattr(getattr(client, "config", None), "decision_model", "")
    )
    return bool(configured) and _bare(configured) == bare


def configured_decision_model(client: Any) -> str:
    """The decision model this client calls ("" = off).

    An explicit choice (``R4_DECISION_MODEL``, ``.r4`` layers, the profile,
    ``/decision-model``) is kept as is. While the setting is still the
    packaged default, the platform's live decision role wins (ADR-106 D-10):
    ``svc-decision@<platform source>``, so decisions reach the platform even
    when the chat model is on a user-registered public endpoint.
    """
    config = getattr(client, "config", None)
    raw = getattr(config, "decision_model", "")
    model = normalize_decision_model(raw) if isinstance(raw, str) else ""
    if model != DEFAULT_DECISION_MODEL:
        return model
    from r4.service_roles import resolve_role_model

    return resolve_role_model(config, "decision") or model


@dataclass(frozen=True)
class DecisionModelState:
    model: str  # "" = off
    override: str  # "" or a description of a higher-precedence source

    @property
    def enabled(self) -> bool:
        return bool(self.model)


def _override_source() -> str:
    if os.getenv("R4_DECISION_MODEL", "").strip():
        return "R4_DECISION_MODEL environment variable"
    try:
        from r4.config import _layer_lookup, _load_local_config, _load_repo_config

        layers = [_load_local_config(), _load_repo_config()]
        if str(_layer_lookup(layers, "decision_model") or "").strip():
            return ".r4/local.toml or .r4/config.toml (decision_model)"
    except Exception:  # noqa: BLE001 - status must never fail on a config read
        pass
    return ""


def current_state(client: Any) -> DecisionModelState:
    return DecisionModelState(model=configured_decision_model(client), override=_override_source())


def set_decision_model(client: Any, model: str, *, persist: bool = True) -> DecisionModelState:
    """Switch the live client (and, by default, the profile) to ``model`` ("" = off)."""
    model = normalize_decision_model(model)
    config = client.config
    if persist:
        from r4.config import ProfileConfigStore

        ProfileConfigStore().save(
            getattr(config, "profile", "default"), decision_model=model or "off"
        )
    config.decision_model = model
    return current_state(client)


def refresh_session_tools(session: Any, client: Any) -> bool:
    """Add or drop ``decision_evaluate`` on a running session; True when it changed.

    ``session.tools`` is read fresh on every turn, so the change applies from
    the next message on.
    """
    tools = getattr(session, "tools", None)
    if not isinstance(tools, list):
        return False
    from r4.companion.decision_tools import build_decision_tools

    kept = [t for t in tools if t.get("name") != "decision_evaluate"]
    fresh = build_decision_tools(client)
    changed = len(kept) != len(tools) or bool(fresh)
    session.tools = kept + fresh
    return changed


#: How long an availability answer is reused (seconds).
AVAILABILITY_TTL_SEC = 300.0


def decision_model_available(client: Any, model: str) -> bool:
    """False only when the platform answered and does not list ``model``.

    The decision model is on by default for every user (2026-10-03), so on
    a site that does not serve it the harness must not spend a failing call
    at every gate. The answer is cached on the client for
    ``AVAILABILITY_TTL_SEC``; "could not tell" counts as available (the call
    is tried, and a failure is logged and changes nothing).
    """
    import time

    cache = getattr(client, "_decision_model_availability", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            client._decision_model_availability = cache
        except Exception:  # noqa: BLE001 - an immutable client: no cache
            pass
    now = time.monotonic()
    hit = cache.get(model)
    if hit is not None and now - hit[0] < AVAILABILITY_TTL_SEC:
        return hit[1]
    served = _served(client, model)
    available = served is not False
    cache[model] = (now, available)
    return available


def _served(client: Any, model: str) -> bool | None:
    """A role alias resolved from the live role map is served by definition
    (connect-info lists a role only while it has a ready replica); any other
    model is looked up bare, without its ``@source`` (the listing never
    carries the suffix, which used to switch ``name@source`` off)."""
    from r4.service_roles import is_role_alias

    if is_role_alias(model):
        return True
    return gateway_serves(client, _bare_name(model), timeout=5)


def _bare_name(model: str) -> str:
    from r4.model_routing import parse_model_ref

    return parse_model_ref(model).model


def gateway_serves(client: Any, model: str, *, timeout: float = 10) -> bool | None:
    """Whether the platform currently lists ``model`` (None = could not tell)."""
    try:
        return model in list(client.list_gateway_models(timeout=timeout))
    except Exception:  # noqa: BLE001 - availability is advisory
        return None
