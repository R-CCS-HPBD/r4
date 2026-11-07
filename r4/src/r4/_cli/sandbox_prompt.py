"""The local tool sandbox on the command line (ADR-101 D-1, D-2, D-12).

``r4 -p`` and the Rich REPL call :func:`apply_local_sandbox`: the launch flag
``r4 --sandbox on|off`` (handed over in ``R4_LAUNCH_SANDBOX`` and taken out of
the environment here), else the session's own choice, ``R4_SANDBOX``, the
profile default, else off. There is no question: a local session that runs
with the sandbox off, or without a usable backend, prints one line on stderr
saying so and how to turn it on (the ADR-099 D-2 "says so in one line"
precedent). These surfaces have no ``/sandbox``, so the off line names
``r4 --sandbox on``, ``R4_SANDBOX=on`` and ``r4 config sandbox on``
(:data:`SANDBOX_NOTICE_OFF_CLI`). A frontend venue worker resolves to off
(``venue``) and is silent.

``r4 team plan``, ``r4 teamup`` and ``r4 recipe run`` have no session: they
call :func:`cli_agent_sandbox_and_web`, which installs a process-level
context from ``r4 --sandbox`` (else ``R4_SANDBOX``, the profile default,
off) before the web question (whose note reads the sandbox), then binds the
command's web access to it.

A session or run that opens with the sandbox on moves local MCP servers
under it (ADR-101 D-9) and says on stderr when they did not follow.

``r4 config sandbox [on|off|show]`` (:func:`handle_config_sandbox`) writes or
shows the profile default and moves the MCP servers with it; ``on`` is
refused, like ``/sandbox default on``, when no backend passes the probe and
the self-test here (D-3). ``r4 doctor`` reports the backend
(:func:`doctor_sandbox_check`).
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from ..companion.sandbox import (
    SANDBOX_DETAIL_LINES,
    SANDBOX_NOTICE_OFF,
    SANDBOX_NOTICE_OFF_CLI,
    SandboxContext,
    SandboxError,
    backend_for_platform,
    launch_setting,
    make_sandbox_setting,
    profile_of,
    remember_sandbox_default,
    resolve_sandbox,
    safe_profile_default,
    safe_user_policy,
    set_current_sandbox,
    take_launch_sandbox,
)
from ..companion.ui.sandbox_bridge import (
    mcp_follow,
    mcp_for_opening,
    open_session_sandbox,
    sandbox_notice,
    sandbox_state,
)
from ..companion.web_access import ambient_web_access


def _err(line: str) -> None:
    print(line, file=sys.stderr)


def cli_notice(notice: str) -> str:
    """The console's notice for a surface without ``/sandbox``."""
    return SANDBOX_NOTICE_OFF_CLI if notice == SANDBOX_NOTICE_OFF else notice


def apply_local_sandbox(
    session: Any, *, launch_env: MutableMapping[str, str] | None = None
) -> dict[str, Any]:
    """Settle a local CLI session's sandbox and print its notice line.

    Returns the session's ``SandboxState`` (the bridge snapshot's shape).
    """
    from ..companion.ui.sandbox_bridge import activate_sandbox

    profile = profile_of(getattr(getattr(session, "client", None), "config", None))
    open_session_sandbox(session, take_launch_sandbox(launch_env), profile)
    activate_sandbox(session)
    state = sandbox_state(session)
    if state["notice"]:
        _err(cli_notice(state["notice"]))
    if state["mcp_error"]:
        _err(f"sandbox: {state['mcp_error']}")
    return state


def _install_cli_sandbox(
    args: Any, config: Any, web_access: Callable[[], Any], env: Mapping[str, str] | None
) -> SandboxContext:
    profile = profile_of(config)
    setting = resolve_sandbox(
        metadata={},
        env=os.environ if env is None else env,
        launch=launch_setting(getattr(args, "root_sandbox", None)),
        profile_default=safe_profile_default(profile),
    )
    context = SandboxContext(
        setting=lambda: setting,
        web_access=web_access,
        workspace_root=os.getcwd(),
        user_policy=safe_user_policy(profile),
    )
    set_current_sandbox(context)
    return context


def _report_cli_sandbox(context: SandboxContext, profile: str | None) -> None:
    """On only: the unusable-backend line, and MCP moved under it (D-9)."""
    setting = context.setting
    if not setting.enabled:
        return
    note = sandbox_notice(context.status())
    if note:
        _err(note)
    mcp_error = mcp_for_opening(setting, profile)
    if mcp_error:
        _err(f"sandbox: {mcp_error}")


def cli_agent_sandbox_and_web(
    args: Any,
    config: Any,
    make_web: Callable[[], Callable[[], Any]],
    *,
    env: Mapping[str, str] | None = None,
) -> Callable[[], Any]:
    """Install the sandbox of a session-less command-line agent run, then
    build its web getter (``make_web``) and return it.

    The web question's note reads the installed sandbox, so it is asked
    after the sandbox is settled; the context reads the getter it returns.
    Prints a line only when the sandbox is on and cannot be used (every
    sandboxed command is then refused, D-3) or local MCP servers did not
    follow it.
    """
    getters: list[Callable[[], Any]] = []
    context = _install_cli_sandbox(args, config, lambda: getters[0](), env)
    getters.append(make_web())
    _report_cli_sandbox(context, profile_of(config))
    return getters[0]


# ── r4 config sandbox ───────────────────────────────────────────────────────


def _backend_line() -> str:
    backend = backend_for_platform()
    ok, reason = backend.probe()
    if ok:
        return f"backend: {backend.name} available"
    return f"backend: unavailable ({reason})"


def sandbox_unusable(profile: str | None) -> str:
    """ "" when the backend passes its probe and the self-test here, else why not.

    Run on a scratch workspace: a profile default does not depend on the
    directory the command was typed in (a home directory is never a root).
    """
    with tempfile.TemporaryDirectory(prefix="r4-sandbox-check-") as scratch:
        context = SandboxContext(
            setting=lambda: make_sandbox_setting("on", source="profile"),
            web_access=ambient_web_access,
            workspace_root=scratch,
            user_policy=safe_user_policy(profile),
            cwd_writable=False,
        )
        try:
            context.check_available()
        except SandboxError as exc:
            return exc.reason
        finally:
            context.close()
    return ""


def _show(profile: str | None) -> int:
    default = safe_profile_default(profile) or "off (not set)"
    policy = safe_user_policy(profile)
    print(f"sandbox default: {default}")
    print(_backend_line())
    print(f"extra_write_paths: {', '.join(policy.extra_write_paths) or '(none)'}")
    print(f"extra_deny_read_paths: {', '.join(policy.extra_deny_read_paths) or '(none)'}")
    print(f"env_keep: {', '.join(policy.env_keep) or '(none)'}")
    for line in SANDBOX_DETAIL_LINES:
        print(f"  {line}")
    return 0


def _mcp_with_default(action: str, profile: str | None) -> int:
    """Move local MCP servers with the new default (D-9): on requires the
    sandbox, off releases it. 1 when they did not follow."""
    setting = make_sandbox_setting(action, source="profile")
    restarted, error = mcp_follow(setting, default=True, profile=profile)
    if restarted:
        print(f"restarted MCP servers: {', '.join(restarted)}")
    if error:
        _err(f"r4 config sandbox: {error}")
        return 1
    return 0


def _refused_on(profile: str | None) -> int:
    """``on`` without a working backend: 1 and nothing written (D-3, review LC-8)."""
    reason = sandbox_unusable(profile)
    if not reason:
        return 0
    _err(f"r4 config sandbox: not changed, the sandbox cannot run here: {reason}")
    return 1


def handle_config_sandbox(args: Any, config: Any) -> int:
    """``r4 config sandbox [on|off|show]``: write or show the profile default."""
    action = str(getattr(args, "sandbox_action", None) or "show")
    profile = profile_of(config)
    if action == "show":
        return _show(profile)
    if action == "on" and _refused_on(profile):
        return 1
    try:
        remember_sandbox_default(action, profile=profile)
    except (OSError, SandboxError) as exc:
        _err(f"r4 config sandbox: {exc}")
        return 1
    print(f"sandbox default: {action} (profile {profile or 'default'})")
    if action == "on":
        print(_backend_line())
    return _mcp_with_default(action, profile)


# ── r4 doctor ───────────────────────────────────────────────────────────────


def doctor_sandbox_check() -> dict[str, object]:
    """One informational doctor line: the backend and why it is missing."""
    backend = backend_for_platform()
    ok, reason = backend.probe()
    message = f"sandbox: {backend.name} available" if ok else f"sandbox: unavailable ({reason})"
    return {
        "ok": True,
        "backend": backend.name,
        "available": ok,
        "default": safe_profile_default(None) or "off",
        "message": message,
    }
