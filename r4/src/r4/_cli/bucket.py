"""``r4 bucket`` — your personal buckets on the site's object store (ADR-079).

Commands:

    r4 bucket create PROJECT ALIAS    Create <you>.<project>.<alias> (+ -output)
    r4 bucket list                    Your buckets (personal + provisioned)

UMRS creates the buckets and performs every S3 call; you never get storage
credentials. Move data with ``r4 files upload|list|download --bucket``; name
the bucket in a workflow's ``bucket`` param so the HPC lane stages it.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace

from ..files import FilesClient
from .base import CommandHandler
from .registry import command


@command
class BucketCommand(CommandHandler):
    """``r4 bucket`` — create and list your buckets."""

    name = "bucket"
    help = "Create and list your personal buckets (data for workflows on HPC lanes)"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Personal buckets live on the site's object store, named "
                "<user>.<project>.<alias>; the paired <...>-output bucket "
                "receives workflow results. UMRS performs every S3 call."
            ),
            epilog=(
                "Examples:\n"
                "  r4 bucket create example-project-b videos\n"
                "  r4 bucket list\n"
                "  r4 files upload ./clips --bucket <you>.example-project-b.videos --recursive\n"
            ),
        )
        sub = parser.add_subparsers(dest="bucket_command", metavar="<bucket_command>")
        create = sub.add_parser(
            "create", help="Create <you>.<project>.<alias> and its -output pair"
        )
        create.add_argument("project", help="Project / experiment name (letters, digits, '-')")
        create.add_argument("alias", help="Bucket alias, e.g. videos")
        create.add_argument("--json", "-j", action="store_true", help="Emit JSON")
        ls = sub.add_parser("list", help="List your buckets")
        ls.add_argument("--json", "-j", action="store_true", help="Emit JSON")
        access = sub.add_parser(
            "access", help="Show or set who may use a bucket (user/project/group/system)"
        )
        access.add_argument("bucket", help="Bucket name, e.g. <you>.<project>.<alias>")
        access.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        access.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owner may use the bucket",
        )
        return parser

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "bucket_command", None)
        if sub == "create":
            return self._create(args)
        if sub == "list":
            return self._list(args)
        if sub == "access":
            return self._access(args)
        print("Usage: r4 bucket create|list|access (see --help)", file=sys.stderr)
        return 2

    def _create(self, args: Namespace) -> int:
        try:
            info = FilesClient().create_bucket(args.project, args.alias)
        except Exception as exc:  # noqa: BLE001 - surface the gateway message
            print(f"✗ Create failed: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(info, indent=2))
            return 0
        print(f"✓ Bucket ready: {info.get('bucket')}")
        print(f"  results land in: {info.get('output_bucket')}")
        bucket = info.get("bucket")
        print(f"  upload data:     r4 files upload <path> --bucket {bucket} [--recursive]")
        return 0

    def _list(self, args: Namespace) -> int:
        try:
            buckets = FilesClient().list_buckets()
        except Exception as exc:  # noqa: BLE001
            print(f"✗ List failed: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(buckets, indent=2))
            return 0
        if not buckets:
            print("No buckets yet — create one: r4 bucket create <project> <alias>")
            return 0
        for b in buckets:
            print(f"{b.get('bucket'):<50} {b.get('kind', ''):<12} output: {b.get('output_bucket')}")
        return 0

    def _access(self, args: Namespace) -> int:
        """Show or replace a bucket's ADR-080 access list."""
        from ..client import Client

        client = Client()
        try:
            if args.private or args.access_set:
                data = client.set_bucket_access(
                    args.bucket, [] if args.private else args.access_set
                )
            else:
                data = client.get_bucket_access(args.bucket)
        except Exception as exc:  # noqa: BLE001 - surface the gateway message
            print(f"✗ Access failed: {exc}", file=sys.stderr)
            return 1
        scopes = data.get("access") or []
        owner = data.get("owner") or data.get("owner_id") or "?"
        print(f"{args.bucket}  owner: {owner}")
        print("  access: " + (", ".join(scopes) if scopes else "owner only"))
        return 0


def cmd_bucket(args: Namespace) -> int:
    """Programmatic entry point used by aliases/tests."""
    return BucketCommand().handle(args)
