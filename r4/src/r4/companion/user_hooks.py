"""User/project-configured shell hooks (G13, 2026-10-29).

The companion harness had exactly two internal hook points (Pre/PostToolUse,
``r4/agent/hooks.py``) and no user-configurable layer — the enabler for
community long-running patterns (hook-driven iteration loops, transcript
archiving, prompt context injection).

Sources (merged, later files win on conflicts by *adding* their commands):

- ``~/.config/r4/hooks.json`` — always (user-owned machine config).
- ``<workspace>/.r4/hooks.json`` — only when ``R4_COMPANION_PROJECT_HOOKS=1``:
  a checked-in hook file is code execution from repo content, so it stays
  opt-in. While the local tool sandbox is on (ADR-101), these project hooks
  run inside it (``wrap_spawn``), and the sandbox keeps an existing
  ``.r4/hooks.json`` read-only: a sandboxed agent must not plant commands r4
  then runs unconfined. User-level hooks stay unwrapped, as in Claude Code
  (2026-10-30).

File shape::

    {"UserPromptSubmit": [{"command": "context.sh", "timeout_sec": 5}],
     "PreToolUse":      [{"command": ["deny-writes.sh"]}],
     "PostToolUse":     [],
     "PreCompact":      ["archive-transcript.sh"]}

Contract per command: JSON event payload on stdin; exit 0 = allow (stdout
may carry a JSON object ``{"block": bool, "reason": str, "inject": str}``);
exit 2 = block (stderr becomes the reason). Errors and timeouts log and
allow — a broken hook comm must never take the session down. ``inject``
text from ``UserPromptSubmit`` lands in the turn's volatile context block.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HOOK_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "PreCompact")
_DEFAULT_TIMEOUT_SEC = 10
_MAX_TIMEOUT_SEC = 30
_MAX_PAYLOAD_CHARS = 32_000


@dataclass
class HookDecision:
    """Aggregated outcome of one event's commands."""

    block: bool = False
    reason: str = ""
    inject: str = ""


@dataclass
class _HookCommand:
    argv: list[str]
    timeout_sec: int
    #: From ``<workspace>/.r4/hooks.json``: runs under the session's sandbox.
    project: bool = False


def _parse_commands(
    raw: Any, event: str, source: Path, project: bool = False
) -> list[_HookCommand]:
    """Validate one event's command list from one config file."""
    if not raw:
        return []
    if not isinstance(raw, list):
        logger.warning("hooks config %s: %s must be a list — skipped", source, event)
        return []
    commands = []
    for entry in raw:
        spec = entry if isinstance(entry, dict) else {"command": entry}
        argv = _argv_of(spec.get("command"), source)
        if argv:
            timeout = spec.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)
            timeout_sec = min(_MAX_TIMEOUT_SEC, max(1, int(timeout or 1)))
            commands.append(_HookCommand(argv, timeout_sec, project))
    return commands


def _argv_of(command: Any, source: Path) -> list[str]:
    if isinstance(command, str) and command.strip():
        return shlex.split(command)
    if isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        return list(command)
    if command is not None:
        logger.warning("hooks config %s: invalid command %r — skipped", source, command)
    return []


def _load_file(path: Path, into: dict[str, list[_HookCommand]], project: bool = False) -> None:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("hooks config %s unreadable (%s) — skipped", path, exc)
        return
    if not isinstance(data, dict):
        logger.warning("hooks config %s: top level must be an object — skipped", path)
        return
    for event in HOOK_EVENTS:
        into[event].extend(_parse_commands(data.get(event), event, path, project))


@dataclass
class UserHooks:
    """The merged, validated hook commands for one session."""

    commands: dict[str, list[_HookCommand]] = field(
        default_factory=lambda: {event: [] for event in HOOK_EVENTS}
    )

    def any(self, event: str) -> bool:
        return bool(self.commands.get(event))

    def run(self, event: str, payload: dict[str, Any]) -> HookDecision:
        """Run every command registered for ``event``; any block wins."""
        decision = HookDecision()
        for command in self.commands.get(event, []):
            single = _run_hook_command(command, event, payload)
            if single.inject:
                parts = [part for part in (decision.inject, single.inject) if part]
                decision.inject = "\n".join(parts)
            if single.block and not decision.block:
                decision.block, decision.reason = True, single.reason
        return decision


def _hook_spawn(command: _HookCommand) -> tuple[list[str], dict[str, str] | None]:
    """argv and env: a project hook through the session's sandbox (SandboxError:
    not run), a user hook as configured."""
    if not command.project:
        return command.argv, None
    from .sandbox import wrap_spawn

    spec = wrap_spawn(command.argv, shell=False, cwd=None, env=dict(os.environ))
    argv = spec.argv if isinstance(spec.argv, list) else [spec.argv]
    return argv, spec.env


def _run_hook_command(command: _HookCommand, event: str, payload: dict[str, Any]) -> HookDecision:
    text = json.dumps(payload, default=str)[:_MAX_PAYLOAD_CHARS]
    try:
        argv, env = _hook_spawn(command)
        completed = subprocess.run(
            argv,
            input=text,
            capture_output=True,
            text=True,
            timeout=command.timeout_sec,
            check=False,
            env=env,
        )
    except Exception as exc:
        logger.warning("%s hook %s failed (%s) — allowing", event, command.argv[0], exc)
        return HookDecision()
    if completed.returncode == 2:
        reason = completed.stderr.strip() or f"blocked by {event} hook {command.argv[0]}"
        return HookDecision(block=True, reason=reason)
    if completed.returncode != 0:
        logger.warning(
            "%s hook %s exited %d — allowing", event, command.argv[0], completed.returncode
        )
        return HookDecision()
    return _decision_from_stdout(completed.stdout)


def _decision_from_stdout(stdout: str) -> HookDecision:
    out = stdout.strip()
    if not out.startswith("{"):
        return HookDecision()
    try:
        data = json.loads(out)
    except ValueError:
        return HookDecision()
    if not isinstance(data, dict):
        return HookDecision()
    return HookDecision(
        block=bool(data.get("block")),
        reason=str(data.get("reason") or ""),
        inject=str(data.get("inject") or ""),
    )


def _project_hooks_enabled() -> bool:
    return os.environ.get("R4_COMPANION_PROJECT_HOOKS", "").strip() in ("1", "true", "yes", "on")


def load_user_hooks(workspace_root: str | None) -> UserHooks:
    """Load and validate both hook sources for a session."""
    hooks = UserHooks()
    user_file = Path(os.path.expanduser("~/.config/r4/hooks.json"))
    if user_file.exists():
        _load_file(user_file, hooks.commands)
    if workspace_root and _project_hooks_enabled():
        project_file = Path(workspace_root) / ".r4" / "hooks.json"
        if project_file.exists():
            _load_file(project_file, hooks.commands, project=True)
    return hooks


# ---------------------------------------------------------------------------
# Tool-call integration (PreToolUse / PostToolUse into r4.agent.hooks)
# ---------------------------------------------------------------------------

#: The session currently owning the dispatcher below (one console per
#: process; tests reset this via the companion-suite conftest guard).
_current_user_hooks: UserHooks | None = None


def set_current_user_hooks(hooks: UserHooks | None) -> None:
    global _current_user_hooks
    _current_user_hooks = hooks


def register_dispatchers() -> None:
    """Register the tool-event dispatchers with the default hook registry.

    Idempotent: re-registering would double-fire. The dispatcher reads the
    module-global current hooks, so a new session simply swaps the global.
    """
    from r4.agent.hooks import _default_hook_registry

    registry = _default_hook_registry()
    if getattr(registry, "_r4_user_hook_dispatchers", False):
        return
    registry._r4_user_hook_dispatchers = True  # type: ignore[attr-defined]
    registry.add_pre_hook(_pre_tool_dispatch)
    registry.add_post_hook(_post_tool_dispatch)


def _pre_tool_dispatch(tool_name: str, args: dict[str, Any]) -> Any:
    from r4.agent.hooks import HookAction, PreToolResult

    hooks = _current_user_hooks
    if hooks is None or not hooks.any("PreToolUse"):
        return PreToolResult(action=HookAction.ALLOW)
    decision = hooks.run("PreToolUse", {"tool_name": tool_name, "arguments": args})
    if decision.block:
        return PreToolResult(action=HookAction.BLOCK, reason=decision.reason)
    return PreToolResult(action=HookAction.ALLOW)


def _post_tool_dispatch(tool_name: str, args: dict[str, Any], output: Any) -> Any:
    from r4.agent.hooks import PostToolResult

    hooks = _current_user_hooks
    if hooks is None or not hooks.any("PostToolUse"):
        return PostToolResult()
    decision = hooks.run(
        "PostToolUse",
        {"tool_name": tool_name, "arguments": args, "output_head": str(output)[:8_000]},
    )
    if decision.block:
        return PostToolResult(suppress=True, reason=decision.reason or "suppressed by hook")
    if decision.inject and isinstance(output, str):
        return PostToolResult(modified_output=f"{output}\n{decision.inject}")
    return PostToolResult()
