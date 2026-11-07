"""``r4 governance`` — inspect and manage resource governance.

User-facing subcommands (quotas, ownership, audit, shares) are registered
as a top-level ``r4 governance`` command.  Operator-only subcommands
(analytics) remain under ``r4 admin analytics``.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


def _run_quotas(args: Namespace) -> int:
    """List quotas for a project."""
    from ..client import Client
    from ..config import get_config

    project_id = args.project_id
    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)

    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/quotas",
            params={"project_id": project_id},
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    quotas = data.get("quotas", [])
    if not quotas:
        print(f"No quotas configured for project '{project_id}'.")
        return 0

    # Tabular output
    print(f"Quotas for project: {project_id}")
    print(f"{'Resource Type':<20} {'Usage':>8} {'Warn':>8} {'Max':>8}")
    print("-" * 48)
    for q in quotas:
        warn_str = str(q.get("warn_count") or "-")
        usage_pct = ""
        if q["max_count"] > 0:
            pct = q["current_count"] / q["max_count"] * 100
            usage_pct = f" ({pct:.0f}%)"
        print(
            f"{q['resource_type']:<20} "
            f"{q['current_count']:>8} "
            f"{warn_str:>8} "
            f"{q['max_count']:>8}"
            f"{usage_pct}"
        )

    return 0


def _run_ownership(args: Namespace) -> int:
    """List resource ownership records."""
    from ..client import Client
    from ..config import get_config

    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)

    params: dict[str, str] = {}
    if getattr(args, "owner", None):
        params["owner_id"] = args.owner
    if getattr(args, "project", None):
        params["project_id"] = args.project
    if getattr(args, "resource_type", None):
        params["resource_type"] = args.resource_type
    if not params:
        # Server rejects unfiltered queries (400): scope to current project
        # so bare `r4 governance ownership` is meaningful.
        project_id = getattr(config, "project_id", None)
        if project_id:
            params["project_id"] = project_id

    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/ownership", params=params
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    records = data.get("records", [])
    if not records:
        print("No ownership records found.")
        return 0

    print(f"{'Type':<16} {'Resource ID':<24} {'Owner':<16} {'Tier':<10} {'Project':<16}")
    print("-" * 84)
    for r in records:
        print(
            f"{r.get('resource_type', ''):<16} "
            f"{r.get('resource_id', ''):<24} "
            f"{r.get('owner_id', ''):<16} "
            f"{r.get('tier', ''):<10} "
            f"{r.get('project_id', ''):<16}"
        )

    return 0


def _run_audit(args: Namespace) -> int:
    """Query governance-related audit events."""
    from ..client import Client
    from ..config import get_config

    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)
    params = _governance_time_params(args, include_limit=True)
    if params is None:
        return 1
    params["event"] = "governance."

    try:
        data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace", params=params)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    events = data.get("events", [])
    _print_governance_audit_events(events)
    return 0


def _governance_time_params(
    args: Namespace,
    *,
    include_limit: bool = False,
) -> dict[str, str | int | float] | None:
    params: dict[str, str | int | float] = {}
    if include_limit:
        params["limit"] = getattr(args, "limit", 50)
    if getattr(args, "project", None):
        params["project_id"] = args.project
    if not getattr(args, "since", None):
        return params

    from .trace import _parse_timestamp

    try:
        params["since"] = _parse_timestamp(args.since)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    return params


def _print_governance_audit_events(events: list[dict]) -> None:
    if not events:
        print("No governance audit events found.")
        return

    for event in events:
        print(_governance_audit_line(event))


def _governance_outcome_label(outcome: str) -> str:
    if outcome == "denied":
        return "[DENIED] "
    if outcome == "warning":
        return "[WARN]   "
    return "[OK]     "


def _governance_audit_line(event: dict) -> str:
    parts = [_governance_outcome_label(event.get("outcome", "")), event.get("event", "")]
    optional_fields = [
        ("actor", event.get("actor", "")),
        ("project", event.get("project_id", "")),
    ]
    for label, value in optional_fields:
        if value:
            parts.append(f"{label}={value}")
    details = event.get("details", {})
    if details:
        parts.append(" ".join(f"{key}={value}" for key, value in details.items()))
    return f"{event.get('ts', '')}  {' '.join(parts)}"


def _run_analytics(args: Namespace) -> int:
    """Display governance analytics summary."""
    from ..client import Client
    from ..config import get_config

    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)
    params = _governance_time_params(args)
    if params is None:
        return 1

    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/analytics", params=params
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    _print_governance_analytics(data)
    return 0


def _print_governance_analytics(data: dict) -> None:
    quotas = _print_quota_utilization(data.get("quota_utilization", []))
    counts = _print_resource_counts(data.get("resource_counts", {}))
    events = _print_event_summary(data.get("event_summary", {}))
    _print_recent_governance_events("Recent Denials", data.get("recent_denials", []))
    _print_recent_governance_events("Recent Warnings", data.get("recent_warnings", []))

    if not quotas and not counts and not events:
        print("No governance analytics data available.")


def _print_quota_utilization(quotas: list[dict]) -> bool:
    if quotas:
        print("Quota Utilization")
        print(f"{'Project':<20} {'Resource':<18} {'Usage':>8} {'Warn':>8} {'Max':>8} {'Util':>8}")
        print("-" * 72)
        for q in quotas:
            warn_str = str(q.get("warn_count") or "-")
            pct = q.get("utilization_pct", 0)
            marker = ""
            if pct >= 100:
                marker = " ** EXCEEDED"
            elif q.get("warn_count") and q["current_count"] >= q["warn_count"]:
                marker = " * WARNING"
            print(
                f"{q['project_id']:<20} "
                f"{q['resource_type']:<18} "
                f"{q['current_count']:>8} "
                f"{warn_str:>8} "
                f"{q['max_count']:>8} "
                f"{pct:>7.1f}%"
                f"{marker}"
            )
        print()
    return bool(quotas)


def _print_resource_counts(counts: dict) -> bool:
    if counts:
        print("Resource Counts")
        for rtype, count in sorted(counts.items()):
            print(f"  {rtype:<24} {count}")
        print()
    return bool(counts)


def _print_event_summary(events: dict) -> bool:
    if events:
        print("Governance Event Distribution")
        for event, count in sorted(events.items(), key=lambda x: -x[1]):
            print(f"  {event:<40} {count}")
        print()
    return bool(events)


def _print_recent_governance_events(title: str, events: list[dict]) -> bool:
    if not events:
        return False
    print(f"{title} ({len(events)})")
    for event in events[:5]:
        print(f"  {event.get('ts', '')}  {event.get('event', '')}  {event.get('details', {})}")
    print()
    return True


def _run_shares(args: Namespace) -> int:
    """List resource shares."""
    from ..client import Client
    from ..config import get_config

    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)

    params: dict[str, str] = {}
    if getattr(args, "resource_type", None):
        params["resource_type"] = args.resource_type
    if getattr(args, "resource_id", None):
        params["resource_id"] = args.resource_id

    try:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/shares", params=params
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    shares = data.get("shares", [])
    if not shares:
        print("No active shares found.")
        return 0

    print(
        f"{'Type':<16} {'Resource':<20} {'Shared With':<16} {'Kind':<8} "
        f"{'Access':<8} {'By':<12} {'Expires':<12}"
    )
    print("-" * 94)
    for s in shares:
        expires = s.get("expires_at") or "never"
        print(
            f"{s.get('resource_type', ''):<16} "
            f"{s.get('resource_id', ''):<20} "
            f"{s.get('granted_to', ''):<16} "
            f"{s.get('granted_to_type', ''):<8} "
            f"{s.get('access_level', ''):<8} "
            f"{s.get('granted_by', ''):<12} "
            f"{expires:<12}"
        )

    return 0


@command
class GovernanceCommand(CommandHandler):
    """Inspect and manage resource governance (user-facing)."""

    name = "governance"
    help = "Inspect resource governance (quotas, ownership, audit, shares)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="governance_action")

        # quotas subcommand
        p_quotas = sub.add_parser("quotas", help="List quotas for a project")
        p_quotas.add_argument("project_id", help="Project ID to query")
        p_quotas.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        # ownership subcommand
        p_own = sub.add_parser("ownership", help="List resource ownership records")
        p_own.add_argument("--owner", help="Filter by owner ID")
        p_own.add_argument("--project", help="Filter by project ID")
        p_own.add_argument("--resource-type", help="Filter by resource type")
        p_own.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        # audit subcommand
        p_audit = sub.add_parser("audit", help="View governance audit events")
        p_audit.add_argument("--project", help="Filter by project ID")
        p_audit.add_argument("--since", help="Start time (ISO 8601 or relative: 1h, 30m, 2d)")
        p_audit.add_argument("--limit", type=int, default=50, help="Max events to return")
        p_audit.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        # shares subcommand
        p_shares = sub.add_parser("shares", help="List resource shares")
        p_shares.add_argument("--resource-type", help="Filter by resource type")
        p_shares.add_argument("--resource-id", help="Filter by resource ID")
        p_shares.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        return parser

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "governance_action", None)
        if action == "quotas":
            return _run_quotas(args)
        elif action == "ownership":
            return _run_ownership(args)
        elif action == "audit":
            return _run_audit(args)
        elif action == "shares":
            return _run_shares(args)
        else:
            print("Usage: r4 governance {quotas|ownership|audit|shares}", file=sys.stderr)
            return 1


class AdminGovernanceCommand(CommandHandler):
    """Operator-only governance commands (analytics)."""

    name = "analytics"
    help = "Governance analytics dashboard (operator-only)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        parser.add_argument("--project", help="Filter by project ID")
        parser.add_argument("--since", help="Start time (ISO 8601 or relative: 1h, 30m, 2d)")
        parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
        return parser

    def handle(self, args: Namespace) -> int:
        return _run_analytics(args)
