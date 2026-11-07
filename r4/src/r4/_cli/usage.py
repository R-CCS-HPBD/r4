"""``r4 usage`` command handler — LLM token usage summary and export.

Queries the UMRS usage endpoints (``{api_prefix}/usage/*``) to display
billing-relevant token consumption grouped by model, day, user, or project.
Mode-aware 2026-10-19 (cleanup C-2(iii)): the paths ride ``_api_prefix``
(``/gw`` behind the edge, ``/umrs`` direct); hard-coded ``/umrs/*`` paths
404 behind the gateway (the only ``/umrs/`` location there is
``/umrs/zmq/``).
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


def _fmt_tokens(n: int | float | None) -> str:
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _print_summary_table(rows: list[dict], group_by: str) -> None:
    if not rows:
        print("No usage records found.")
        return

    # Determine group column header
    group_label = group_by.replace("_", " ").title()

    # Build table
    headers = [
        group_label,
        "Requests",
        "Prompt",
        "Completion",
        "Reasoning",
        "Total",
        "Cached Read",
        "Avg ms",
    ]
    widths = [max(len(h), 12) for h in headers]

    def _row_vals(r: dict) -> list[str]:
        # The group value might be under different keys depending on group_by
        group_val = ""
        for key in (group_by, "model", "user_id", "project_id", "event_time"):
            if key in r:
                group_val = str(r[key])
                break
        return [
            group_val[:40],
            str(int(r.get("request_count", 0))),
            _fmt_tokens(r.get("total_prompt_tokens", 0)),
            _fmt_tokens(r.get("total_completion_tokens", 0)),
            _fmt_tokens(r.get("total_reasoning_tokens", 0)),
            _fmt_tokens(r.get("total_tokens", 0)),
            _fmt_tokens(r.get("total_cached_read_tokens", 0)),
            f"{float(r.get('avg_duration_ms') or 0):.0f}",
        ]

    # Compute column widths
    all_rows = [_row_vals(r) for r in rows]
    for vals in all_rows:
        for i, v in enumerate(vals):
            widths[i] = max(widths[i], len(v))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for vals in all_rows:
        print(fmt.format(*vals))

    # Totals
    total_req = sum(int(r.get("request_count", 0)) for r in rows)
    total_prompt = sum(int(r.get("total_prompt_tokens", 0)) for r in rows)
    total_comp = sum(int(r.get("total_completion_tokens", 0)) for r in rows)
    total_reasoning = sum(int(r.get("total_reasoning_tokens", 0)) for r in rows)
    total_tok = sum(int(r.get("total_tokens", 0)) for r in rows)
    print()
    print(
        f"Total: {total_req} requests, {_fmt_tokens(total_prompt)} prompt, "
        f"{_fmt_tokens(total_comp)} completion, {_fmt_tokens(total_reasoning)} reasoning, "
        f"{_fmt_tokens(total_tok)} tokens"
    )


def _build_query_params(args: Namespace) -> dict:
    params: dict = {}
    if args.since:
        params["since"] = args.since
    if args.until:
        params["until"] = args.until
    if getattr(args, "user", None):
        params["user_id"] = args.user
    if args.project:
        params["project_id"] = args.project
    if args.model:
        params["model"] = args.model
    return params


def _response_payload(resp):
    return resp.json() if hasattr(resp, "json") else resp


def _print_record_rows(records: list[dict]) -> None:
    if not records:
        print("No usage records found.")
        return

    for rec in records:
        ts = rec.get("event_time", "")[:19]
        model = rec.get("model", "")
        prompt = rec.get("prompt_tokens", 0)
        comp = rec.get("completion_tokens", 0)
        reasoning = rec.get("reasoning_tokens", 0)
        total = rec.get("total_tokens", 0)
        cached = rec.get("cached_read_tokens", 0)
        user = rec.get("user_id", "")
        streaming = "stream" if rec.get("is_streaming") else "batch"
        print(
            f"{ts}  {model:<30s}  {streaming:<6s}  "
            f"p={prompt:<6d} c={comp:<6d} r={reasoning:<6d} "
            f"cache={cached:<6d} t={total:<6d}  user={user}"
        )


def _print_payload(data: dict, *, as_json: bool, group_by: str) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    _print_summary_table(data.get("rows", []), group_by)


def _show_billing(client, params: dict, *, as_json: bool) -> None:
    resp = client.get(f"{client._api_prefix}/usage/billing", params=params)
    _print_payload(_response_payload(resp), as_json=as_json, group_by="user")


def _show_records(client, params: dict, *, as_json: bool, limit: int) -> None:
    query = dict(params)
    query["limit"] = limit
    resp = client.get(f"{client._api_prefix}/usage/records", params=query)
    data = _response_payload(resp)
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    _print_record_rows(data.get("records", []))


def _show_summary(client, params: dict, *, as_json: bool, group_by: str) -> None:
    query = dict(params)
    query["group_by"] = group_by
    resp = client.get(f"{client._api_prefix}/usage/summary", params=query)
    _print_payload(_response_payload(resp), as_json=as_json, group_by=group_by)


@command
class UsageHandler(CommandHandler):
    """Query LLM token usage for billing."""

    name = "usage"
    help = "Show LLM token usage summary"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Display LLM token usage summary for billing and quota tracking.",
        )
        p.add_argument(
            "--group-by",
            "-g",
            default="model",
            choices=["model", "day", "user", "user_model", "project", "model_day"],
            help="Group usage by (default: model)",
        )
        p.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive)")
        p.add_argument("--until", help="End date (YYYY-MM-DD, exclusive)")
        p.add_argument("--user", help="Filter by user ID (admin only)")
        p.add_argument("--project", help="Filter by project ID")
        p.add_argument("--model", help="Filter by model name")
        p.add_argument(
            "--json",
            dest="as_json",
            action="store_true",
            help="Output as JSON",
        )
        p.add_argument(
            "--records",
            action="store_true",
            help="Show raw records instead of summary",
        )
        p.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Max records to return (with --records, default: 100)",
        )
        p.add_argument(
            "--billing",
            action="store_true",
            help="Show per-user billing aggregation (admin only)",
        )
        return p

    def handle(self, args: Namespace) -> int:
        from ..client import Client
        from ..config import get_config

        config = get_config()
        client = Client(config=config)
        params = _build_query_params(args)

        try:
            if args.billing:
                _show_billing(client, params, as_json=args.as_json)
            elif args.records:
                _show_records(client, params, as_json=args.as_json, limit=args.limit)
            else:
                _show_summary(
                    client,
                    params,
                    as_json=args.as_json,
                    group_by=args.group_by,
                )

        except Exception as exc:
            print(f"Error querying usage: {exc}", file=sys.stderr)
            return 1

        return 0
