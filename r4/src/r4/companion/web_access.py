"""The session's web access: the user's choice and its local guard (ADR-099 D-2, D-9).

The user chooses the agent's web access when a session opens, local or
remote: ``none`` (off), ``allowlist`` (with domains) or ``public``. The
choice lives in session metadata as ``web_access`` and travels with the
session, so the remote worker restores the same choice the local session had.

Locally the companion's ``web_fetch`` and ``web_search`` executors check it
before each call (:func:`guarded_web_fetch`, :func:`guarded_web_search`).
Domains follow squid ``dstdomain`` semantics, the same rule squid enforces in
the remote sandbox: ``arxiv.org`` is that host only, ``.arxiv.org`` is the
host and every subdomain. Shell, Python and MCP tools are not filtered here:
with the local tool sandbox on (ADR-101 D-6) their network is isolated, and
the sandbox's egress proxy applies this same choice (:func:`host_allowed`) to
the shell and Python; local MCP servers follow their own sandbox entry.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping, Sequence

WEB_MODES: tuple[str, ...] = ("none", "allowlist", "public")
#: Where a choice came from (``WebAccess.source``): ``session`` is the
#: session's own earlier choice reused by a start, ``server`` the choice the
#: remote sandbox was started with (the worker's restore).
WEB_SOURCES: tuple[str, ...] = (
    "prompt",
    "flag",
    "env",
    "remembered",
    "default_off",
    "session",
    "server",
)
MAX_DOMAINS = 64
#: The env var for a non-interactive choice; same grammar as ``--web-access``.
WEB_ACCESS_ENV = "R4_WEB_ACCESS"
#: ``r4 --web`` hands its flag to the process it launches (the console's
#: bridge, ``r4 -p``) in this variable; the consumer removes it, so shells
#: the agent opens never inherit the flag (RS-1 fix W5).
WEB_ACCESS_LAUNCH_ENV = "R4_LAUNCH_WEB_ACCESS"

_DOMAIN_RE = re.compile(r"^\.?[a-z0-9-]+(\.[a-z0-9-]+)+$")
_FLAG_ALIASES = {"off": "none"}
_MODE_LABELS = {"none": "off", "allowlist": "allowlist", "public": "public"}

#: The way out every refusal names: ``/web`` exists in the console, ``--web``
#: on the command-line surfaces (``r4 -p``, ``r4 team plan``, ``r4 teamup``,
#: ``r4 recipe run``; RS-1 round-2 fix W2-2).
WAY_OUT = "the user can change it with /web in the console or r4 --web on the command line"
REFUSAL_OFF = f"web access is off for this session (the user's choice); do not retry; {WAY_OUT}"
REFUSAL_UNCHOSEN = (
    "web access has not been chosen for this session, so it is off; do not retry; "
    "the user can choose it with /web in the console or r4 --web off|allowlist|public "
    "on the command line"
)
#: A server run (the chat's ``team_plan_run(venue="server")``, a server plan
#: loop) started from a session without the user's choice (D-2: refused, the
#: same code UMRS answers; RS-1 round-2 fix W2-1).
WEB_CHOICE_REQUIRED = "sessionpolicy.web_choice_required"
REMOTE_UNCHOSEN = (
    f"{WEB_CHOICE_REQUIRED}: a server run needs the user's web-access choice and this "
    "session has none; do not retry; the user can choose it with /web in the console "
    "or r4 --web off|allowlist|public on the command line"
)

#: A remote start (``session.venue_start``) from a session without the
#: user's choice: refused on the client, never left to the server's
#: transition setting (RS-1 round 4, web R4-2).
REMOTE_START_UNCHOSEN = (
    f"{WEB_CHOICE_REQUIRED}: a remote start needs the user's web-access choice and this "
    "session has none (a local run's default off is no choice); choose it with /web "
    "in the console or r4 --web off|allowlist|public, then start again"
)


class WebAccessError(ValueError):
    """A web-access choice that cannot be used (unknown mode, bad domain)."""


@dataclass(frozen=True)
class WebAccess:
    """One session's web-access choice; ``mode == ""`` means not chosen yet."""

    mode: str
    domains: tuple[str, ...] = ()
    chosen_at: str = ""
    source: str = ""

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> "WebAccess":
        """The choice stored in session metadata; unchosen when absent or unreadable."""
        raw = metadata.get("web_access") if isinstance(metadata, Mapping) else None
        if not isinstance(raw, Mapping):
            return cls(mode="")
        mode = str(raw.get("mode") or "")
        if mode not in WEB_MODES:
            return cls(mode="")
        domains = tuple(d for d in _domain_tokens(raw.get("domains")) if _valid_domain(d))
        return cls(mode, domains, str(raw.get("chosen_at") or ""), str(raw.get("source") or ""))

    def to_metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "domains": list(self.domains),
            "chosen_at": self.chosen_at,
            "source": self.source,
        }

    @property
    def chosen(self) -> bool:
        return self.mode in WEB_MODES

    def label(self) -> str:
        """``off`` / ``allowlist (a, b)`` / ``public`` / ``not chosen``."""
        if not self.chosen:
            return "not chosen"
        if self.mode == "allowlist":
            return f"allowlist ({', '.join(self.domains)})"
        return _MODE_LABELS[self.mode]


def normalize_domain(value: str) -> str:
    """Squid ``dstdomain`` entry: lower-case, trimmed, one trailing dot stripped."""
    return str(value).strip().lower().rstrip(".")


def _valid_domain(domain: str) -> bool:
    return bool(_DOMAIN_RE.match(domain))


def _domain_tokens(value: Any) -> list[str]:
    """Domains from a comma/space separated string or a list of them, normalized."""
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    if not isinstance(value, str):
        return []
    return [d for d in (normalize_domain(v) for v in re.split(r"[,\s]+", value)) if d]


def parse_domains(value: Any) -> tuple[str, ...]:
    """Validated, de-duplicated domains; raises :class:`WebAccessError`."""
    out: list[str] = []
    for domain in _domain_tokens(value):
        if not _valid_domain(domain):
            raise WebAccessError(f"not a domain: {domain!r} (use e.g. arxiv.org or .arxiv.org)")
        if domain not in out:
            out.append(domain)
    if len(out) > MAX_DOMAINS:
        raise WebAccessError(f"at most {MAX_DOMAINS} domains, got {len(out)}")
    return tuple(out)


def parse_web_flag(value: str) -> str:
    """``off`` → ``none``; ``none|allowlist|public`` pass; anything else raises."""
    mode = str(value or "").strip().lower()
    mode = _FLAG_ALIASES.get(mode, mode)
    if mode not in WEB_MODES:
        raise WebAccessError(f"unknown web access {value!r}: use off, allowlist or public")
    return mode


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_web_access(mode: str, domains: Any = (), *, source: str, now: str = "") -> WebAccess:
    """A validated choice. ``allowlist`` needs at least one domain (D-2):
    squid would deny everything, which is ``none`` under another name."""
    resolved = parse_web_flag(mode)
    parsed = parse_domains(domains) if resolved == "allowlist" else ()
    if resolved == "allowlist" and not parsed:
        raise WebAccessError("allowlist web access needs at least one domain (--web-domain D)")
    return WebAccess(resolved, parsed, now or _now(), source)


def parse_web_access_spec(spec: str, *, source: str) -> WebAccess:
    """``MODE[:d1,d2]`` (the ``--web-access`` and ``R4_WEB_ACCESS`` grammar)."""
    mode, _, domains = str(spec or "").partition(":")
    return make_web_access(mode, domains, source=source)


def format_web_access_spec(mode: str, domains: Sequence[str] = ()) -> str:
    """Inverse of :func:`parse_web_access_spec`."""
    return f"{mode}:{','.join(domains)}" if domains else mode


def web_access_from_env(env: Mapping[str, str]) -> WebAccess | None:
    """The ``R4_WEB_ACCESS`` choice; None when unset (an invalid value raises)."""
    spec = str(env.get(WEB_ACCESS_ENV) or "").strip()
    if not spec:
        return None
    return parse_web_access_spec(spec, source="env")


def take_launch_web_access(env: MutableMapping[str, str] | None = None) -> WebAccess | None:
    """The ``r4 --web`` flag handed to this process (``R4_LAUNCH_WEB_ACCESS``),
    removed from ``env`` so nothing this process starts inherits it.

    None when unset or unreadable (``r4 --web`` validated it already).
    """
    target = os.environ if env is None else env
    spec = str(target.pop(WEB_ACCESS_LAUNCH_ENV, "") or "").strip()
    if not spec:
        return None
    try:
        return parse_web_access_spec(spec, source="flag")
    except WebAccessError:
        return None


def started_web_access(metadata: Mapping[str, Any]) -> WebAccess | None:
    """The web access a remote session's sandbox was started with: the
    server's runtime row the owner recorded (``venue_runtime.web_policy``).

    Frontend sessions only; None without a readable row. An allowlist row
    without its domains is unusable (squid would allow nothing): None.
    """
    if not isinstance(metadata, Mapping) or str(metadata.get("venue") or "") != "frontend":
        return None
    runtime = metadata.get("venue_runtime")
    if not isinstance(runtime, Mapping) or runtime.get("web_policy") not in WEB_MODES:
        return None
    try:
        return make_web_access(
            str(runtime["web_policy"]), runtime.get("web_allow_domains") or (), source="server"
        )
    except WebAccessError:
        return None


def session_web_access(metadata: Mapping[str, Any], env: Mapping[str, str]) -> WebAccess:
    """The choice a session's web tools follow: its own, else ``R4_WEB_ACCESS``,
    else (a remote session) the choice its sandbox was started with.

    The env fallback serves the surfaces that open sessions without asking
    (ACP editors) and the remote worker, whose container gets the server's
    start choice as ``R4_WEB_ACCESS``; the console, the Rich REPL and
    ``r4 -p`` store the choice in the metadata. Nothing usable: unchosen, which the
    tools treat as off and say so.
    """
    own = WebAccess.from_metadata(metadata)
    if own.chosen:
        return own
    try:
        from_env = web_access_from_env(env)
    except WebAccessError:
        from_env = None
    return from_env or started_web_access(metadata) or own


def remote_start_choice(access: WebAccess | None) -> WebAccess | None:
    """The user's choice a remote start may carry, or None (ADR-099 D-2).

    A local session's ``default_off`` is what the guard falls back to when
    nobody chose: a remote start without a choice is refused, so it is no
    choice here (RS-1 round-2 fix W2-4)."""
    if access is None or not access.chosen or access.source == "default_off":
        return None
    return access


def ambient_web_access() -> WebAccess:
    """The guard's choice for an agent built without a session's getter (a
    team agent, a recipe): ``R4_WEB_ACCESS``, else unchosen, which refuses.
    Never unguarded (RS-1 fix W3)."""
    return session_web_access({}, os.environ)


# ── the guard ───────────────────────────────────────────────────────────────


def host_allowed(host: str, domains: Sequence[str]) -> bool:
    """Squid ``dstdomain``: ``a.org`` matches that host, ``.a.org`` it and subdomains."""
    name = normalize_domain(host)
    if not name:
        return False
    for raw in domains:
        entry = normalize_domain(raw)
        if entry.startswith("."):
            if name == entry[1:] or name.endswith(entry):
                return True
        elif name == entry:
            return True
    return False


def _allowlist_refusal(access: WebAccess, url: str) -> str | None:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host and host_allowed(host, access.domains):
        return None
    listed = ", ".join(access.domains)
    return (
        f"web access for this session is limited to an allowlist ({listed}); "
        f"{host or url!r} is not on it; do not retry this host; {WAY_OUT}"
    )


def check_url(access: WebAccess, url: str) -> str | None:
    """The refusal text for fetching ``url`` under ``access``, or None when allowed."""
    if access.mode == "public":
        return None
    if access.mode == "allowlist":
        return _allowlist_refusal(access, url)
    return REFUSAL_OFF if access.mode == "none" else REFUSAL_UNCHOSEN


def check_search(access: WebAccess) -> str | None:
    """The refusal text for ``web_search`` under ``access``, or None when allowed.

    A search engine is a host the allowlist does not name, and its results
    point anywhere: search runs only with ``public`` access.
    """
    if access.mode == "public":
        return None
    if access.mode == "allowlist":
        return (
            "web search is not available with allowlist web access (a search reaches "
            f"hosts outside the list); use web_fetch on an allowed host; {WAY_OUT}"
        )
    return check_url(access, "")


class WebAccessRefused(urllib.error.URLError):
    """A redirect hop the session's web access does not allow."""


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect hop is checked before it is followed (D-9)."""

    def __init__(self, access: WebAccess) -> None:
        super().__init__()
        self._access = access

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        refusal = check_url(self._access, newurl)
        if refusal:
            raise WebAccessRefused(f"redirect to {newurl} refused: {refusal}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def checked_opener(access: WebAccess) -> urllib.request.OpenerDirector:
    """A urllib opener whose redirects are checked against ``access``."""
    return urllib.request.build_opener(_CheckedRedirects(access))


def _page_text(raw: str, max_chars: int) -> str:
    """The web_fetch text: scripts, styles and tags stripped, whitespace folded."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated at {max_chars} chars]"
    return text


def fetch_checked(access: WebAccess, url: str, max_chars: int = 50000) -> str:
    """``web_fetch`` through the checked opener; the tool's text or ``Error: …``."""
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"
    refusal = check_url(access, url)
    if refusal:
        return f"Error: {refusal}"
    request = urllib.request.Request(url, headers={"User-Agent": "r4-agent/1.0"}, method="GET")
    try:
        with checked_opener(access).open(request, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except WebAccessRefused as exc:
        return f"Error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — the tool reports every fetch failure
        return f"Error: Fetch failed: {exc}"
    return _page_text(raw, int(max_chars or 50000))


WebAccessGetter = Callable[[], WebAccess]
AgentToolRunner = Callable[[str, dict[str, Any]], str]


def guarded_web_fetch(access: WebAccess, args: dict[str, Any], run: AgentToolRunner) -> str:
    """The companion's ``web_fetch``: public runs the agent handler unchanged,
    any other mode refuses or fetches through the checked opener."""
    if access.mode == "public":
        return run("web_fetch", args)
    return fetch_checked(access, str(args.get("url") or ""), args.get("max_chars") or 50000)


def guarded_web_search(access: WebAccess, args: dict[str, Any], run: AgentToolRunner) -> str:
    """The companion's ``web_search``: only with public access."""
    refusal = check_search(access)
    return f"Error: {refusal}" if refusal else run("web_search", args)


#: Agent built-in tools the web-access guard wraps (D-9).
WEB_GUARDS: dict[str, Callable[[WebAccess, dict[str, Any], AgentToolRunner], str]] = {
    "web_fetch": guarded_web_fetch,
    "web_search": guarded_web_search,
}


# ── allowed choices under a server's caps (the preview's ``limits``) ───────


def allowed_modes(limits: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The modes the caps allow; all three without caps. A domain cap removes
    ``public`` (ADR-099 D-3)."""
    web = _web_caps(limits)
    allow = web.get("allow")
    modes = [m for m in WEB_MODES if not isinstance(allow, list) or m in allow]
    if web.get("domains"):
        modes = [m for m in modes if m != "public"]
    return tuple(modes)


def _web_caps(limits: Mapping[str, Any] | None) -> Mapping[str, Any]:
    web = (limits or {}).get("web") if isinstance(limits, Mapping) else None
    return web if isinstance(web, Mapping) else {}


def domain_cap(limits: Mapping[str, Any] | None) -> tuple[str, ...]:
    return tuple(_domain_tokens(_web_caps(limits).get("domains")))


def suggested_domains(limits: Mapping[str, Any] | None) -> tuple[str, ...]:
    return tuple(_domain_tokens(_web_caps(limits).get("suggested_domains")))


def domain_within(domain: str, cap: Sequence[str]) -> bool:
    """A chosen domain inside a cap entry (ADR-099 D-3): equal, or under a ``.`` entry."""
    chosen = normalize_domain(domain)
    for raw in cap:
        entry = normalize_domain(raw)
        if chosen == entry:
            return True
        if entry.startswith(".") and (chosen == entry[1:] or chosen.endswith(entry)):
            return True
    return False


def check_within_caps(access: WebAccess, limits: Mapping[str, Any] | None) -> None:
    """Raise :class:`WebAccessError` when ``access`` is wider than the caps."""
    modes = allowed_modes(limits)
    if access.mode not in modes:
        raise WebAccessError(
            f"web access {_MODE_LABELS.get(access.mode, access.mode)!r} is not allowed here; "
            f"allowed: {', '.join(_MODE_LABELS[m] for m in modes) or 'none'}"
        )
    cap = domain_cap(limits)
    outside = [d for d in access.domains if cap and not domain_within(d, cap)]
    if outside:
        raise WebAccessError(
            f"domains outside the allowed list ({', '.join(cap)}): {', '.join(outside)}"
        )


def venue_policy_preview(client: Any, url: str, project: str) -> tuple[dict[str, Any] | None, str]:
    """``GET …/venue/policy`` (ADR-099 preview): ``(body, "")`` or ``(None, why)``.

    A server without the route (404, before RS-1) or an unreadable answer
    gives no caps: the question then offers every mode and the server's
    start check stays the enforcement.
    """
    try:
        body = client.get(url, params={"project": project} if project else None, timeout=15)
    except Exception as exc:  # noqa: BLE001 — the caps are advisory here; start enforces
        status = (getattr(exc, "details", None) or {}).get("status_code")
        return None, "" if status == 404 else str(exc)
    return (body, "") if isinstance(body, dict) else (None, "unreadable policy preview")


# ── the remembered last choice (per r4 profile, D-2) ───────────────────────


def remembered_web_access(config: Any) -> WebAccess | None:
    """The profile's last choice (``Config.web_access_last``), or None.

    It preselects the question in an interactive client and is used without
    one only by a non-interactive start (D-2).
    """
    mode = str(getattr(config, "web_access_last", "") or "")
    domains = getattr(config, "web_access_domains_last", ()) or ()
    try:
        return make_web_access(mode, domains, source="remembered") if mode else None
    except WebAccessError:
        return None


def remembered_for_profile(profile: str, *, store: Any = None) -> WebAccess | None:
    """The profile's last choice read from the profile store now (a long-lived
    process's Config predates the choices made since it started)."""
    from types import SimpleNamespace

    from ..config import ProfileConfigStore

    stored = (store or ProfileConfigStore()).load(profile)
    return remembered_web_access(
        SimpleNamespace(
            web_access_last=stored.get("web_access_last"),
            web_access_domains_last=stored.get("web_access_domains_last"),
        )
    )


def remember_web_access(access: WebAccess, *, profile: str, store: Any = None) -> None:
    """Record an interactive choice as the profile's last one."""
    if not access.chosen:
        return
    from ..config import ProfileConfigStore

    (store or ProfileConfigStore()).save(
        profile,
        web_access_last=access.mode,
        web_access_domains_last=list(access.domains),
    )
