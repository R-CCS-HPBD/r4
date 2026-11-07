"""Register a project on the platform: ``POST /gw/projects`` (ADR-051 D-1).

Maintainer 2026-11-03: "i was not sure [how] to register new project on
server". ``r4 project create`` and the console's ``/project create`` write
the local registry only (``client.project_expected_on_platform``): such a
project has no server row, so the first platform call answers
``project_not_member``. The server lets any verified user create a project
and makes the caller its owner; until now only ``./r4-local register``
(curl) called that route.

One path for every surface: ``r4 project register`` (CLI), the bridge's
``project_register`` RPC (console ``/project register``). On success the
local registry gets the project too, recorded against the gateway it now
lives on, so later lookups treat a 404 as a denial (fail closed) rather than
"local only". Never moves the current-project selection.
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import APIError, AuthenticationError, R4ConnectionError


class RegisterError(Exception):
    """A registration that did not happen, with the words to show for it."""


def _gateway(client: Any) -> str:
    return str(getattr(getattr(client, "config", None), "gateway_url", "") or "").rstrip("/")


def _adopt_locally(pm: Any, project_id: str, name: str, gateway: str) -> str:
    """'added' (new local entry), 'linked' (gateway recorded) or 'kept'."""
    from .project import ProjectConfig

    existing = pm.get(project_id)
    if existing is None:
        pm.create(
            ProjectConfig(project_id=project_id, name=name or None, gateway_url=gateway or None)
        )
        return "added"
    if existing.gateway_url or not gateway:
        return "kept"
    existing.gateway_url = gateway
    pm.create(existing)
    return "linked"


def _taken(exc: APIError) -> bool:
    code = str(exc.details.get("code") or "")
    return exc.status_code == 409 or code.endswith("conflict") or "taken" in exc.message


def _api_error_text(exc: APIError, project_id: str, gateway: str) -> str:
    if _taken(exc):
        return f"project id '{project_id}' is already taken on {gateway} — pick another id"
    return f"the platform at {gateway} refused to register '{project_id}': {exc.message}"


def error_text(exc: Exception, project_id: str, gateway: str) -> str:
    """Plain words for a failed registration (the next step included)."""
    if isinstance(exc, AuthenticationError):
        return (
            f"not signed in to {gateway} (or the token is not accepted) — sign in first "
            "(`r4 auth login`, or /connect in the console), then register again"
        )
    if isinstance(exc, R4ConnectionError):
        return f"cannot reach the platform at {gateway}: {exc.message}"
    if isinstance(exc, APIError):
        return _api_error_text(exc, project_id, gateway)
    return f"registering '{project_id}' failed: {exc}"


def _checked_target(client: Any, project_id: str) -> tuple[str, str]:
    """(project id, gateway) or :class:`RegisterError` when either is missing."""
    project_id = (project_id or "").strip()
    if not project_id:
        raise RegisterError("a project id is required: r4 project register <id>")
    gateway = _gateway(client)
    if not gateway:
        raise RegisterError(
            "no platform is configured for this profile, so the project can only live on "
            "this machine — connect to one first (`r4 init`, or /connect in the console)"
        )
    return project_id, gateway


def _server_row(client: Any, project_id: str, name: str, gateway: str) -> dict[str, Any]:
    """POST /gw/projects → the row; any failure as :class:`RegisterError`."""
    try:
        reply = client.create_platform_project(project_id, name=name)
    except Exception as exc:  # noqa: BLE001 — every failure gets its own words
        raise RegisterError(error_text(exc, project_id, gateway)) from exc
    return (reply or {}).get("project") or {}


def _manager(pm: Optional[Any]) -> Any:
    if pm is not None:
        return pm
    from .project import get_project_manager

    return get_project_manager()


def register_project(
    client: Any,
    project_id: str,
    *,
    name: str = "",
    pm: Optional[Any] = None,
) -> dict[str, Any]:
    """Create ``project_id`` on the platform (idempotent for its owner) and
    record it locally. Raises :class:`RegisterError` with user-facing text."""
    project_id, gateway = _checked_target(client, project_id)
    row = _server_row(client, project_id, name, gateway)
    row_name = str(row.get("name") or name or "")
    local = _adopt_locally(_manager(pm), project_id, row_name, gateway)
    return {
        "project_id": project_id,
        "name": row_name,
        "owner": str(row.get("owner_id") or ""),
        "gateway_url": gateway,
        "local": local,
    }


def registered_text(result: dict[str, Any]) -> str:
    """The success line every surface prints."""
    owner = f" (owner: {result['owner']})" if result.get("owner") else ""
    return (
        f"✓ project {result['project_id']} is registered on {result['gateway_url']}{owner}; "
        "loops, jobs and knowledge can use it now"
    )


#: What `create` says about a project that exists on this machine only.
LOCAL_ONLY_NOTE = (
    "note: this project is on this machine only; to use it with the platform (loops, "
    "jobs, knowledge), register it once: r4 project register {project_id}"
)
