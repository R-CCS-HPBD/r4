"""Web-access RPCs for the console bridge (ADR-099 D-2, D-9).

The console asks the user for the agent's web access when a session opens,
local or remote, and on ``/web``. These verbs carry that choice:

- ``initialize`` takes an additive ``web_access: {mode, domains}`` (the
  answer to the question asked before the session was created); without it,
  a session that has no choice gets the launch preset: ``r4 --web``, handed
  over as ``--web-access`` (or ``R4_LAUNCH_WEB_ACCESS``, taken out of the
  environment). ``load_session`` applies the same preset. The snapshot
  carries ``session.web_access``; ``mode == ""`` tells the console to ask
  before the first turn.
- ``R4_WEB_ACCESS`` found in the environment is not a preset here: the
  console is interactive, so it only preselects the question (RS-1 fix W5).
  The bridge takes it out of its environment at start, so neither the
  session's web tools nor the shells the agent opens follow it unasked.
- ``session.web_access_get`` (read): the choice, the preselection (the
  ``R4_WEB_ACCESS`` value, else the profile's remembered last choice, in
  ``remembered``) and whether a launch preset exists.
- ``session.web_access_set``: the user's answer. Locally it applies to the
  next tool call. A running remote session answers
  ``venue.restart_required``: squid is configured when the sandbox starts.
- ``session.venue_policy`` (read): the server's preview of the caps a remote
  start would get (``GET …/venue/policy``), so the question offers only what
  is allowed. A server without the route answers ``limits: null``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..web_access import (
    WEB_ACCESS_ENV,
    WEB_SOURCES,
    WebAccess,
    WebAccessError,
    make_web_access,
    parse_web_access_spec,
    remember_web_access,
    remembered_for_profile,
    remote_start_choice,
    take_launch_web_access,
    venue_policy_preview,
    web_access_from_env,
)

logger = logging.getLogger(__name__)

_LIVE_REMOTE_STATES = frozenset({"provisioning", "running"})
RESTART_REQUIRED = (
    "venue.restart_required: a running remote session keeps the web access it started "
    "with (the sandbox proxy is set up at start); stop it with /venue stop and start it "
    "again to change it"
)


def launch_web_access(spec: str | None) -> WebAccess | None:
    """The launch preset (``r4 --web``): ``--web-access MODE[:d1,d2]``, else
    ``R4_LAUNCH_WEB_ACCESS`` (taken out of the environment).

    An invalid value is logged and ignored: the console then asks.
    """
    if not spec:
        return take_launch_web_access()
    try:
        return parse_web_access_spec(spec, source="flag")
    except WebAccessError as exc:
        logger.warning("ignoring the launch web access %r: %s", spec, exc)
        return None


def take_env_preselection() -> WebAccess | None:
    """``R4_WEB_ACCESS`` from the environment, taken out of it: in the
    console it preselects the question, it never answers it (RS-1 fix W5)."""
    try:
        found = web_access_from_env(os.environ)
    except WebAccessError as exc:
        logger.warning("ignoring R4_WEB_ACCESS: %s", exc)
        found = None
    os.environ.pop(WEB_ACCESS_ENV, None)
    return found


def remote_runtime_live(session: Any) -> bool:
    """True while the session's remote sandbox is provisioning or running."""
    metadata = getattr(session, "metadata", None) or {}
    if str(metadata.get("venue") or "") != "frontend":
        return False
    runtime = metadata.get("venue_runtime")
    return isinstance(runtime, dict) and runtime.get("state") in _LIVE_REMOTE_STATES


def _requested(params: dict[str, Any], default_source: str) -> WebAccess:
    source = str(params.get("source") or default_source)
    if source not in WEB_SOURCES:
        source = default_source
    try:
        return make_web_access(
            str(params.get("mode") or ""), params.get("domains") or [], source=source
        )
    except WebAccessError as exc:
        raise ValueError(f"web_access.invalid: {exc}") from exc


class WebAccessBridgeMixin:
    """Mixin expecting ``session``, ``client``, ``_store``, ``_venue_url`` and
    ``_session_id_str`` on the host, and ``_web_access_preset`` set in its
    ``__init__``."""

    _web_access_preset: WebAccess | None = None
    _web_access_preselect: WebAccess | None = None

    def _host(self) -> Any:
        """The bridge server this mixin is part of (its session, client, store)."""
        return self

    def _profile_name(self) -> str:
        config = getattr(getattr(self, "client", None), "config", None)
        return str(getattr(config, "profile", "") or "default")

    def _record_web_access(self, session: Any, access: WebAccess) -> None:
        """Store the choice on the session; an answered question is remembered."""
        session.metadata["web_access"] = access.to_metadata()
        if access.source != "prompt":
            return
        try:
            remember_web_access(access, profile=self._profile_name())
        except OSError:  # the profile store is a convenience; the session keeps it
            logger.warning("could not remember the web access choice", exc_info=True)

    def _apply_initial_web_access(self, session: Any, raw: Any) -> None:
        """``initialize``'s ``web_access`` answer, else the launch preset for a
        session without a choice (a resumed session keeps its own, D-2)."""
        if isinstance(raw, dict) and raw.get("mode"):
            self._record_web_access(session, _requested(raw, "prompt"))
            return
        preset = self._web_access_preset
        # A local ``default_off`` is no choice: ``r4 --web`` answers for it
        # (RS-1 round 4, web R4-3; round-3 N2's rule).
        own = remote_start_choice(WebAccess.from_metadata(session.metadata))
        if preset is not None and own is None:
            session.metadata["web_access"] = preset.to_metadata()

    def session_web_access_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        remembered = self._web_access_preselect or remembered_for_profile(self._profile_name())
        return {
            "web_access": WebAccess.from_metadata(self._host().session.metadata).to_metadata(),
            "remembered": remembered.to_metadata() if remembered else None,
            "preset": self._web_access_preset is not None,
        }

    def session_web_access_set(self, params: dict[str, Any]) -> dict[str, Any]:
        host = self._host()
        session = host.session
        if remote_runtime_live(session):
            raise ValueError(RESTART_REQUIRED)
        access = _requested(params, "prompt")
        self._record_web_access(session, access)
        host._store.save(session)
        answer: dict[str, Any] = {"web_access": access.to_metadata()}
        notice = _search_server_notice(access)
        if notice:
            answer["notice"] = notice
            answer["search_server_offer"] = _can_start_search_server()
        return answer

    def session_venue_policy(self, params: dict[str, Any]) -> dict[str, Any]:
        """The preview body; ``limits: null`` (every mode offered) when the
        server has no preview, with ``error`` when the call failed."""
        host = self._host()
        sid = str(params.get("session_id") or host._session_id_str())
        config = host.session.metadata.get("config")
        config = config if isinstance(config, dict) else {}
        project = str(params.get("project") or config.get("project_id") or "")
        preview, error = venue_policy_preview(host.client, host._venue_url(sid, "/policy"), project)
        answer = _preview_answer(preview or {}, sid, project)
        if error:
            answer["error"] = error
        return answer


SEARCH_SERVER_NOTICE = (
    "web_search needs a search server: run `r4 search-server up` (a local SearXNG) "
    "or set R4_WEB_SEARCH_URL. web_fetch works now."
)


def _search_server_notice(access: WebAccess) -> str:
    """ADR-102: public access without a search backend says how to add one."""
    if access.mode != "public":
        return ""
    from r4.agent import web_search

    return "" if web_search.configured() else SEARCH_SERVER_NOTICE


def _can_start_search_server() -> bool:
    """ADR-102 amendment: the console offers ``r4 search-server up`` only
    where it can run (podman or docker installed)."""
    from r4.agent.searxng_local import container_engine

    return container_engine() is not None


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _preview_answer(preview: dict[str, Any], sid: str, project: str) -> dict[str, Any]:
    """The preview in the bridge's shape (every key present, typed)."""
    return {
        "session_id": str(preview.get("session_id") or sid),
        "project_id": str(preview.get("project_id") or project),
        "choice_required": preview.get("choice_required") is not False,
        "limits": _dict_or_none(preview.get("limits")),
        "current": _dict_or_none(preview.get("current")),
    }
