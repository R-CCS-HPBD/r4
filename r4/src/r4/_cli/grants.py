"""``r4 grant`` command handler (ADR-041, WP-06).

Version-pinned grants with tombstone-on-revoke — sharing by grant, not by
copy/re-scope. Thin wrapper over the gateway's ``/gw/grants*`` routes (or
``/umrs/grants*`` in direct-UMRS mode).
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class GrantCommand(CommandHandler):
    """``r4 grant`` — share assets by grant, not by copy."""

    name = "grant"
    help = "Asset grants (share-by-grant, tombstone-on-revoke)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help="Share assets via version-pinned grants (ADR-041)",
            description=(
                "Create/list/revoke asset grants. A grant authorizes READS only; "
                "revoke tombstones the row (the audit trail survives)."
            ),
        )
        sub = p.add_subparsers(dest="grant_command", metavar="<grant-command>")
        sub.required = True

        create = sub.add_parser("create", help="Grant read access to one asset")
        create.add_argument("asset_kind", choices=["model", "knowledge_graph"])
        create.add_argument("asset_id")
        create.add_argument("--to-user", dest="grantee_user", default="")
        create.add_argument("--to-project", dest="grantee_project", default="")
        create.add_argument(
            "--to-circle",
            dest="grantee_circle",
            default="",
            metavar="PROJECT/CIRCLE",
            help="grant to a circle's members (ADR-086)",
        )
        create.add_argument("--version-pin", default=None)
        create.add_argument("--expires-at", default=None, help="ISO-8601 expiry")
        create.add_argument("--json", "-j", action="store_true", dest="as_json")

        list_cmd = sub.add_parser("list", help="Grants on one asset (writers only)")
        list_cmd.add_argument("asset_kind", choices=["model", "knowledge_graph"])
        list_cmd.add_argument("asset_id")
        list_cmd.add_argument("--json", "-j", action="store_true", dest="as_json")

        mine = sub.add_parser("mine", help="Grants I received (user + project)")
        mine.add_argument("--json", "-j", action="store_true", dest="as_json")

        revoke = sub.add_parser("revoke", help="Tombstone a grant")
        revoke.add_argument("grant_id")
        revoke.add_argument("--json", "-j", action="store_true", dest="as_json")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="grant")
        return cmd_grant(args)


def _emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(human)


def _grantee(args) -> tuple[str, str]:
    chosen = [
        bool(args.grantee_user),
        bool(args.grantee_project),
        bool(getattr(args, "grantee_circle", "")),
    ]
    if sum(chosen) != 1:
        print(
            "Error: exactly one of --to-user, --to-project, or --to-circle is required.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.grantee_user:
        return "user", args.grantee_user
    if args.grantee_project:
        return "project", args.grantee_project
    return "circle", args.grantee_circle


def _create(client, args) -> int:
    grantee_kind, grantee_id = _grantee(args)
    result = client.create_grant(
        args.asset_kind,
        args.asset_id,
        grantee_kind,
        grantee_id,
        version_pin=args.version_pin,
        expires_at=args.expires_at,
    )
    grant = result.get("grant", {})
    _emit(
        result,
        args.as_json,
        f"Granted {grant.get('asset_kind')}:{grant.get('asset_id')} "
        f"→ {grant.get('grantee_kind')}:{grant.get('grantee_id')} "
        f"({grant.get('grant_id')})",
    )
    return 0


def _list(client, args) -> int:
    result = client.list_grants(args.asset_kind, args.asset_id)
    grants = result.get("grants", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for grant in grants:
            tomb = " TOMBSTONED" if grant.get("tombstoned_at") else ""
            print(
                f"{grant['grant_id']}  {grant['grantee_kind']}:{grant['grantee_id']}"
                f"  pin={grant.get('version_pin') or '-'}{tomb}"
            )
        if not grants:
            print("No grants on this asset.")
    return 0


def _mine(client, args) -> int:
    result = client.list_my_grants()
    grants = result.get("grants", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for grant in grants:
            print(
                f"{grant['grant_id']}  {grant['asset_kind']}:{grant['asset_id']}"
                f"  from {grant.get('created_by') or '-'}"
            )
        if not grants:
            print("No assets have been granted to you.")
    return 0


def cmd_grant(args) -> int:
    from ..client import Client

    client = Client()
    if args.grant_command == "create":
        return _create(client, args)
    if args.grant_command == "list":
        return _list(client, args)
    if args.grant_command == "mine":
        return _mine(client, args)
    result = client.revoke_grant(args.grant_id)
    _emit(
        result,
        args.as_json,
        f"Grant {args.grant_id} tombstoned."
        if result.get("revoked")
        else f"Grant {args.grant_id} was already tombstoned.",
    )
    return 0
