"""``r4 chat`` command handler (Phase 2m, 2026-04-28).

Owns both the argparse subparser and dispatch. The actual chat
implementation still lives in :func:`r4.cli.cmd_chat` for now (will
move here once helper extraction Phase 3 lands); we delegate to it.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime

from ._helpers import _die
from .agent import _format_thinking
from .base import CommandHandler
from .registry import command


@command
class ChatCommand(CommandHandler):
    """``r4 chat`` — quick one-shot chat with the AI model.

    Routing flags: ``--task`` / ``--agent`` are forwarded as the
    ``X-R4-Task`` header so the gateway can log per-task usage and
    apply per-agent quota policies.
    """

    name = "chat"
    help = "Quick one-shot chat with the AI model"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("message", nargs="?", help="Message to send")
        p.add_argument("--model", "-m", help="Model to use")
        p.add_argument("--system", "-s", help="System prompt")
        p.add_argument("--stream", action="store_true", help="Stream response")
        p.add_argument("--temperature", "-t", type=float, default=0.7)
        p.add_argument("--max-tokens", type=int, help="Max tokens")
        p.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
        p.add_argument(
            "--companion",
            action="store_true",
            help="Launch the research companion UI",
        )
        p.add_argument(
            "--legacy-tui",
            action="store_true",
            help=(
                "(was: Ink TUI — deleted 2026-09-11 per ADR-014) "
                "With --companion, use the Rich REPL fallback instead of Console v3"
            ),
        )
        p.add_argument(
            "--resume",
            metavar="SESSION_ID",
            help="With --companion, resume a previous companion session",
        )
        p.add_argument(
            "--skill",
            help="With --companion, pre-activate a companion skill",
        )
        p.add_argument(
            "--fs-root",
            nargs="?",
            const=".",
            help=(
                "Enable read-only local filesystem tools rooted at PATH. "
                "If provided without a value, uses the current directory."
            ),
        )
        # Routing flags — forwarded as ``X-R4-Task`` header by ``cmd_chat``.
        p.add_argument("--task", help="Task name for routing/logging")
        p.add_argument("--agent", help="Agent name for routing/logging")
        p.add_argument(
            "--source",
            metavar="NAME",
            help="Endpoint source to use (see `r4 source list`)",
        )
        p.add_argument(
            "--secure",
            action="store_true",
            help=(
                "Enable secure chat mode: no auto-retry, no fallback "
                "endpoints, pre-send PII/secret scanning, per-file attachment "
                "consent, and /save disabled."
            ),
        )
        p.add_argument(
            "--no-discover",
            action="store_true",
            dest="no_discover",
            help=(
                "Disable auto-discovery: fail fast if the model is not on "
                "the active source instead of probing all sources."
            ),
        )
        p.add_argument(
            "--knowledge",
            "-k",
            metavar="BUNDLE",
            help=(
                "Load a knowledge bundle (.kb file or directory) for RAG-augmented chat (ADR-013)"
            ),
        )
        return p

    def handle(self, args: Namespace) -> int:
        from ..source import resolve_active_source
        from ..source_context import use_source

        # Source resolution: --source flag or active source override config.
        # The source gets only its own credential (onboarding plan S1).
        _source = resolve_active_source(getattr(args, "source", None))
        if _source is not None:
            use_source(_source)

        from ._helpers import _preflight_check

        _preflight_check(action="chat")

        cmd_chat(args)
        return 0


def _launch_companion(args, client=None) -> None:
    """Launch the companion on ``client`` (a new one, with ``--knowledge``
    loaded, when none is given), so its router and ``search_docs`` search
    the bundle ``r4 chat --knowledge`` loaded."""
    from ..client import Client
    from ..companion.ui import launch

    if client is None:
        client = Client()
        _chat_load_knowledge(client, args)
    launch(
        client=client,
        session_id=getattr(args, "resume", None),
        model=getattr(args, "model", None),
        skill=getattr(args, "skill", None),
        system=getattr(args, "system", None),
        # Threaded only when explicitly set — keeps the legacy call shape.
        **({"legacy_tui": True} if getattr(args, "legacy_tui", False) else {}),
    )


def _maybe_discover_source(model: str) -> None:
    """Auto-discover and persist a source binding for *model* if needed.

    Skips discovery when the model already has a stored route, when
    ``@source`` notation is used, or when only one source is registered.
    """
    import sys

    from ..config import Config, ProfileConfigStore
    from ..model_routing import (
        discover_model_source,
        parse_model_ref,
        update_model_route_map,
    )
    from ..source import get_source_store

    ref = parse_model_ref(model)
    if ref.source is not None:
        return  # explicit @source — no discovery needed

    cfg = Config.from_env()
    routes = cfg.model_routes
    if ref.model in routes:
        return  # already has a route binding

    store = get_source_store()
    sources = store.list()
    if len(sources) <= 1:
        return  # only one source — nothing to discover

    active = store.get_active()
    found = discover_model_source(ref.model, active_source_name=active)
    if found and found != active:
        # Persist route binding so subsequent calls are instant
        new_routes = update_model_route_map(cfg, ref.model, found)
        ProfileConfigStore().save(cfg.profile, model_routes=new_routes)
        print(
            f"info: model '{ref.model}' found on source '{found}', routing there.",
            file=sys.stderr,
        )


def _is_bundle_path(path) -> bool:
    """A .kb file, a .kb.tar.gz export archive or a bundle directory."""
    if not path.exists():
        return False
    return path.is_dir() or path.suffix == ".kb" or path.name.endswith(".tar.gz")


def _bundle_summary_line(path, summary: dict) -> str:
    """``Loaded knowledge bundle: <path> (v1.3, 318 chunks: 318 node; offline BM25)``."""
    sources = summary.get("text_sources") or {}
    mix = ", ".join(f"{n} {source}" for source, n in sorted(sources.items()))
    search = "BM25" + (f" + vectors in {summary['space']}" if summary.get("vectors") else "")
    version = f"v{summary['version']}, " if summary.get("version") else ""
    return (
        f"Loaded knowledge bundle: {path} ({version}{summary.get('chunks', 0)} chunks"
        f"{': ' + mix if mix else ''}; offline {search})"
    )


def _report_bundle(path, summary: dict) -> None:
    """What ``--knowledge`` loaded, and the excerpt caveat."""
    print(_bundle_summary_line(path, summary))
    if summary.get("text_sources", {}).get("excerpt"):
        print(
            "  Note: excerpt chunks carry only their first 500 characters; a bundle "
            "exported with --chunk-text (r4-kb v1.3) carries whole chunks."
        )


def _chat_load_knowledge(client, args) -> None:
    """Load the --knowledge bundle as the client's RAG backend (ADR-013,
    ADR-093 D-12) and export its path in ``R4_KNOWLEDGE_BUNDLE``, so the
    companion this command launches searches it too (the console's bridge
    process inherits the environment). Without a configured project the
    retrieval router labels the turn with the bundle's project
    (``retrieval_router._project_id``); the session's configuration is left
    as it is."""
    knowledge_bundle = getattr(args, "knowledge", None)
    if not knowledge_bundle:
        return
    from pathlib import Path

    from ..rag import KNOWLEDGE_BUNDLE_ENV

    bundle_path = Path(knowledge_bundle)
    if not _is_bundle_path(bundle_path):
        print(f"Loading knowledge for project: {knowledge_bundle}")
        return
    try:
        client.rag.load_bundle(bundle_path)
    except Exception as exc:  # noqa: BLE001 - a bad archive or JSONL row, named to the user
        _die(f"Cannot load knowledge bundle {bundle_path}: {exc}", action="chat --knowledge")
        return
    os.environ[KNOWLEDGE_BUNDLE_ENV] = str(bundle_path.resolve())
    _report_bundle(bundle_path, client.rag.local_bundle_summary())


def _chat_discover_source(args) -> None:
    """Auto-discover model source when not explicitly routed."""
    model_name = getattr(args, "model", None)
    if model_name and not getattr(args, "no_discover", False):
        _maybe_discover_source(model_name)


def _resolve_secure_flag(args) -> bool:
    """Effective secure mode: CLI flag, env var (R4_SECURITY_LEVEL), or project setting."""
    if getattr(args, "secure", False):
        return True
    if os.environ.get("R4_SECURITY_LEVEL", "").lower() == "high":
        return True
    try:
        from ..project import get_project_manager

        proj = get_project_manager().get_current_config()
        return bool(proj and proj.security_level == "high")
    except Exception:
        return False


def _interactive_chat_loop(client, args) -> None:
    """Legacy input()/print() interactive REPL (R4_CHAT_LEGACY=1 path)."""
    from ..client import ChatMessage

    print("Interactive chat mode (Ctrl+C to exit)")
    print("=" * 40)

    messages = []
    if args.system:
        messages.append(ChatMessage.system(args.system))

    while True:
        try:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ("/quit", "/exit", "/q"):
                break

            messages.append(ChatMessage.user(user_input))

            print("\nAssistant: ", end="", flush=True)

            if args.stream:
                response_text = ""
                for chunk in client.chat_completion(
                    messages,
                    model=args.model,
                    stream=True,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                ):
                    print(chunk, end="", flush=True)
                    response_text += chunk
                print()
                messages.append(ChatMessage.assistant(response_text))
            else:
                response = client.chat_completion(
                    messages,
                    model=args.model,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                )
                print(_format_thinking(response.content))
                messages.append(ChatMessage.assistant(response.content))

        except KeyboardInterrupt:
            print("\n")
            break


def _one_shot_chat(client, args) -> None:
    """Single-message chat (stdin fallback) with optional streaming."""
    if not args.message:
        args.message = sys.stdin.read().strip()

    if not args.message:
        _die("No message provided", action="chat")

    if args.stream:
        collected = []
        for chunk in client.chat(
            args.message,
            model=args.model,
            system=args.system,
            stream=True,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            task=getattr(args, "task", None),
            agent=getattr(args, "agent", None),
        ):
            collected.append(chunk)
        print(_format_thinking("".join(collected)))
    else:
        response = client.chat(
            args.message,
            model=args.model,
            system=args.system,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            task=getattr(args, "task", None),
            agent=getattr(args, "agent", None),
        )
        print(_format_thinking(response.content))


def _is_interactive(args) -> bool:
    """Interactive when asked, or when no message on a full TTY."""
    return bool(
        args.interactive or (not args.message and sys.stdin.isatty() and sys.stdout.isatty())
    )


def _use_legacy_repl() -> bool:
    """R4_CHAT_LEGACY=1 opts into the legacy input() REPL."""
    return os.environ.get("R4_CHAT_LEGACY", "").lower() in ("1", "true", "yes")


def cmd_chat(args):
    """Handle chat command."""
    from ..client import Client

    if getattr(args, "companion", False):
        _launch_companion(args)
        return

    # Multi-agent teamup mode (A-806)
    if getattr(args, "teamup", False):
        cmd_chat_teamup(args)
        return

    # bug.memo D2 hard-removal (Phase 2h, 2026-04-25): plain chat stays a
    # permanent first-class one-shot command alongside `r4 companion`.
    client = Client()
    _chat_load_knowledge(client, args)
    _chat_discover_source(args)
    _resolve_secure_flag(args)

    interactive_mode = _is_interactive(args)
    if interactive_mode and not _use_legacy_repl():
        _launch_companion(args, client)
        return

    if interactive_mode:
        _interactive_chat_loop(client, args)
    else:
        _one_shot_chat(client, args)


_TEAMUP_ROLE_DESCRIPTIONS = {
    "PM": "Project Manager",
    "SE": "System Engineer",
    "PG": "Programmer",
    "CD": "Code Doctor",
}


def _teamup_roles(args):
    """Parse agent roles from the comma-separated --agents list."""
    from ..teamup import AgentRole

    role_mapping = {
        "PM": AgentRole.PM,
        "SE": AgentRole.SE,
        "PG": AgentRole.PG,
        "CD": AgentRole.CD,
    }
    agents_str = getattr(args, "agents", "PM,SE,PG,CD")
    roles = []
    for role_str in agents_str.split(","):
        role_str = role_str.strip().upper()
        if role_str in role_mapping:
            roles.append(role_mapping[role_str])
        else:
            print(f"Warning: Unknown agent role '{role_str}', skipping", file=sys.stderr)
    if not roles:
        roles = [AgentRole.PM, AgentRole.SE, AgentRole.PG, AgentRole.CD]
    return roles


def _teamup_goal(args) -> str:
    """Optimization goal from --message or stdin; dies when empty."""
    goal = args.message or sys.stdin.read().strip()
    if not goal:
        _die(
            "No optimization goal provided",
            action="chat --teamup",
            hint='Usage: r4 teamup create <name> --goal "Optimize this code for GPU"',
        )
    return goal


def cmd_chat_teamup(args):
    """Handle multi-agent teamup chat mode (A-806).

    Implements VibeCodeHPC-style collaboration with multiple
    specialized AI agents (PM, SE, PG, CD).
    """
    from ..client import Client
    from ..teamup import (
        AgentRole,
        TeamupSessionConfig,
    )

    role_descriptions = {AgentRole[k]: v for k, v in _TEAMUP_ROLE_DESCRIPTIONS.items()}
    roles = _teamup_roles(args)
    goal = _teamup_goal(args)

    print("=" * 60)
    print("Multi-Agent Teamup Mode (VibeCodeHPC)")
    print(f"Agents: {', '.join(role_descriptions.get(r, r.value) for r in roles)}")
    print(f"Goal: {goal}")
    print("=" * 60)

    # Create teamup configuration
    from ..project import get_project_manager

    config = TeamupSessionConfig(
        name=f"teamup-cli-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        roles=roles,
        max_iterations=getattr(args, "max_iterations", 3),
        optimization_goal=goal,
        # 2026-10-01: stamp the active project so the work pane can scope
        # sessions to it; None when no project is current (honest absence).
        project_id=get_project_manager().get_current(),
    )

    # Create client and session: opened through TeamService (ADR-072 one
    # team seam, ADR-092 slice 3), started as TeamupSession.create did.
    from ..teams.service import TeamService

    client = Client()
    session = TeamService(client).open_team_session(config, running=True)

    try:
        # Run the multi-agent collaboration
        print("\n[Starting multi-agent collaboration...]\n")

        for iteration, agent_outputs in session.run_iteratively():
            print("-" * 40)
            print(f"Iteration {iteration}")
            print("-" * 40)

            for agent, output in agent_outputs.items():
                desc = role_descriptions.get(agent, agent.value)
                print(f"\n[{agent.value.upper()}] {desc}:")
                print(output[:500] + "..." if len(output) > 500 else output)

            if session.status in ("completed", "failed"):
                break

        # Generate final report
        print("\n" + "=" * 60)
        print("Final Report")
        print("=" * 60)

        report = session.get_summary()
        print(report)

    except KeyboardInterrupt:
        print("\n\n[Teamup session interrupted]")
        sys.exit(130)
