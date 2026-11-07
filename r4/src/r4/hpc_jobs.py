"""Jobs across HPC sites (multi-HPC plan S3): ``r4 hpc jobs|cancel|retry``.

Every umrs-front shares one ``jobs`` table, so asking N gateways for "my
jobs" returns each job N times. This module asks every site once, in
parallel, keeps each job once (by ``job_id``) and labels it with the site
that runs it, taken from the row's ``venue`` (the server's site name, mapped
to the user's source name through the site inventory).

Cancel and retry go through the gateway of the site that runs the job, never
simply the session's own. A job whose site cannot be told (the server does
not report ``venue`` yet, or names a site that is not one of yours) is
refused with a request to name it as ``<id>@<site>``.

Nothing is stored locally: every listing is rebuilt from the sites
(AGENTS.md rule 10).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import sites as _sites
from .errors import R4Error
from .sites import PROBE_TIMEOUT_SEC, SiteRow, split_handle

#: Listing budget per site (a jobs list is heavier than a probe).
LIST_TIMEOUT_SEC = 10.0
UNREPORTED = "?"
SHARED = "shared"


class HpcSiteError(R4Error):
    """A site that cannot be resolved, or a job whose site cannot be told."""


@dataclass
class SiteJob:
    """One job, once, with the site that runs it."""

    job: Any
    site: str
    venue: Optional[str]
    seen_via: list[str] = field(default_factory=list)
    #: The venue names one of the user's sites (so cancel/retry can route).
    mapped: bool = False

    @property
    def handle(self) -> str:
        """``id@site``; the bare id when the site is not one to name (a shared
        row, a server that does not report venues): the bare id is what
        ``r4 hpc cancel|retry`` take back for those."""
        if self.site in (SHARED, UNREPORTED) or not self.mapped:
            return str(self.job.job_id)
        return f"{self.job.job_id}@{self.site}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job.job_id,
            "handle": self.handle,
            "site": self.site,
            "venue": self.venue,
            "job_type": self.job.job_type,
            "status": self.job.status,
            "created_at": str(self.job.created_at) if self.job.created_at else None,
            "seen_via": list(self.seen_via),
        }


def venue_map(rows: list[SiteRow], home: Optional[str] = None) -> dict[str, str]:
    """Server site name (``venue``) → the user's source name for that site.

    A site name two sources report maps to neither (2026-10-24 review: a
    registered gateway could claim another's site name and take its work);
    ``home`` relabels the home row (its source may be the ``home`` stand-in).
    """
    owners: dict[str, set[str]] = {}
    for row in rows:
        if row.site:
            owners.setdefault(row.site, set()).add(_row_name(row, home))
    return {site: names.pop() for site, names in owners.items() if len(names) == 1}


def _row_name(row: SiteRow, home: Optional[str]) -> str:
    return home if (row.home and home) else row.source


def site_label(venue: Optional[str], vmap: dict[str, str]) -> str:
    if venue is None:
        return UNREPORTED
    if venue == "":
        return SHARED
    return vmap.get(venue, venue)


def _untellable(job_id: str, venue: Any) -> HpcSiteError:
    return HpcSiteError(
        f"cannot tell which site runs job {job_id!r} (venue {venue!r}); "
        f"name it as {job_id}@<site> (r4 hpc sites)"
    )


def _bounded(fn: Callable[[Any], Any], items: list[Any], timeout: float) -> list[tuple[Any, str]]:
    """``fn`` over ``items`` in parallel: ``(result, "")`` or ``(None, error)``."""
    if not items:
        return []
    pool = ThreadPoolExecutor(max_workers=min(len(items), 8), thread_name_prefix="r4-hpc-jobs")
    futures = [pool.submit(fn, item) for item in items]
    done, _ = wait(futures, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)
    return [_outcome(fut, done, timeout) for fut in futures]


def _outcome(fut: Any, done: set[Any], timeout: float) -> tuple[Any, str]:
    if fut not in done:
        return None, f"no answer within {timeout:g} s"
    exc = fut.exception()
    if exc is not None:
        return None, (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
    return fut.result(), ""


def merge(per_site: list[tuple[str, list[Any]]], vmap: dict[str, str]) -> list[SiteJob]:
    """Each job once (first seen wins), labelled from its venue."""
    merged: dict[str, SiteJob] = {}
    for source_name, jobs in per_site:
        for job in jobs:
            row = merged.get(job.job_id)
            if row is None:
                venue = getattr(job, "venue", None)
                label = site_label(venue, vmap)
                row = merged[job.job_id] = SiteJob(job, label, venue, mapped=venue in vmap)
            row.seen_via.append(source_name)
    return list(merged.values())


def list_across_sites(
    base: Any = None, *, status: Optional[str] = None, limit: int = 50
) -> tuple[list[SiteJob], dict[str, str]]:
    """``(jobs, {site: error})`` over every site, deduplicated by ``job_id``."""
    base = _sites._base_config(base)
    sources = [source for source, _home in _sites.site_sources(base)]

    def _list(source: Any) -> list[Any]:
        return list(_sites._client_for(source, base).jobs.list(status=status, limit=limit))

    outcomes = _bounded(_list, sources, LIST_TIMEOUT_SEC)
    errors = {s.name: err for s, (_res, err) in zip(sources, outcomes) if err}
    per_site = [(s.name, res) for s, (res, err) in zip(sources, outcomes) if not err]
    return merge(per_site, venue_map(_sites.inventory(base, timeout=PROBE_TIMEOUT_SEC))), errors


# ── cancel / retry routing ───────────────────────────────────────────────────


def source_for_site(name: str, base: Any) -> Any:
    """The source for a site name (source name, else server site name);
    fails closed on anything else. ``shared`` is the home site (a shared
    row runs anywhere); ``?`` asks for the site by name."""
    if name == SHARED:
        return _home_source(base)
    if name == UNREPORTED:
        raise _untellable("<id>", None)
    pairs = _sites.site_sources(base)
    for source, _home in pairs:
        if source.name == name:
            return source
    by_site = venue_map(_sites.inventory(base))
    for source, _home in pairs:
        if by_site.get(name) == source.name:
            return source
    valid = ", ".join(s.name for s, _h in pairs) or "none"
    raise HpcSiteError(f"unknown_site: no HPC site named {name!r}; valid sites: {valid}")


def _home_source(base: Any) -> Any:
    for source, home in _sites.site_sources(base):
        if home:
            return source
    raise HpcSiteError("no home site: this profile names no gateway")


def source_for_job(job_id: str, base: Any) -> Any:
    """The source of the site that runs ``job_id``.

    The jobs table is shared, so the home site answers ``jobs.get`` for any
    job (one call, any age; review: listing 500 jobs per site missed older
    ones). A shared row (``""``) runs at home; a venue that is none of the
    user's sites is refused; a server that does not report venues is home
    in a one-site profile and refused otherwise.
    """
    home = _home_source(base)
    job = _sites._client_for(home, base).jobs.get(job_id)
    venue = getattr(job, "venue", None)
    pairs = _sites.site_sources(base)
    if venue == "" or (venue is None and len(pairs) < 2):
        return home
    vmap = venue_map(_sites.inventory(base)) if venue else {}
    if venue not in vmap:
        raise _untellable(job_id, venue)
    return source_for_site(vmap[venue], base)


def act_on_job(action: str, handle: str, base: Any = None) -> tuple[str, Any]:
    """``cancel``/``retry`` a job through its own site's gateway."""
    base = _sites._base_config(base)
    job_id, site = split_handle(handle)
    source = source_for_site(site, base) if site else source_for_job(job_id, base)
    jobs = _sites._client_for(source, base).jobs
    return str(source.name), getattr(jobs, action)(job_id)
