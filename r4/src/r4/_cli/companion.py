"""``r4 companion`` command handler (bug.memo D3 Stage 1, 2026-04-25)."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class CompanionCommand(CommandHandler):
    """``r4 companion`` — research companion TUI."""

    name = "companion"
    help = "Launch the AI research companion"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("--resume", metavar="SESSION_ID", help="Resume a previous session")
        p.add_argument("--skill", help="Pre-activate a skill")
        p.add_argument("--model", "-m", help="Override model")
        p.add_argument(
            "--source",
            "-s",
            metavar="NAME",
            help="Platform source to use (see `r4 source list`)",
        )
        p.add_argument(
            "--legacy-tui",
            action="store_true",
            help=(
                "(was: Ink TUI — deleted 2026-09-11 per ADR-014) "
                "Use the Rich REPL fallback instead of Console v3 (OpenTUI)"
            ),
        )
        c_sub = p.add_subparsers(
            dest="companion_command",
            title="subcommands",
            metavar="<companion-command>",
        )
        c_sub.add_parser("sessions", help="List saved companion sessions")
        c_export = c_sub.add_parser("export", help="Export session as markdown")
        c_export.add_argument("session_id", help="Session ID to export")
        return p

    def handle(self, args: Namespace) -> int:

        cmd_companion(args)
        return 0


def _resolve_companion_source(args):
    """Return the platform source from --source or the active source, else None.

    (Before 2026-10-15 this passed ``override=`` to ``resolve_active_source``,
    which takes ``source_name`` — ``r4 companion --source X`` raised TypeError.)
    """
    from ..source import resolve_active_source

    source = resolve_active_source(getattr(args, "source", None))
    if source and source.kind == "platform":
        return source
    return None


def _companion_client(source):
    """A Client for ``source`` carrying only its own credential (onboarding plan S1)."""
    from ..client import Client

    if source is None:
        return Client()
    from ..config import get_config
    from ..source_context import config_for_source

    return Client(config=config_for_source(source, get_config()))


def _companion_sessions() -> None:
    from ..companion.session import CompanionSessionStore

    store = CompanionSessionStore()
    sessions = store.list_sessions()
    if not sessions:
        print("No saved sessions.")
        return
    for s in sessions:
        print(f"  {s['id'][:8]}  {s['name']}  {s['updated_at']}")


def _companion_export(args, client) -> None:
    from ..companion.session import CompanionSessionStore

    store = CompanionSessionStore()
    session = store.load(args.session_id, client)
    if not session:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        sys.exit(1)
    print(session.export_markdown())


def cmd_companion(args):
    """Handle companion command — launch the research companion.

    Scripting subcommands (sessions, export) remain here.
    Interactive mode launches the interactive companion surface (Console v3
    by default; the legacy Ink TUI was removed 2026-09-11 per ADR-014 —
    --legacy-tui now picks the Rich REPL fallback).
    """
    source = _resolve_companion_source(args)
    source_name = source.name if source is not None else None
    client = _companion_client(source)

    subcmd = getattr(args, "companion_command", None)
    if subcmd == "sessions":
        _companion_sessions()
        return
    if subcmd == "export":
        _companion_export(args, client)
        return

    from ..companion.ui import launch

    launch(
        client=client,
        session_id=getattr(args, "resume", None),
        model=getattr(args, "model", None),
        skill=getattr(args, "skill", None),
        source_name=source_name,
        # Threaded only when explicitly set — keeps the legacy call shape.
        **({"legacy_tui": True} if getattr(args, "legacy_tui", False) else {}),
    )
