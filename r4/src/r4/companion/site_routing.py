"""Per-call site routing for the agent's platform tools (multi-HPC plan S3).

A tool called with ``site=<name>`` (or a handle ``<id>@<name>``) runs through
that site's own gateway, with that site's own credential and config
(:func:`r4.source_context.config_for_source`); the session's client and the
process config are never changed. No site means the home site, the one the
session already talks to.

Fail closed, never fall back to the home site:

* ``unknown_site`` — the name is no registered platform source and no site
  name the inventory has seen (a typo, or a public model source). Before
  2026-10-24 ``_resolve_source_client`` silently returned the home client in
  these cases, so ``endpoint_provision(source="typo")`` provisioned at home
  and said "Provisioned on source 'typo'" (X-a).
* ``site_unavailable`` — the inventory could not reach the site.
* ``site_access_undeclared`` — a non-home site that has not declared its HPC
  access mode (``GET /sites/me``); the guard turns itself off per site as the
  site declares one (plan §3.2, S1b). Applied only to calls that start or
  change work (``act=True``); reads only resolve the name.
* ``no_site_account`` — the site reports the caller's account there absent,
  disabled or unknown (eligible needs an active row, plan §3.3).
* ``site_conflict`` — ``site=`` and the handle's ``@site`` disagree, two
  sources report one server site name, or a job is named at a site other
  than the one that runs it.

Every result names its site; ids come back as ``id@site`` handles.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from ..sites import split_handle

__all__ = [
    "SiteRefused",
    "SiteTarget",
    "client_for_site",
    "job_action_target",
    "job_site_labeller",
    "route",
    "split_handle",
    "tag",
]


class SiteRefused(Exception):
    """A typed refusal; ``str()`` is ``"<code>: <message>"`` for tool text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SiteTarget:
    """Where one tool call goes."""

    client: Any
    site: str
    home: bool = True
    ident: str = ""
    source: Any = None

    @property
    def handle(self) -> str:
        return f"{self.ident}@{self.site}" if self.ident else self.site


def _store() -> Any:
    from ..source import get_source_store

    return get_source_store()


def _config(client: Any) -> Any:
    config = getattr(client, "config", None)
    if config is not None:
        return config
    from ..config import get_config

    return get_config()


def home_label(client: Any) -> str:
    """The home site's name: the bound source, the registered source at the
    session's gateway, else ``home``. A URL is never a label (``bound_source``
    holds the gateway URL when no registered source names it)."""
    from ..sites import HOME_NAME, is_home, platform_sources

    config = _config(client)
    bound = str(getattr(config, "bound_source", None) or "")
    if bound and "://" not in bound:
        return bound
    for source in platform_sources():
        if is_home(source, config):
            return str(source.name)
    return HOME_NAME


def _multi_site(config: Any) -> bool:
    from ..sites import site_sources

    try:
        return len(site_sources(config)) >= 2
    except Exception:  # noqa: BLE001 — an unreadable registry is one site
        return False


def _valid_names(client: Any) -> list[str]:
    """Names that resolve: home, registered platform sources, server site
    names one source reports (never an ambiguous one)."""
    from ..sites import platform_sources

    names = [home_label(client)] + [str(s.name) for s in platform_sources()]
    try:
        names += [site for site in _site_name_owners(_config(client)) if site not in names]
    except Exception:  # noqa: BLE001 — the list is a hint only
        pass
    return list(dict.fromkeys(names))


def _unknown(name: str, client: Any, why: str = "") -> SiteRefused:
    valid = ", ".join(_valid_names(client)) or "none registered (r4 source add)"
    detail = f" ({why})" if why else ""
    return SiteRefused("unknown_site", f"no HPC site named {name!r}{detail}; valid sites: {valid}")


def _site_name_owners(config: Any) -> dict[str, list[Any]]:
    """Server site name (``sites/me``) → the inventory rows reporting it."""
    from ..sites import inventory

    owners: dict[str, list[Any]] = {}
    for row in inventory(config):
        owners.setdefault(row.site, []).append(row)
    return owners


def _row_by_site_name(name: str, config: Any) -> Any:
    """The one inventory row whose server reports ``site == name``, or None.

    Two sources reporting one site name is refused (``site_conflict``): a
    gateway could otherwise claim another's name and take its work.
    """
    rows = _site_name_owners(config).get(name, [])
    sources = sorted({str(r.source) for r in rows})
    if len(sources) > 1:
        raise SiteRefused(
            "site_conflict",
            f"{len(sources)} sources report the site name {name!r} "
            f"({', '.join(sources)}); name the source instead",
        )
    return rows[0] if rows else None


#: ``find_source``'s answer for the session's own site.
HOME = object()


def find_source(name: str, client: Any) -> Any:
    """The platform source for ``name`` (source name, else server site name),
    or :data:`HOME` for the session's own site under any of its names."""
    source = _store().get(name)
    if source is not None and source.kind != "platform":
        raise _unknown(name, client, "a public model source, not an HPC site")
    if source is None or not getattr(source, "url", None):
        source = _source_by_site_name(name, client)
    return source


def _source_by_site_name(name: str, client: Any) -> Any:
    """The source whose server reports ``site == name`` (or :data:`HOME`)."""
    row = _row_by_site_name(name, _config(client))
    if row is not None and row.home:
        return HOME
    source = _store().get(row.source) if row is not None else None
    if source is None or not getattr(source, "url", None):
        raise _unknown(name, client)
    return source


def require_eligible(source: Any, client: Any) -> None:
    """The guard for a non-home site (see the module doc for the codes)."""
    from ..sites import row_for

    row = row_for(source, _config(client))
    if row.error:
        raise SiteRefused(
            "site_unavailable",
            f"{source.name}: {row.error} (re-probe with hpc_sites(refresh=true))",
        )
    if not row.declared:
        raise SiteRefused(
            "site_access_undeclared",
            f"{source.name} has not declared its HPC access mode "
            f"({row.access_mode}); work cannot be started or changed there from "
            "another site until it does. Ask the user, or switch the session to it.",
        )
    if row.account != "present":
        raise SiteRefused("no_site_account", f"your account at {source.name} is {row.account}")


def _routed_client(source: Any, client: Any) -> Any:
    from ..client import Client
    from ..source_context import config_for_source

    return Client(config=config_for_source(source, _config(client)))


def _is_home_name(name: str, client: Any) -> bool:
    from ..sites import HOME_NAME

    return name in (HOME_NAME, home_label(client))


def resolve_source_client(client: Any, name: Optional[str]) -> SiteTarget:
    """Resolve ``name`` to that site's client; fail closed on unknown names
    (X-a). No access-mode guard: identity and URL routing only.

    Every name the session prints for its own site resolves home: ``home``,
    the home label, and the server's own site name (review M1, 2026-10-24:
    ``77@home`` handles were printed and then refused)."""
    from ..sites import is_home

    if not name or _is_home_name(name, client):
        return SiteTarget(client, home_label(client))
    source = find_source(name, client)
    if source is HOME:
        return SiteTarget(client, home_label(client))
    if is_home(source, _config(client)):
        return SiteTarget(client, str(source.name), source=source)
    routed = _routed_client(source, client)
    return SiteTarget(routed, str(source.name), home=False, source=source)


def client_for_site(client: Any, site: Optional[str]) -> SiteTarget:
    """:func:`resolve_source_client` plus the eligibility guard off home."""
    target = resolve_source_client(client, site)
    if not target.home:
        require_eligible(target.source, client)
    return target


def _known_site_name(name: str, client: Any) -> bool:
    """Whether ``name`` names a site (no probe in a one-site session)."""
    if "/" in name:
        return False
    if _is_home_name(name, client):
        return True
    source = _store().get(name)
    if source is not None:
        return bool(source.kind == "platform")
    config = _config(client)
    return _multi_site(config) and name in _site_name_owners(config)


def _parse_handle(
    client: Any, site: Optional[str], handle: Any, lenient: bool
) -> tuple[str, Optional[str]]:
    """``(id, site from the handle)``.

    ``lenient`` is for ids that may hold ``@`` themselves (a file key such
    as ``photo@2x.png``, review M2): with ``site=`` given the whole text is
    the id, and a suffix counts as a site only when it names one.
    """
    whole = str(handle or "").strip()
    if lenient and site:
        return whole, None
    ident, handle_site = split_handle(handle)
    if lenient and handle_site and not _known_site_name(handle_site, client):
        return whole, None
    return ident, handle_site


def route(
    client: Any,
    site: Optional[str] = None,
    handle: Any = "",
    *,
    act: bool = False,
    lenient: bool = False,
) -> SiteTarget:
    """Resolve a tool call's site from ``site=`` and/or an ``id@site`` handle.

    ``act`` marks a call that starts or changes work (run, cancel, retry,
    provision, stop, upload): only then does a non-home site have to be
    eligible (declared mode, active account). Reads (status, lists, results,
    downloads, health) only resolve the name, failing closed on unknown ones
    (review P3: ``endpoint_list(source=...)`` worked before the guard).
    """
    ident, handle_site = _parse_handle(client, site, handle, lenient)
    if site and handle_site and site != handle_site:
        raise SiteRefused("site_conflict", f"site={site!r} but the handle names {handle_site!r}")
    resolve = client_for_site if act else resolve_source_client
    target = resolve(client, site or handle_site)
    return replace(target, ident=ident)


def tag(payload: dict[str, Any], target: SiteTarget) -> dict[str, Any]:
    """Name the site (and the ``id@site`` handle) on a JSON tool result."""
    payload["site"] = target.site
    if target.ident:
        payload["handle"] = target.handle
    return payload


# ── jobs: label and route by the row's venue (plan §3.9, review M3) ─────────


def job_site_labeller(client: Any, target: SiteTarget) -> Callable[[Any], str]:
    """``label(job)`` → the site that runs ``job`` (from its ``venue``).

    The jobs table is shared by every site, so a gateway lists every site's
    jobs: a row is labelled with the site its venue names, not the gateway
    that listed it. A shared row (``""``) or a server that does not report
    venues keeps the listing site; a venue that is none of the user's sites
    gives ``""`` (no handle). One-site sessions never probe."""
    config = _config(client)
    if not _multi_site(config):
        return lambda _job: target.site
    from ..hpc_jobs import venue_map
    from ..sites import inventory

    vmap = venue_map(inventory(config), home=home_label(client))

    def label(job: Any) -> str:
        venue = getattr(job, "venue", None)
        return vmap.get(venue, "") if venue else target.site

    return label


def job_action_target(client: Any, site: Optional[str], job_id: str) -> SiteTarget:
    """Where ``job_cancel``/``job_retry`` go: the site that runs the job.

    A bare id is looked up at home and sent to the gateway of its venue's
    site; an explicit site that is not the job's own is refused
    (``site_conflict``), and so is a venue that is none of the user's sites.
    """
    target = route(client, site, job_id, act=True)
    if not _multi_site(_config(client)):
        return target
    job = target.client.jobs.get(target.ident)
    runs_at = job_site_labeller(client, target)(job)
    if runs_at == target.site:
        return target
    explicit = bool(site or split_handle(job_id)[1])
    if not runs_at or explicit:
        raise _wrong_site(target, job, runs_at)
    return replace(route(client, runs_at, act=True), ident=target.ident)


def _wrong_site(target: SiteTarget, job: Any, runs_at: str) -> SiteRefused:
    if not runs_at:
        return SiteRefused(
            "unknown_site",
            f"job {target.ident} runs at {getattr(job, 'venue', '')!r}, which is "
            "none of your sites (r4 hpc sites); it is not sent anywhere else",
        )
    return SiteRefused(
        "site_conflict",
        f"job {target.ident} runs at {runs_at}, not {target.site}; use {target.ident}@{runs_at}",
    )
