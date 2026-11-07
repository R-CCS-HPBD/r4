"""``r4 quota`` command handler (ADR-051 read model).

Prints the caller's two resource poles (CPU-process units + catalog-
attributed disk bytes). The numbers come from
``GET /gw/quota/units`` (or ``/umrs/quota/units`` direct) — the same
stores and env knobs the enforcement edges read, nothing fabricated.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class QuotaCommand(CommandHandler):
    """``r4 quota`` — the caller's two resource poles."""

    name = "quota"
    help = "My resource poles (CPU-process units + disk bytes)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help="My resource poles (ADR-051 gauge = the enforced gate)",
            description=(
                "Show the caller's CPU-process units and catalog-attributed "
                "disk usage against the site caps, exactly as the spawn and "
                "upload gates read them."
            ),
        )
        p.add_argument("--json", "-j", action="store_true", dest="as_json")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="quota")
        from ..client import Client

        result = Client().quota_units()
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            cpu = result["cpu_process"]
            disk = result["disk_bytes"]

            def _ratio(row: dict) -> str:
                if not row["limit"]:
                    return f"{row['current']} (uncapped)"
                return f"{row['current']}/{row['limit']}"

            print(f"user:           {result.get('user_id', '?')}")
            print(f"cpu processes:  {_ratio(cpu)}")
            print(f"disk bytes:     {_ratio(disk)}")
        return 0
