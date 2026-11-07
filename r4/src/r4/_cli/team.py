"""``r4 team`` — alias for ``r4 teamup`` (UX redesign).

Provides the shorter ``r4 team`` command name that delegates to the
existing ``TeamupCommand`` handler.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command
from .teamup import build_teamup_parser


@command
class TeamCommand(CommandHandler):
    """``r4 team`` — multi-agent collaboration (new name)."""

    name = "team"
    help = "Multi-agent collaboration for HPC optimization (alias for teamup)"

    def register(self, subparsers) -> ArgumentParser:
        return build_teamup_parser(subparsers, name=self.name)

    def handle(self, args: Namespace) -> int:
        from .teamup import cmd_teamup

        cmd_teamup(args)
        return 0
