"""The context window an Ollama server actually serves a model with (2026-10-30).

R4's name table (:mod:`r4.model_context`) holds a model's *trained* window:
131,072 for ``llama3.2`` and ``qwen3``. An Ollama server serves fewer tokens,
its ``num_ctx`` (``OLLAMA_CONTEXT_LENGTH``: 32,768 in the stand-alone stack,
``deploy/standalone/env/base.env``), and silently drops the start of a longer
prompt. Budgeting from the trained window let a session outgrow the served
window before compaction started.

For a chat route that goes straight to an Ollama server, the window is
``min(trained, served)``:

* served: ``GET /api/ps`` ``context_length`` of the loaded model, else the
  ``num_ctx`` parameter ``POST /api/show`` reports (a Modelfile
  ``PARAMETER``), else :data:`OLLAMA_SERVED_DEFAULT` (the stand-alone
  stack's ``OLLAMA_CONTEXT_LENGTH``) when the server does not say; the
  basis is then :data:`DEFAULT_BASIS`, a guess, not ``endpoint``;
* trained: ``/api/show`` ``model_info`` ``<arch>.context_length``, else the
  name table.

A route is an Ollama server when its source declares provider ``ollama`` (as
``r4 source add-ollama`` registers it). A public URL on Ollama's port 11434
without that provider is one only when ``GET /api/tags`` answers with a
``models`` list, as :func:`r4.model_scale._probe_public` checks: the R-CCS
LiteLLM proxy (``r4 up --public rccs``) listens on 11434 too, and a server
that is not Ollama keeps its published limits and the name table (``None``).

``R4_MODEL_CONTEXT_WINDOW`` overrides all of this (``context_budget``). A
route through UMRS needs none of it: UMRS publishes the endpoint's registered
``max_model_len`` as ``context_window``. Every request honours r4's
``verify_ssl`` (as every model-scale probe does), is bounded at
:data:`TIMEOUT_S`, never raises, and the answer (``None`` included) is kept
for :data:`TTL_S`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from r4.model_context import known_context_length

OLLAMA_SERVED_DEFAULT = 32_768
OLLAMA_PORT = 11434
TIMEOUT_S = 2.0
TTL_S = 60.0
SERVED_BASIS = "endpoint"
DEFAULT_BASIS = "ollama default"

#: Tests install an ``httpx.MockTransport`` here; ``None`` is the network.
_TRANSPORT: httpx.BaseTransport | None = None
_MEMO: dict[tuple[str, str], tuple[float, "ServedWindow | None"]] = {}
_NUM_CTX = re.compile(r"^\s*num_ctx\s+(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class OllamaRoute:
    """Where an Ollama-looking chat route goes and how to ask it."""

    base: str
    headers: dict[str, str]
    declared: bool  # provider ``ollama``; else only the port says so
    verify: bool = True  # r4's ``verify_ssl``


@dataclass(frozen=True)
class ServedWindow:
    """The window in use and its basis (:data:`SERVED_BASIS` or :data:`DEFAULT_BASIS`)."""

    window: int
    basis: str


def clear_ollama_window_memo() -> None:
    """Forget every remembered window (tests; a changed server)."""
    _MEMO.clear()


def _config(client: Any) -> Any:
    for attr in ("config", "_config"):
        found = getattr(client, attr, None)
        if found is not None:
            return found
    return None


def _route_source(config: Any, model: str) -> tuple[str, str, str | None] | None:
    """``(url, provider, api_key)`` of the public route ``model`` takes, else None."""
    from r4.model_routing import resolve_model_route
    from r4.source import get_source_store

    route = resolve_model_route(config, model)
    if route.kind != "public":
        return None
    named = get_source_store().get(route.source_name) if route.source_name else None
    if named is not None:
        return named.url, named.provider or "", named.api_key
    provider = getattr(config, "endpoint_provider", None) or ""
    return route.base_url, provider, getattr(config, "endpoint_api_key", None)


def _on_ollama_port(url: str) -> bool:
    try:
        return urlsplit(url).port == OLLAMA_PORT
    except ValueError:
        return False


def ollama_route(client: Any, model: str) -> OllamaRoute | None:
    """Where ``model``'s chat route goes when it may be an Ollama server: a
    declared ``ollama`` source, or a public URL on port 11434 (unconfirmed)."""
    config = _config(client)
    try:
        found = _route_source(config, model)
    except Exception:  # noqa: BLE001 - the chat call reports a bad route itself
        return None
    if found is None:
        return None
    url, provider, api_key = found
    declared = str(provider).lower() == "ollama"
    if not declared and not _on_ollama_port(str(url)):
        return None
    base = str(url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    verify = bool(getattr(config, "verify_ssl", True))
    return OllamaRoute(base[:-3] if base.endswith("/v1") else base, headers, declared, verify)


def _request(route: OllamaRoute, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """One bounded JSON request; ``{}`` on any failure."""
    try:
        with httpx.Client(timeout=TIMEOUT_S, transport=_TRANSPORT, verify=route.verify) as http:
            response = http.request(method, f"{route.base}{path}", headers=route.headers, **kwargs)
        data = response.json() if response.status_code < 400 else {}
    except Exception:  # noqa: BLE001 - an unknown window falls back
        return {}
    return data if isinstance(data, dict) else {}


def _positive(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _tagged(name: str) -> str:
    """Ollama's own spelling: a name without a tag means ``:latest``."""
    return name if ":" in name else f"{name}:latest"


def _ps_window(route: OllamaRoute, name: str) -> int | None:
    """``context_length`` of ``name`` while loaded (``GET /api/ps``)."""
    models = _request(route, "GET", "/api/ps").get("models")
    for entry in models if isinstance(models, list) else []:
        if isinstance(entry, dict) and _tagged(str(entry.get("name") or "")) == _tagged(name):
            return _positive(entry.get("context_length"))
    return None


def _show_windows(route: OllamaRoute, name: str) -> tuple[int | None, int | None]:
    """``(num_ctx parameter, trained window)`` from ``POST /api/show``."""
    shown = _request(route, "POST", "/api/show", json={"model": name})
    match = _NUM_CTX.search(str(shown.get("parameters") or ""))
    info = shown.get("model_info")
    info = info if isinstance(info, dict) else {}
    arch = str(info.get("general.architecture") or "")
    trained = _positive(info.get(f"{arch}.context_length")) if arch else None
    return (int(match.group(1)) if match else None), trained


def is_ollama_server(route: OllamaRoute) -> bool:
    """A declared ``ollama`` source, or ``GET /api/tags`` lists ``models``."""
    if route.declared:
        return True
    return isinstance(_request(route, "GET", "/api/tags").get("models"), list)


def served_window(route: OllamaRoute, name: str) -> ServedWindow:
    """``min(trained, served)`` for ``name`` on the Ollama server ``route`` names."""
    num_ctx, trained = _show_windows(route, name)
    served = _ps_window(route, name) or num_ctx
    window = served or OLLAMA_SERVED_DEFAULT
    trained = trained or known_context_length(name)
    window = min(window, trained) if trained else window
    guessed = not served and window == OLLAMA_SERVED_DEFAULT
    return ServedWindow(window, DEFAULT_BASIS if guessed else SERVED_BASIS)


def _lookup(route: OllamaRoute, name: str) -> ServedWindow | None:
    return served_window(route, name) if is_ollama_server(route) else None


def ollama_served_window(client: Any, model: str) -> ServedWindow | None:
    """The window ``model`` is served with, and its basis, when its route is an
    Ollama server, else ``None`` (another kind of route: its published limits
    and the name table apply)."""
    route = ollama_route(client, model)
    if route is None:
        return None
    from r4.model_routing import parse_model_ref

    key = (route.base, parse_model_ref(model).model)
    kept = _MEMO.get(key)
    if kept is not None and time.monotonic() < kept[0]:
        return kept[1]
    found = _lookup(route, key[1])
    _MEMO[key] = (time.monotonic() + TTL_S, found)
    return found


def ollama_context_window(client: Any, model: str) -> int | None:
    """Just the window of :func:`ollama_served_window` (``None``: not Ollama)."""
    found = ollama_served_window(client, model)
    return found.window if found is not None else None
