"""My HPCs: one inventory of every HPC site the user can reach (multi-HPC plan S2).

One r4 process holds several platform sources, one per gateway (site). This
module probes each of them in parallel, bounded in time, and returns one
:class:`SiteRow` per site:

* readiness of the gateway (``healthy``);
* ``GET {prefix}/sites/me`` — the site's declared HPC access mode, the
  caller's own account row there, and the staged-lane/GPU capabilities.
  A gateway without the route (404, server not yet deployed) gives a row
  marked ``unknown``, not an error;
* an unreachable site is named with its error; the other rows are unaffected.

Rows are cached per source (and credential) for :data:`SITES_TTL_SEC`, so
the agent's ``<hpc_sites>`` block and the ``site=`` guard
(:mod:`r4.companion.site_routing`) do not probe every gateway on every turn.
A row that failed (unreachable, timed out, refused) is kept only
:data:`ERROR_TTL_SEC`, so one blip does not refuse a site for 15 minutes.

Everything a gateway reports is untrusted text that reaches the agent's
system prompt: names and modes are kept only when they are plain tokens
(:func:`safe_token`), and error text loses control characters and angle
brackets (2026-10-24 review: a gateway could close ``</hpc_sites>``).

A site whose access mode is not declared (``undeclared``/``unknown``) is shown
but is not selectable: the agent may not route work there from another site
(plan §3.2, the self-enforcing guard; it switches itself on per site as the
site declares its mode, S1b).

The client holds no job store and no scheduler here (AGENTS.md rule 10): this
is a read-only view, rebuilt from the sites themselves.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

#: How long a probed row stays fresh (the agent's block uses the same TTL).
SITES_TTL_SEC = 900.0
#: How long a failed row (unreachable, timed out, refused) stays cached.
ERROR_TTL_SEC = 30.0
#: Per-site probe budget; the whole inventory returns within about this.
PROBE_TIMEOUT_SEC = 5.0
#: The agent's ``<hpc_sites>`` block waits at most about this for the probes
#: (a down source must not stall the first turn by the full probe budget).
PROMPT_PROBE_TIMEOUT_SEC = 2.0

UNDECLARED = "undeclared"
UNKNOWN = "unknown"
HOME_NAME = "home"


@dataclass
class SiteRow:
    """One HPC site as the caller sees it."""

    site: str
    source: str
    url: str = ""
    home: bool = False
    healthy: Optional[bool] = None
    scheduler: Optional[str] = None
    access_mode: str = UNKNOWN
    modes_allowed: list[str] = field(default_factory=list)
    operator: Optional[str] = None
    account: str = UNKNOWN
    slurm_account: Optional[bool] = None
    relay: Optional[str] = None  # client_relay sites (AM A-6) are not built yet
    staged_lane: Optional[bool] = None
    gpu: Optional[bool] = None
    error: Optional[str] = None

    @property
    def declared(self) -> bool:
        return self.access_mode not in (UNDECLARED, UNKNOWN)

    def refusal(self) -> Optional[str]:
        """Why the agent may not route work here from another site (None = may).

        Eligible needs an active account row (plan §3.3): ``unknown`` (the
        site could not read its user store) is refused too, never assumed.
        """
        if self.error:
            return f"{'error' if self.healthy else 'unreachable'}: {self.error}"
        if not self.declared:
            return f"access mode {self.access_mode}"
        if self.account != "present":
            return f"no active account here ({self.account})"
        return None

    @property
    def selectable(self) -> bool:
        if self.home:
            return not self.error
        return self.refusal() is None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["selectable"] = self.selectable
        out["not_selectable_reason"] = None if self.selectable else self.refusal()
        return out


_TOKEN = re.compile(r"[A-Za-z0-9._-]{1,64}")
_UNSAFE_TEXT = re.compile(r"[^\x20-\x7e]|[<>]")


def safe_token(value: Any) -> Optional[str]:
    """``value`` when it is a plain name (``[A-Za-z0-9._-]{1,64}``), else None."""
    text = str(value).strip() if isinstance(value, (str, int)) else ""
    return text if _TOKEN.fullmatch(text) else None


def safe_text(value: Any, limit: int = 200) -> str:
    """Gateway-supplied free text for the prompt: printable ASCII, no ``<>``."""
    return _UNSAFE_TEXT.sub(" ", str(value))[:limit].strip()


def split_handle(value: Any) -> tuple[str, Optional[str]]:
    """``"run-42@bdrt"`` → ``("run-42", "bdrt")``; a bare id → ``(id, None)``."""
    text = str(value or "").strip()
    ident, sep, site = text.rpartition("@")
    if not sep or not ident or not site:
        return text, None
    return ident, site


# ── probing one site ─────────────────────────────────────────────────────────


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return safe_text(text) or type(exc).__name__


def _status_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _section(me: dict[str, Any], key: str) -> dict[str, Any]:
    value = me.get(key)
    return value if isinstance(value, dict) else {}


def _apply_access(row: SiteRow, access: dict[str, Any]) -> None:
    """A mode that is no plain token reads as unknown (not selectable)."""
    row.access_mode = safe_token(access.get("mode")) or UNKNOWN
    allowed = access.get("modes_allowed")
    modes = allowed if isinstance(allowed, list) else []
    row.modes_allowed = [m for m in (safe_token(v) for v in modes) if m]
    operator = access.get("operator")
    row.operator = safe_text(operator, 64) or None if operator else None


def _apply_me(row: SiteRow, me: dict[str, Any]) -> None:
    """Copy a ``sites/me`` answer into the row (missing keys stay unknown)."""
    account = _section(me, "account")
    caps = _section(me, "capabilities")
    row.site = safe_token(me.get("site")) or row.site
    _apply_access(row, _section(me, "access"))
    row.account = safe_token(account.get("status")) or UNKNOWN
    row.slurm_account = _flag(account.get("slurm_account"))
    row.scheduler = safe_token(caps.get("scheduler_kind")) or row.scheduler
    row.staged_lane = _flag(caps.get("staged_lane"))
    row.gpu = _flag(caps.get("gpu"))


def _flag(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _fill_capabilities(row: SiteRow, client: Any) -> None:
    """``sites/me`` absent: keep what the open ``/health`` still says."""
    caps = client.site_capabilities() or {}
    row.scheduler = safe_token(caps.get("scheduler_kind")) or row.scheduler
    row.site = safe_token(caps.get("site_name")) or row.site


def _fill_me(row: SiteRow, client: Any, timeout: float) -> None:
    try:
        me = client.site_me(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a site's refusal is that row's fact
        if _status_code(exc) != 404:
            row.error = _short_error(exc)
            return
        _fill_capabilities(row, client)
        return
    if isinstance(me, dict):
        _apply_me(row, me)


def _client_for(source: Any, base: Any) -> Any:
    from .client import Client
    from .source_context import config_for_source

    return Client(config=config_for_source(source, base))


def probe_site(
    source: Any, base: Any, *, home: bool = False, timeout: float = PROBE_TIMEOUT_SEC
) -> SiteRow:
    """Probe one platform source: readiness, then ``sites/me``."""
    row = SiteRow(site=source.name, source=source.name, url=str(source.url or ""), home=home)
    try:
        client = _client_for(source, base)
        row.healthy = bool(client.readiness().get("ready"))
    except Exception as exc:  # noqa: BLE001 — unreachable is a row, not a crash
        row.healthy = False
        row.error = _short_error(exc)
        return row
    _fill_me(row, client, timeout)
    return row


# ── which sites ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _HomeSource:
    """The session's own deployment when no registered source names it."""

    name: str
    url: str
    kind: str = "platform"
    api_key: Optional[str] = None
    project: Optional[str] = None


def _base_config(base: Any = None) -> Any:
    if base is not None:
        return base
    from .config import get_config

    return get_config()


def platform_sources() -> list[Any]:
    """Registered platform sources with a URL (public model sources are not sites)."""
    try:
        from .source import get_source_store

        return [s for s in get_source_store().list() if s.kind == "platform" and s.url]
    except Exception:  # noqa: BLE001 — an unreadable registry is no sites
        return []


def is_home(source: Any, base: Any) -> bool:
    from .source_context import same_deployment

    return bool(getattr(base, "gateway_url", None)) and same_deployment(
        source.url, base.gateway_url
    )


def site_sources(base: Any = None) -> list[tuple[Any, bool]]:
    """``(source, is_home)`` for every site: registered platform sources, plus
    the session's own gateway when no registered source points at it."""
    base = _base_config(base)
    pairs = [(s, is_home(s, base)) for s in platform_sources()]
    gateway = str(getattr(base, "gateway_url", "") or "")
    if gateway and not any(home for _, home in pairs):
        pairs.insert(0, (_HomeSource(name=HOME_NAME, url=gateway), True))
    return pairs


# ── cache + parallel probe ───────────────────────────────────────────────────

_CACHE: dict[tuple[str, str, str], tuple[float, SiteRow]] = {}
_LOCK = threading.Lock()
_clock: Callable[[], float] = time.monotonic


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _cache_key(source: Any) -> tuple[str, str, str]:
    """Name, URL and a digest of the credential: a fixed key re-probes."""
    from .source_context import credential_for

    try:
        key = str(credential_for(source) or "")
    except Exception:  # noqa: BLE001 — no readable key is its own cache line
        key = ""
    digest = hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""
    return (str(source.name), str(source.url), digest)


def _cached(source: Any, ttl: float) -> Optional[SiteRow]:
    with _LOCK:
        hit = _CACHE.get(_cache_key(source))
    if hit is None:
        return None
    limit = min(ttl, ERROR_TTL_SEC) if hit[1].error else ttl
    return None if _clock() - hit[0] >= limit else hit[1]


def _store(source: Any, row: SiteRow) -> None:
    with _LOCK:
        _CACHE[_cache_key(source)] = (_clock(), row)


def cache_has_errors() -> bool:
    """Whether any cached row is a failed one (the prompt refreshes sooner)."""
    with _LOCK:
        return any(row.error for _at, row in _CACHE.values())


def _timed_out(source: Any, home: bool, timeout: float) -> SiteRow:
    return SiteRow(
        site=source.name,
        source=source.name,
        url=str(source.url or ""),
        home=home,
        healthy=False,
        error=f"no answer within {timeout:g} s",
    )


def _probe_many(pairs: list[tuple[Any, bool]], base: Any, timeout: float) -> list[SiteRow]:
    """Probe in parallel; whatever has not answered by the deadline is a
    timed-out row (the stragglers finish in the background, unread)."""
    pool = ThreadPoolExecutor(max_workers=min(len(pairs), 8), thread_name_prefix="r4-sites")
    futures = [pool.submit(probe_site, s, base, home=h, timeout=timeout) for s, h in pairs]
    done, _ = wait(futures, timeout=timeout + 0.5)
    pool.shutdown(wait=False, cancel_futures=True)
    return [
        fut.result() if fut in done else _timed_out(src, home, timeout)
        for fut, (src, home) in zip(futures, pairs)
    ]


def inventory(
    base: Any = None,
    *,
    refresh: bool = False,
    ttl: float = SITES_TTL_SEC,
    timeout: float = PROBE_TIMEOUT_SEC,
) -> list[SiteRow]:
    """Every site's row, fresh ones from the cache, stale ones probed together."""
    base = _base_config(base)
    pairs = site_sources(base)
    rows: dict[int, SiteRow] = {}
    stale: list[tuple[int, tuple[Any, bool]]] = []
    for idx, pair in enumerate(pairs):
        hit = None if refresh else _cached(pair[0], ttl)
        if hit is None:
            stale.append((idx, pair))
        else:
            rows[idx] = hit
    if stale:
        probed = _probe_many([pair for _, pair in stale], base, timeout)
        for (idx, pair), row in zip(stale, probed):
            _store(pair[0], row)
            rows[idx] = row
    return [rows[idx] for idx in range(len(pairs))]


def row_for(source: Any, base: Any = None, *, ttl: float = SITES_TTL_SEC) -> SiteRow:
    """One site's row (cached, else probed alone)."""
    base = _base_config(base)
    hit = _cached(source, ttl)
    if hit is not None:
        return hit
    row = _probe_many([(source, is_home(source, base))], base, PROBE_TIMEOUT_SEC)[0]
    _store(source, row)
    return row


# ── rendering ────────────────────────────────────────────────────────────────


def _yes_no(value: Optional[bool]) -> str:
    return "?" if value is None else ("yes" if value else "no")


def describe(row: SiteRow) -> str:
    """One line per site, the same for the CLI table and the agent block."""
    name = f"{row.source} (home)" if row.home else row.source
    if row.error and not row.healthy:
        return f"- {name}: unreachable: {row.error}; not selectable"
    state = "healthy" if row.healthy else "not ready"
    parts = [
        state,
        f"server={row.site}",
        f"mode={row.access_mode}",
        f"account={row.account}",
        f"scheduler={row.scheduler or '?'}",
        f"staged_lane={_yes_no(row.staged_lane)}",
        f"gpu={_yes_no(row.gpu)}",
    ]
    if row.error:
        parts.append(f"error={row.error}")
    tail = "" if row.selectable else f"; NOT SELECTABLE ({row.refusal()})"
    return f"- {name}: " + ", ".join(parts) + tail


def prompt_block(base: Any = None, *, timeout: float = PROMPT_PROBE_TIMEOUT_SEC) -> str:
    """The ``<hpc_sites>`` text for the agent, or "" with fewer than two sites.

    A one-site session needs no routing; it keeps its prompt unchanged. The
    probes get ``timeout`` (not the full probe budget): a site that has not
    answered by then is listed as unreachable and re-probed soon.
    """
    base = _base_config(base)
    if len(site_sources(base)) < 2:
        return ""
    rows = inventory(base, timeout=timeout)
    lines = [
        "HPC sites this session can address with site=<source name> on workflow_*, "
        "job_*, file_*_platform, endpoint_* and platform_health (no site = home). "
        "Start or change work only at selectable sites; never move work to a site "
        "the user did not name without asking. MCP servers are not a site route: "
        "never use them to reach a site that is refused or not selectable. "
        "Refresh with hpc_sites(refresh=true).",
        *(describe(r) for r in rows),
    ]
    return "\n".join(lines)


class SitesPromptCache:
    """The agent's ``<hpc_sites>`` text, built once per session and kept for
    ``ttl`` seconds (not rebuilt every turn: that would probe every gateway
    per message and break prompt caching)."""

    def __init__(self, ttl: float = SITES_TTL_SEC) -> None:
        self._ttl = ttl
        self._text: Optional[str] = None
        self._at = 0.0
        self._degraded = False

    def text(self, base: Any = None) -> str:
        now = _clock()
        ttl = min(self._ttl, ERROR_TTL_SEC) if self._degraded else self._ttl
        if self._text is not None and now - self._at < ttl:
            return self._text
        try:
            self._text = prompt_block(base)
        except Exception:  # noqa: BLE001 — a context block is best-effort
            self._text = ""
        # A row that failed is re-probed soon; the same text keeps the
        # prompt (and its cache) unchanged while a site stays down.
        self._degraded = cache_has_errors()
        self._at = now
        return self._text
