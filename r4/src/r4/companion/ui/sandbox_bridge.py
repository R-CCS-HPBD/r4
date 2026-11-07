"""Local tool sandbox RPCs for the console bridge (ADR-101 D-1, D-2, D-9, D-12).

- The launch preset ``r4 --sandbox on|off`` reaches the bridge as
  ``--sandbox`` (or ``R4_LAUNCH_SANDBOX``, always taken out of the
  environment so nothing the agent starts inherits it). ``initialize`` and
  ``load_session`` store it on the session (:func:`opening_sandbox`); a
  session that opens with the sandbox on moves local MCP servers under it
  (:func:`open_session_sandbox`).
- The snapshot carries ``session.sandbox`` (:func:`sandbox_state`): the
  context's status plus ``notice``, the one line the console shows when a
  local session opens with the sandbox off (D-1) or without a usable
  backend, ``mcp_error`` when its MCP servers did not follow an on sandbox,
  and ``detail``, what turning it on confines. A frontend session reports
  ``mode: "remote"`` and no notice: it runs in the server's podman sandbox
  (ADR-046).
- ``session.sandbox_get`` (read) and ``session.sandbox_set {mode, default?}``.
  Turning it on runs the backend probe and the self-test first and is refused
  with a typed error (``data.code`` ``sandbox.unavailable`` |
  ``sandbox.self_test_failed``) that changes nothing. ``default: true``
  writes the profile default first and puts the previous one back when the
  session cannot be saved, so a refusal changes nothing either. On success
  the choice is stored and saved, local MCP servers are moved under the
  sandbox (strictest wins, D-9) and the processes started before it that
  still run unconfined are listed, never killed. Only ``default`` off lets
  MCP sandboxing go back off. ``mcp_error`` says when the MCP servers did
  not follow (never swallowed).
- :func:`activate_sandbox` installs the active session's context for spawns
  that pin none. A turn pins its own session's context, and a background
  subagent or team plan the context it started under
  (:func:`r4.companion.sandbox.bind_sandbox`), so a session switch never
  reroutes a running agent's spawns (2026-10-30, review ESC-2/3, LC-5).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..sandbox import (
    REFUSED,
    SANDBOX_DETAIL_LINES,
    SANDBOX_NOTICE_OFF,
    SandboxContext,
    SandboxError,
    SandboxSetting,
    launch_setting,
    make_sandbox_setting,
    remember_sandbox_default,
    restore_sandbox_default,
    safe_profile_default,
    set_current_sandbox,
)

logger = logging.getLogger(__name__)

SANDBOX_REMOTE = "sandbox.remote"
REMOTE_REASON = (
    "a remote session runs in the server's sandbox (ADR-046); /sandbox applies to local sessions"
)
#: The sources a session pins at open: an environment or profile ``on`` is
#: kept on resume, so a later shell without them never weakens the session.
_PINNED_SOURCES = frozenset({"env", "profile"})
#: Settings r4 does not own here: a venue worker (off) and a process already
#: inside a sandbox (its MCP servers are not this process's to move).
_UNMANAGED_SOURCES = frozenset({"venue", "inherited"})
MCP_STALE = (
    "the running MCP daemon could not be restarted and keeps its previous "
    "sandbox setting; stop it so the next start applies this one"
)


class SandboxBridgeError(SandboxError):
    """A refused ``session.sandbox_set``; clients branch on ``data["code"]``."""

    @property
    def data(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


def launch_sandbox(spec: str | None) -> SandboxSetting | None:
    """The launch preset: ``--sandbox on|off``, else ``R4_LAUNCH_SANDBOX``.

    The variable is taken out of the environment either way. An invalid
    value is logged and ignored (the session's own order then applies).
    """
    return launch_setting(spec)


def is_frontend(session: Any) -> bool:
    """True for a session whose turns run in the remote sandbox."""
    metadata = getattr(session, "metadata", None)
    return isinstance(metadata, dict) and str(metadata.get("venue") or "") == "frontend"


def opening_sandbox(
    own: SandboxSetting | None, launch: SandboxSetting | None, resolved: SandboxSetting
) -> SandboxSetting | None:
    """What a session stores when it opens or resumes (D-2), None to keep it.

    A session without its own choice takes the launch flag, else pins an
    ``on`` that came from ``R4_SANDBOX`` or the profile default. A resumed
    session keeps its own choice; a launch ``on`` still tightens it (the
    strictest wins), a launch ``off`` never loosens it: ``/sandbox off`` does.
    """
    if own is not None:
        tighten = launch is not None and launch.enabled and not own.enabled
        return launch if tighten else None
    if launch is not None:
        return launch
    if resolved.enabled and resolved.source in _PINNED_SOURCES:
        return resolved
    return None


def apply_opening_sandbox(session: Any, launch: SandboxSetting | None) -> None:
    """Store :func:`opening_sandbox`'s answer on a local session."""
    if is_frontend(session) or _context_of(session) is None:
        return
    own = SandboxSetting.from_metadata(session.metadata)
    chosen = opening_sandbox(own, launch, session.sandbox_setting())
    if chosen is not None:
        session.set_sandbox(chosen)


def unavailable_notice(reason: str, *, enabled: bool = False) -> str:
    """The one line a session without a usable backend shows (D-1, D-3).

    On, every sandboxed spawn is refused (fail closed); off, they run with
    the user's permissions and turning the sandbox on is refused."""
    why = reason.strip() or "no sandbox backend on this system"
    if enabled:
        return (
            f"Sandbox unavailable: {why}. The sandbox is on, so every shell and Python "
            "command is refused until a backend works or you turn it off."
        )
    return (
        f"Sandbox unavailable: {why}. Shell, Python and MCP servers run with your "
        "full permissions; turning the sandbox on is refused until a backend works."
    )


def sandbox_notice(status: dict[str, Any]) -> str:
    """``notice`` of a status: the off line, the unavailable line, else ""."""
    if status.get("source") in _UNMANAGED_SOURCES or status.get("mode") == "remote":
        return ""
    if not status.get("available"):
        reason = str(status.get("reason") or "")
        return unavailable_notice(reason, enabled=status.get("mode") == "on")
    return SANDBOX_NOTICE_OFF if status.get("mode") == "off" else ""


def remote_state() -> dict[str, Any]:
    """``session.sandbox`` of a frontend session (D-11)."""
    return {
        "mode": "remote",
        "source": "venue",
        "backend": "",
        "available": True,
        "reason": REMOTE_REASON,
        "tmp_dir": "",
        "write_roots": [],
        "network": "remote sandbox proxy",
        "notice": "",
        "mcp_error": "",
        "detail": [],
    }


def _fallback_status(session: Any) -> dict[str, Any]:
    """A session double without a context: its stored choice, nothing probed
    (``available`` is not claimed false: the notice is the off line)."""
    metadata = getattr(session, "metadata", None)
    setting = SandboxSetting.from_metadata(metadata if isinstance(metadata, dict) else {})
    mode, source = (setting.mode, setting.source) if setting else ("off", "default_off")
    return {
        "mode": mode,
        "source": source,
        "backend": "",
        "available": True,
        "reason": "not probed",
        "tmp_dir": "",
        "write_roots": [],
        "network": "",
    }


def _context_of(session: Any) -> SandboxContext | None:
    """The session's context; None for a double that has none."""
    context = getattr(session, "sandbox_context", None)
    return context if isinstance(context, SandboxContext) else None


def sandbox_state(session: Any) -> dict[str, Any]:
    """The snapshot's ``session.sandbox`` (``SandboxState``).

    ``notice`` is the off or unavailable line, ``mcp_error`` why local MCP
    servers did not follow an on sandbox, ``detail`` what turning it on
    confines: the console renders them as sent, never its own copy.
    """
    if is_frontend(session):
        return remote_state()
    context = _context_of(session)
    status = dict(context.status()) if context is not None else _fallback_status(session)
    status["notice"] = sandbox_notice(status)
    status["mcp_error"] = str(getattr(session, "_sandbox_mcp_error", "") or "")
    status["detail"] = list(SANDBOX_DETAIL_LINES)
    return status


def activate_sandbox(session: Any) -> None:
    """Install the active session's context: what a spawn reads when none is pinned.

    A running turn, background subagent or team plan reads the context it
    pinned when it started, never this one (review ESC-2/3, LC-5).
    """
    context = _context_of(session)
    if context is not None:
        set_current_sandbox(context)


def _requested(params: dict[str, Any]) -> SandboxSetting:
    try:
        return make_sandbox_setting(str(params.get("mode") or ""), source="slash")
    except SandboxError as exc:
        raise SandboxBridgeError(exc.code, exc.reason) from exc


def _check_available(session: Any) -> None:
    try:
        session.sandbox_context.check_available()
    except SandboxError as exc:
        raise SandboxBridgeError(exc.code, exc.reason) from exc


def _mcp_stale() -> str:
    """:data:`MCP_STALE` when a running daemon did not pick the sandbox up."""
    from .. import mcp_exposure

    try:
        report = mcp_exposure.mcp_sandbox_status()
    except Exception:  # noqa: BLE001 — a failed probe is no evidence either way
        logger.warning("mcp_sandbox_status failed", exc_info=True)
        return ""
    in_sync = report.get("in_sync", True) if isinstance(report, dict) else True
    return "" if in_sync else MCP_STALE


def _mcp_call(hook: Callable[[], Any]) -> tuple[list[str], str]:
    """Run an MCP hook: (servers restarted, "" or why MCP did not follow)."""
    try:
        names = [str(item) for item in (hook() or [])]
    except Exception as exc:  # noqa: BLE001 — reported as mcp_error, never swallowed
        logger.warning("MCP sandbox hook failed", exc_info=True)
        return [], f"local MCP servers did not follow the sandbox: {exc}"
    return names, _mcp_stale()


def mcp_follow(
    setting: SandboxSetting, *, default: bool, profile: str | None = None
) -> tuple[list[str], str]:
    """Local MCP servers after a sandbox change (D-9, strictest wins).

    On moves them under the sandbox (snapshotting ``profile``'s user
    policy); only a profile default off, which the caller has written,
    releases them. Returns the servers restarted and "" or why they did not
    follow.
    """
    from .. import mcp_exposure

    if setting.enabled:
        return _mcp_call(lambda: mcp_exposure.require_mcp_sandbox(profile))
    if default:
        return _mcp_call(mcp_exposure.release_mcp_sandbox_default)
    return [], ""


def mcp_for_opening(setting: SandboxSetting, profile: str | None = None) -> str:
    """A session or run that opens with the sandbox on moves MCP under it too."""
    if not setting.enabled or setting.source in _UNMANAGED_SOURCES:
        return ""
    return mcp_follow(setting, default=False, profile=profile)[1]


def open_session_sandbox(
    session: Any, launch: SandboxSetting | None, profile: str | None = None
) -> str:
    """:func:`apply_opening_sandbox`, then MCP (:func:`mcp_for_opening`).

    Returns "" or why local MCP servers are not under an on sandbox; the
    session's snapshot carries it too (``mcp_error``).
    """
    if is_frontend(session) or _context_of(session) is None:
        return ""
    apply_opening_sandbox(session, launch)
    error = mcp_for_opening(session.sandbox_setting(), profile)
    session._sandbox_mcp_error = error
    return error


def _write_default(setting: SandboxSetting, profile: str) -> str:
    """``default: true``: the profile default, before anything else changes.

    Returns the previous default ("" when none was set) for the rollback.
    """
    previous = safe_profile_default(profile)
    try:
        remember_sandbox_default(setting.mode, profile=profile)
    except Exception as exc:  # noqa: BLE001 — any store failure refuses the verb
        raise SandboxBridgeError(REFUSED, f"could not write the profile default: {exc}") from exc
    return previous


def _restore_default(previous: str, profile: str) -> None:
    try:
        restore_sandbox_default(previous, profile=profile)
    except Exception:  # noqa: BLE001 — the save failure is the error reported
        logger.warning("could not restore the sandbox profile default", exc_info=True)


def _store_setting(host: Any, session: Any, setting: SandboxSetting) -> None:
    """Set and save; a failed save leaves the live session as it was."""
    before = session.metadata.get("sandbox")
    session.set_sandbox(setting)
    try:
        host._store.save(session)
    except Exception:
        if before is None:
            session.metadata.pop("sandbox", None)
        else:
            session.metadata["sandbox"] = before
        raise


def _store_with_default(host: Any, session: Any, setting: SandboxSetting, profile: str) -> None:
    """``default: true``: the profile default, then the session; a failed save
    puts the previous default back (review LC-6)."""
    previous = _write_default(setting, profile)
    try:
        _store_setting(host, session, setting)
    except Exception:
        _restore_default(previous, profile)
        raise


def _still_running(setting: SandboxSetting) -> list[str]:
    if not setting.enabled:
        return []
    from ..tools import unconfined_running

    try:
        return [str(item) for item in unconfined_running()]
    except Exception:  # noqa: BLE001 — a listing never fails the verb
        logger.warning("unconfined_running failed", exc_info=True)
        return []


class SandboxBridgeMixin:
    """Mixin expecting ``session``, ``client``, ``_store`` and ``snapshot`` on
    the host, and ``_sandbox_preset`` set in its ``__init__``."""

    _sandbox_preset: SandboxSetting | None = None

    def _sandbox_host(self) -> Any:
        return self

    def _sandbox_profile(self) -> str:
        config = getattr(getattr(self, "client", None), "config", None)
        return str(getattr(config, "profile", "") or "default")

    def _apply_initial_sandbox(self, session: Any) -> None:
        """``initialize`` / ``load_session``: the launch preset (D-2), MCP (D-9)."""
        open_session_sandbox(session, self._sandbox_preset, self._sandbox_profile())

    def session_sandbox_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"sandbox": sandbox_state(self._sandbox_host().session)}

    def session_sandbox_set(self, params: dict[str, Any]) -> dict[str, Any]:
        host = self._sandbox_host()
        session = host.session
        if is_frontend(session):
            raise SandboxBridgeError(SANDBOX_REMOTE, REMOTE_REASON)
        setting = _requested(params)
        if setting.enabled:
            _check_available(session)
        default = bool(params.get("default"))
        profile = self._sandbox_profile()
        if default:
            _store_with_default(host, session, setting, profile)
        else:
            _store_setting(host, session, setting)
        activate_sandbox(session)
        restarted, mcp_error = mcp_follow(setting, default=default, profile=profile)
        session._sandbox_mcp_error = mcp_error
        return {
            "sandbox": sandbox_state(session),
            "restarted_mcp": restarted,
            "still_running": _still_running(setting),
            "mcp_error": mcp_error,
            "state": host.snapshot(),
        }
