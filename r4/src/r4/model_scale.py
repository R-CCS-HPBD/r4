"""Model-scale detection across every registered source (ADR-093 D-14, C22).

The maintainer (2026-10-10): "r4 needs a logic to detect the size/scale of a
model user registered to r4, as we have multiple registration of sources
(self-deployed, rikyu, ollama, or any other public endpoint)".

:func:`detect_model_scale` answers it. The first decisive basis wins:

1. **override**: ``R4_MODEL_SCALE=<class>`` for the process (eval, CI), then
   ``<config>/model_scales.json`` (XDG-aware like the rest of r4's config;
   key ``"<source>/<model>"`` before ``"*/<model>"``), written by
   ``r4 model scale set``. Overrides are never cached.
2. **probe**, chosen by what the source is. The whole probe (every request
   it makes) is bounded at :data:`PROBE_TIMEOUT_S` of wall-clock time and
   never raises; a failure falls through and is recorded in ``detail``. A
   probe still running at the bound goes on in the background and its answer
   serves the next detection. Every string a source returns is shown without
   control characters (:func:`display_text`).

   - ollama (``provider="ollama"``, ``r4 source add-ollama``, or any public
     URL that answers ``GET /api/tags``): ``POST /api/show`` gives the exact
     parameter count, the context length and, for MoE, the expert counts
     (active ≈ total × used / count when nothing else states it: basis
     ``ollama-moe-estimate``);
   - platform (UMRS-registered, self-deployed): the model registry entry
     (``GET <gateway>/gw/models``; a source pointing straight at UMRS, where
     that is a 404, ``GET <url>/umrs/models``): ``huggingface_id``, declared
     context, and ``param_count``/``params_b`` when the registry carries them;
   - OpenAI-compatible (rikyu, vLLM, LiteLLM, any other): ``GET /v1/models``
     with the source's bearer key (never logged): ``max_model_len``,
     ``root``/``parent`` (often the HF path), and a parameter field when one
     is published;
   - bedrock / anthropic: hosted frontier models, large by family.
3. **name rule** (:func:`r4_contracts.models.scale_from_name`) over the model
   name and every name the probe found (HF id, ``root``, size label).
4. **unknown**: the caller reads with the medium profile
   (:func:`r4_contracts.models.profile_for_class`) and the companion shows one
   notice per session (:meth:`ModelScale.notice`).

The size class comes from the effective size (sqrt(total × active) for a
mixture-of-experts model, else the total), with the shared thresholds.
Probe results are cached in ``<config>/cache/model_scale.json`` for
:data:`CACHE_TTL_S` (``refresh=True`` re-probes) and memoised per process
for as long; a probe that found nothing is memoised for
:data:`FAILURE_TTL_S` only, so a source that was down when the process
started is asked again. A session keeps its scale for its lifetime
(:func:`session_model_scale`) unless the source did not answer (the scale is
``provisional``) or a pin changes. Overrides and the cache are written under
a lock that also holds across r4 processes where the OS offers ``flock``.

Which source a bare model is probed on is the chat call's own route
(:func:`r4.model_routing.resolve_model_route`, as ``Client`` resolves it), so
detection never sizes a different model from the one the session reads
with. A configured gateway or endpoint that no registered source names is
labelled ``platform`` or ``public`` and cached under that label plus a
digest of its URL, so two deployments serving one alias never share an
entry.

Every probe honours r4's ``verify_ssl`` (``R4_VERIFY_SSL``, the config file,
``r4 config set``), read from the client's config as every other r4 request
does: a self-signed gateway with ``verify_ssl`` off is probed, not silently
skipped, and a certificate failure with it on says so in ``detail``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import httpx

try:  # an advisory lock across r4 processes (POSIX)
    import fcntl
except ImportError:  # pragma: no cover - Windows: the in-process lock only
    fcntl = None  # type: ignore[assignment]
from r4_contracts.models import (
    RETRIEVAL_PROFILES,
    SIZE_AGREEMENT,
    NameScale,
    active_param_billions,
    effective_params_b,
    model_param_billions,
    profile_for_class,
    scale_from_name,
    size_class_for,
)

from .model_context import known_context_length, resolve_max_context
from .model_routing import parse_model_ref, resolve_model_route
from .source import Source, get_source_store

if TYPE_CHECKING:
    from .client import Client

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 2.0  # the whole probe, every request it makes, wall clock
CACHE_TTL_S = 7 * 24 * 3600.0
FAILURE_TTL_S = 60.0  # a probe that found nothing is asked again after this
PROBE_THREAD_NAME = "r4-model-scale-probe"
SCALE_ENV = "R4_MODEL_SCALE"
RAG_PROFILE_ENV = "R4_RAG_PROFILE"
OVERRIDES_FILE = "model_scales.json"
CACHE_FILE = Path("cache") / "model_scale.json"
SCALE_CLASSES: tuple[str, ...] = tuple(RETRIEVAL_PROFILES)  # small, medium, large
NO_SOURCE = "default"
SESSION_SCALE_ATTR = "_r4_model_scale"
SESSION_NOTICE_ATTR = "_r4_model_scale_noticed"
NOTICE = (
    "model scale unknown for {model}@{source}; "
    "set it with `r4 model scale set {model} small|medium|large`"
)

_PARAM_FIELDS = ("param_count", "params_b", "num_parameters", "parameters", "params")
_ACTIVE_FIELDS = ("active_param_count", "active_params_b", "active_parameters")
_CONTEXT_FIELDS = ("max_model_len", "context_window", "context_length", "max_context_length")
_HOSTED_PROVIDERS = ("bedrock", "anthropic")
_LISTED_FIELDS = ("owned_by", "root", "parent", "max_model_len", "context_window")
_RAW_COUNT_MIN = 1e5  # a parameter value above this is a raw count, not billions
_PINNED_BASES = ("env", "override")
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_TEXT_MAX = 160  # one string a source returned, as shown in ``detail``
_DETAIL_MAX = 1_000
_TEXT_FIELDS = ("model", "source", "size_class", "basis", "detail")
_SIZE_FIELDS = ("params_b", "active_params_b", "effective_params_b", "context")

#: Tests install an ``httpx.MockTransport`` here; ``None`` is the network.
_TRANSPORT: Optional[httpx.BaseTransport] = None
_MEMO: dict[str, _Memo] = {}
_MEMO_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCK = threading.Lock()


def _now() -> float:
    """Epoch seconds (one seam for the cache, the memo and the tests)."""
    return time.time()


def display_text(value: Any, limit: int = _TEXT_MAX) -> str:
    """``value`` as one line of display text: control and format characters
    (terminal escapes, bidi overrides) dropped, whitespace collapsed to one
    space, at most ``limit`` characters. Every string a source returns
    passes through it."""
    kept = (ch for ch in str(value) if ch.isspace() or _printable(ch))
    return " ".join("".join(kept).split())[:limit]


def _printable(ch: str) -> bool:
    return unicodedata.category(ch) not in _CONTROL_CATEGORIES


@dataclass(frozen=True)
class ModelScale:
    """The detected scale of one model on one source.

    ``size_class`` is ``small``, ``medium``, ``large`` or ``unknown``; sizes
    are billions of parameters; ``basis`` names the step that decided the
    class (``env``, ``override``, ``ollama``, ``ollama-moe-estimate``,
    ``registry``, ``endpoint``, ``family``, ``name``, ``hosted`` or
    ``unknown``); ``detail`` says what each step found or why it failed;
    ``detected_at`` is epoch seconds. ``provisional`` is true when the source
    did not answer (unreachable, timed out, still probing): the class is the
    name rule's for now, and a session asks again on its next turn.
    """

    model: str
    source: str
    size_class: str
    params_b: float | None = None
    active_params_b: float | None = None
    effective_params_b: float | None = None
    context: int | None = None
    basis: str = "unknown"
    detail: str = ""
    detected_at: float = 0.0
    provisional: bool = field(default=False, compare=False)

    @property
    def profile(self) -> str:
        """The retrieval profile this scale reads with (``unknown`` → medium)."""
        return profile_for_class(self.size_class)[0]

    def notice(self) -> str | None:
        """The one-per-session notice for an unknown scale, else ``None``."""
        if self.size_class != "unknown":
            return None
        return NOTICE.format(model=self.model, source=self.source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelScale:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class _Probe:
    """What one source said about a model (all optional)."""

    params_b: float | None = None
    active_b: float | None = None
    moe_estimate_b: float | None = None
    context: int | None = None
    names: tuple[str, ...] = ()
    basis: str = ""
    detail: tuple[str, ...] = ()
    found: bool = False  # the source answered about this model (cacheable)
    hosted: bool = False
    transient: bool = False  # no answer from the source (unreachable, timed out, pending)


@dataclass(frozen=True)
class _Reply:
    data: dict[str, Any] | None = None
    error: str | None = None
    reachable: bool = True
    status: int | None = None


@dataclass(frozen=True)
class _Memo:
    scale: ModelScale
    until: float  # epoch seconds


@dataclass
class _Target:
    model: str
    source: Source | None
    label: str
    notes: list[str] = field(default_factory=list)
    verify: bool = True  # r4's verify_ssl, for every probe
    #: The memo and cache identity when the label alone is ambiguous (a
    #: configured endpoint no registered source names): label + URL digest.
    identity: str = ""

    @property
    def key(self) -> str:
        return scale_key(self.model, self.identity or self.label)


# ------------------------------------------------------------------ paths ---


def _config_dir() -> Path:
    from .config import _resolve_config_dir

    return _resolve_config_dir()


def overrides_path() -> Path:
    """``<config>/model_scales.json`` (``$XDG_CONFIG_HOME/r4`` or ``~/.config/r4``)."""
    return _config_dir() / OVERRIDES_FILE


def cache_path() -> Path:
    """``<config>/cache/model_scale.json``."""
    return _config_dir() / CACHE_FILE


def scale_key(model: str, source: str | None = None) -> str:
    """``"<source>/<model>"``; ``"*/<model>"`` for any source."""
    return f"{source or '*'}/{model}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold ``path``'s read-modify-write against other threads and, where the
    OS offers ``flock``, other r4 processes (two shells running ``r4 model
    scale set``, the TUI bridge writing the cache beside the CLI): an
    advisory lock on a sidecar ``.<name>.lock`` file, released on close."""
    with _FILE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name(f".{path.name}.lock"), "a", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield


# -------------------------------------------------------------- overrides ---


def load_overrides() -> dict[str, dict[str, Any]]:
    """Every override entry, by ``"<source>/<model>"`` key."""
    data = _read_json(overrides_path())
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def set_override(
    model: str,
    size_class: str,
    *,
    source: str | None = None,
    params_b: float | None = None,
    active_params_b: float | None = None,
    note: str | None = None,
) -> str:
    """Pin ``model`` (on ``source``, else every source) to ``size_class``; the key."""
    if size_class not in SCALE_CLASSES:
        raise ValueError(f"size class must be one of {', '.join(SCALE_CLASSES)}")
    entry = {"size_class": size_class, "params_b": params_b}
    entry.update(active_params_b=active_params_b, note=note)
    key = scale_key(model, source)
    with _locked(overrides_path()):
        data = load_overrides()
        data[key] = {k: v for k, v in entry.items() if v is not None}
        _write_json(overrides_path(), data)
    return key


def unset_override(model: str, *, source: str | None = None) -> bool:
    """Remove the override for ``model`` (on ``source``); whether one existed."""
    key = scale_key(model, source)
    with _locked(overrides_path()):
        data = load_overrides()
        if data.pop(key, None) is None:
            return False
        _write_json(overrides_path(), data)
    return True


def load_cached_scales() -> dict[str, ModelScale]:
    """Every well-formed cached probe result (fresh or stale), by
    ``"<source>/<model>"``. A malformed row (a hand edit, another version's
    format) is skipped on its own and never hides the others."""
    result = {}
    for key, raw in _read_json(cache_path()).items():
        scale = _scale_from_raw(raw)
        if scale is not None:
            result[key] = scale
    return result


def _scale_from_raw(raw: Any) -> ModelScale | None:
    if not isinstance(raw, dict):
        return None
    try:
        scale = ModelScale.from_dict(raw)
    except (TypeError, ValueError, AttributeError):
        return None
    return scale if _well_formed(scale) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _well_formed(scale: ModelScale) -> bool:
    """Every field of the type a reader formats it as (a cache row is data)."""
    texts = all(isinstance(getattr(scale, name), str) for name in _TEXT_FIELDS)
    sizes = all(getattr(scale, n) is None or _is_number(getattr(scale, n)) for n in _SIZE_FIELDS)
    known = scale.size_class in (*SCALE_CLASSES, "unknown")
    return texts and sizes and known and _is_number(scale.detected_at)


def _env_scale(target: _Target) -> ModelScale | None:
    value = os.environ.get(SCALE_ENV, "").strip().lower()
    if value not in SCALE_CLASSES:
        return None
    detail = f"{SCALE_ENV}={value} (process override)"
    return _make(target, value, basis="env", detail=detail)


def _override_scale(target: _Target) -> ModelScale | None:
    overrides = load_overrides()
    for key in (scale_key(target.model, target.label), scale_key(target.model)):
        scale = _from_override(target, key, overrides.get(key))
        if scale is not None:
            return scale
    return None


def _from_override(target: _Target, key: str, entry: Any) -> ModelScale | None:
    if not isinstance(entry, dict):
        return None
    total = _params_b(entry.get("params_b"))
    active = _params_b(entry.get("active_params_b"))
    effective = effective_params_b(total, active)
    size = str(entry.get("size_class") or "").strip().lower()
    if size not in SCALE_CLASSES:
        size = size_class_for(effective)
    if size not in SCALE_CLASSES:
        return None
    note = f": {display_text(entry['note'])}" if entry.get("note") else ""
    detail = f"override {key} in {overrides_path()}{note}"
    sizes = {"params_b": total, "active_params_b": active, "effective_params_b": effective}
    return _make(target, size, basis="override", detail=detail, **sizes)


# ------------------------------------------------------------------ cache ---


def _read_cache(key: str) -> ModelScale | None:
    scale = load_cached_scales().get(key)
    if scale is None or _now() - scale.detected_at >= CACHE_TTL_S:
        return None
    return scale


def _write_cache(key: str, scale: ModelScale) -> None:
    """Add ``scale`` under ``key``; a malformed row already there is dropped,
    so a bad hand edit heals on the next write."""
    try:
        with _locked(cache_path()):
            rows = _read_json(cache_path()).items()
            data = {k: v for k, v in rows if _scale_from_raw(v) is not None}
            data[key] = scale.to_dict()
            _write_json(cache_path(), data)
    except OSError as exc:
        logger.debug("model scale cache not written: %s", type(exc).__name__)


def clear_scale_memo() -> None:
    """Forget every in-process detection (the disk cache stays)."""
    with _MEMO_LOCK:
        _MEMO.clear()


def _memo_get(key: str) -> ModelScale | None:
    memo = _MEMO.get(key)
    return memo.scale if memo is not None and _now() < memo.until else None


def _known(key: str) -> ModelScale | None:
    """A detection still in date: this process's memo, else the disk cache."""
    scale = _memo_get(key)
    if scale is None:
        scale = _read_cache(key)
        if scale is not None:
            _MEMO[key] = _Memo(scale, scale.detected_at + CACHE_TTL_S)
    return scale


def _remember(target: _Target, probe: _Probe) -> ModelScale:
    """Decide from ``probe`` and keep the answer: a source's answer for
    :data:`CACHE_TTL_S` (memo and disk), anything else in memory for
    :data:`FAILURE_TTL_S` only, so a source that was down is asked again."""
    scale = _decide(target, probe)
    ttl = CACHE_TTL_S if probe.found else FAILURE_TTL_S
    _MEMO[target.key] = _Memo(scale, _now() + ttl)
    if probe.found:
        _write_cache(target.key, scale)
    return scale


def _key_lock(key: str) -> threading.Lock:
    with _MEMO_LOCK:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


# ------------------------------------------------------------------- http ---


_TLS_FAILURE_MARKS = ("CERTIFICATE_VERIFY_FAILED", "certificate verify failed")


def _transport_failure(exc: httpx.TransportError) -> str:
    """Why a request never got a reply; a certificate failure names the fix."""
    if any(mark in str(exc) for mark in _TLS_FAILURE_MARKS):
        return "TLS certificate not verified (verify_ssl is on; a self-signed gateway needs it off)"
    return f"unreachable ({type(exc).__name__})"


def _http_json(method: str, url: str, *, verify: bool = True, **kwargs: Any) -> _Reply:
    """One bounded JSON request; never raises, never records headers.
    ``verify`` is r4's ``verify_ssl``."""
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_S, transport=_TRANSPORT, verify=verify) as http:
            response = http.request(method, url, **kwargs)
        if response.status_code >= 400:
            code = response.status_code
            return _Reply(error=f"HTTP {code}", status=code)
        data = response.json()
    except httpx.TimeoutException:
        return _Reply(error=f"timed out after {PROBE_TIMEOUT_S:g}s", reachable=False)
    except httpx.TransportError as exc:
        return _Reply(error=_transport_failure(exc), reachable=False)
    except Exception as exc:  # noqa: BLE001 - a probe never raises
        return _Reply(error=type(exc).__name__)
    if not isinstance(data, dict):
        return _Reply(error="not a JSON object")
    return _Reply(data=data)


def _endpoint_root(url: str | None) -> str:
    root = str(url or "").rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def _failed(step: str, reply: _Reply) -> _Probe:
    """A request that got no usable reply; no reply at all (unreachable,
    timed out) or a server error is transient: the source may answer later."""
    logger.debug("model scale probe %s: %s", step, reply.error)
    transient = not reply.reachable or (reply.status or 0) >= 500
    return _Probe(detail=(f"{step}: {reply.error}",), transient=transient)


def _params_b(value: Any) -> float | None:
    """Billions of parameters from a count, a number of billions or ``"70B"``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return model_param_billions(value)
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value) / 1e9 if value > _RAW_COUNT_MIN else float(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(entry: dict[str, Any], keys: tuple[str, ...], parse: Any) -> Any:
    return next((v for v in (parse(entry.get(k)) for k in keys) if v is not None), None)


# ----------------------------------------------------------------- probes ---


def _probe_ollama(base: str, model: str, verify: bool = True) -> _Probe:
    reply = _http_json("POST", f"{base}/api/show", verify=verify, json={"model": model})
    if reply.error:
        return _failed("ollama POST /api/show", reply)
    info = _mapping(reply.data.get("model_info"))
    details = _mapping(reply.data.get("details"))
    total = _params_b(info.get("general.parameter_count"))
    exact = "general.parameter_count" if total is not None else "details.parameter_size"
    if total is None:
        total = _params_b(details.get("parameter_size"))
    return _ollama_probe(info, total, exact)


def _ollama_probe(info: dict[str, Any], total: float | None, exact: str) -> _Probe:
    arch = str(info.get("general.architecture") or "")
    keys = ("general.size_label", "general.basename")
    names = tuple(display_text(info[k]) for k in keys if info.get(k))
    experts = _positive_int(info.get(f"{arch}.expert_count"))
    used = _positive_int(info.get(f"{arch}.expert_used_count"))
    estimate = total * used / experts if total and experts and used else None
    note = (
        f"ollama /api/show: {exact} {total:g}B"
        if total is not None
        else "ollama /api/show: no size"
    )
    return _Probe(
        params_b=total,
        active_b=active_param_billions(*names),
        moe_estimate_b=estimate,
        context=_positive_int(info.get(f"{arch}.context_length")),
        names=names,
        basis="ollama",
        detail=(note,),
        found=True,
    )


def _source_http(source: Source, client: Any) -> Any:
    """The HTTP core whose headers belong to ``source``.

    The client's own core only when ``source`` is the client's deployment;
    any other source gets a config of its own with only its own credential
    (onboarding plan S1, :func:`r4.source_context.config_for_source`).
    """
    from ._http import HTTPCore
    from .source_context import config_for_source, same_deployment

    config = getattr(client, "config", None)
    http = getattr(client, "platform_http", None)
    if http is not None and same_deployment(source.url, getattr(config, "gateway_url", None)):
        return http
    if config is not None and hasattr(config, "gateway_url"):
        return HTTPCore(config_for_source(source, config))
    from .model_routing import _source_probe_config

    return HTTPCore(_source_probe_config(source))


def _platform_headers(source: Source, client: Any) -> dict[str, str]:
    if source.api_key:
        return {"Authorization": f"Bearer {source.api_key}", "Accept": "application/json"}
    try:
        return dict(_source_http(source, client).build_headers())
    except Exception as exc:  # noqa: BLE001 - unauthenticated probe on failure
        logger.debug("platform probe headers unavailable: %s", type(exc).__name__)
        return {"Accept": "application/json"}


def _entry_ids(entry: dict[str, Any]) -> set[str]:
    ids = (entry.get(k) for k in ("name", "model_id", "huggingface_id"))
    return {str(i).lower() for i in ids if i}


def _registry_entry(models: Any, model: str) -> dict[str, Any] | None:
    listed = [e for e in models if isinstance(e, dict)] if isinstance(models, list) else []
    return next((e for e in listed if model.lower() in _entry_ids(e)), None)


def _model_info(entry: dict[str, Any]) -> Any:
    from .models import ModelInfo

    try:
        return ModelInfo.from_dict(entry)
    except Exception:  # noqa: BLE001 - a partial entry still has raw fields
        return None


#: Where a platform source's model registry answers: through the gateway,
#: else (a source pointing straight at UMRS, where ``/gw`` is a 404) UMRS's own.
_REGISTRY_PATHS = ("/gw/models", "/umrs/models")


def _registry_reply(source: Source, headers: dict[str, str], verify: bool) -> tuple[str, _Reply]:
    """``(path, reply)`` of the first registry path that is not a 404."""
    base = str(source.url).rstrip("/")
    for path in _REGISTRY_PATHS:
        reply = _http_json(
            "GET", f"{base}{path}", verify=verify, headers=headers, params={"limit": 500}
        )
        if reply.status != 404:
            return path, reply
    return path, reply


def _probe_platform(model: str, source: Source, client: Any, verify: bool = True) -> _Probe:
    headers = _platform_headers(source, client)
    path, reply = _registry_reply(source, headers, verify)
    if reply.error:
        return _failed(f"registry GET {path}", reply)
    entry = _registry_entry(reply.data.get("models"), model)
    if entry is None:
        return _Probe(detail=(f"registry {path}: model not registered",))
    info = _model_info(entry)
    caps = getattr(info, "capabilities", None)
    names = (entry.get("huggingface_id"), getattr(caps, "model_family", None))
    params = _first(entry, _PARAM_FIELDS, _params_b) or _params_b(getattr(info, "params_b", None))
    shown = display_text(entry.get("name") or model)
    return _Probe(
        params_b=params,
        active_b=_first(entry, _ACTIVE_FIELDS, _params_b),
        context=resolve_max_context(info) if info is not None else None,
        names=tuple(display_text(n) for n in names if n),
        basis="registry",
        detail=(f"registry {path}: {shown} (hf {display_text(entry.get('huggingface_id'))})",),
        found=True,
    )


def _listed_entry(entries: Any, model: str) -> dict[str, Any] | None:
    listed = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    exact = next((e for e in listed if e.get("id") == model), None)
    return exact or next((e for e in listed if str(e.get("id")).lower() == model.lower()), None)


def _probe_openai(base: str, model: str, source: Source, verify: bool = True) -> _Probe:
    headers = {"Authorization": f"Bearer {source.api_key}"} if source.api_key else {}
    reply = _http_json("GET", f"{base}/v1/models", verify=verify, headers=headers)
    if reply.error:
        return _failed("GET /v1/models", reply)
    entry = _listed_entry(reply.data.get("data"), model)
    if entry is None:
        return _Probe(detail=("/v1/models: model not listed",))
    names = tuple(display_text(entry[k]) for k in ("root", "parent") if entry.get(k))
    found = ", ".join(f"{k} {display_text(entry[k])}" for k in _LISTED_FIELDS if entry.get(k))
    return _Probe(
        params_b=_first(entry, _PARAM_FIELDS, _params_b),
        active_b=_first(entry, _ACTIVE_FIELDS, _params_b),
        context=_first(entry, _CONTEXT_FIELDS, _positive_int),
        names=names,
        basis="endpoint",
        detail=(f"/v1/models: listed ({found or 'id only'})",),
        found=True,
    )


def _probe_public(model: str, source: Source, verify: bool = True) -> _Probe:
    base = _endpoint_root(source.url)
    if not base:
        return _Probe(detail=("public source has no URL",))
    tags = _http_json("GET", f"{base}/api/tags", verify=verify)
    if not tags.reachable:
        return _failed("GET /api/tags", tags)
    if isinstance((tags.data or {}).get("models"), list):
        probe = _probe_ollama(base, model, verify)
        return replace(probe, detail=("GET /api/tags: an ollama server", *probe.detail))
    return _probe_openai(base, model, source, verify)


def _probe_source(target: _Target, client: Any) -> _Probe:
    model, source = target.model, target.source
    if source is None:
        return _Probe(detail=("no registered source",))
    provider = (source.provider or "").lower()
    if source.kind == "platform":
        return _probe_platform(model, source, client, target.verify)
    if provider in _HOSTED_PROVIDERS:
        return _Probe(detail=(f"{provider}: hosted frontier models",), hosted=True)
    if provider == "ollama":
        return _probe_ollama(_endpoint_root(source.url), model, target.verify)
    return _probe_public(model, source, target.verify)


class _ProbeRun:
    """One probe on a daemon thread, waited for at most :data:`PROBE_TIMEOUT_S`.

    httpx bounds each phase of each request, not the request, and a public
    source takes two requests: a reply that trickles a byte at a time (or a
    slow DNS lookup) would hold the turn for a minute. The caller waits the
    bound and no longer; a probe still running then is reported as pending
    (the name rule answers meanwhile, provisionally), and when it ends its
    answer is remembered for the next detection.
    """

    def __init__(self, target: _Target, client: Any) -> None:
        self._target, self._client = target, client
        self._lock = threading.Lock()
        self._probe: _Probe | None = None
        self._abandoned = False

    def _run(self) -> None:
        try:
            probe = _probe_source(self._target, self._client)
        except Exception as exc:  # noqa: BLE001 - a probe never raises
            probe = _Probe(detail=(f"probe failed ({type(exc).__name__})",), transient=True)
        with self._lock:
            self._probe, late = probe, self._abandoned
        if late:
            _remember(self._target, probe)

    def scale(self) -> ModelScale:
        worker = threading.Thread(target=self._run, name=PROBE_THREAD_NAME, daemon=True)
        worker.start()
        worker.join(PROBE_TIMEOUT_S)
        with self._lock:  # a late answer waits until the pending one is kept
            if self._probe is not None:
                return _remember(self._target, self._probe)
            self._abandoned = True
            pending = f"probe still running after {PROBE_TIMEOUT_S:g}s (name rule meanwhile)"
            return _remember(self._target, _Probe(detail=(pending,), transient=True))


# ----------------------------------------------------------------- decide ---


def _same_size(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= SIZE_AGREEMENT * b


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _make(target: _Target, size_class: str, *, basis: str, detail: str, **sizes: Any) -> ModelScale:
    sizes = {k: _round(v) for k, v in sizes.items()}
    told = "; ".join(part for part in (*target.notes, detail) if part)
    return ModelScale(
        model=target.model,
        source=target.label,
        size_class=size_class,
        basis=basis,
        detail=display_text(told, _DETAIL_MAX),
        detected_at=_now(),
        **sizes,
    )


def _active_for(probe: _Probe, by_name: NameScale) -> tuple[float | None, str]:
    if probe.active_b is not None:
        return probe.active_b, probe.basis
    if by_name.active_b is not None and _same_size(probe.params_b, by_name.total_b):
        return by_name.active_b, probe.basis
    if probe.moe_estimate_b is not None:
        return probe.moe_estimate_b, f"{probe.basis}-moe-estimate"
    return None, probe.basis


def _decide(target: _Target, probe: _Probe) -> ModelScale:
    """The scale ``probe`` and the name rule give; provisional when the
    source never answered."""
    scale = _decide_class(target, probe)
    return replace(scale, provisional=True) if probe.transient else scale


def _decide_class(target: _Target, probe: _Probe) -> ModelScale:
    by_name = scale_from_name(target.model, *probe.names)
    context = probe.context or known_context_length(target.model, *probe.names)
    detail = "; ".join(probe.detail)
    if probe.params_b is not None:
        active, basis = _active_for(probe, by_name)
        effective = effective_params_b(probe.params_b, active)
        sizes = {"params_b": probe.params_b, "active_params_b": active}
        sizes.update(effective_params_b=effective, context=context)
        return _make(target, size_class_for(effective), basis=basis, detail=detail, **sizes)
    if by_name.size_class != "unknown":
        sizes = {"params_b": by_name.total_b, "active_params_b": by_name.active_b}
        sizes.update(effective_params_b=by_name.effective_b, context=context)
        return _make(target, by_name.size_class, basis=by_name.basis, detail=detail, **sizes)
    if probe.hosted:
        return _make(target, "large", basis="hosted", detail=detail, context=context)
    return _make(target, "unknown", basis="unknown", detail=detail, context=context)


# ----------------------------------------------------------------- target ---


def _registered_by_url(url: str | None, kind: str) -> Source | None:
    wanted = str(url or "").rstrip("/")
    if not wanted:
        return None
    sources = get_source_store().list()
    return next((s for s in sources if s.kind == kind and s.url.rstrip("/") == wanted), None)


def _config_source(config: Any, kind: str) -> tuple[Source | None, bool]:
    """The configured endpoint of ``kind`` as a source, and whether a
    registered source names it (else it is labelled ``public``/``platform``)."""
    if kind == "public":
        url = getattr(config, "endpoint_url", None) or ""
        provider = getattr(config, "endpoint_provider", None)
        key = getattr(config, "endpoint_api_key", None)
        found = _registered_by_url(url, "public")
        made = Source(name="public", kind="public", url=url, provider=provider, api_key=key)
        return (found, True) if found else (made, False)
    url = getattr(config, "gateway_url", None)
    if not url:
        return None, False
    found = _registered_by_url(url, "platform")
    made = Source("platform", "platform", url, api_key=getattr(config, "api_key", None))
    return (found, True) if found else (made, False)


def _active_config(client: Any) -> Any:
    config = getattr(client, "config", None)
    if config is not None:
        return config
    from .config import get_config

    return get_config()


def _url_identity(source: Source) -> str:
    """``<label>@<digest of the URL>``: the cache identity of an endpoint no
    registered source names, so two deployments serving one alias (a
    gateway's ``chat-default``, a LiteLLM ``default``) never share an entry."""
    url = str(source.url or "").rstrip("/")
    return f"{source.name}@{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def _routed_target(model: str, client: Any, verify: bool) -> _Target:
    """The source the chat call sends a bare ``model`` to, resolved by the
    client's own resolver (:func:`resolve_model_route`, as
    ``Client._resolve_gated_route`` calls it): the route map, the configured
    endpoint, a registered public source that serves the model (the
    auto-fallback), else the platform gateway."""
    config = _active_config(client)
    try:
        route = resolve_model_route(config, model)
    except Exception as exc:  # noqa: BLE001 - the chat call fails the same way
        note = f"route not resolved ({type(exc).__name__})"
        return _Target(model, None, NO_SOURCE, [note], verify)
    named = get_source_store().get(route.source_name) if route.source_name else None
    if named is not None:
        return _Target(model, named, named.name, verify=verify)
    source, registered = _config_source(config, route.kind)
    if source is None:
        return _Target(model, None, NO_SOURCE, verify=verify)
    identity = "" if registered else _url_identity(source)
    return _Target(model, source, source.name, verify=verify, identity=identity)


def _verify_ssl(client: Any) -> bool:
    """r4's ``verify_ssl`` from the client's (else the active) config, as
    every r4 request reads it (``HTTPCore``); on by default."""
    try:
        return bool(getattr(_active_config(client), "verify_ssl", True))
    except Exception:  # noqa: BLE001 - no config: verify, the safe default
        return True


def _resolve_target(model: str, source: Source | str | None, client: Any) -> _Target:
    ref = parse_model_ref(model)
    verify = _verify_ssl(client)
    if isinstance(source, Source):
        return _Target(ref.model, source, source.name, verify=verify)
    name = source or ref.source
    if name:
        found = get_source_store().get(name)
        notes = [] if found else [f"source {name!r} is not registered"]
        return _Target(ref.model, found, name, notes, verify)
    return _routed_target(ref.model, client, verify)


# ------------------------------------------------------------------ entry ---


def _detect(target: _Target, client: Any, refresh: bool) -> ModelScale:
    forced = _env_scale(target) or _override_scale(target)
    if forced is not None:
        return forced
    with _key_lock(target.key):
        known = None if refresh else _known(target.key)
        return known if known is not None else _ProbeRun(target, client).scale()


def detect_model_scale(
    model: str,
    *,
    source: Source | str | None = None,
    client: Client | None = None,
    refresh: bool = False,
) -> ModelScale:
    """The scale of ``model`` as served by ``source`` (see the module docstring).

    ``source`` is a :class:`~r4.source.Source`, a registered source name, or
    ``None``: then ``model@source``, else the route the client's (or the
    active) config gives the model. ``client`` supplies that config and the
    platform credentials. Never raises: on an internal failure the name rule
    still answers.
    """
    try:
        return _detect(_resolve_target(model, source, client), client, refresh)
    except Exception as exc:  # noqa: BLE001 - detection never breaks a turn
        logger.debug("model scale detection failed: %s", type(exc).__name__)
        target = _Target(parse_model_ref(str(model or "")).model, None, NO_SOURCE)
        failed = _Probe(detail=(f"detection failed ({type(exc).__name__})",), transient=True)
        return _decide(target, failed)


# --------------------------------------------------------- session glue ---


def rag_profile_name(scale: ModelScale) -> str:
    """The retrieval profile for ``scale``: ``R4_RAG_PROFILE`` wins when it names one."""
    return profile_for_class(scale.size_class, os.environ.get(RAG_PROFILE_ENV))[0]


def retrieve_reader_fields(model: str | None, *, client: Any = None) -> dict[str, str | None]:
    """``profile`` and ``reader_model`` for a retrieve whose passages ``model``
    reads (D-14: "sends the resolved profile with reader_model on every
    retrieve"): the model's detected scale, ``R4_RAG_PROFILE`` overriding it.
    No model sends neither, and the site's default applies."""
    reader = (model or "").strip()
    if not reader:
        return {"profile": None, "reader_model": None}
    scale = detect_model_scale(reader, client=client)
    return {"profile": rag_profile_name(scale), "reader_model": reader}


def configured_reader_fields(model: str | None, client: Any) -> dict[str, str]:
    """:func:`retrieve_reader_fields` for a consumer whose chat call reads the
    passages with ``model``, else with ``client``'s configured model (what
    ``Client.chat``/``chat_completion`` use for ``model=None``): the research
    loop, ``search_agentic``'s RAG leg, a workflow RAG step (r4-scale-router-10).
    Only the fields that are set, to spread into ``rag.retrieve(...)``; ``{}``
    without a model or on any failure (the site's default then applies)."""
    reader = model or getattr(getattr(client, "config", None), "model", None)
    if not isinstance(reader, str):
        return {}
    try:
        found = retrieve_reader_fields(reader, client=client)
    except Exception as exc:  # noqa: BLE001 - sizing never costs the retrieve
        logger.debug("reader fields unavailable: %s", type(exc).__name__)
        return {}
    return {key: value for key, value in found.items() if value}


def _is_session_scale(cached: Any, model: str, source: str | None) -> bool:
    same_source = source is None or getattr(cached, "source", None) == source
    return isinstance(cached, ModelScale) and cached.model == model and same_source


def _pinned_now(cached: ModelScale) -> ModelScale | None:
    """The process or file pin for the session's model now: ``r4 model scale
    set`` mid-session reaches the router as it reaches ``search_docs``."""
    target = _Target(cached.model, None, cached.source)
    return _env_scale(target) or _override_scale(target)


def session_model_scale(session: Any, model: str) -> ModelScale:
    """``model``'s scale for ``session``, detected once per session and model.

    Pins are read every time (a pin set or removed mid-session applies at
    once, as it does for ``search_docs``), and a provisional scale (the
    source did not answer) is asked again, from this process's memo, so the
    router and ``search_docs`` always read the same class."""
    ref = parse_model_ref(model)
    cached = getattr(session, SESSION_SCALE_ATTR, None)
    if _is_session_scale(cached, ref.model, ref.source):
        pinned = _pinned_now(cached)
        if pinned is not None:
            return pinned
        if cached.basis not in _PINNED_BASES and not cached.provisional:
            return cached
    scale = detect_model_scale(model, client=getattr(session, "client", None))
    try:
        setattr(session, SESSION_SCALE_ATTR, scale)
    except Exception:  # noqa: BLE001 - a frozen session just re-detects (memoised)
        pass
    return scale


def take_session_notice(session: Any, scale: ModelScale) -> str | None:
    """The unknown-scale notice, once per session (``None`` afterwards)."""
    notice = scale.notice()
    shown = getattr(session, SESSION_NOTICE_ATTR, None)
    if notice is None or shown == notice:
        return None
    try:
        setattr(session, SESSION_NOTICE_ATTR, notice)
    except Exception:  # noqa: BLE001 - cannot remember: show it this once
        pass
    return notice
