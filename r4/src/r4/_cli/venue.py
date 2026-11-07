"""``r4 venue`` — frontend-venue worker entrypoint (runs inside the sandbox).

``r4 venue worker`` is a chat session's loop; ``r4 venue worker --plan-run
<id>`` hosts one team plan run headless (ADR-092 slice 8). UMRS chooses which
when it starts the session (the worker command is part of its job spec).
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class VenueCommand(CommandHandler):
    """``r4 venue worker`` — the sandbox loop (ADR-046 A2)."""

    name = "venue"
    help = "Frontend-venue runtime verbs (worker loop runs inside the sandbox)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="venue_subcommand", metavar="subcommand")
        worker = sub.add_parser(
            "worker",
            help=(
                "Run the session worker loop: pop queued turns through the rail, "
                "execute them with the sidecar as the LLM route, mirror the journal"
            ),
        )
        worker.add_argument(
            "--plan-run",
            metavar="RUN_ID",
            default=None,
            help=(
                "Host one team plan run headless instead of the chat loop (ADR-092 "
                "slice 8: UMRS starts this; never run it by hand)"
            ),
        )
        return parser

    def handle(self, args: Namespace) -> int:
        cmd = getattr(args, "venue_subcommand", None)
        if cmd == "worker":
            run_id = getattr(args, "plan_run", None)
            if run_id:
                from ..teams.plan_worker import main as plan_main

                return plan_main(run_id)
            from ..venue_worker import main

            return main()
        print("Usage: r4 venue worker", file=sys.stderr)
        return 2
