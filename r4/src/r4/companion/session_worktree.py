"""The session's worktree: enter, leave, and auto mode (ADR-108 D-2, W1c).

Maintainer 2026-11-04: the worktree "should be automatically built by agents
on cli, tui, vscode when it is enabled". One controller per companion
session, so every surface drives the same code: the console's
``/worktree`` (bridge), the agent's ``worktree_enter`` / ``worktree_exit``
tools, ``r4 -p``, the REPL and VS Code.

- **enter.** Make or reuse a worktree (``r4.worktrees``), ``chdir`` into it
  and re-root the session (``rebind``). Afterwards the session's file, shell
  and Python tools run under the workspace guard (ADR-092
  ``agent_workspace``): writes must land in the worktree, reads may also
  reach the checkout it was made from. An absolute path into the user's
  checkout is refused by name, never written.
- **exit.** Back to where the session was. ``remove`` removes the worktree
  when it has no changes, or with ``discard``; a changed one is kept.
- **mode.** ``off`` (default) or ``auto``. Resolution: the session's
  ``/worktree mode``, else ``R4_WORKTREE``, else the profile's
  ``worktree_mode`` (``r4 config worktree``). In ``auto``, before the
  session's first writing tool call in a git repository the session enters
  worktree ``auto-<session>``. Reads never trigger it.
- **events.** Each enter or exit is told to the listeners (the bridge sends
  ``worktree_event`` to the console, so the tab's chip follows a change the
  agent made).
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

MODES = ("off", "auto")
MODE_ENV = "R4_WORKTREE"
PROFILE_KEY = "worktree_mode"

#: Tools that write the local filesystem or start a local process (the
#: ADR-092 writer set): the first one in auto mode enters the worktree.
WRITERS = frozenset(
    {
        "file_write",
        "file_edit",
        "apply_patch",
        "shell_exec",
        "python_exec",
        "compile_code",
        "run_code",
        "test_code",
        "test_fix_loop",
        "project_scaffold",
    }
)

Listener = Callable[[dict[str, Any]], None]


def _parse_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in MODES else ""


def profile_mode(profile: Optional[str] = None) -> str:
    """The profile's stored mode ("" when unset or unreadable)."""
    try:
        from ..config import ProfileConfigStore
        from .sandbox.setting import profile_name

        stored = ProfileConfigStore().load(profile_name(profile))
    except Exception:  # noqa: BLE001 - an unreadable store is no default
        return ""
    return _parse_mode(stored.get(PROFILE_KEY))


def remember_profile_mode(mode: str, profile: Optional[str] = None) -> None:
    """``r4 config worktree auto|off``: store the profile default."""
    from ..config import ProfileConfigStore
    from .sandbox.setting import profile_name

    parsed = _parse_mode(mode)
    if not parsed:
        raise ValueError(f"worktree mode must be one of {', '.join(MODES)}")
    ProfileConfigStore().save(profile_name(profile), **{PROFILE_KEY: parsed})


def _result(ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "message": message, "workspace_root": os.getcwd(), **extra}


def _row(wt: Any) -> dict[str, Any]:
    from r4.worktrees import is_changed

    return {**wt.record(), "changed": is_changed(wt)}


class WorktreeController:
    """One session's worktree state."""

    def __init__(self, *, owner: str, rebind: Callable[[], None], session_key: str = "") -> None:
        self.owner = owner
        self._rebind = rebind
        self._session_key = (session_key or "session")[:8]
        self.origin = ""
        self.worktree: Any = None
        self.session_mode = ""
        self._listeners: list[Listener] = []

    # ── mode ──────────────────────────────────────────────────────────────

    def mode(self) -> tuple[str, str]:
        """``(mode, source)``: session, env, profile, else ``off``/default."""
        for value, source in (
            (self.session_mode, "session"),
            (os.environ.get(MODE_ENV), "env"),
            (profile_mode(), "profile"),
        ):
            parsed = _parse_mode(value)
            if parsed:
                return parsed, source
        return "off", "default"

    def set_session_mode(self, mode: str) -> str:
        parsed = _parse_mode(mode)
        if not parsed:
            raise ValueError(f"worktree mode must be one of {', '.join(MODES)}")
        self.session_mode = parsed
        return parsed

    # ── listeners ─────────────────────────────────────────────────────────

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _notify(self, event: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a console that went away must not fail a tool
                logger.debug("worktree listener failed", exc_info=True)

    # ── enter / exit ──────────────────────────────────────────────────────

    def enter(self, name: str, *, base: str = "head", source: str = "user") -> dict[str, Any]:
        """Make or reuse ``name`` and work there; a refusal is ``ok: false``."""
        from r4.worktrees import WorkspaceError

        name = (name or "").strip()
        if not name:
            return _result(
                False, "a worktree name is required", created=False, warning="", worktree=None
            )
        if self.worktree is not None:
            message = f"already in worktree {self.worktree.id}: leave it first"
            return _result(False, message, created=False, warning="", worktree=None)
        try:
            wt, created, warning = self._worktree_for(name, base)
        except WorkspaceError as exc:
            return _result(
                False,
                f"worktree not entered: {exc.detail}",
                created=False,
                warning="",
                worktree=None,
            )
        self.origin, self.worktree = os.getcwd(), wt
        os.chdir(wt.path)
        self._rebind()
        self._register_exit_cleanup()
        verb = "created and entered" if created else "entered"
        message = f"worktree {wt.id} {verb}: {wt.path} (branch {wt.branch})"
        result = _result(True, message, created=created, warning=warning, worktree=_row(wt))
        self._notify({"action": "enter", "source": source, **result})
        return result

    def _worktree_for(self, name: str, base: str) -> tuple[Any, bool, str]:
        from r4.worktrees import create, find

        existing = find(os.getcwd(), name)
        if existing is not None:
            return existing, False, ""
        wt, warning = create(os.getcwd(), name, owner=self.owner, level="session", base=base)
        return wt, True, warning

    def exit(
        self, *, action: str = "keep", discard: bool = False, source: str = "user"
    ) -> dict[str, Any]:
        """Back to the origin; ``remove`` removes an unchanged worktree."""
        if self.worktree is None:
            return _result(
                False,
                "this session is not in a worktree",
                worktree_id="",
                removed=False,
                changed=False,
            )
        wt_id, origin = self.worktree.id, self.origin
        os.chdir(origin)
        self.origin, self.worktree = "", None
        self._rebind()
        removed, changed, message = _leave(origin, wt_id, action, discard)
        result = _result(True, message, worktree_id=wt_id, removed=removed, changed=changed)
        self._notify({"action": "exit", "source": source, **result})
        return result

    def _register_exit_cleanup(self) -> None:
        """Claude Code's rule at the end of a session: an unchanged worktree
        is removed, a changed one is kept (and said so on stderr)."""
        if getattr(self, "_exit_registered", False):
            return
        import atexit

        atexit.register(self.close)
        self._exit_registered = True

    def close(self) -> str:
        """Leave the worktree as the session ends: removed when unchanged,
        kept otherwise; the line said, "" when not in one."""
        if self.worktree is None:
            return ""
        try:
            result = self.exit(action="remove", source="close")
        except Exception:  # noqa: BLE001 - an exiting process keeps the worktree
            logger.debug("worktree close failed", exc_info=True)
            return ""
        line = f"r4: {result['message']}"
        try:
            import sys

            print(line, file=sys.stderr)
        except Exception:  # noqa: BLE001 - stderr may be gone at exit
            pass
        return line

    # ── the agent's side ──────────────────────────────────────────────────

    def maybe_auto(self, tool_name: str) -> str:
        """In auto mode, enter ``auto-<session>`` before the first writer
        call; the one-line note for the transcript, else ""."""
        if self.worktree is not None or tool_name not in WRITERS or self.mode()[0] != "auto":
            return ""
        if not _in_repo(os.getcwd()):
            return ""
        result = self.enter(f"auto-{self._session_key}", source="auto")
        if not result["ok"]:
            logger.info("auto worktree not entered: %s", result["message"])
            return ""
        root = result["workspace_root"]
        return f"[r4] auto worktree: the session now works in {root}; your checkout is not written"

    def workspace(self) -> Any:
        """The guard's workspace while in a worktree (writes stay inside it,
        reads may reach the checkout it was made from), else None."""
        if self.worktree is None:
            return None
        from .agent_workspace import AgentWorkspace

        return AgentWorkspace.of(
            self.worktree.path, self.origin, label=f"worktree {self.worktree.id}"
        )

    @contextlib.contextmanager
    def guard(self) -> Iterator[None]:
        """Run a tool under the worktree guard; a plan agent's own workspace
        (already set) wins."""
        from .agent_workspace import agent_workspace, current_workspace

        ws = self.workspace()
        if ws is None or current_workspace() is not None:
            yield
            return
        with agent_workspace(ws):
            yield


def _in_repo(path: str) -> bool:
    from r4.worktrees import WorkspaceError, main_repo

    try:
        main_repo(path)
    except WorkspaceError:
        return False
    return True


def _leave(origin: str, wt_id: str, action: str, discard: bool) -> tuple[bool, bool, str]:
    """``(removed, changed, message)`` after leaving ``wt_id``."""
    from r4.worktrees import WorkspaceError, find, is_changed, remove

    wt = find(origin, wt_id)
    changed = bool(wt and is_changed(wt))
    if action != "remove":
        return False, changed, f"left worktree {wt_id}; kept (branch r4/{wt_id})"
    try:
        remove(origin, wt_id, discard=discard)
    except WorkspaceError as exc:
        return False, changed, f"left worktree {wt_id}; kept: {exc.detail}"
    return True, changed, f"left and removed worktree {wt_id}"


_CURRENT: Optional[WorktreeController] = None


def set_current_controller(controller: Optional[WorktreeController]) -> None:
    """The process's session controller (one session per bridge process)."""
    global _CURRENT
    _CURRENT = controller


def current_controller() -> Optional[WorktreeController]:
    return _CURRENT
