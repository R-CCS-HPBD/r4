"""Login-time adoption of server-visible projects into the local registry.

Maintainer ask (2026-10-20): project and circle state must sync from the
server at login. The console's project list reads the per-machine local
registry (``ProjectManager.list``); a fresh machine showed nothing until
every platform project was re-adopted by id by hand — invisible to anyone
who did not already know the ids. After a successful ``r4 auth login``
(which the console's `/login` and `/connect` wizard both shell out to) the
client pulls the two server truths and adopts their project ids:

- owned rows from ``GET /gw/projects`` (``Client.list_platform_projects``;
  platform admins see every row), and
- circle-derived projects from ``GET /gw/circles/mine`` (ADR-086:
  affiliation = holding a circle in the project).
- the account's live memberships, the ``memberships`` ids of the same
  ``GET /gw/projects`` answer (ADR-097: what ``umrs admin user
  add-to-project`` grants; the gateway accepts any of them as the
  request's ``X-Project-ID``).

Rules never violated: existing local entries are never touched (names,
gateways, models and metadata belong to the machine); the current-project
selection is never moved; a server/network failure degrades that leg to an
empty list and never breaks the login. Adopted entries record the
profile's resolved gateway url (the edge the rows came from) and the
platform name when the owned-row listing carries one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SYNC_TIMEOUT_SECONDS = 10


def _platform_listing(client: Any) -> dict[str, Any]:
    """``GET /gw/projects``: owned rows and live memberships; {} when the leg
    fails alone."""
    try:
        payload = client.list_platform_projects(timeout=SYNC_TIMEOUT_SECONDS) or {}
    except Exception as exc:  # noqa: BLE001 — the circles leg still runs
        logger.warning("project sync: platform projects unreadable: %s", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _owned_rows(listing: dict[str, Any]) -> list[dict[str, Any]]:
    """The caller's durable project rows."""
    return [r for r in listing.get("projects") or [] if isinstance(r, dict)]


def _membership_ids(listing: dict[str, Any]) -> list[str]:
    """ADR-097 live memberships; [] from a server that predates them (or
    could not read them: ``null``)."""
    return [
        str(p).strip() for p in listing.get("memberships") or [] if isinstance(p, str) and p.strip()
    ]


def _circle_project_ids(client: Any) -> list[str]:
    """ADR-086 derived projects (affiliation), [] when the leg fails alone."""
    try:
        payload = client.list_my_circles(timeout=SYNC_TIMEOUT_SECONDS) or {}
    except Exception as exc:  # noqa: BLE001 — the owned leg still runs
        logger.warning("project sync: circles unreadable: %s", exc)
        return []
    return [
        str(p).strip() for p in payload.get("projects") or [] if isinstance(p, str) and p.strip()
    ]


def collect_server_project_ids(client: Any) -> tuple[list[str], dict[str, str]]:
    """Owned ∪ member ∪ circle-derived ids in first-seen order, plus owned-row names."""
    ids: list[str] = []
    names: dict[str, str] = {}
    listing = _platform_listing(client)
    for row in _owned_rows(listing):
        pid = str(row.get("project_id") or "").strip()
        if pid and pid not in ids:
            ids.append(pid)
            names[pid] = str(row.get("name") or "")
    for pid in _membership_ids(listing) + _circle_project_ids(client):
        if pid not in ids:
            ids.append(pid)
    return ids, names


def sync_server_projects(client: Any, pm: Any, *, project_config_cls: Any) -> dict[str, list[str]]:
    """Adopt every synced id the registry lacks; return adopted + already-known.

    Existing entries stay untouched (this machine's customizations win) and
    the current selection is never moved.
    """
    ids, names = collect_server_project_ids(client)
    gateway = getattr(getattr(client, "_config", None), "gateway_url", None) or None
    adopted: list[str] = []
    for pid in ids:
        if pm.get(pid) is not None:
            continue
        pm.create(
            project_config_cls(
                project_id=pid,
                name=names.get(pid) or None,
                gateway_url=gateway,
            )
        )
        adopted.append(pid)
    return {"adopted": adopted, "known": [p for p in ids if p not in adopted]}
