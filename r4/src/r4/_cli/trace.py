"""``r4 trace`` — inspect distributed request traces."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone

from .base import CommandHandler
from .registry import command


def _format_ts(ts: float) -> str:
    """Format a unix timestamp as human-readable local time."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_timestamp(value: str) -> float:
    """Parse a user-supplied timestamp string to unix epoch.

    Accepts:
      - ISO 8601 datetime  (e.g. 2026-05-20T10:00:00)
      - Date only          (e.g. 2026-05-20 → midnight UTC)
      - Relative shorthand (e.g. 1h, 30m, 2d → N units ago)
    """
    import re

    value = value.strip()
    m = re.fullmatch(r"(\d+)([smhd])", value)
    if m:
        from time import time as _now

        amount, unit = int(m.group(1)), m.group(2)
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return _now() - amount * multiplier

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue

    raise ValueError(f"Cannot parse timestamp: {value!r}")


def _run_trace(args: Namespace) -> int:
    """Query audit events for a request ID."""
    from ..client import Client
    from ..config import get_config

    request_id = args.request_id
    as_json = bool(getattr(args, "as_json", False))

    config = get_config()
    client = Client(config=config)
    params = _trace_params(args, request_id)
    if params is None:
        return 1

    try:
        data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace", params=params)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    events = data.get("events", [])

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    if getattr(args, "summary", False):
        return _print_summary(events, request_id)

    return _print_trace_events(events, request_id)


def _trace_params(args: Namespace, request_id: str) -> dict[str, str | int | float] | None:
    params: dict[str, str | int | float] = {"limit": getattr(args, "limit", 50)}
    optional_fields = [
        ("request_id", request_id),
        ("event", getattr(args, "event", None)),
        ("actor", getattr(args, "actor", None)),
    ]
    for key, value in optional_fields:
        if value:
            params[key] = value

    return _add_trace_time_bounds(args, params)


def _add_trace_time_bounds(
    args: Namespace,
    params: dict[str, str | int | float],
) -> dict[str, str | int | float] | None:
    for attr in ("since", "until"):
        value = getattr(args, attr, None)
        if not value:
            continue
        try:
            params[attr] = _parse_timestamp(value)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
    return params


def _print_trace_events(events: list[dict], request_id: str) -> int:
    if not events:
        target = f" for request_id={request_id}" if request_id else ""
        print(f"No audit events found{target}.")
        return 0

    print(f"Trace: {request_id or '(all)'}")
    print(f"Events: {len(events)}\n")

    for event in events:
        _print_trace_event(event)

    return 0


def _trace_outcome_marker(outcome: str) -> str:
    if outcome == "success":
        return "✓"
    if outcome in ("denied", "error"):
        return "✗"
    return "–"


def _trace_event_line(event: dict) -> str:
    ts = _format_ts(event.get("ts", 0))
    event_name = event.get("event", "?")
    outcome = event.get("outcome", "?")
    line = f"  {_trace_outcome_marker(outcome)} {ts}  {event_name}"
    for key, label in (("actor", "actor"), ("project_id", "project")):
        value = event.get(key, "")
        if value:
            line += f"  {label}={value}"
    if outcome not in ("success", ""):
        line += f"  [{outcome}]"
    return line


def _print_trace_event(event: dict) -> None:
    print(_trace_event_line(event))
    for key, value in event.get("details", {}).items():
        print(f"      {key}: {value}")


def _print_summary(events: list[dict], request_id: str) -> int:
    """Print aggregated trace summary statistics."""
    if not events:
        target = f" for request_id={request_id}" if request_id else ""
        print(f"No audit events found{target}.")
        return 0

    (
        event_counts,
        outcome_counts,
        actor_counts,
        project_counts,
        timestamps,
    ) = _trace_summary_stats(events)
    total = len(events)
    successes = outcome_counts.get("success", 0)
    errors = outcome_counts.get("error", 0)
    denials = outcome_counts.get("denied", 0)

    print(f"Trace Summary: {request_id or '(all events)'}")
    print(f"{'=' * 50}")
    print(f"  Total events:  {total}")
    print(f"  Successes:     {successes}")
    print(f"  Errors:        {errors}")
    print(f"  Denials:       {denials}")

    _print_trace_time_range(timestamps)
    _print_counter_section("Event Types", event_counts, 15)
    _print_counter_section("Actors", actor_counts, 10)
    _print_counter_section("Projects", project_counts, 10)
    _print_failure_clusters(events)
    return 0


def _trace_summary_stats(events: list[dict]):
    from collections import Counter

    event_counts: Counter = Counter()
    outcome_counts: Counter = Counter()
    actor_counts: Counter = Counter()
    project_counts: Counter = Counter()
    timestamps: list[float] = []

    for event in events:
        event_counts[event.get("event", "unknown")] += 1
        outcome_counts[event.get("outcome", "unknown")] += 1
        actor = event.get("actor", "")
        if actor:
            actor_counts[actor] += 1
        project = event.get("project_id", "")
        if project:
            project_counts[project] += 1
        ts = event.get("ts", 0)
        if ts:
            timestamps.append(ts)
    return event_counts, outcome_counts, actor_counts, project_counts, timestamps


def _print_trace_time_range(timestamps: list[float]) -> None:
    if not timestamps:
        return
    oldest = min(timestamps)
    newest = max(timestamps)
    print(f"  Time range:    {_format_ts(oldest)} → {_format_ts(newest)}")
    print(f"  Span:          {_format_trace_span(newest - oldest)}")


def _format_trace_span(span: float) -> str:
    if span > 86400:
        return f"{span / 86400:.1f} days"
    if span > 3600:
        return f"{span / 3600:.1f} hours"
    if span > 60:
        return f"{span / 60:.1f} minutes"
    return f"{span:.1f} seconds"


def _print_counter_section(title: str, counts, limit: int) -> None:
    if not counts:
        return
    print(f"\n  {title} ({len(counts)}):")
    for name, count in counts.most_common(limit):
        print(f"    {count:>4}  {name}")


def _print_failure_clusters(events: list[dict]) -> None:
    from collections import Counter

    failures = [event for event in events if event.get("outcome") in ("error", "denied")]
    if not failures:
        return
    failure_events: Counter = Counter(event.get("event", "unknown") for event in failures)
    print(f"\n  Failure Clusters ({len(failures)} total):")
    for event_name, count in failure_events.most_common(10):
        print(f"    {count:>4}  {event_name}")


@command
class TraceCommand(CommandHandler):
    """``r4 trace`` — inspect distributed request traces."""

    name = "trace"
    help = "Inspect audit trail for a request ID"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument("request_id", nargs="?", default="", help="Request/correlation ID to trace")
        p.add_argument("--event", "-e", help="Filter by event name substring")
        p.add_argument("--actor", "-a", help="Filter by actor/user")
        p.add_argument(
            "--limit", "-n", type=int, default=50, help="Maximum events to return (default: 50)"
        )
        p.add_argument(
            "--since",
            help="Show events after timestamp (ISO, date, or relative: 1h, 30m, 2d)",
        )
        p.add_argument(
            "--until",
            help="Show events before timestamp (ISO, date, or relative: 1h, 30m, 2d)",
        )
        p.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
        p.add_argument(
            "--summary",
            "-s",
            action="store_true",
            help="Show aggregated summary instead of individual events",
        )
        return p

    def handle(self, args: Namespace) -> int:
        return _run_trace(args)
