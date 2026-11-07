"""The session's one mode and what it means for tool calls (ADR-104 D-1).

A session has one mode, ``chat | plan | autopilot``, kept in
``metadata["interaction_mode"]``. The permission mode the
:class:`~r4.companion.permissions.PermissionManager` runs with is derived
from it here and never stored on its own:

    chat       auto_safe  read-only tools run; everything else asks
    plan       auto_safe  read-only tools only (other calls are refused by the
                          turn, ``turn_context.PLAN_MODE_REFUSAL``); a plan first
    autopilot  trust      every tool, except what the ladder always asks

Every surface (bridge RPC and slash command, ACP, plan confirmation, session
restore, the remote settings document) changes the mode through
:func:`apply_mode` and reads it through :func:`session_mode`.

Retired keys (read once at restore, then removed): ``permission_override``
(a sticky permission mode that outlived mode switches) and ACP's
``auto_approve_tools``. A legacy ``trust`` in either becomes ``autopilot``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from .permissions import PermissionMode

logger = logging.getLogger(__name__)

MODES: tuple[str, ...] = ("chat", "plan", "autopilot")
DEFAULT_MODE = "chat"
MODE_KEY = "interaction_mode"
PLAN_STATE_KEY = "plan_mode_state"
_RETIRED_KEYS = ("permission_override", "auto_approve_tools")

# Other words surfaces have used for the same modes (ACP ``ask``/``agent``,
# the retired ``/trust`` and ``--yolo`` shorthands, permission-mode names).
_ALIASES: dict[str, str] = {
    "ask": "chat",
    "default": "chat",
    "ask_always": "chat",
    "auto_safe": "chat",
    "agent": "autopilot",
    "auto": "autopilot",
    "trust": "autopilot",
    "yolo": "autopilot",
}

_PERMISSION_FOR: dict[str, PermissionMode] = {
    "chat": PermissionMode.AUTO_SAFE,
    "plan": PermissionMode.AUTO_SAFE,
    "autopilot": PermissionMode.TRUST,
}


def normalize_mode(value: Any) -> str | None:
    """The mode a word names (aliases included), or None when it names none."""
    word = str(value or "").strip().lower().replace("-", "_")
    word = _ALIASES.get(word, word)
    return word if word in MODES else None


def permission_for(mode: str) -> PermissionMode:
    """The permission mode a session mode runs with (``chat`` for unknown)."""
    return _PERMISSION_FOR.get(normalize_mode(mode) or DEFAULT_MODE, PermissionMode.AUTO_SAFE)


def _metadata(session: Any) -> Mapping[str, Any]:
    metadata = getattr(session, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _legacy_autopilot(metadata: Mapping[str, Any]) -> bool:
    """A session saved before ADR-104 that effectively ran in TRUST."""
    override = str(metadata.get("permission_override") or "").strip().lower()
    return override == PermissionMode.TRUST.value or metadata.get("auto_approve_tools") is True


def mode_of(metadata: Mapping[str, Any]) -> str:
    """The mode recorded in session metadata (legacy keys honoured)."""
    stored = normalize_mode(metadata.get(MODE_KEY)) or DEFAULT_MODE
    if stored == "chat" and _legacy_autopilot(metadata):
        return "autopilot"
    return stored


def session_mode(session: Any) -> str:
    """The session's current mode."""
    return mode_of(_metadata(session))


def migrate_metadata(metadata: MutableMapping[str, Any]) -> str:
    """Fold the retired keys into ``interaction_mode`` and drop them; returns
    the mode. Idempotent; a session without them is unchanged."""
    mode = mode_of(metadata)
    if not any(key in metadata for key in _RETIRED_KEYS):
        return mode
    for key in _RETIRED_KEYS:
        metadata.pop(key, None)
    if mode == DEFAULT_MODE:
        metadata.pop(MODE_KEY, None)
    else:
        metadata[MODE_KEY] = mode
    return mode


def _record_mode(metadata: MutableMapping[str, Any], mode: str, *, keep_plan: bool) -> None:
    previous = mode_of(metadata)
    for key in _RETIRED_KEYS:
        metadata.pop(key, None)
    metadata[MODE_KEY] = mode
    if previous == "plan" and mode != "plan" and not keep_plan:
        metadata.pop(PLAN_STATE_KEY, None)


def sync_permissions(session: Any) -> PermissionMode:
    """Set the session's permission mode from its mode (and wire the server
    trust allowlist whenever that is TRUST, ADR-035); returns it."""
    permission = permission_for(session_mode(session))
    set_mode = getattr(getattr(session, "permissions", None), "set_mode", None)
    if callable(set_mode):
        set_mode(permission)
    wire = getattr(session, "_wire_trust_checker", None)
    if permission is PermissionMode.TRUST and callable(wire):
        wire(getattr(session, "_client_ref", None), permission)
    return permission


def apply_mode(session: Any, mode: Any, *, keep_plan: bool = False) -> str:
    """Switch ``session`` to ``mode``: record it, drop the retired keys, set
    the permission mode. Leaving ``plan`` clears a pending plan unless
    ``keep_plan`` (a plan being executed keeps its state). Raises
    ``ValueError`` for a word that names no mode."""
    normalized = normalize_mode(mode)
    if normalized is None:
        raise ValueError(f"unknown mode: {mode!r} (chat|plan|autopilot)")
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, MutableMapping):
        _record_mode(metadata, normalized, keep_plan=keep_plan)
    sync_permissions(session)
    return normalized


__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "MODE_KEY",
    "apply_mode",
    "migrate_metadata",
    "mode_of",
    "normalize_mode",
    "permission_for",
    "session_mode",
    "sync_permissions",
]
