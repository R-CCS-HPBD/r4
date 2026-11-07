"""
R4 CLI.

Command-line interface for the R4 Platform.

Usage:
    r4 chat "What is quantum computing?"
    r4 knowledge query "What does the data show?" --scope rag -p my-project
    r4 knowledge upload document.pdf --project my-project
    r4 model list
    r4 config show

    NOTE (ADR-027 P1b): corpus verbs (upload/bulk-upload/query/docs/stats)
    are canonical under `r4 knowledge`; `r4 rag <verb>` still works and
    prints a fold note. `r4 rag ingest|retriever` (pipeline definitions)
    stay under `r4 rag`.
"""

# pylint: disable=unused-import

from __future__ import annotations

import argparse
import os
import sys
from importlib import import_module
from typing import Any

from . import __version__
from ._cli._helpers import (  # noqa: F401 — re-export for backward compat
    _configure_profile_auth,
    _current_project_id,
    _die,
    _display_status_detail,
    _endpoint_reference_hint,
    _endpoint_wait_detail,
    _format_endpoint_wait_line,
    _format_wait_duration,
    _friendly_status_detail,
    _get_current_session,
    _is_http_404,
    _parse_context_len,
    _parse_lookback,
    _parse_lora_specs,
    _pick_model_interactive,
    _print_active_context,
    _print_readiness_report,
    _probe_umrs_direct,
    _prompt_max_model_len,
    _render_wait_bar,
    _resolve_endpoint_models,
    _run_with_provision_progress,
    _wait_for_endpoint_ready,
)
from ._cli.completion import (
    emit_completion_if_requested,
    install_completion,
    print_completion_script,
    uninstall_completion,
)
from .redaction import redact_argv

_HELP_FORMATTER = argparse.RawDescriptionHelpFormatter
_AGENT_SUBCOMMANDS = {
    "chat",
    "create",
    "run",
    "sessions",
    "tools",
    "scan",
    "audit",
}
_AGENT_GUIDANCE_PREFIXES = (
    "how ",
    "how do ",
    "how can ",
    "what ",
    "what is ",
    "what are ",
    "which ",
    "where ",
    "why ",
    "explain ",
    "show me how ",
    "tell me how ",
)
_ROOT_EPILOG = (
    "Quick start:\n"
    "  r4                              Launch the research companion UI\n"
    "  r4 console                      Live Console v3 (catalog, knowledge, research)\n"
    "  r4 console --demo               Same console with fixture data (DEMO MODE)\n"
    '  r4 -p "Hello!"                  One-shot prompt and exit\n'
    '  r4 -p "task" --yolo             Headless with auto-approved tools\n'
    "  r4 --mode plan                  Launch in plan mode\n"
    "  r4 init --gateway-url <url>     Set up your profile and gateway\n"
    "  r4 auth login --api-key-stdin   Save your JWT token (read from a pipe)\n"
    "  r4 login                        Unified login (replaces auth login + onboard)\n"
    "  r4 health                       Check platform health\n"
    "  r4 guide                        Agent tool playbook (no codebase needed)\n"
    "  r4 model list                   List available models\n"
    "  r4 up                           Provision an endpoint interactively\n"
    '  eval "$(r4 completion bash)"   Enable shell tab completion\n\n'
    "Conversational:\n"
    "  r4 agent            AI workflow agent (chat, run, create, tools)\n"
    "  r4 search           Agentic multi-hop search\n"
    "  r4 team             Multi-agent HPC optimization\n\n"
    "User:\n"
    "  r4 governance       Quotas, ownership, audit, shares\n"
    "  r4 map              Connection map with security boundary\n\n"
    "Operator:\n"
    "  r4 admin analytics  Governance analytics dashboard\n"
    "  r4 admin trace      Request audit trail\n"
    "  r4 run              Track agent executions (A2L)\n\n"
    "Sources & Pools:\n"
    "  r4 source           Manage endpoint sources\n"
    "  r4 pool             Provision/stop endpoint rosters\n\n"
    "Inside the companion:\n"
    "  /help          Show all slash commands\n"
    "  /skills        Manage skill bundles\n"
    "  /models        List or switch models\n"
    "  /mcp           Manage MCP servers\n"
    "  /rag           RAG operations\n"
    "  /endpoint      Endpoint list, up, down, status\n"
    "  /health        Check gateway and endpoint status\n"
    "  /quit          Save and exit\n\n"
    "CLI map:\n"
    "  r4              Unified TUI / Gateway-mediated research workflows\n"
    "  umrs              UMRS diagnostics/migration helpers\n"
    "  umrs-server     Offline operator administration"
)
_PRE_REGISTER_MODULES = (
    "acp",
    "admin",
    "agent",
    "artifact",
    "auth",
    "catalog",
    "chat",
    "companion",
    "config",
    "console",
    "doctor",
    "endpoint",
    "evidence",
    "explain",
    "governance",
    "grants",
    "circle",
    "guide",
    "health",
    "hpc",
    "job",
    "knowledge",
    "login",
    "map",
    "mcp",
    "model",
    "onboard",
    "project",
    "rag",
    "recipe",
    "run",
    "schedule",
    "search",
    "search_server",
    "worktree",
    "sessions",
    "skill",
    "source",
    "sweep",
    "team",
    "traces",
    "tui",
    "usage",
    "venue",
    "vision",
    "pool",
    "up",
    "down",
    "workflow",
    "course",
    "data",
    "dashboard",
    "files",
    "bucket",
)
_PRE_REGISTER_COMMANDS = (
    "workflow",
    "rag",
    "chat",
    "companion",
    "console",
    "governance",
    "grant",
    "circle",
    "guide",
    "health",
    "hpc",
    "doctor",
    "map",
    "onboard",
    "sessions",
    "artifact",
    "acp",
    "agent",
    "model",
    "project",
    "job",
    "config",
    "auth",
    "catalog",
    "login",
    "mcp",
    "endpoint",
    "evidence",
    "explain",
    "up",
    "down",
    "recipe",
    "schedule",
    "search",
    "search-server",
    "skill",
    "vision",
    "admin",
    "team",
    "traces",
    "tui",
    "usage",
    "pool",
    "quota",
    "source",
    "knowledge",
    "sweep",
    "course",
    "data",
    "dashboard",
    "files",
    "bucket",
)
_DISPATCH_MODULES = (
    "acp",
    "admin",
    "agent",
    "artifact",
    "catalog",
    "chat",
    "companion",
    "config",
    "console",
    "doctor",
    "down",
    "endpoint",
    "evidence",
    "explain",
    "governance",
    "grants",
    "circle",
    "health",
    "hpc",
    "init",
    "job",
    "knowledge",
    "login",
    "logout",
    "map",
    "mcp",
    "model",
    "onboard",
    "ops",
    "pool",
    "project",
    "provenance",
    "rag",
    "quota",
    "refresh",
    "run",
    "search",
    "search_server",
    "worktree",
    "sessions",
    "source",
    "sweep",
    "team",
    "traces",
    "tui",
    "usage",
    "venue",
    "vision",
    "up",
    "recipe",
    "schedule",
    "skill",
    "workflow",
    "course",
    "data",
    "dashboard",
    "files",
    "bucket",
)


class HelpfulArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that prints local help before exiting on parse errors.

    Unknown top-level commands (lazy-loaded or deprecated) are dispatched
    through the command registry instead of failing outright.
    """

    def _dispatchable_unknown_command(self, message: str) -> str | None:
        """The unknown command in ``message`` iff THIS parser may dispatch it.

        ONLY the root parser may. ``_lazy_subparsers_action`` is set exactly
        once, on the top-level parser, so its absence means ``self`` is a
        SUBparser — and an unknown token there belongs to that subcommand's
        namespace, not to the global registry. ``r4 project run`` means "run is
        not a valid ``project`` subcommand", not "run the top-level ``r4 run``".
        Dispatching it globally misrouted the call into agent-exec and died with
        an uncaught ``AttributeError`` on a Namespace that only carried
        ``command`` (2026-09-14; ``r4 project run`` sat in QUICKSTART for ~10
        months after the verb was retired, so users hit it directly).
        """
        if getattr(self, "_lazy_subparsers_action", None) is None:
            return None
        if "invalid choice:" not in message:
            return None
        for part in message.split("'"):
            if part and len(part) <= 30 and not part.startswith("-"):
                return part
        return None

    def error(self, message: str) -> None:
        message = redact_argv(message)
        unknown_cmd = self._dispatchable_unknown_command(message)
        if unknown_cmd:
            self._try_dispatch_lazy(unknown_cmd)
            return

        self.print_help(sys.stderr)
        print(file=sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        if "required" in message and "command" in message:
            print("hint: choose one of the subcommands shown above.", file=sys.stderr)
        self.exit(2)

    def _try_dispatch_lazy(self, cmd: str) -> None:
        """Attempt to load and dispatch a lazy/deprecated command."""
        registry = _load_dispatch_registry()
        handler = registry.get(cmd) if cmd in registry else None
        if handler is None:
            suggestion = _suggest_command(cmd, list(registry.names()))
            if suggestion:
                print(
                    f"Unknown command '{cmd}'. Did you mean '{suggestion}'?",
                    file=sys.stderr,
                )
            else:
                self.print_help()
            sys.exit(1)

        # Get the existing subparsers action from the main parser
        sub_action = getattr(self, "_lazy_subparsers_action", None)

        if sub_action is not None:
            self._dispatch_with_subparsers(cmd, handler, sub_action)
        else:
            self._dispatch_bare(cmd, handler)

    def _dispatch_with_subparsers(self, cmd: str, handler: Any, sub_action: Any) -> None:
        """Register the handler's subcommands, re-parse argv, and run it.

        argv is re-parsed exactly as typed, and the result takes the same path
        as a pre-registered command (:func:`_run_parsed`). Before 2026-10-21
        (c2-cli) this rewrote argv to ``[r4, cmd, *argv[2:]]`` — so a root flag
        in front (``r4 -v quota``, ``r4 --source b ops health``) turned into a
        parse error — skipped the ``--source`` override, and answered ANY
        handler failure (a 500, an HTML page) with the root help on stdout,
        exit 2, the actual error never shown.
        """
        try:
            handler.register(sub_action)
        except argparse.ArgumentError:
            pass  # 3.11+: its parser is already registered (pre-registered commands)
        if cmd not in sub_action.choices:
            self._exit_unknown_command(cmd, list(sub_action.choices))
        sys.exit(_run_parsed(self.parse_args(), self))

    def _exit_unknown_command(self, cmd: str, known: list[str]) -> None:
        """A registry name whose handler registers another parser name.

        ``r4 sessions`` (removed; its handler registers ``session``): the
        re-parse met the same invalid choice and dispatched again until
        RecursionError, a 5k-line traceback on Python 3.10 (stab cycle 3,
        python-runtime#3). Before 2026-10-21 it printed the root help, exit 2.
        """
        suggestion = _suggest_command(cmd, known)
        if suggestion:
            print(f"Unknown command '{cmd}'. Did you mean '{suggestion}'?", file=sys.stderr)
        else:
            self.print_help(sys.stderr)
        self.exit(2)

    def _dispatch_bare(self, cmd: str, handler: Any) -> None:
        """Run a handler with a bare namespace when no subparsers exist.

        Defensive: a handler that reads its own parsed options finds them
        missing here, and an ``AttributeError`` reaching the user as a raw
        traceback is never the right answer for a CLI typo.
        """
        args = argparse.Namespace(command=cmd)
        try:
            rc = handler.handle(args)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"{self.prog}: error: cannot run '{cmd}': {exc}", file=sys.stderr)
            self.exit(2)
        sys.exit(rc if isinstance(rc, int) else 0)


def _rewrite_agent_shortcut() -> None:
    # `r4 agent <goal words>` → `r4 agent chat|run "<goal>"` (there is no `r4 ask`)
    if (
        len(sys.argv) >= 3
        and sys.argv[1] == "agent"
        and not sys.argv[2].startswith("-")
        and sys.argv[2] not in _AGENT_SUBCOMMANDS
    ):
        goal_parts, passthrough_args = _split_agent_shortcut_args(sys.argv[2:])
        goal_text = " ".join(goal_parts)
        agent_command = "chat" if _looks_like_agent_guidance_request(goal_text) else "run"
        sys.argv = [sys.argv[0], "agent", agent_command, goal_text, *passthrough_args]


def _split_agent_shortcut_args(args: list[str]) -> tuple[list[str], list[str]]:
    for index, value in enumerate(args):
        if value.startswith("-"):
            return args[:index], args[index:]
    return args, []


def _looks_like_agent_guidance_request(goal_text: str) -> bool:
    normalized = " ".join(goal_text.strip().lower().split())
    return normalized == "help" or normalized.startswith(_AGENT_GUIDANCE_PREFIXES)


def _build_parser() -> HelpfulArgumentParser:
    parser = HelpfulArgumentParser(
        prog="r4",
        description="R4 Platform CLI — run with no args to launch the interactive TUI",
        epilog=_ROOT_EPILOG,
        formatter_class=_HELP_FORMATTER,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_root_parser_args(parser)
    subparsers = parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    parser._lazy_subparsers_action = subparsers
    _add_completion_parser(subparsers)
    _register_preparsed_commands(subparsers)
    return parser


def _add_root_parser_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose/debug output",
    )
    # TUI flags (used when no subcommand is given, or with -p for one-shot)
    parser.add_argument(
        "-p",
        "--prompt",
        type=str,
        default=None,
        help="Run a one-shot prompt and exit (programmatic mode)",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=None,
        dest="tui_model",
        help="Model to use in the TUI",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["chat", "plan", "autopilot"],
        default="chat",
        help="TUI interaction mode (default: chat)",
    )
    parser.add_argument(
        "--allow-all-tools",
        "--yolo",
        action="store_true",
        default=False,
        help="Approve all tool calls without prompting",
    )
    parser.add_argument(
        "--fs-root",
        nargs="?",
        const=".",
        default=None,
        help="Enable local filesystem tools rooted at PATH",
    )
    parser.add_argument(
        "--secure",
        action="store_true",
        default=False,
        help="Enable secure mode (pre-send scanning, per-file consent)",
    )
    parser.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="Endpoint source to use (see `r4 source list`)",
    )
    # ADR-099 D-2: the agent's web access for the session this opens; the
    # console asks when it is not given (R4_WEB_ACCESS preselects it there).
    parser.add_argument(
        "--web",
        dest="root_web",
        choices=["off", "none", "allowlist", "public"],
        default=None,
        help="The agent's web access: off, allowlist (with --web-domain) or public",
    )
    parser.add_argument(
        "--web-domain",
        dest="root_web_domains",
        action="append",
        metavar="DOMAIN[,DOMAIN]",
        default=None,
        help="An allowlist domain for --web allowlist (repeatable; .a.org = with subdomains)",
    )
    # ADR-101 D-2: the local tool sandbox for the session this opens (default
    # off; R4_SANDBOX and the profile default `r4 config sandbox` also set it).
    parser.add_argument(
        "--sandbox",
        dest="root_sandbox",
        choices=["on", "off"],
        default=None,
        help="Run the agent's shell, Python and MCP servers in the local sandbox (on|off)",
    )
    # ADR-108 D-2: start the console inside an isolated worktree of the
    # current repository (made if new, entered if it exists).
    parser.add_argument(
        "--worktree",
        "-w",
        dest="root_worktree",
        metavar="NAME",
        default=None,
        help="Work in the repository worktree NAME (.r4/worktrees/NAME, branch r4/NAME)",
    )


def _apply_web_flag(args: argparse.Namespace) -> None:
    """``r4 --web MODE [--web-domain D]`` → ``R4_LAUNCH_WEB_ACCESS`` (``MODE[:d1,d2]``).

    A launch-only variable (RS-1 fix W5): the console hands it to its bridge
    as ``--web-access`` and ``r4 -p`` reads it, both taking it out of their
    environment, so shells the agent opens do not inherit the flag. Both
    open the session with the user's flag (ADR-099 D-2).
    """
    mode = getattr(args, "root_web", None)
    if not mode:
        return
    from .companion.web_access import (
        WEB_ACCESS_LAUNCH_ENV,
        WebAccessError,
        format_web_access_spec,
        make_web_access,
    )

    try:
        access = make_web_access(mode, getattr(args, "root_web_domains", None) or [], source="flag")
    except WebAccessError as exc:
        print(f"--web: {exc}", file=sys.stderr)
        sys.exit(2)
    os.environ[WEB_ACCESS_LAUNCH_ENV] = format_web_access_spec(access.mode, access.domains)


def _apply_sandbox_flag(args: argparse.Namespace) -> None:
    """``r4 --sandbox on|off`` → ``R4_LAUNCH_SANDBOX`` (ADR-101 D-2).

    Launch-only, like ``R4_LAUNCH_WEB_ACCESS``: the console hands it to its
    bridge as ``--sandbox``, ``r4 -p`` and the REPL read it, each taking it
    out of the environment, so nothing the agent starts inherits it.
    """
    mode = getattr(args, "root_sandbox", None)
    if not mode:
        return
    from .companion.sandbox import SANDBOX_LAUNCH_ENV

    os.environ[SANDBOX_LAUNCH_ENV] = str(mode)


def _apply_worktree_flag(args: argparse.Namespace) -> None:
    """``r4 --worktree NAME``: make or reuse the worktree, then work there
    (ADR-108 D-2). The console's workspace and its bridge follow
    ``R4_TUI_WORKSPACE``; a failure exits before the console starts."""
    name = getattr(args, "root_worktree", None)
    if not name:
        return
    from .worktrees import WorkspaceError, create, find

    try:
        wt = find(os.getcwd(), name)
        warning = ""
        if wt is None:
            wt, warning = create(os.getcwd(), name, owner="cli", level="session")
    except WorkspaceError as exc:
        print(f"✗ worktree {name}: {exc.detail} ({exc.code})", file=sys.stderr)
        sys.exit(1)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"worktree {wt.id}: {wt.path} (branch {wt.branch})", file=sys.stderr)
    os.chdir(wt.path)
    os.environ["R4_TUI_WORKSPACE"] = wt.path


def _add_completion_parser(subparsers: argparse._SubParsersAction) -> None:
    completion_p = subparsers.add_parser("completion", help="Shell completion setup")
    comp_sub = completion_p.add_subparsers(dest="completion_action")
    # `r4 completion bash` / `r4 completion zsh` — backward compat via subcommand aliases
    comp_sub.add_parser("bash", help="Print bash completion script")
    comp_sub.add_parser("zsh", help="Print zsh completion script")
    # `r4 completion install [--shell bash|zsh]`
    inst_p = comp_sub.add_parser("install", help="Install completion into your shell RC file")
    inst_p.add_argument("--shell", choices=("bash", "zsh"), default=None)
    # `r4 completion uninstall [--shell bash|zsh]`
    uninst_p = comp_sub.add_parser("uninstall", help="Remove completion from your shell RC file")
    uninst_p.add_argument("--shell", choices=("bash", "zsh"), default=None)


def _register_preparsed_commands(subparsers: argparse._SubParsersAction) -> None:
    for module_name in _PRE_REGISTER_MODULES:
        try:
            import_module(f"r4._cli.{module_name}")
        except Exception:  # noqa: BLE001 - keep main argparse robust
            pass
    try:
        from ._cli import registry as cli_registry
    except Exception:  # noqa: BLE001
        return

    for command_name in _PRE_REGISTER_COMMANDS:
        try:
            handler = cli_registry.get(command_name)
            handler.register(subparsers)
        except Exception:  # noqa: BLE001 - one bad command must not hide the rest
            pass


def _probe_gateway_status(config) -> None:
    """Print a one-line gateway connectivity status."""
    try:
        from .client import Client

        client = Client(config=config)
        readiness = client.readiness()
        if readiness.get("ready"):
            print("  Gateway: \u2713 reachable and ready")
        else:
            print("  Gateway: \u26a0 reachable but not ready")
            checks = readiness.get("checks", {})
            for k, v in checks.items():
                if str(v) != "ok":
                    print(f"    - {k}: {v}")
    except Exception:
        print("  Gateway: \u2717 unreachable")


def _validate_auth_status(config) -> tuple[bool | None, str | None]:
    """Did the Gateway accept the stored credential?

    True: accepted. False: rejected (401/403). None: r4 could not tell — the
    gateway was unreachable, answered 5xx, or answered a non-JSON page. Before
    2026-10-21 (c2-cli) a 500 or an HTML page counted as "accepts them"
    (exit 0) and a closed port as "rejected them" (regenerate your token).
    """
    try:
        from .client import Client
        from .errors import APIError, AuthenticationError

        _client = Client(config=config)
        reply = _client.get(
            f"{getattr(_client, '_api_prefix', '/gw')}/models", params={"limit": 1}, timeout=10
        )
    except AuthenticationError as exc:
        return False, str(exc)
    except APIError as exc:
        return _classify_auth_api_error(exc)
    except Exception as exc:
        return None, str(exc)
    if isinstance(reply, dict) and "_raw" in reply:
        return None, "the gateway answered with a non-JSON page (a proxy or login portal?)"
    return True, None


def _classify_auth_api_error(exc: Any) -> tuple[bool | None, str | None]:
    """An HTTP error from the auth probe: 401/403 reject, 5xx is unknown, other 4xx accept.

    A status below 400 is a 2xx page that is not an API result (``wrap_non_json``:
    a proxy or sign-in portal) and proves nothing either. Stab cycle 3
    (python-runtime#2): it counted as "Gateway accepts them", exit 0.
    """
    status_code = exc.details.get("status_code") if isinstance(exc.details, dict) else None
    if status_code in (401, 403):
        return False, str(exc)
    if not isinstance(status_code, int) or not 400 <= status_code < 500:
        return None, str(exc)
    return True, None


def _profile_authenticated(auth_manager) -> bool:
    """Whether the auth manager reports a usable credential."""
    if hasattr(auth_manager, "is_authenticated"):
        return bool(auth_manager.is_authenticated())
    return bool(getattr(auth_manager, "token", None) or getattr(auth_manager, "api_key", None))


def _show_jwt_identity(auth_manager, profile: str) -> None:
    """Display JWT claims for UX (never authoritative)."""
    import time

    from .auth import decode_jwt_claims

    token_info = auth_manager.token_store.load(profile)
    raw_token = None
    if token_info:
        raw_token = token_info.access_token
    else:
        raw_token = auth_manager.api_key_manager.get_api_key(profile)

    if not raw_token:
        return
    claims = decode_jwt_claims(raw_token)
    if not claims:
        return

    print("  JWT identity (display only):")
    claim_labels = (
        ("sub", "Username"),
        ("email", "Email"),
        ("project_id", "Project"),
        ("session_id", "Session"),
    )
    for key, label in claim_labels:
        if key in claims:
            print(f"    {label}: {claims[key]}")
    expiry = claims.get("exp")
    if expiry is None:
        return
    remaining = expiry - time.time()
    if remaining > 0:
        print(f"    Expires in: {int(remaining // 3600)}h {int((remaining % 3600) // 60)}m")
    else:
        print("    Token EXPIRED — run `r4 auth refresh` or `r4 auth login`")


def _fallback_to_active_source(profile: str, config, auth_manager):
    """Fall back to the profile that actually serves the active runtime.

    Credentials may live under a named source profile (e.g. rccs-cloud) while
    the top-level profile slug stays at the legacy "default". This mirrors the
    active-source overlay in Config.from_env(). Returns
    (profile, config, auth_manager) — unchanged when the requested profile is
    already authenticated or no authenticated active source is available.
    """
    from .auth import get_auth_manager
    from .config import reload_config
    from .source import resolve_active_source

    if _profile_authenticated(auth_manager):
        return profile, config, auth_manager
    active = resolve_active_source()
    if active is None or active.name == profile:
        return profile, config, auth_manager
    alt_manager = get_auth_manager(active.name)
    if not _profile_authenticated(alt_manager):
        return profile, config, auth_manager
    return active.name, reload_config(profile=active.name), alt_manager


def _print_get_started_help() -> None:
    """Print the standard no-credentials onboarding instructions."""
    print()
    print("  Get started:")
    print("    1. Generate a token on the server:")
    print("         umrs-server admin token generate-jwt --user <you> --project <project-id>")
    print("    2. Save it locally (the token comes from a pipe, never an argument):")
    print("         printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin")
    print("    3. Verify:")
    print("         r4 health")


def _print_platform_source_credentials_note() -> None:
    """Print a note when credentials exist under named platform source profiles."""
    from .source import get_source_store

    sources = get_source_store().list()
    platform_sources = [s.name for s in sources if s.kind == "platform" and s.api_key]
    if not platform_sources:
        return
    print()
    print("  note: credentials exist under source profile(s):")
    for name in platform_sources:
        print(f"        - {name} (use `r4 auth status --profile {name}`)")


def _exit_when_bound_source_has_no_credential(config) -> None:
    """``auth status`` when the active source is another deployment than the
    profile's login and has no credential of its own: r4 sends it none, so
    there is nothing for its gateway to accept or reject (onboarding plan S1)."""
    if not getattr(config, "bound_source", None) or config.api_key:
        return
    from .source_context import bound_login_hint

    print(
        f"\u2717 Source '{config.bound_source}' has no credential of its own, so r4 sends "
        "it none (your profile's login belongs to another deployment)."
    )
    print(f"  Gateway: {config.gateway_url}")
    print(f"  hint: {bound_login_hint(config)}")
    sys.exit(1)


def _print_auth_unverified(profile: str, config: Any, auth_error: str | None) -> None:
    """``auth status``: the credential could not be checked (not a rejection)."""
    who = (
        f"Source '{config.bound_source}'"
        if getattr(config, "bound_source", None)
        else f"Profile '{profile}'"
    )
    print(f"\u2717 {who} has credentials, but r4 could not verify them with the Gateway.")
    print(f"  Gateway: {config.gateway_url}")
    if auth_error:
        print(f"  Error: {auth_error}")
    print("  hint: the gateway gave no verdict on it; check the gateway with `r4 health`.")


def _print_auth_rejected(profile: str, config, auth_error) -> None:
    """``auth status``: the gateway refused the credential r4 sent it."""
    who = (
        f"Source '{config.bound_source}'"
        if getattr(config, "bound_source", None)
        else f"Profile '{profile}'"
    )
    print(f"\u2717 {who} has credentials, but Gateway rejected them.")
    print(f"  Gateway: {config.gateway_url}")
    if config.project_id:
        print(f"  Project: {config.project_id}")
    if auth_error:
        print(f"  Error: {auth_error}")
    print()
    if getattr(config, "bound_source", None):
        from .source_context import bound_login_hint

        print(f"  hint: {bound_login_hint(config)}")
        return
    print("  Regenerate a Gateway-compatible token, then save it again:")
    print("    printf '%s' \"$TOKEN\" | r4 auth login --api-key-stdin")


def _resolve_auth_command(args: argparse.Namespace) -> str | None:
    sub = getattr(args, "auth_command", None)
    if sub not in ("status",):
        return sub
    from .auth import get_auth_manager
    from .config import reload_config

    profile = getattr(args, "profile", "default")
    config = reload_config(profile=profile)
    auth_manager = get_auth_manager(profile)
    profile, config, auth_manager = _fallback_to_active_source(profile, config, auth_manager)

    # config.api_key: an active platform source's own key (source add
    # --api-key-stdin) is what every platform command sends, though the
    # profile's auth manager has none — `source list` showed AUTH ✓ while this
    # said "no credentials" (c2-cli, 2026-10-21).
    if not (_profile_authenticated(auth_manager) or config.api_key):
        print(f"\u2717 Profile '{profile}' has no credentials.")
        _print_get_started_help()
        _print_platform_source_credentials_note()
        sys.exit(1)

    _exit_when_bound_source_has_no_credential(config)
    accepted, auth_error = _validate_auth_status(config)
    if accepted is None:
        _print_auth_unverified(profile, config, auth_error)
        sys.exit(1)
    if not accepted:
        _print_auth_rejected(profile, config, auth_error)
        sys.exit(1)

    print(f"\u2713 Profile '{profile}' has credentials and Gateway accepts them")
    print(f"  Gateway: {config.gateway_url}")
    if config.project_id:
        print(f"  Project: {config.project_id}")

    # Display JWT identity if the credential is a JWT.
    _show_jwt_identity(auth_manager, profile)

    _probe_gateway_status(config)
    sys.exit(0)


def _resolve_dispatch_command(args: argparse.Namespace) -> str | None:
    cmd = args.command
    if cmd == "auth":
        return _resolve_auth_command(args)
    if cmd == "config" and getattr(args, "config_command", None) == "init":
        return "init"
    if cmd == "session":
        return "sessions"
    if cmd == "admin":
        return cmd  # admin group handles its own dispatch
    return cmd


def _load_dispatch_registry():
    import_module("r4._cli")
    for module_name in _DISPATCH_MODULES:
        import_module(f"r4._cli.{module_name}")
    from ._cli import registry as cli_registry

    return cli_registry


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _suggest_command(typo: str, known: list[str], *, max_distance: int = 2) -> str | None:
    """Return the closest known command if within max_distance, else None."""
    best, best_dist = None, max_distance + 1
    for name in known:
        d = _levenshtein(typo, name)
        if d < best_dist:
            best, best_dist = name, d
    return best if best_dist <= max_distance else None


def _dispatch(args: argparse.Namespace, parser: HelpfulArgumentParser) -> None:
    registry = _load_dispatch_registry()
    cmd = _resolve_dispatch_command(args)
    if cmd is None:
        parser.print_help()
        sys.exit(1)

    handler = registry.get(cmd) if cmd in registry else None
    if handler is None:
        suggestion = _suggest_command(cmd, list(registry.names()))
        if suggestion:
            print(
                f"Unknown command '{cmd}'. Did you mean '{suggestion}'?",
                file=sys.stderr,
            )
        else:
            parser.print_help()
        sys.exit(1)
    # Source routing fix (2026-09-18 finding): the TUI path re-pointed the
    # config to an explicit --source (resolve → platform gateway base), but
    # every non-TUI subcommand kept the *globally active* source's gateway
    # — `endpoint up --source rccs` then POSTed provision calls to the
    # bdrt gateway (whose much-shared pool 500s). Honor --source here too;
    # unknown names still exit 2 (never silently route elsewhere).
    _source_name = getattr(args, "source", None)
    if _source_name:
        _apply_tui_source_override(_source_name)
    # T1 (c2, 2026-10-21): read-only views wait 30 s for a reply, not 300 s.
    from ._cli.read_timeout import apply_read_only_timeout

    apply_read_only_timeout(parser, args)
    rc = handler.handle(args)
    sys.exit(rc if isinstance(rc, int) else 0)


def _handle_completion_command(args) -> int:
    """Route `r4 completion …` variants to the shell util."""
    action = getattr(args, "completion_action", None)
    if action == "install":
        print(install_completion("r4", shell=args.shell))
        return 0
    if action == "uninstall":
        print(uninstall_completion("r4", shell=args.shell))
        return 0
    if action in ("bash", "zsh"):
        print_completion_script("r4", "_R4_COMPLETE", action)
        return 0
    # No sub-action: print bash script by default
    print_completion_script("r4", "_R4_COMPLETE", "bash")
    return 0


def _apply_tui_source_override(source_name) -> None:
    """Apply a --source override to the active config before TUI launch.

    Mirrors `r4 chat` routing so one-shot/TUI flows use the requested source.
    Unknown names must fail fast — silently falling back to the gateway makes
    the user believe the override worked.

    Onboarding plan S1: the config is built by
    :func:`r4.source_context.config_for_source` — the source's own credential
    (never the active deployment's), its ZMQ data plane, and none of the
    active deployment's project or user. A source with no credential of its
    own is sent none, and the CLI says how to give it one.
    """
    import sys as _sys

    from .source import get_source_store
    from .source_context import login_hint, use_source

    source = get_source_store().get(source_name)
    if source is None:
        print(
            f"Source '{source_name}' not found. Run `r4 source list` to see registered sources.",
            file=_sys.stderr,
        )
        _sys.exit(2)
    cfg = use_source(source)
    if getattr(cfg, "bound_source", None) and not cfg.api_key:
        print(f"note: {login_hint(source, home_unknown=cfg.login_home is None)}", file=_sys.stderr)


def _discover_tui_route(args, tui_model) -> None:
    """Discover the route binding for one-shot prompts with a public model.

    Must run before the session guard (same path as `r4 chat`); discovery is
    best-effort — the session guard reports failures.
    """
    if not getattr(args, "prompt", None) or not tui_model:
        return
    try:
        from ._cli.chat import _maybe_discover_source

        _maybe_discover_source(tui_model)
    except Exception:
        pass


def _launch_tui(args) -> None:
    """Launch the companion TUI when no subcommand was given."""
    from .client import Client
    from .companion.ui import launch

    _source_name = getattr(args, "source", None)
    if _source_name:
        _apply_tui_source_override(_source_name)
        # Console v3 runs in a child process that builds its own config from
        # the environment: name the source there too, or `r4 --source b`
        # would open a console on the active deployment (2026-10-15).
        os.environ["R4_SOURCE"] = _source_name

    _apply_web_flag(args)
    _apply_sandbox_flag(args)
    _apply_worktree_flag(args)
    _tui_model = getattr(args, "tui_model", None)
    _discover_tui_route(args, _tui_model)

    launch(
        client=Client(),
        model=_tui_model,
        prompt=getattr(args, "prompt", None),
        secure=getattr(args, "secure", False),
        fs_root=getattr(args, "fs_root", None),
        allow_all_tools=getattr(args, "allow_all_tools", False),
    )


def main():
    """Main CLI entry point."""
    _rewrite_agent_shortcut()

    parser = _build_parser()
    if emit_completion_if_requested(parser, "r4", "_R4_COMPLETE"):
        return 0
    return _run_parsed(parser.parse_args(), parser)


def _configure_verbose_logging(args: argparse.Namespace) -> None:
    """F-022: global --verbose flag sets root logger to DEBUG."""
    if getattr(args, "verbose", False):
        import logging

        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")


def _report_cli_error(exc: Exception) -> None:
    """The CLI error boundary: one line and its hint, never a traceback."""
    print(f"Error: {exc}", file=sys.stderr)
    hint = getattr(exc, "hint", None)
    if hint:
        print(f"  hint: {hint}", file=sys.stderr)


def _run_parsed(args: argparse.Namespace, parser: HelpfulArgumentParser) -> int:
    """Run a parsed command line: the TUI when no command was given, else a command.

    Shared by :func:`main` and the lazy-dispatch path. The TUI launch sits
    inside the error boundary too: ``r4 -p`` used to print a raw traceback
    for an ordinary R4Error such as "No model selected" (c2-cli, 2026-10-21).
    """
    if args.command == "completion":
        return _handle_completion_command(args)
    _configure_verbose_logging(args)
    try:
        if not args.command:
            # No subcommand → launch the JS companion TUI.
            _launch_tui(args)
            sys.exit(0)
        _dispatch(args, parser)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        _report_cli_error(e)
        sys.exit(1)
    return 0


# --- cmd_X re-exports (Phase 4, 2026-04-28) ---
# F-040: Lazy re-exports — function bodies live in _cli/<module>.py files.
# These are resolved on first access to avoid importing every command module
# at CLI startup.
_CMD_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "cmd_agent": ("._cli.agent", "cmd_agent"),
    "cmd_agent_audit": ("._cli.agent", "cmd_agent_audit"),
    "cmd_agent_chat": ("._cli.agent", "cmd_agent_chat"),
    "cmd_agent_create": ("._cli.agent", "cmd_agent_create"),
    "cmd_agent_run": ("._cli.agent", "cmd_agent_run"),
    "cmd_agent_scan": ("._cli.agent", "cmd_agent_scan"),
    "cmd_agent_sessions": ("._cli.agent", "cmd_agent_sessions"),
    "cmd_agent_tools": ("._cli.agent", "cmd_agent_tools"),
    "cmd_artifact": ("._cli.artifact", "cmd_artifact"),
    "cmd_chat": ("._cli.chat", "cmd_chat"),
    "cmd_chat_teamup": ("._cli.chat", "cmd_chat_teamup"),
    "cmd_companion": ("._cli.companion", "cmd_companion"),
    "cmd_config": ("._cli.config", "cmd_config"),
    "cmd_down": ("._cli.down", "cmd_down"),
    "cmd_endpoint": ("._cli.endpoint", "cmd_endpoint"),
    "cmd_health": ("._cli.health", "cmd_health"),
    "cmd_init": ("._cli.init", "cmd_init"),
    "cmd_job": ("._cli.job", "cmd_job"),
    "cmd_login": ("._cli.login", "cmd_login"),
    "cmd_logout": ("._cli.logout", "cmd_logout"),
    "cmd_mcp": ("._cli.mcp", "cmd_mcp"),
    "cmd_mcp_hatch": ("._cli.mcp", "cmd_mcp_hatch"),
    "cmd_model": ("._cli.model", "cmd_model"),
    "cmd_project": ("._cli.project", "cmd_project"),
    "cmd_rag": ("._cli.rag", "cmd_rag"),
    "cmd_search": ("._cli.search", "cmd_search"),
    "cmd_sessions": ("._cli.sessions", "cmd_sessions"),
    "cmd_teamup": ("._cli.teamup", "cmd_teamup"),
    "cmd_up": ("._cli.up", "cmd_up"),
}


def __getattr__(name: str):  # noqa: N807
    entry = _CMD_LAZY_IMPORTS.get(name)
    if entry is not None:
        mod = import_module(entry[0], __package__)
        val = getattr(mod, entry[1])
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    main()
