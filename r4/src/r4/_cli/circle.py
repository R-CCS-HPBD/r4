"""``r4 circle`` command handler (ADR-086).

Thin wrapper over the gateway's ``/gw/circles*`` routes (or
``/umrs/circles*`` in direct-UMRS mode). Circles are the project-scoped
context + audience cells behind ``circle:<project>/<id>`` grant targets
and access lists: entering a project is its own casual verb (your personal
circle), named circles need affiliation, roster ops need the circle owner,
a circle admin, the project owner, or the platform admin (four-rung
ladder, ADR-086 amendment 2026-10-19).
"""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class CircleCommand(CommandHandler):
    """``r4 circle`` — project-scoped circles + membership authority."""

    name = "circle"
    help = "Circles + membership (project-scoped audiences, ADR-086)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help="Project-scoped circles + membership (ADR-086)",
            description=(
                "Manage circles — the context boundary and smallest sharing "
                "audience inside a project (`circle:<project>/<id>`). "
                "`circle enter <project>` creates your personal cell (casual "
                "project entry); named circles need affiliation, the project "
                "owner, or an admin. Retire tombstones a circle and stops its "
                "grants from resolving immediately."
            ),
        )
        sub = p.add_subparsers(dest="circle_command", metavar="<circle-command>")
        sub.required = True

        enter = sub.add_parser("enter", help="Enter a project (create your personal circle)")
        enter.add_argument("project_id")
        enter.add_argument("--json", "-j", action="store_true", dest="as_json")

        create = sub.add_parser("create", help="Create a named circle (idempotent)")
        create.add_argument("project_id")
        create.add_argument("circle_id")
        create.add_argument("--name", default="")
        create.add_argument("--for-user", default=None, help="admin-only: owner override")
        create.add_argument("--json", "-j", action="store_true", dest="as_json")

        mine = sub.add_parser("mine", help="My circles everywhere (+ derived project list)")
        mine.add_argument("--include-retired", action="store_true")
        mine.add_argument("--json", "-j", action="store_true", dest="as_json")

        list_cmd = sub.add_parser("list", help="Circles of one project")
        list_cmd.add_argument("project_id")
        list_cmd.add_argument("--include-retired", action="store_true")
        list_cmd.add_argument("--json", "-j", action="store_true", dest="as_json")

        show = sub.add_parser("show", help="One circle row")
        show.add_argument("project_id")
        show.add_argument("circle_id")
        show.add_argument("--json", "-j", action="store_true", dest="as_json")

        retire = sub.add_parser("retire", help="Tombstone a circle (owner-or-admin)")
        retire.add_argument("project_id")
        retire.add_argument("circle_id")
        retire.add_argument("--json", "-j", action="store_true", dest="as_json")

        members = sub.add_parser("members", help="List members of a circle")
        members.add_argument("project_id")
        members.add_argument("circle_id")
        members.add_argument("--json", "-j", action="store_true", dest="as_json")

        add = sub.add_parser(
            "member-add",
            help="Add a member (owner / circle admin / project owner / platform admin)",
        )
        add.add_argument("project_id")
        add.add_argument("circle_id")
        add.add_argument("user_id")
        add.add_argument(
            "--role",
            choices=["member", "admin"],
            default="member",
            help="roster role (admin may manage members)",
        )
        add.add_argument("--json", "-j", action="store_true", dest="as_json")

        remove = sub.add_parser(
            "member-remove",
            help="Remove a member (owner / circle admin / project owner / platform admin)",
        )
        remove.add_argument("project_id")
        remove.add_argument("circle_id")
        remove.add_argument("user_id")
        remove.add_argument("--json", "-j", action="store_true", dest="as_json")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="circle")
        return cmd_circle(args)


def _emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(human)


def _print_rows(circles: list[dict]) -> None:
    for row in circles:
        tomb = " TOMBSTONED" if row.get("retired_at") else ""
        name = row.get("name") or "-"
        print(f"{row['project_id']}/{row['circle_id']}  owner={row['owner_id']}  {name}{tomb}")


def _enter(client, args) -> int:
    me = os.environ.get("USER", "").strip()
    if not me:
        print("circle enter: $USER is not set", file=sys.stderr)
        return 2
    result = client.create_circle(args.project_id, me)
    circle = result.get("circle", {})
    _emit(
        result,
        args.as_json,
        f"Entered {args.project_id} as {circle.get('project_id')}/"
        f"{circle.get('circle_id')} — your personal cell.",
    )
    return 0


def _create(client, args) -> int:
    result = client.create_circle(
        args.project_id, args.circle_id, name=args.name, for_user=args.for_user
    )
    circle = result.get("circle", {})
    _emit(
        result,
        args.as_json,
        f"Circle {circle.get('project_id')}/{circle.get('circle_id')}"
        f" (owner {circle.get('owner_id')}) ready.",
    )
    return 0


def _mine(client, args) -> int:
    result = client.list_my_circles(include_retired=args.include_retired)
    circles = result.get("circles", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_rows(circles)
        projects = result.get("projects", [])
        if projects:
            print(f"projects: {', '.join(projects)}")
        if not circles:
            print("No circles yet — `r4 circle enter <project>` makes your cell.")
    return 0


def _list(client, args) -> int:
    result = client.list_circles(args.project_id, include_retired=args.include_retired)
    circles = result.get("circles", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_rows(circles)
        if not circles:
            print(
                f"No circles of yours in {args.project_id} — "
                "`r4 circle enter <project>` joins the project."
            )
    return 0


def _show(client, args) -> int:
    result = client.get_circle(args.project_id, args.circle_id)
    circle = result.get("circle", {})
    _emit(
        result,
        args.as_json,
        f"{circle.get('project_id')}/{circle.get('circle_id')}  "
        f"{circle.get('status')}  owner={circle.get('owner_id')}",
    )
    return 0


def _retire(client, args) -> int:
    result = client.retire_circle(args.project_id, args.circle_id)
    _emit(
        result,
        args.as_json,
        f"Circle {args.project_id}/{args.circle_id} tombstoned; its grants stopped resolving.",
    )
    return 0


def _members(client, args) -> int:
    result = client.list_circle_members(args.project_id, args.circle_id)
    members = result.get("members", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for row in members:
            by = row.get("added_by") or "-"
            print(f"{row['user_id']}  role={row.get('role', 'member')}  by={by}")
        if not members:
            print("No members.")
    return 0


def _member_add(client, args) -> int:
    result = client.add_circle_member(args.project_id, args.circle_id, args.user_id, role=args.role)
    _emit(
        result,
        args.as_json,
        f"User {args.user_id} ({args.role}) added to circle {args.project_id}/{args.circle_id}.",
    )
    return 0


def _member_remove(client, args) -> int:
    result = client.remove_circle_member(args.project_id, args.circle_id, args.user_id)
    _emit(
        result,
        args.as_json,
        f"User {args.user_id} removed from circle {args.project_id}/{args.circle_id}.",
    )
    return 0


def cmd_circle(args) -> int:
    from ..client import Client

    client = Client()
    handlers = {
        "enter": _enter,
        "create": _create,
        "mine": _mine,
        "list": _list,
        "show": _show,
        "retire": _retire,
        "members": _members,
        "member-add": _member_add,
        "member-remove": _member_remove,
    }
    return handlers[args.circle_command](client, args)
