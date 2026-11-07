"""ADR-062 amendment (2026-10-15): derived source locality.

Every direct-dial (public-kind) source gets a COMPUTED locality —
``local`` | ``institutional`` | ``public`` — from its URL, never from a
user label. :func:`classify_url` classifies exactly the host the HTTP
client dials for that URL (:func:`r4.source_context.dialed_host`); a URL
whose host is not plainly one host — a ``%`` in a name, userinfo, a
backslash, a non-http scheme — is ``public`` (2026-10-16). The workstation
gate also classifies the URL the route's provider object dials
(``Provider.dial_url``); a provider that picks its own endpoint (Bedrock:
the AWS SDK and its config decide) is ``public`` whatever URL its source
was registered with (2026-10-16).

- ``local`` — the URL reaches this machine (a loopback host, per
  :func:`r4.source_context.is_loopback_host`). The WORKSTATION gate admits
  a loopback model for a sensitive project whichever gateway hosts the
  project, a remote site's included: the workstation is the machine the
  data is on. The amendment's Decision 3 clause "never counts as local for
  a remote gateway's project" binds the SERVER gates (venue start, lease
  proxy) — a remote site cannot see the user's machine (ADR-046), so its
  gates never treat loopback as local (clarified 2026-10-16). A host-based
  check cannot see what a loopback listener forwards to: a local relay or
  an ``ssh -L`` tunnel to a commercial API also classifies ``local``.
- ``institutional`` — the host is on the institution's allowlist (Rikyu
  first). The allowlist is the SITE's: the advertisement ``r4 login``
  cached from connect-info on the platform source of the deployment that
  serves the gated project replaces the packaged default; an advertised
  EMPTY list means the site has no institutional providers. When no
  platform source of that deployment carries an advertisement, the
  packaged default applies.
- ``public`` — everything else; refused for ``data_class=sensitive``
  projects at the workstation gate. The venue-start and lease gates
  enforce the same rule server-side against the site's own allowlist
  (``UMRS_INSTITUTIONAL_PROVIDER_HOSTS``).
"""

from __future__ import annotations

from typing import Any, Optional

# The packaged institutional allowlist (ADR-062 amendment): a UMRS site
# replaces it through connect-info / UMRS_INSTITUTIONAL_PROVIDER_HOSTS.
PACKAGED_INSTITUTIONAL_HOSTS = frozenset({"api.rikyu.r-ccs.riken.jp"})


def classify_url(url: Any, *, hosts: Optional[frozenset] = None) -> str:
    """``local`` | ``institutional`` | ``public`` for an http(s) URL.

    Never raises; a URL without one plain dialled host classifies
    ``public`` (fail closed for a sensitive project, which is the only
    class that consults this).
    """
    from .source_context import dialed_host, is_loopback_host

    host = dialed_host(url)
    if not host:
        return "public"
    if is_loopback_host(host):
        return "local"
    allow = hosts if hosts is not None else PACKAGED_INSTITUTIONAL_HOSTS
    return "institutional" if host in allow else "public"


def _after_scheme(url: Any) -> tuple[str, str]:
    """``(scheme + "://", rest)`` of ``url``; ``("", url)`` without a scheme."""
    text = str(url or "").strip()
    scheme, sep, rest = text.partition("://")
    return (scheme + sep, rest) if sep else ("", text)


def carries_userinfo(url: Any) -> bool:
    """Whether ``url`` holds an ``@`` after its scheme — a ``user:pass@``
    part, which :func:`classify_url` reads as ``public``."""
    return "@" in _after_scheme(url)[1]


def display_url(url: Any) -> str:
    """``url`` safe to print: everything up to its last ``@`` (a user name,
    a password) is replaced by ``***``."""
    prefix, rest = _after_scheme(url)
    if "@" not in rest:
        return prefix + rest
    return f"{prefix}***@{rest.rsplit('@', 1)[1]}"


def effective_institutional_hosts(gateway_url: Any = None) -> frozenset:
    """The allowlist of the deployment at ``gateway_url`` — the gateway that
    serves the gated project (default: the gateway this process targets).

    The advertisement stored on that deployment's platform source wins (an
    advertised ``[]`` is honoured as "no institutional providers", not as
    an absence). Never the ACTIVE source's: a public source carries none,
    and another deployment's list says nothing about this one (2026-10-16).
    Two platform sources of one deployment that both carry a list: only a
    host on every list counts (fail closed). No advertisement for that
    deployment — or an unreadable source store — falls back to the
    packaged default.
    """
    try:
        target = gateway_url if gateway_url else _current_gateway_url()
        stored = _deployment_advertisement(target)
    except Exception:  # noqa: BLE001 — locality must never break routing
        return PACKAGED_INSTITUTIONAL_HOSTS
    return PACKAGED_INSTITUTIONAL_HOSTS if stored is None else stored


def _current_gateway_url() -> Optional[str]:
    """The gateway a client built now would target (and gate projects on)."""
    from .config import Config

    return Config.from_env().gateway_url or None


def _deployment_advertisement(gateway_url: Any) -> Optional[frozenset]:
    """What the deployment's platform sources store; ``None`` = none carried."""
    if not gateway_url:
        return None
    from .source import get_platform_sources
    from .source_context import same_deployment

    lists = [
        hosts
        for hosts in (
            _advertised_hosts(src)
            for src in get_platform_sources()
            if same_deployment(src.url, gateway_url)
        )
        if hosts is not None
    ]
    return frozenset.intersection(*lists) if lists else None


def _advertised_hosts(source: Any) -> Optional[frozenset]:
    """The platform source's stored advertisement; ``None`` = none carried."""
    if source is None or getattr(source, "kind", "public") != "platform":
        return None
    raw = getattr(source, "institutional_hosts", None)
    if raw is None:
        return None
    return frozenset(h.strip().lower() for h in raw if isinstance(h, str) and h.strip())


def source_locality(
    source: Any, *, hosts: Optional[frozenset] = None, gateway_url: Any = None
) -> str:
    """A registered source's locality.

    Platform sources answer ``"platform"`` — they are the site's own
    governed route; the workstation gate only classifies direct-dial
    (public-kind) sources. ``gateway_url`` names the deployment whose
    allowlist applies (see :func:`effective_institutional_hosts`).
    """
    if getattr(source, "kind", "public") == "platform":
        return "platform"
    effective = hosts if hosts is not None else effective_institutional_hosts(gateway_url)
    return classify_url(getattr(source, "url", ""), hosts=effective)
