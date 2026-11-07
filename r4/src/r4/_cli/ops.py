"""``r4 ops`` command handler (COR-006, 2026-05-14).

Thin CLI wrapper for UMRS operational introspection endpoints.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler
from .registry import command


@command
class OpsCommand(CommandHandler):
    """``r4 ops`` — operational introspection for UMRS."""

    name = "ops"
    help = "UMRS operational introspection"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Inspect UMRS runtime health, topology, and activity timeline.\n\n"
                "Answers: what is running, who authorized it, what failed,\n"
                "and which component owns the next action."
            ),
            epilog=(
                "Examples:\n"
                "  r4 ops health\n"
                "  r4 ops topology --json\n"
                "  r4 ops timeline --window 120"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="ops_command",
            title="subcommands",
            metavar="<ops-command>",
        )
        sub.required = True

        sub_health = sub.add_parser("health", help="Subsystem health summary")
        sub_health.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
        )

        sub_topo = sub.add_parser("topology", help="Active runtime topology")
        sub_topo.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
        )

        sub_timeline = sub.add_parser("timeline", help="Job-attempt timeline")
        sub_timeline.add_argument(
            "--window",
            "-w",
            type=int,
            default=60,
            help="Look-back window in minutes (default: 60)",
        )
        sub_timeline.add_argument(
            "--limit",
            "-l",
            type=int,
            default=50,
            help="Max events to show",
        )
        sub_timeline.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
        )

        return parser

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="ops")

        sub = getattr(args, "ops_command", None)
        if not sub:
            print("Usage: r4 ops <health|topology|timeline>", file=sys.stderr)
            return 1

        if sub == "health":
            return _cmd_ops_health(args)
        if sub == "topology":
            return _cmd_ops_topology(args)
        if sub == "timeline":
            return _cmd_ops_timeline(args)

        print(f"Unknown ops command: {sub}", file=sys.stderr)
        return 1


def _request(endpoint: str, params: dict | None = None) -> dict:
    from ..client import Client

    client = Client()
    url = f"{client.config.gateway_url}{getattr(client, '_api_prefix', '/gw')}/ops/{endpoint}"
    return client.http.request("GET", url, params=params)


def _cmd_ops_health(args: Namespace) -> int:
    try:
        data = _request("health")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    overall = data.get("overall", "unknown")
    icon = {"healthy": "✓", "degraded": "⚠", "unhealthy": "✗"}.get(overall, "?")

    print(f"UMRS Health: {icon} {overall}")
    print(f"  Instance:  {data.get('instance_id', 'unknown')}")
    print(f"  Uptime:    {data.get('uptime_seconds', 0):.0f}s")
    print()

    for sub in data.get("subsystems", []):
        sub_icon = {"healthy": "✓", "degraded": "⚠", "unavailable": "✗"}.get(
            sub.get("status", ""), "?"
        )
        print(f"  [{sub_icon}] {sub.get('name', ''):<16} {sub.get('detail', '')}")

    return 0


def _cmd_ops_topology(args: Namespace) -> int:
    try:
        data = _request("topology")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    print(f"UMRS Topology (instance={data.get('instance_id', '?')})")
    print(f"  Active Jobs:     {data.get('active_jobs', 0)}")
    print(f"  Active Attempts: {data.get('active_attempts', 0)}")
    print()

    for comp in data.get("components", []):
        print(
            f"  {comp.get('component', ''):<24} {comp.get('role', ''):<20} {comp.get('detail', '')}"
        )

    return 0


def _cmd_ops_timeline(args: Namespace) -> int:
    params = {
        "window_minutes": args.window,
        "limit": args.limit,
    }

    try:
        data = _request("timeline", params=params)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    events = data.get("events", [])

    if getattr(args, "as_json", False):
        print(json.dumps(events, indent=2))
        return 0

    if not events:
        print(f"No events in the last {args.window} minutes.")
        return 0

    event_icons = {
        "created": "📋",
        "attempt_started": "▶",
        "succeeded": "✓",
        "failed": "✗",
        "lost": "⚠",
    }

    print(f"{'TIMESTAMP':<24}  {'EVENT':<18}  {'JOB ID':<20}  {'TYPE':<18}  DETAIL")
    print("-" * 110)

    for ev in events:
        ts = ev.get("timestamp", "")[:19]
        icon = event_icons.get(ev.get("event", ""), "?")
        att_str = ""
        if ev.get("attempt_no"):
            att_str = f" att#{ev['attempt_no']}"

        print(
            f"{ts:<24}  {icon} {ev.get('event', ''):<16}  "
            f"{ev.get('job_id', ''):<20}  "
            f"{ev.get('job_type', ''):<18}  "
            f"{ev.get('detail', '')}{att_str}"
        )

    print(f"\n{len(events)} events in last {data.get('window_minutes', args.window)} minutes")
    return 0
