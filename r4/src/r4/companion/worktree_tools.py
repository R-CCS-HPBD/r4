"""The agent's worktree tools (ADR-108 D-2, W1c), modeled on Claude Code's
EnterWorktree / ExitWorktree.

``worktree_enter`` moves the session into an isolated git worktree of the
repository it works in (``.r4/worktrees/<name>``, branch ``r4/<name>``);
``worktree_exit`` moves it back. Both drive the session's one controller
(``session_worktree``), so the console's chip follows. With worktree mode
``auto`` the session enters one by itself before its first write; these tools
are for when the user asks.
"""

from __future__ import annotations

import json
from typing import Any

ENTER_DESCRIPTION = (
    "Move this session into an isolated git worktree of the current repository "
    "(.r4/worktrees/<name>, branch r4/<name>), made from the current state, "
    "uncommitted work included; the user's checkout is never written. Use it "
    "only when the user asks to work in a worktree (or the project's rules say "
    "so); with worktree mode 'auto' the session enters one by itself before its "
    "first write. Afterwards relative paths resolve in the worktree and writes "
    "outside it are refused."
)
EXIT_DESCRIPTION = (
    "Leave the session's worktree and return to the original checkout. "
    "action 'keep' (default) keeps it for the user to review or merge "
    "(git merge r4/<name>); 'remove' removes it only when it has no changes, "
    "unless discard is true (only when the user asked to throw the work away)."
)


def _controller() -> Any:
    from .session_worktree import current_controller

    return current_controller()


def _enter(name: str = "", base: str = "head") -> str:
    controller = _controller()
    if controller is None:
        return json.dumps({"ok": False, "message": "no session worktree support here"})
    chosen = (name or "").strip() or f"agent-{controller._session_key}"
    return json.dumps(controller.enter(chosen, base=base or "head", source="agent"))


def _exit(action: str = "keep", discard: bool = False) -> str:
    controller = _controller()
    if controller is None:
        return json.dumps({"ok": False, "message": "no session worktree support here"})
    return json.dumps(
        controller.exit(action=action or "keep", discard=bool(discard), source="agent")
    )


def build_worktree_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "worktree_enter",
            "description": ENTER_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Worktree name (letters, digits, '.', '_', '-'); "
                            "default agent-<session>"
                        ),
                    },
                    "base": {
                        "type": "string",
                        "enum": ["head", "default"],
                        "description": "Start from the current state (head) or the default branch",
                    },
                },
            },
            "execute": _enter,
        },
        {
            "name": "worktree_exit",
            "description": EXIT_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["keep", "remove"]},
                    "discard": {"type": "boolean"},
                },
            },
            "execute": _exit,
        },
    ]
