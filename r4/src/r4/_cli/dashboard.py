"""``r4 dashboard`` — unified ops board (U1 consumer).

One-screen aggregate of the UMRS dashboard overview: jobs, models, health,
workflow executions (durable registry), and connector intake state. Lean
text view (no Textual dependency); TUI front-ends can adopt the same
``r4.dashboard.DashboardClient`` payload later.
"""

from __future__ import annotations

import argparse
import json
import sys
from argparse import ArgumentParser, Namespace

from ._helpers import _preflight_check
from .base import CommandHandler
from .registry import command

_HEALTH_GLYPH = {"healthy": "✓", "degraded": "≈", "unhealthy": "✗"}


@command
class DashboardCommand(CommandHandler):
    """``r4 dashboard`` — unified ops board (jobs, health, workflows, intake)."""

    name = "dashboard"
    help = "Unified ops board (jobs, models, health, workflows, intake)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Unified ops board: jobs, models, health, workflows, intake.",
            epilog=(
                "Examples:\n"
                "  r4 dashboard          Full board\n"
                "  r4 dashboard --json   Machine-readable overview\n"
                "  r4 dashboard --jobs   Jobs section only\n"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--section",
            choices=["jobs", "models", "health", "workflows", "connectors"],
            dest="only",
            help="Render one section only",
        )
        parser.add_argument("--json", "-j", action="store_true", dest="as_json")
        return parser

    def handle(self, args: Namespace) -> int:
        _preflight_check(action="dashboard")
        from ..client import Client

        client = Client()
        try:
            overview = client.dashboard.overview()
        except Exception as exc:  # noqa: BLE001
            print(f"Error: dashboard unreachable: {exc}", file=sys.stderr)
            return 1

        if args.as_json:
            print(json.dumps(overview, indent=2, default=str))
            return 0

        _render_board(overview, only=getattr(args, "only", None))
        return 0


def _render_board(overview: dict, only: str | None = None) -> None:
    """Full board or one section, with health glyph first on head."""
    health = overview.get("system_health") or "unknown"
    glyph = _HEALTH_GLYPH.get(health, "?")
    print(f"R4 platform — system {glyph} {health}")
    print("=" * 72)

    if only in (None, "jobs"):
        _render_jobs(overview)
    if only in (None, "models"):
        _render_models(overview)
    if only in (None, "workflows"):
        _render_workflows(overview.get("workflows"))
    if only in (None, "connectors"):
        _render_connectors(overview.get("connectors"))


def _render_jobs(overview: dict) -> None:
    print(
        f" jobs          : total {overview.get('total_jobs', '?')}"
        f"  running {overview.get('running_jobs', '?')}"
        f"  queued {overview.get('queued_jobs', '?')}"
        f"  completed {overview.get('completed_jobs', '?')}"
        f"  failed {overview.get('failed_jobs', '?')}"
    )


def _render_models(overview: dict) -> None:
    print(
        f" models        : total {overview.get('total_models', '?')}"
        f"  available {overview.get('available_models', '?')}"
        f"  staging {overview.get('staging_models', '?')}"
        f"  deprecated {overview.get('deprecated_models', '?')}"
    )


def _render_workflows(wf: dict | None) -> None:
    if not wf:
        print(" workflows     : n/a (registry absent)")
        return
    print(
        f" workflows     : total {wf.get('total', '?')}"
        f"  running {wf.get('running', '?')}"
        f"  completed {wf.get('completed', '?')}"
        f"  failed {wf.get('failed', '?')}"
        f"  cancelled {wf.get('cancelled', '?')}"
    )


def _render_connectors(connectors: dict | None) -> None:
    if connectors is None:
        print(" connectors    : n/a (dashboard section absent)")
        return
    avail = connectors.get("available_types") or []
    unavail = connectors.get("unavailable") or []
    sync_map = connectors.get("sync") or {}
    print(f" connectors    : {len(avail)} available, {len(unavail)} import-failed")
    for name in sorted(sync_map):
        s = sync_map[name]
        dlq = f", dlq {s.get('dlq')}" if s.get("dlq") else ""
        print(
            f"   card {name:<13} {s.get('status', '?')} @ {s.get('last_sync_at', '?')}"
            f"  docs {s.get('docs_synced', 0)}{dlq}"
        )
