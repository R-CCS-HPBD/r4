"""``r4 memory``: saved memory from the shell (ADR-111).

The same verbs as the console's ``/memory`` (``r4.companion.memory_commands``)
over the local files: ``list``, ``show``, ``add``, ``forget``, ``lessons``,
``promote`` and ``path``. The project scope follows the active profile's
project, else the repository of the current directory. Not to be confused
with ``r4 evidence memory``, the project's scientific memory on UMRS.
"""

from __future__ import annotations

import argparse
from argparse import ArgumentParser, Namespace
from typing import Any

from .base import CommandHandler
from .registry import command

_EPILOG = """\
verbs:
  list                                   saved facts (user + project) and lessons
  show <name>                            one fact
  add [--user] [--type T] [--expires D] <text>
  forget <name> [--user]
  lessons [clear | forget <n>]           automatic lessons from your corrections
  promote <name> briefing                append to R4.md (review, then commit)
  promote <name> knowledge <graph>       stage a knowledge proposal
  path                                   where the files are
"""


@command
class MemoryCommand(CommandHandler):
    """``r4 memory``: the local saved memory."""

    name = "memory"
    help = "Saved memory: your private notes the agent reads (ADR-111)"

    def register(self, subparsers: Any) -> ArgumentParser:
        p: ArgumentParser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Private saved memory, one file per fact: the user scope (all "
                "projects) and the current project's scope. The agent sees a "
                "short index each turn."
            ),
            epilog=_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument("words", nargs=argparse.REMAINDER, help="verb and arguments")
        return p

    def handle(self, args: Namespace) -> int:
        return cmd_memory(args)


def _client_or_none() -> Any:
    """A client for ``promote … knowledge``; None without a usable config."""
    try:
        from ..client import Client

        return Client()
    except Exception:  # noqa: BLE001 - the local verbs never need one
        return None


def cmd_memory(args: Namespace) -> int:
    from ..companion.harness.steering import SteeringLoop
    from ..companion.memory_commands import MemoryCommandContext, run_memory_command
    from ..companion.memory_store import store_for

    words = list(getattr(args, "words", None) or [])
    if words[:1] in (["off"], ["on"]):
        print("off and on apply to a chat session: use /memory off in the console.")
        return 1
    client = _client_or_none() if words[:1] == ["promote"] else None
    config_source = client if client is not None else _config_holder()
    ctx = MemoryCommandContext(
        store=store_for(config_source), steering=SteeringLoop(), client=client
    )
    lines = run_memory_command(words, ctx)
    print("\n".join(lines))
    return 1 if lines and lines[0].startswith(("usage:", "memory:")) else 0


def _config_holder() -> Any:
    """An object with ``.config`` (project and gateway) from the active profile."""
    from types import SimpleNamespace

    try:
        from ..config import Config

        return SimpleNamespace(config=Config.from_env())
    except Exception:  # noqa: BLE001 - no profile: the workspace scope
        return None
