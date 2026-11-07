"""One rule for the agent settings a session runs with, local and remote.

ADR-099 D-8 (maintainer decision M-6, 2026-10-23): a remote session behaves
like the user's local session with the same settings. The local bridge
computes the settings with :func:`effective_agent_settings` and applies them
with :func:`apply_agent_settings`; it sends the same document to the server
(``PUT …/venue/settings``), and the remote worker applies it with the same
function. Before this, the worker ignored autopilot, never got the bridge's
``auto_safe`` default and never set workflow arming.

The document (``schema`` 1)::

    permission_mode         ask_always | auto_safe | trust | deny  (derived from
                            interaction_mode, ADR-104; ``trust`` alone = autopilot)
    interaction_mode        chat | plan | autopilot
    write_requires_plan     bool
    workflow_armed          bool
    ask_user_mode           ask | auto   (the user's own setting)
    approval_ttl_s          remembered per-call approval TTL, seconds
    session_approved_tools  tools approved "always" in this session

Unknown keys are ignored here and kept by the server.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from .agent_mode import MODES, apply_mode, normalize_mode, permission_for, session_mode
from .permissions import _remembered_approval_ttl

logger = logging.getLogger(__name__)

SETTINGS_SCHEMA = 1
ASK_USER_MODES = ("ask", "auto")
INTERACTION_MODES = MODES
DEFAULT_ASK_USER_MODE = "ask"
_APPLIED_ATTR = "_applied_agent_settings"

#: The tool result when the user's ``ask_user_mode`` is ``auto`` (local and
#: remote use this one text).
ASK_USER_AUTO_ANSWER = (
    "[ask_user is set to auto-answer in the user's settings (ask_user_mode=auto): "
    "the question was not shown to the user — choose the most reasonable option, "
    "state the assumption, and continue]"
)

#: The tool result when a remote question expired unanswered (ADR-099 D-8).
ASK_USER_NO_ANSWER = (
    "[ask_user: the user has not answered; do not assume an answer: end the turn "
    "and restate the question]"
)

#: The tool result when the owner's r4 client predates remote questions (it
#: never sent agent settings, rev 0) and so cannot show one: the worker waits
#: ``R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC`` (120 s), then says why.
ASK_USER_OLD_CLIENT = (
    "[ask_user: no answer: your r4 client is too old to answer remote questions; "
    "upgrade r4. Do not assume an answer: end the turn, restate the question and "
    "tell the user to upgrade r4 to answer it]"
)


def ask_user_auto_answer() -> str:
    """The one auto-answer text, local and remote."""
    return ASK_USER_AUTO_ANSWER


def normalize_ask_user_mode(value: Any) -> str:
    """``ask`` or ``auto``; anything else is the default ``ask``."""
    mode = str(value or "").strip().lower()
    return mode if mode in ASK_USER_MODES else DEFAULT_ASK_USER_MODE


def configured_ask_user_mode(config: Any = None) -> str:
    """The user's ``ask_user_mode``: the r4 ``Config`` field, else
    ``R4_ASK_USER_MODE``, else ``ask``."""
    value = getattr(config, "ask_user_mode", "") if config is not None else ""
    return normalize_ask_user_mode(value or os.environ.get("R4_ASK_USER_MODE", ""))


def _metadata(session: Any) -> Mapping[str, Any]:
    metadata = getattr(session, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _interaction_mode(session: Any, launch_mode: str, allow_all_tools: bool = False) -> str:
    """The launch flag's mode (``--allow-all-tools`` is autopilot), else the
    session's own (ADR-104 D-1)."""
    if allow_all_tools:
        return "autopilot"
    return normalize_mode(launch_mode) or session_mode(session)


def _approved_tools(session: Any) -> list[str]:
    names = getattr(getattr(session, "permissions", None), "session_approved_names", ())
    try:
        return sorted(str(name) for name in names)
    except TypeError:
        return []


def effective_agent_settings(
    session: Any,
    *,
    launch_mode: str = "",
    allow_all_tools: bool = False,
    ask_user_mode: str = "",
) -> dict[str, Any]:
    """The settings ``session`` runs with under the local bridge's rules."""
    metadata = _metadata(session)
    interaction = _interaction_mode(session, launch_mode, allow_all_tools)
    return {
        "schema": SETTINGS_SCHEMA,
        # Derived from the mode, never set on its own (ADR-104 D-1); kept in
        # the document for the umrs validator and older workers.
        "permission_mode": permission_for(interaction).value,
        "interaction_mode": interaction,
        "write_requires_plan": bool(metadata.get("write_requires_plan")),
        "workflow_armed": bool(metadata.get("workflow_armed")),
        "ask_user_mode": normalize_ask_user_mode(ask_user_mode),
        "approval_ttl_s": int(_remembered_approval_ttl()),
        "session_approved_tools": _approved_tools(session),
    }


def _document_mode(settings: Mapping[str, Any]) -> str | None:
    """The mode a document asks for: ``interaction_mode``; an older client's
    ``permission_mode: trust`` beside chat (or alone) was its sticky trust
    override and means autopilot (as :func:`agent_mode.migrate_metadata`
    reads it); None when the document names no mode."""
    mode = normalize_mode(settings.get("interaction_mode"))
    if str(settings.get("permission_mode") or "") == "trust" and mode in (None, "chat"):
        return "autopilot"
    return mode


def _apply_mode(session: Any, settings: Mapping[str, Any]) -> None:
    mode = _document_mode(settings)
    if mode is None:
        return
    if not hasattr(session, "metadata") or not hasattr(session, "permissions"):
        return
    apply_mode(session, mode)


def _apply_metadata(session: Any, settings: Mapping[str, Any]) -> None:
    """Session-scoped settings into metadata, writing only what changes (a
    document computed from the same metadata leaves it byte-identical)."""
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return
    for flag in ("write_requires_plan", "workflow_armed"):
        if flag in settings and bool(settings[flag]) != bool(metadata.get(flag)):
            metadata[flag] = bool(settings[flag])


def _apply_workflow_arm(settings: Mapping[str, Any]) -> None:
    if "workflow_armed" not in settings:
        return
    from .tools import set_workflow_armed_flag

    set_workflow_armed_flag(bool(settings["workflow_armed"]))


def _apply_approval_ttl(value: Any) -> None:
    """The remembered-approval TTL is read from the environment per call;
    set it only when it differs (the remote worker serves one session)."""
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        return
    if ttl > 0 and ttl != int(_remembered_approval_ttl()):
        os.environ["R4_PERMISSION_APPROVAL_TTL"] = str(ttl)


def _apply_approved_tools(session: Any, tools: Any) -> None:
    permissions = getattr(session, "permissions", None)
    approve = getattr(permissions, "approve_for_session", None)
    if not callable(approve) or not isinstance(tools, (list, tuple)):
        return
    already = set(_approved_tools(session))
    for name in tools:
        if isinstance(name, str) and name and name not in already:
            approve(name)


def apply_agent_settings(session: Any, settings: Mapping[str, Any]) -> None:
    """Make ``session`` run with ``settings`` (known keys only; additive for
    approved tools). The applied document is kept on the session object."""
    _apply_mode(session, settings)
    _apply_metadata(session, settings)
    _apply_workflow_arm(settings)
    if "approval_ttl_s" in settings:
        _apply_approval_ttl(settings["approval_ttl_s"])
    _apply_approved_tools(session, settings.get("session_approved_tools"))
    try:
        setattr(session, _APPLIED_ATTR, dict(settings))
    except AttributeError:
        logger.debug("applied agent settings not recorded on %r", type(session).__name__)


def applied_agent_settings(session: Any) -> dict[str, Any]:
    """The document last applied to ``session`` ({} when none)."""
    applied = getattr(session, _APPLIED_ATTR, None)
    return dict(applied) if isinstance(applied, dict) else {}


__all__ = [
    "ASK_USER_AUTO_ANSWER",
    "ASK_USER_MODES",
    "ASK_USER_NO_ANSWER",
    "ASK_USER_OLD_CLIENT",
    "SETTINGS_SCHEMA",
    "applied_agent_settings",
    "apply_agent_settings",
    "ask_user_auto_answer",
    "configured_ask_user_mode",
    "effective_agent_settings",
    "normalize_ask_user_mode",
]
