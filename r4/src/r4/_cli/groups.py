"""Command group infrastructure for namespaced CLI commands.

Provides ``CommandGroup`` — a lightweight namespace parent that can host
child ``CommandHandler`` instances under a single top-level subcommand
(e.g. ``r4 admin trace``, ``r4 admin pool``).
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler


class CommandGroup(CommandHandler):
    """A namespace parent that hosts child commands.

    Subclasses declare ``children`` — a sequence of ``CommandHandler``
    instances whose parsers are registered as sub-subparsers of this group.

    Example::

        @command
        class AdminGroup(CommandGroup):
            name = "admin"
            help = "Admin and operator commands"
            children = [GovernanceHandler(), TraceHandler(), ...]
    """

    #: Child handlers to register under this group.
    children: list[CommandHandler] = []

    #: Optional epilog shown after children in ``--help``.
    group_epilog: str = ""

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=self.help,
            epilog=self.group_epilog or None,
            formatter_class=RawDescriptionHelpFormatter,
        )
        child_sub = p.add_subparsers(
            dest=f"{self.name}_command",
            title="subcommands",
            metavar="<subcommand>",
        )
        child_sub.required = True

        for child in self.children:
            child.register(child_sub)

        return p

    def handle(self, args: Namespace) -> int:
        child_cmd = getattr(args, f"{self.name}_command", None)
        if child_cmd is None:
            return 1

        for child in self.children:
            if child.name == child_cmd:
                return child.handle(args)

        return 1
