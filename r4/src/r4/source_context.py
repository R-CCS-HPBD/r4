"""Per-source credential and identity isolation (onboarding plan 2026-10-15, S1 / Phase 4-A).

One r4 process can talk to several deployments: the active platform source,
another platform source named by ``model@source`` or ``--source``, and public
providers. Each request carries only what belongs to its target:

- :func:`credential_for` — the source's own key, else the login stored under
  the source's own name (when that login records the source's deployment),
  else ``None``. It never returns the active deployment's key
  (``R4_API_KEY``, the profile's login).
- :func:`config_for_source` — a new :class:`~r4.config.Config` for one source:
  its URL, its credential, its ZMQ endpoint, its own default model and
  project, and no other deployment's project or user. The deployment the
  profile's own login belongs to (``Config.login_home``) gets that login
  back, even while the process targets another source.
- :func:`same_deployment` — whether two URLs name one deployment.
- :func:`is_loopback_url` — loopback detection that resists URL tricks
  (``127.1``, ``[::1]``, ``localhost.``, IPv4-mapped IPv6, the IDNA full
  stops ``。．｡`` that the HTTP client reads as dots).
- :func:`dialed_host` — the one host the HTTP client dials for a URL, or
  None for anything that is not plainly one host (the data-class gate
  classifies exactly that host; 2026-10-16).

A config built for a source that is not the profile's own deployment carries
``bound_source``: :class:`~r4._http.HTTPCore` and the ZMQ session then never
fall back to the profile's stored login.

Stdlib only (plus httpx, already an r4 dependency, for its host parsing);
``r4`` never imports ``umrs`` (CONSTITUTION §1.1).
"""

from __future__ import annotations

import ipaddress
import os
import re
import unicodedata
from dataclasses import replace
from typing import Any, Optional
from urllib.parse import SplitResult, urlsplit

_DEFAULT_ZMQ_PORT = 5555
_DEFAULT_PORTS = {"http": 80, "https": 443}
#: Host names that always mean this machine (RFC 6761 ``*.localhost`` too).
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
#: The one address each loopback name stands for when deployments are compared.
_LOOPBACK_ALIASES = {
    "localhost": "127.0.0.1",
    "localhost.localdomain": "127.0.0.1",
    "ip6-localhost": "::1",
    "ip6-loopback": "::1",
}
#: IDNA label separators the HTTP client reads as ``.`` (UTS-46 / RFC 3490 §3.1).
_IDNA_DOTS = str.maketrans({"。": ".", "．": ".", "｡": "."})
#: One DNS label as the HTTP client sends it (LDH, RFC 1123; an IDN is ``xn--``).
_LDH_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
#: JWT claims that name the user, in the order login reads them.
_IDENTITY_CLAIMS = ("sub", "user_id", "email")


# ------------------------------------------------------------------ URLs ---


def _split(url: Any) -> Optional[SplitResult]:
    """``urlsplit`` of a URL that may lack a scheme; ``None`` when unparsable."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"//{text}"
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 — raises ValueError on a malformed port
    except ValueError:
        return None
    return parts


def normalize_host(raw: Any) -> Optional[str]:
    """A host as the HTTP client reads it.

    The IDNA full stops ``。．｡`` become ``.``, the name is NFKC-folded
    (full-width digits become ASCII) and lower-cased, and a trailing dot and
    an IPv6 zone are removed. Only an IPv6 literal has a zone: a ``%`` in a
    DNS name stays, because the HTTP client dials the whole name
    (``gw.example%2e.evil.com`` is not ``gw.example`` — 2026-10-16).
    """
    text = unicodedata.normalize("NFKC", str(raw or "").translate(_IDNA_DOTS))
    host = text.strip().lower()
    if ":" in host:  # an IPv6 literal (urlsplit drops the brackets): drop its zone
        host = host.split("%", 1)[0]
    host = host.rstrip(".")
    return host or None


def url_host(url: Any) -> Optional[str]:
    """The URL's host, normalised by :func:`normalize_host`."""
    parts = _split(url)
    if parts is None or not parts.hostname:
        return None
    return normalize_host(parts.hostname)


def _client_host(url: Any) -> Optional[str]:
    """The host httpx would connect to for ``url`` (its own IDNA mapping), or None."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"http://{text}"
    try:
        import httpx

        return httpx.URL(text).host or None
    except Exception:  # noqa: BLE001 — a URL httpx refuses is never connected to
        return None


def _strict_host(raw: Optional[str]) -> Optional[str]:
    """``raw`` as one plain host, else None.

    An IPv6 literal (its zone dropped) in its canonical spelling, or a DNS
    name of LDH labels (``a-z``, ``0-9``, ``-``; one trailing dot allowed),
    lower-cased. A ``%``, ``\\``, ``@``, space or any other character makes
    it None.
    """
    host = str(raw or "").lower()
    if ":" in host:
        try:
            return str(ipaddress.IPv6Address(host.split("%", 1)[0]))
        except ValueError:
            return None
    name = host[:-1] if host.endswith(".") else host
    if not name or len(name) > 253:
        return None
    return name if all(_LDH_LABEL.fullmatch(label) for label in name.split(".")) else None


def dialed_host(url: Any) -> Optional[str]:
    """The host the HTTP client dials for ``url``, or None when it is not
    exactly one plain host (fail closed — the data-class gate reads None as
    public; ADR-062 amendment, 2026-10-16).

    The host is httpx's (its own IDNA mapping: what is really dialled), and
    the URL counts only when its scheme is http(s) (a bare ``host[:port]``
    reads as http), it carries no userinfo (``user@`` — the classic vehicle
    for parsers disagreeing on the host, and never needed by an inference
    endpoint), its host passes :func:`_strict_host`, and the standard
    library's parser reads the same host. ``api.example%2e.evil.com``,
    ``evil.com\\@api.example`` and ``api.example .evil.com`` answer None.
    """
    text = _plain_http_url(url)
    host = _httpx_host(text) if text else None
    if host is None or host != _strict_host(url_host(text)):
        return None
    return host


def _plain_http_url(url: Any) -> Optional[str]:
    """``url`` (a bare ``host[:port]`` as http) when it is http(s) with no
    userinfo and no backslash in its authority; else None."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"http://{text}"
    parts = _split(text)
    if parts is None or parts.scheme.lower() not in _DEFAULT_PORTS:
        return None
    return None if "@" in parts.netloc or "\\" in parts.netloc else text


def _httpx_host(text: str) -> Optional[str]:
    """The host httpx dials for ``text`` through :func:`_strict_host`, else None."""
    try:
        import httpx

        parsed = httpx.URL(text)
        if parsed.userinfo:
            return None
        return _strict_host(parsed.raw_host.decode("ascii"))
    except Exception:  # noqa: BLE001 — a URL httpx refuses is never dialled
        return None


def _c_int(part: str) -> Optional[int]:
    """One ``inet_aton`` number: decimal, ``0x`` hex or leading-``0`` octal."""
    try:
        if part[:2] == "0x":
            return int(part[2:], 16)
        if len(part) > 1 and part[0] == "0":
            return int(part, 8)
        return int(part, 10) if part.isdigit() else None
    except ValueError:
        return None


def _inet_aton(host: str) -> Optional[ipaddress.IPv4Address]:
    """IPv4 in every form a C resolver accepts (``127.1``, ``0x7f.1``, ``2130706433``)."""
    parts = host.split(".")
    numbers = [_c_int(p) for p in parts] if 1 <= len(parts) <= 4 else [None]
    if any(n is None for n in numbers):
        return None
    *head, last = numbers
    room = 4 - len(head)
    if any(n > 255 for n in head) or last >= 256**room:
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    return ipaddress.IPv4Address(value * 256**room + last)


def _parse_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return _inet_aton(host)


def _embedded_v4(ip: ipaddress._BaseAddress) -> Optional[ipaddress.IPv4Address]:
    """The IPv4 address inside an IPv4-mapped or IPv4-compatible IPv6 address."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.packed[:12] == bytes(12) and int(ip) > 1:
        return ipaddress.IPv4Address(ip.packed[12:])  # IPv4-compatible ::a.b.c.d
    return None


def _ip_is_local(ip: ipaddress._BaseAddress) -> bool:
    embedded = _embedded_v4(ip)
    if embedded is not None:
        return _ip_is_local(embedded)
    return ip.is_loopback or ip.is_unspecified


def _canonical_host(host: str) -> str:
    """One spelling per address, for comparing deployments.

    An IP literal in any form becomes its address (an IPv4-mapped IPv6
    address its IPv4 address); ``localhost`` is ``127.0.0.1`` and
    ``ip6-localhost`` is ``::1``. Every other name stays as written: other
    loopback addresses (``127.0.0.2``, ``foo.localhost``) are hosts of their
    own, because another listener (an SSH forward, say) can sit there.
    """
    alias = _LOOPBACK_ALIASES.get(host)
    if alias is not None:
        return alias
    ip = _parse_ip(host)
    if ip is None:
        return host
    return str(_embedded_v4(ip) or ip)


def _deployment_key(url: Any) -> Optional[tuple[str, str, Optional[int], str]]:
    from .config import _normalize_gateway_url

    text = str(url or "").strip()
    if not text:
        return None
    normalized = _normalize_gateway_url(text)
    parts, host = _split(normalized), url_host(normalized)
    if parts is None or host is None:
        return None
    scheme = parts.scheme.lower()
    port = parts.port or _DEFAULT_PORTS.get(scheme)
    return scheme, _canonical_host(host), port, parts.path.rstrip("/")


def same_deployment(a: Any, b: Any) -> bool:
    """Whether two URLs name one deployment (scheme, host, port and path).

    Host names compare case-insensitively, default ports and a trailing
    slash do not matter, and spellings of one address are one host
    (``localhost`` is ``127.0.0.1``, ``127.1`` is ``127.0.0.1``). Any other
    host — including another loopback address — is another deployment.
    """
    key_a = _deployment_key(a)
    return key_a is not None and key_a == _deployment_key(b)


def within_deployment(url: Any, deployment_url: Any) -> bool:
    """Whether ``url`` is on ``deployment_url``'s deployment: same scheme,
    host and port, and a path under the deployment's path (its routes)."""
    key, base = _deployment_key(url), _deployment_key(deployment_url)
    if key is None or base is None or key[:3] != base[:3]:
        return False
    return key[3] == base[3] or key[3].startswith(f"{base[3]}/")


def is_loopback_host(host: Optional[str]) -> bool:
    """Whether ``host`` reaches this machine: a loopback name or address."""
    name = normalize_host(host)
    if not name:
        return False
    if name in _LOOPBACK_NAMES or name.endswith(".localhost"):
        return True
    ip = _parse_ip(name)
    return ip is not None and _ip_is_local(ip)


def is_loopback_url(url: Any) -> bool:
    """Whether the URL points at this machine (no DNS lookup is made).

    Resists the usual spellings: ``127.1``, ``0x7f.0.0.1``, ``2130706433``,
    ``[::1]``, ``[::ffff:127.0.0.1]``, ``LOCALHOST.``, ``foo.localhost``,
    ``0.0.0.0``, and the IDNA full stops (``127。0。0。1``). The host is read
    both as written and as the HTTP client maps it. A DNS name that resolves
    to loopback (``localtest.me``) is not detected.
    """
    return is_loopback_host(url_host(url)) or is_loopback_host(_client_host(url))


# ----------------------------------------------------------- credentials ---


def _jwt_claim(token: Any, claim: str) -> Optional[str]:
    """One claim of a JWT credential (unverified, never authoritative), else None."""
    if not token:
        return None
    from .auth import decode_jwt_claims

    value = (decode_jwt_claims(str(token)) or {}).get(claim)
    return str(value) if value else None


def recorded_login_home(profile: dict[str, Any], key: Any = None) -> Optional[str]:
    """The deployment a stored login records it was made for, else None.

    The gateway ``r4 login`` recorded (``login_gateway_url``, since
    2026-10-15), else the ``gateway_url`` claim of its key, else the
    profile's ``gateway_url``.
    """
    for value in (
        profile.get("login_gateway_url"),
        _jwt_claim(key or profile.get("api_key"), "gateway_url"),
        profile.get("gateway_url"),
    ):
        if value:
            return str(value)
    return None


def _login_in_store(name: str, profile: dict[str, Any]) -> Optional[str]:
    from .auth import TokenStore

    token = TokenStore().load(name)
    if token is not None and token.access_token and not token.is_expired:
        return token.access_token
    stored = profile.get("api_key")
    return str(stored) if stored else None


def stored_login(name: str, url: Any = None) -> Optional[str]:
    """The login kept in the r4 auth store under ``name`` (a source's own name).

    ``R4_API_KEY_<NAME>`` for this process first; else an unexpired token
    from ``r4 login --profile <name>``, else the key saved for that name in
    ``config.json``. The process-wide ``R4_API_KEY`` is not read: it belongs
    to the active deployment.

    With ``url``, a stored login counts only when it records that it was
    made for that deployment (:func:`recorded_login_home`): a login that
    records another deployment, or none at all, is not this source's — a
    source re-pointed at a new URL never inherits an older login.
    """
    if not name:
        return None
    env_key = os.environ.get(f"R4_API_KEY_{name.upper()}")
    if env_key:
        return env_key
    try:
        from .config import ProfileConfigStore

        profile = ProfileConfigStore().load(name)
        key = _login_in_store(name, profile)
    except Exception:  # noqa: BLE001 — an unreadable store means no stored login
        return None
    if key is None or url is None:
        return key
    home = recorded_login_home(profile, key)
    return key if home and same_deployment(home, url) else None


def credential_for(source: Any) -> Optional[str]:
    """The credential that belongs to ``source``, or ``None``.

    The source's own ``sources.json`` key, else :func:`stored_login` under
    the source's own name (when that login was made for the source's URL).
    Never the active deployment's key: a source with no credential of its
    own is sent none.
    """
    own = getattr(source, "api_key", None)
    if own:
        return str(own)
    return stored_login(str(getattr(source, "name", "") or ""), getattr(source, "url", None))


def identity_of(credential: Optional[str]) -> Optional[str]:
    """The user a JWT credential names (``sub``, ``user_id`` or ``email``)."""
    if not credential:
        return None
    from .auth import decode_jwt_claims

    claims = decode_jwt_claims(credential) or {}
    return next((str(claims[k]) for k in _IDENTITY_CLAIMS if claims.get(k)), None)


def _insecure_http_flag(url: Any) -> str:
    """``--allow-insecure-http`` when storing a key for ``url`` needs it (G-4)."""
    from ._cli.source import _is_credentialed_http_unsafe

    return " --allow-insecure-http" if _is_credentialed_http_unsafe(str(url or "")) else ""


def _is_active_source(name: str) -> bool:
    try:
        from .source import resolve_active_source

        active = resolve_active_source()
    except Exception:  # noqa: BLE001 — the hint is advisory
        return False
    return active is not None and active.name == name


def login_hint(source: Any, *, home_unknown: bool = False) -> str:
    """How to give ``source`` a credential of its own without touching the active login.

    ``r4 source set`` stores only the key: every other field of the row
    (ZMQ, tunnel, description, model, project) is kept. ``home_unknown``
    (r4 could not tell which deployment the profile's login was made for)
    adds, for the active source, how to record that the login is its own.
    """
    name = getattr(source, "name", "?")
    url = getattr(source, "url", "")
    if getattr(source, "api_key", None):
        state = f"source '{name}' refused the credential stored for it"
    else:
        state = (
            f"source '{name}' has no credential of its own, so r4 sent none (another "
            "deployment's login is never reused)"
        )
    hint = (
        f"{state}. Give it one (your active login is kept): printf '%s' \"$TOKEN\" | "
        f"r4 source set {name} --api-key-stdin{_insecure_http_flag(url)}"
    )
    if home_unknown and _is_active_source(name):
        hint += (
            ". If your profile's login was made for this deployment, record that instead: "
            f"printf '%s' \"$TOKEN\" | r4 auth login --gateway-url {url} --api-key-stdin"
        )
    return hint


def bound_login_hint(config: Any) -> str:
    """How to log in to the source a bound config targets (onboarding plan S1)."""
    name = getattr(config, "bound_source", None) or "?"
    try:
        from .source import get_source_store

        source = get_source_store().get(name)
    except Exception:  # noqa: BLE001 — the hint is advisory
        source = None
    if source is not None:
        return login_hint(source, home_unknown=getattr(config, "login_home", None) is None)
    return (
        f"R4_GATEWAY_URL points at {name}, which is not the deployment your profile's login "
        "was made for, so r4 sent no credential. Export R4_API_KEY for it, or register it: "
        f"printf '%s' \"$TOKEN\" | r4 source add NAME --url {name} --kind platform --api-key-stdin"
    )


def auth_failure_hint(config: Any, exc: BaseException) -> Optional[str]:
    """The recovery hint for ``exc`` raised by a call made with ``config``.

    A 401/403 from a source bound to another deployment says how to give
    THAT source a credential (:func:`bound_login_hint`); anything else keeps
    the error's own hint.
    """
    from .errors import AuthenticationError

    if isinstance(exc, AuthenticationError) and getattr(config, "bound_source", None):
        return bound_login_hint(config)
    return getattr(exc, "hint", None)


# --------------------------------------------------------------- configs ---


#: Env variables that name the ZMQ endpoint explicitly for this process.
_ZMQ_ENV_FIELDS = (
    ("UMRS_SERVER_HOST", "umrs_server_host"),
    ("UMRS_CONTROL_PORT", "umrs_control_port"),
    ("UMRS_TUNNEL_MODE", "umrs_tunnel_mode"),
)


def zmq_fields(source: Any) -> dict[str, Any]:
    """The source's ZMQ data-plane endpoint (same defaults as its umrs profile).

    A field an env variable names for this process (``UMRS_SERVER_HOST``,
    ``UMRS_CONTROL_PORT``, ``UMRS_TUNNEL_MODE``) is left out: the env wins.
    """
    raw = getattr(source, "zmq_host", None)
    host = raw if raw and raw != "0.0.0.0" else (url_host(source.url) or "127.0.0.1")
    fields = {
        "umrs_server_host": host,
        "umrs_control_port": int(getattr(source, "zmq_control_port", None) or _DEFAULT_ZMQ_PORT),
        "umrs_tunnel_mode": bool(getattr(source, "zmq_tunnel_mode", None)),
    }
    explicit = {attr for env, attr in _ZMQ_ENV_FIELDS if os.environ.get(env)}
    return {attr: value for attr, value in fields.items() if attr not in explicit}


def source_model(source: Any, current: Optional[str]) -> Optional[str]:
    """The model to use for ``source`` when the command names none.

    The source's ``default_model``, unless ``R4_MODEL`` names one for this
    process; else ``current``. ``--model`` and ``model@source`` are applied
    by the commands themselves and always win.
    """
    default = getattr(source, "default_model", None)
    if default and not os.environ.get("R4_MODEL"):
        return str(default)
    return current


def source_project(source: Any, current: Optional[str]) -> Optional[str]:
    """The project sent to ``source`` (its own deployment, or the active one).

    ``R4_PROJECT_ID`` for this process wins, then the source's ``project``,
    then ``current``.
    """
    if os.environ.get("R4_PROJECT_ID"):
        return current
    return getattr(source, "project", None) or current


def switched_project_for(url: Any) -> Optional[str]:
    """``r4 project switch``'s current project when its config names ``url``'s
    deployment (``r4 project create --gateway``), else None."""
    try:
        from .project import get_project_manager

        current = get_project_manager().get_current_config()
    except Exception:  # noqa: BLE001 — no project state means no switched project
        return None
    gateway = getattr(current, "gateway_url", None)
    if current is None or not gateway or not same_deployment(gateway, url):
        return None
    return current.project_id


def active_source_project(source: Any, fallback: Optional[str]) -> Optional[str]:
    """The project sent to the ACTIVE source.

    ``R4_PROJECT_ID``, else the project ``r4 project switch`` selected when
    it is recorded for this deployment, else the source's ``project``, else
    ``fallback`` (the profile's project on the profile's own deployment; none
    on another).
    """
    explicit = os.environ.get("R4_PROJECT_ID")
    if explicit:
        return explicit
    return switched_project_for(source.url) or getattr(source, "project", None) or fallback


def _public_source_config(source: Any, base: Any) -> Any:
    """Point the direct-endpoint fields at a public source; the platform side stays."""
    same = same_deployment(source.url, getattr(base, "endpoint_url", None))
    key = credential_for(source) or (base.endpoint_api_key if same else None)
    return replace(
        base,
        endpoint_url=source.url,
        endpoint_provider=source.provider,
        endpoint_api_key=key,
        aws_region=source.aws_region or base.aws_region,
        aws_profile=source.aws_profile or base.aws_profile,
        model=source_model(source, base.model),
    )


def _platform_overrides(source: Any, base: Any) -> dict[str, Any]:
    return {
        "gateway_url": source.url,
        "endpoint_url": None,
        "endpoint_provider": None,
        "model": source_model(source, base.model),
    }


def _is_login_home(source: Any, base: Any) -> bool:
    """Whether ``source`` is the deployment the profile's own login was made for."""
    home = getattr(base, "login_home", None)
    return bool(home) and same_deployment(source.url, home)


def config_for_source(source: Any, base: Any) -> Any:
    """A new Config that sends to ``source`` and carries only what is its own.

    - Public source: the direct-endpoint fields move to it; the key is its
      own, or the base endpoint key only when the URL is the same endpoint.
    - Platform source on the base's own deployment (same URL): the base is
      kept (its credential, project, user and ZMQ belong there); the source's
      own key wins when it has one, and its project when it names one
      (:func:`source_project`).
    - The deployment the profile's login was made for (``base.login_home``)
      while the base targets another source: the profile's own login,
      project and user (:func:`r4.config.home_source_config`).
    - Another platform deployment: its credential (or none), its ZMQ
      endpoint, its own project only (none when it names none — the base's
      project is never sent), and the user its own token names.
      ``bound_source`` stops every fallback to the profile's stored login.

    The source's ``default_model`` becomes the config's model unless
    ``R4_MODEL`` is set (:func:`source_model`). ``base`` is never modified.
    """
    if getattr(source, "kind", None) != "platform":
        return _public_source_config(source, base)
    if same_deployment(source.url, base.gateway_url):
        key = source.api_key or base.api_key or credential_for(source)
        return replace(
            base,
            api_key=key,
            project_id=source_project(source, base.project_id),
            **_platform_overrides(source, base),
        )
    if _is_login_home(source, base):
        from .config import home_source_config

        return home_source_config(source, base)
    key = credential_for(source)
    return replace(
        base,
        api_key=key,
        bound_source=source.name,
        project_id=getattr(source, "project", None),
        umrs_username=identity_of(key),
        **_platform_overrides(source, base),
        **zmq_fields(source),
    )


def use_source(source: Any) -> Any:
    """Make ``source`` this process's target: the global config becomes
    :func:`config_for_source` of it. Returns the new config."""
    from .config import get_config, set_config

    config = config_for_source(source, get_config())
    set_config(config)
    return config


def selected_project(config: Any = None, *, config_fallback: bool = True) -> Optional[str]:
    """The project a command acts on for the deployment ``config`` targets.

    The profile's own deployment: ``r4 project switch``'s current project,
    else (``config_fallback``) the config's project — each caller keeps the
    rule it had before 2026-10-15. A config bound to another source: only its
    own project (the source's ``project``, ``R4_PROJECT_ID`` or a switched
    project recorded for that deployment) — the active deployment's project
    is never sent there.
    """
    if config is None:
        from .config import get_config

        config = get_config()
    if getattr(config, "bound_source", None):
        return getattr(config, "project_id", None) or None
    try:
        from .project import get_project_manager

        current = get_project_manager().get_current()
    except Exception:  # noqa: BLE001 — no project state
        current = None
    if current or not config_fallback:
        return current or None
    return getattr(config, "project_id", None) or None


def _process_targets_elsewhere(config: Any) -> bool:
    """Whether the process-wide config is bound to a deployment other than ``config``'s."""
    from . import config as config_module

    active = getattr(config_module, "_config", None)
    if not getattr(active, "bound_source", None):
        return False
    return not same_deployment(active.gateway_url, getattr(config, "gateway_url", None))


def _profile_bearer(config: Any) -> Optional[str]:
    """The bearer token of the profile's own login (token store or key), else None."""
    try:
        from .auth import get_auth_manager

        header = get_auth_manager(config.profile).get_auth_header() or {}
    except Exception:  # noqa: BLE001 — no stored login
        return None
    parts = str(header.get("Authorization", "")).split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def _session_fields(gateway_url: Any, credential: Optional[str]) -> dict[str, Any]:
    return {
        "session_bound": True,
        "session_gateway_url": gateway_url,
        "session_credential": credential,
    }


def transport_session_fields(config: Any) -> dict[str, Any]:
    """ZMQ CURVE-session fields for a transport serving ``config``.

    A bound config registers its session at its own gateway with its own
    credential. The profile's own deployment, reached while the process is
    bound to another source, registers there with the profile's login. Any
    other config uses the active source's login as before.
    """
    if getattr(config, "bound_source", None):
        return _session_fields(config.gateway_url, config.api_key)
    if _process_targets_elsewhere(config):
        return _session_fields(config.gateway_url, config.api_key or _profile_bearer(config))
    return {"session_bound": False}


__all__ = [
    "active_source_project",
    "auth_failure_hint",
    "bound_login_hint",
    "config_for_source",
    "credential_for",
    "dialed_host",
    "identity_of",
    "is_loopback_host",
    "is_loopback_url",
    "login_hint",
    "normalize_host",
    "recorded_login_home",
    "same_deployment",
    "selected_project",
    "source_model",
    "source_project",
    "stored_login",
    "switched_project_for",
    "transport_session_fields",
    "url_host",
    "use_source",
    "within_deployment",
    "zmq_fields",
]
