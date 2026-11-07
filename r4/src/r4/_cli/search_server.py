"""``r4 search-server`` — a local SearXNG for ``web_search`` (ADR-102).

``up`` pulls the pinned SearXNG image and runs it on 127.0.0.1 with podman
(or docker); ``web_search`` then uses it in sessions whose web access is
public. ``down`` removes the container, ``status`` probes it. The work is in
``r4.agent.searxng_local``.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter, _SubParsersAction
from typing import Any

from .base import CommandHandler
from .registry import command

_OK_STATES = frozenset({"running", "started", "stopped", "absent"})


@command
class SearchServerCommand(CommandHandler):
    """``r4 search-server up|down|status``."""

    name = "search-server"
    help = "Run a local SearXNG so web_search works (podman or docker)"

    def register(self, subparsers: _SubParsersAction[ArgumentParser]) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "A local SearXNG on 127.0.0.1 for the agent's web_search tool.\n"
                "web_search runs only in sessions whose web access is public;\n"
                "R4_WEB_SEARCH_URL, when set, names another instance instead.\n"
                "Environment: R4_SEARXNG_PORT (8888), R4_SEARXNG_IMAGE,\n"
                "R4_CONTAINER_ENGINE (podman, else docker)."
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )
        sub = parser.add_subparsers(dest="search_server_action", metavar="action")
        sub.add_parser("up", help="Pull the image and start the container (no-op if it answers)")
        sub.add_parser("down", help="Remove the container; settings are kept")
        sub.add_parser("status", help="Show the recorded instance and whether it answers")
        return parser

    def handle(self, args: Namespace) -> int:
        from r4.agent import searxng_local

        actions = {
            "up": searxng_local.up,
            "down": searxng_local.down,
            "status": searxng_local.status,
        }
        action = actions.get(getattr(args, "search_server_action", None) or "status")
        if action is None:
            print("Usage: r4 search-server up|down|status", file=sys.stderr)
            return 2
        result = action()
        _print(result, as_json=bool(getattr(args, "as_json", False)))
        return 0 if result.get("state") in _OK_STATES else 1


def _print(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    line = f"search-server: {result.get('state')}"
    if result.get("url"):
        line += f" at {result['url']}"
    stream = sys.stdout if result.get("state") in _OK_STATES else sys.stderr
    print(line, file=stream)
    for key in ("reason", "hint"):
        if result.get(key):
            print(f"  {key}: {result[key]}", file=stream)
    if result.get("state") in ("running", "started"):
        print("  web_search uses it in sessions with public web access (/web public).")
