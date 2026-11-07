"""r4 schedule CLI — manage scheduled jobs.

Usage:
    r4 schedule list
    r4 schedule add --name <name> --cron <expr> --job-type <type>
    r4 schedule pause <id>
    r4 schedule resume <id>
    r4 schedule remove <id>
"""

from __future__ import annotations

import logging
import sys
from argparse import ArgumentParser, Namespace
from typing import Any

from r4._cli.base import CommandHandler
from r4._cli.registry import command

logger = logging.getLogger(__name__)


@command
class ScheduleCommand(CommandHandler):
    name = "schedule"
    help = "Manage scheduled jobs"

    def register(self, subparsers: Any) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="schedule_action")

        # list
        list_p = sub.add_parser("list", help="List active schedules")
        list_p.add_argument("--json", dest="as_json", action="store_true", help="JSON output")

        # add
        add_p = sub.add_parser("add", help="Add a scheduled job")
        add_p.add_argument("--name", required=True, help="Schedule name")
        add_p.add_argument("--cron", required=True, help="Cron expression (5-field)")
        add_p.add_argument(
            "--job-type",
            required=True,
            help="Job type (connector_sync, recipe_run, maintenance)",
        )
        add_p.add_argument(
            "--param",
            "-p",
            action="append",
            default=[],
            help="Job parameter in key=value format",
        )

        # pause
        pause_p = sub.add_parser("pause", help="Pause a schedule")
        pause_p.add_argument("schedule_id", help="Schedule ID")

        # resume
        resume_p = sub.add_parser("resume", help="Resume a paused schedule")
        resume_p.add_argument("schedule_id", help="Schedule ID")

        # remove
        rm_p = sub.add_parser("remove", help="Remove a schedule")
        rm_p.add_argument("schedule_id", help="Schedule ID")

        return parser

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "schedule_action", None)
        if action == "list":
            return self._list(args)
        if action == "add":
            return self._add(args)
        if action == "pause":
            return self._pause(args)
        if action == "resume":
            return self._resume(args)
        if action == "remove":
            return self._remove(args)
        print("Usage: r4 schedule {list|add|pause|resume|remove}", file=sys.stderr)
        return 1

    def _store(self):
        from r4.schedule_store import ScheduleStore

        return ScheduleStore()

    def _list(self, args: Namespace | None = None) -> int:
        records = self._store().list()
        if args and getattr(args, "as_json", False):
            import json as _json

            print(_json.dumps([r.to_dict() for r in records], indent=2))
            return 0
        print(f"Schedules (local store: {self._store().path}):")
        if not records:
            print("  (none yet — add one with `r4 schedule add`)")
            return 0
        print(f"{'ID':<10} {'NAME':<20} {'CRON':<18} {'STATUS':<8} {'NEXT RUN':<20}")
        print("-" * 80)
        for r in records:
            next_run = r.next_run or "-"
            row = f"{r.schedule_id:<10} {r.name[:20]:<20} {r.cron:<18} {r.status:<8}"
            print(f"{row} {next_run:<20}")
        print("\nnote: schedules are persisted locally; UMRS server-side scheduler")
        print("routing is not yet exposed via the platform API")
        return 0

    def _add(self, args: Namespace) -> int:
        # Validate cron locally using the shared schema in r4-contracts.
        from r4_contracts.scheduler import validate_cron

        errors = validate_cron(args.cron)
        if errors:
            print("Invalid cron expression:")
            for e in errors:
                print(f"  - {e}")
            return 1

        params = {}
        for p in args.param:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = v

        record = self._store().add(args.name, args.cron, args.job_type, params)
        print(f"✓ Schedule added: {record.schedule_id}")
        print(f"  Name: {record.name}")
        print(f"  Cron: {record.cron}")
        print(f"  Job type: {record.job_type}")
        print(f"  Params: {record.params}")
        print(f"  Next run: {record.next_run or '(none within 1 year)'}")
        print(f"  Store: {self._store().path} (local; not yet synced to UMRS scheduler)")
        return 0

    def _pause(self, args: Namespace) -> int:
        record = self._store().pause(args.schedule_id)
        if record is None:
            print(f"✗ Schedule not found: {args.schedule_id}", file=sys.stderr)
            return 1
        print(f"✓ Paused schedule: {record.schedule_id} ({record.name})")
        return 0

    def _resume(self, args: Namespace) -> int:
        record = self._store().resume(args.schedule_id)
        if record is None:
            print(f"✗ Schedule not found: {args.schedule_id}", file=sys.stderr)
            return 1
        print(f"✓ Resumed schedule: {record.schedule_id} ({record.name})")
        return 0

    def _remove(self, args: Namespace) -> int:
        record = self._store().remove(args.schedule_id)
        if record is None:
            print(f"✗ Schedule not found: {args.schedule_id}", file=sys.stderr)
            return 1
        print(f"✓ Removed schedule: {record.schedule_id} ({record.name})")
        return 0
