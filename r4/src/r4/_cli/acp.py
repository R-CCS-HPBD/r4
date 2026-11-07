"""``r4 acp`` — start the Agent Client Protocol server."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class ACPCommand(CommandHandler):
    """``r4 acp`` — start the ACP JSON-RPC server for editor integration."""

    name = "acp"
    help = "Start the Agent Client Protocol server (stdin/stdout JSON-RPC)"

    def register(self, subparsers) -> ArgumentParser:
        return subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Start the ACP JSON-RPC server for editor integration.\n"
                "The server communicates over stdin/stdout using newline-delimited JSON."
            ),
        )

    def handle(self, args: Namespace) -> int:
        from ..acp import create_default_server

        server = create_default_server()
        server.serve()
        return 0
