"""Bridge verbs for session worktrees (ADR-108 D-2, W1b/W1c).

The console's ``/worktree`` and the agent's tools drive the session's one
:class:`r4.companion.session_worktree.WorktreeController`. One bridge process
serves one chat tab, so a worktree is tab-scoped, like the project.

- ``worktree.list`` (read): the repository's worktrees, the one this session
  is in, and the mode.
- ``worktree.enter`` / ``worktree.exit``: through the controller. Refused
  mid-turn by the write lane.
- ``worktree.mode``: read, or set this session's ``off|auto``.
- ``worktree_event`` lines: every enter or exit, whoever made it (the user,
  the agent's tool, auto mode), so the console's chip follows the agent.

Every refusal is ``ok: false`` with words to show, never a raise.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

EVENT_CHANNEL = "worktree_event"


def _row(wt: Any) -> dict[str, Any]:
    from r4.worktrees import is_changed

    return {**wt.record(), "changed": is_changed(wt)}


def _refusal(message: str) -> dict[str, Any]:
    """A ``worktree.enter`` that did not happen (WorktreeEnterResult)."""
    return {
        "ok": False,
        "message": message,
        "workspace_root": os.getcwd(),
        "created": False,
        "warning": "",
        "worktree": None,
    }


def _exit_refusal(message: str) -> dict[str, Any]:
    """A ``worktree.exit`` that did not happen (WorktreeExitResult)."""
    return {
        "ok": False,
        "message": message,
        "workspace_root": os.getcwd(),
        "worktree_id": "",
        "removed": False,
        "changed": False,
    }


class WorktreeBridgeMixin:
    """Mixin expecting ``session`` (a companion session) and ``_emit_line``."""

    def _controller(self) -> Any:
        return getattr(getattr(self, "session", None), "worktree", None)

    def _attach_worktree_events(self, session: Any) -> None:
        """Forward the session's worktree changes to the console."""
        controller = getattr(session, "worktree", None)
        emit = getattr(self, "_emit_line", None)
        if controller is None or not callable(emit):
            return
        controller.add_listener(lambda event: emit({EVENT_CHANNEL: event}))

    def worktree_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        from r4.worktrees import WorkspaceError, current, list_worktrees

        root = os.getcwd()
        mode = self._mode_fields()
        try:
            rows = [_row(w) for w in list_worktrees(root)]
            here = current(root)
        except WorkspaceError as exc:
            return {
                "worktrees": [],
                "current": "",
                "workspace_root": root,
                "error": exc.detail,
                **mode,
            }
        return {
            "worktrees": rows,
            "current": here.id if here else "",
            "workspace_root": root,
            **mode,
        }

    def _mode_fields(self) -> dict[str, str]:
        controller = self._controller()
        if controller is None:
            return {"mode": "off", "mode_source": "default"}
        mode, source = controller.mode()
        return {"mode": mode, "mode_source": source}

    def worktree_enter(self, params: dict[str, Any]) -> dict[str, Any]:
        controller = self._controller()
        if controller is None:
            return _refusal("no session")
        name = str(params.get("name") or "").strip()
        if not name:
            return _refusal("a worktree name is required: /worktree <name>")
        entered: dict[str, Any] = controller.enter(name, base=str(params.get("base") or "head"))
        return entered

    def worktree_exit(self, params: dict[str, Any]) -> dict[str, Any]:
        controller = self._controller()
        if controller is None:
            return _exit_refusal("no session")
        left: dict[str, Any] = controller.exit(
            action=str(params.get("action") or "keep"), discard=bool(params.get("discard"))
        )
        return left

    def worktree_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read, or set this session's mode (``{"mode": "auto"}``)."""
        controller = self._controller()
        if controller is None:
            return {"ok": False, "message": "no session", "mode": "off", "mode_source": "default"}
        wanted = str(params.get("mode") or "").strip()
        if wanted:
            try:
                controller.set_session_mode(wanted)
            except ValueError as exc:
                return {"ok": False, "message": str(exc), **self._mode_fields()}
        mode, source = controller.mode()
        return {
            "ok": True,
            "message": f"worktree mode: {mode} ({source})",
            "mode": mode,
            "mode_source": source,
        }
