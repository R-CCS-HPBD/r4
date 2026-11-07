"""``r4 hpc`` — your HPC sites and your jobs across them (multi-HPC plan S2/S3).

Commands:

    r4 hpc sites [--json] [--refresh]     Every site: health, access mode, account
    r4 hpc jobs [--json] [--status S]     Your jobs at every site, each once
    r4 hpc cancel <job>[@<site>]          Cancel through the site that runs it
    r4 hpc retry <job>[@<site>]           Retry through the site that runs it

A site is a registered platform source (``r4 source list``) or this profile's
own gateway. A site that has not declared its HPC access mode is listed but
marked not selectable: the agent will not route work there from another site.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from typing import Any

from .base import CommandHandler
from .registry import command


def _sites(args: Namespace) -> int:
    from ..sites import describe, inventory

    rows = inventory(refresh=bool(args.refresh))
    if args.as_json:
        print(json.dumps({"sites": [r.to_dict() for r in rows]}, indent=2, default=str))
        return 0
    if not rows:
        print("No HPC sites: register one with `r4 source add <name> <url>`.")
        return 0
    for row in rows:
        print(describe(row))
    return 0


def _job_text(row: Any) -> str:
    job = row.job
    seen = "" if len(row.seen_via) < 2 else f"  (listed by {len(row.seen_via)} gateways)"
    where = "" if "@" in row.handle else f"  (site: {row.site})"
    return f"{row.handle:<36} {job.status:<10} {job.job_type:<22} {job.created_at}{where}{seen}"


def _jobs(args: Namespace) -> int:
    """Exit 1 when every site failed (nothing could be listed)."""
    from ..hpc_jobs import list_across_sites
    from ..sites import site_sources

    jobs, errors = list_across_sites(status=args.status, limit=args.limit)
    status = 1 if errors and len(errors) >= len(site_sources()) else 0
    if args.as_json:
        payload = {"jobs": [j.to_dict() for j in jobs], "errors": errors}
        print(json.dumps(payload, indent=2, default=str))
        return status
    for row in jobs:
        print(_job_text(row))
    if not jobs:
        print("No jobs found at any reachable site.")
    for site, error in errors.items():
        print(f"{site}: error: {error}", file=sys.stderr)
    return status


def _act(args: Namespace) -> int:
    from ..errors import R4Error
    from ..hpc_jobs import act_on_job

    try:
        site, job = act_on_job(args.hpc_command, args.job)
    except R4Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Job {job.job_id}@{site}: {args.hpc_command} sent (status={job.status})")
    return 0


_HANDLERS = {"sites": _sites, "jobs": _jobs, "cancel": _act, "retry": _act}


@command
class HpcCommand(CommandHandler):
    """``r4 hpc`` — my HPC sites and jobs across them."""

    name = "hpc"
    help = "My HPC sites (health, access mode, account) and my jobs across them"

    def register(self, subparsers: Any) -> ArgumentParser:
        parser: ArgumentParser = subparsers.add_parser(
            self.name,
            help=self.help,
            formatter_class=RawDescriptionHelpFormatter,
            description=(
                "Every HPC site you can reach (registered platform sources and "
                "this profile's gateway), and your jobs across them, each "
                "labelled with the site that runs it."
            ),
            epilog=(
                "Examples:\n"
                "  r4 hpc sites\n"
                "  r4 hpc jobs --status running\n"
                "  r4 hpc cancel 1234@bdrt\n"
            ),
        )
        sub = parser.add_subparsers(dest="hpc_command", metavar="<hpc_command>")
        sites = sub.add_parser("sites", help="List my HPC sites with live status")
        sites.add_argument("--json", "-j", action="store_true", dest="as_json")
        sites.add_argument("--refresh", action="store_true", help="Probe again (skip the cache)")
        jobs = sub.add_parser("jobs", help="My jobs at every site, each once")
        jobs.add_argument("--json", "-j", action="store_true", dest="as_json")
        jobs.add_argument("--status", default=None, help="queued/running/completed/failed")
        jobs.add_argument("--limit", type=int, default=50, help="Max jobs per site")
        for action in ("cancel", "retry"):
            act = sub.add_parser(action, help=f"{action.title()} a job through its own site")
            act.add_argument("job", help="Job id, or <id>@<site>")
        return parser

    def handle(self, args: Namespace) -> int:
        handler = _HANDLERS.get(getattr(args, "hpc_command", None) or "")
        if handler is None:
            print("usage: r4 hpc {sites,jobs,cancel,retry} ...", file=sys.stderr)
            return 2
        return handler(args)
