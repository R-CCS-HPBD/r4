"""``r4 run`` — alias for ``r4 agent-exec`` (UX redesign).

Provides the shorter ``r4 run`` command name that delegates to the
existing ``AgentExecCommand`` handler.  The old ``r4 agent-exec`` name
remains available with a deprecation warning.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace

from .agent_exec import add_agent_exec_subcommands
from .base import CommandHandler
from .registry import command


@command
class RunCommand(CommandHandler):
    """``r4 run`` — A2L agent execution tracking (new name)."""

    name = "run"
    help = "Track agent executions in A2L (replaces agent-exec)"

    def register(self, subparsers) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Create, list, and update agent execution records.",
            epilog=(
                "Examples:\n"
                "  r4 run list\n"
                "  r4 run show <execution-id>\n"
                "  r4 run create --agent segmenter-v2\n"
                "  r4 run update <execution-id> --status completed\n"
                "\n"
                "The ledger records no artifact links on executions; record\n"
                "lineage with `r4 artifact derive --parent <id>`."
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        # Same tree (and dests) cmd_agent_exec reads; the duplicate tree used
        # execution_id/input/output and lacked offset/user/version/model/
        # prompt_hash/exec_id, so every subcommand hit AttributeError.
        add_agent_exec_subcommands(p, artifact_link_flags=False)
        return p

    def handle(self, args: Namespace) -> int:
        from .agent_exec import cmd_agent_exec

        return cmd_agent_exec(args)
