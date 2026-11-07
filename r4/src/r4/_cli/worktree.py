"""``r4 worktree`` — the work-level git worktrees (ADR-108 D-2).

``list`` shows every registered worktree of the current repository with its
parent, owner, branch and whether it has changes. ``create`` makes one, based
on the current worktree's state when run inside one. ``remove`` removes one
(``--discard`` when it has changes). ``clean`` removes every unchanged,
childless worktree. In the console, ``/worktree`` enters and leaves them.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter, _SubParsersAction
from typing import Any

from .base import CommandHandler
from .registry import command


@command
class WorktreeCommand(CommandHandler):
    """``r4 worktree list|create|remove|clean``."""

    name = "worktree"
    help = "Isolated git worktrees for sessions, teams and loops (ADR-108)"

    def register(self, subparsers: _SubParsersAction[ArgumentParser]) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Each worktree lives at .r4/worktrees/<name> on branch r4/<name>.\n"
                "Made inside a worktree, it starts from that worktree's state\n"
                "(uncommitted work included); otherwise from HEAD, or the\n"
                "default branch with --base default. Your checkout is never touched."
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        parser.add_argument("--json", "-j", action="store_true", dest="as_json")
        sub = parser.add_subparsers(dest="worktree_action", metavar="action")
        sub.add_parser("list", help="List worktrees, their parents and changes")
        make = sub.add_parser("create", help="Create a worktree")
        make.add_argument("name")
        make.add_argument("--base", choices=["head", "default"], default="head")
        drop = sub.add_parser("remove", help="Remove a worktree and its branch")
        drop.add_argument("name")
        drop.add_argument("--discard", action="store_true", help="Also when it has changes")
        sub.add_parser("clean", help="Remove every unchanged worktree without children")
        return parser

    def handle(self, args: Namespace) -> int:
        from r4.worktrees import WorkspaceError

        action = getattr(args, "worktree_action", None) or "list"
        handler = _HANDLERS.get(action)
        if handler is None:
            print("Usage: r4 worktree list|create|remove|clean", file=sys.stderr)
            return 2
        try:
            result = handler(args)
        except WorkspaceError as exc:
            print(f"✗ worktree: {exc.detail} ({exc.code})", file=sys.stderr)
            return 1
        _print(result, as_json=bool(getattr(args, "as_json", False)))
        return 0


def _list(_args: Namespace) -> dict[str, Any]:
    from r4.worktrees import is_changed, list_worktrees

    rows = [{**w.record(), "changed": is_changed(w)} for w in list_worktrees(os.getcwd())]
    return {"worktrees": rows}


def _create(args: Namespace) -> dict[str, Any]:
    from r4.worktrees import create

    wt, warning = create(os.getcwd(), args.name, owner="cli", base=args.base)
    return {"created": wt.record(), "warning": warning}


def _remove(args: Namespace) -> dict[str, Any]:
    from r4.worktrees import remove

    return {"removed": remove(os.getcwd(), args.name, discard=args.discard).id}


def _clean(_args: Namespace) -> dict[str, Any]:
    from r4.worktrees import clean, current

    here = current(os.getcwd())
    keep = frozenset({here.id}) if here else frozenset()
    return {"cleaned": clean(os.getcwd(), keep=keep)}


_HANDLERS = {"list": _list, "create": _create, "remove": _remove, "clean": _clean}


def _print(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    for line in _lines(result):
        print(line)


def _lines(result: dict[str, Any]) -> list[str]:
    if "worktrees" in result:
        rows = result["worktrees"]
        if not rows:
            return ["no worktrees (r4 worktree create <name>, or /worktree <name> in the console)"]
        return [_row_line(r) for r in rows]
    if "created" in result:
        wt = result["created"]
        lines = [f"✓ worktree {wt['id']} at {wt['path']} (branch {wt['branch']})"]
        return lines + ([f"  warning: {result['warning']}"] if result.get("warning") else [])
    if "removed" in result:
        return [f"✓ removed worktree {result['removed']}"]
    cleaned = result.get("cleaned") or []
    return [
        f"✓ removed {len(cleaned)} unchanged worktree(s)"
        + (f": {', '.join(cleaned)}" if cleaned else "")
    ]


def _row_line(row: dict[str, Any]) -> str:
    parent = f" ← {row['parent']}" if row.get("parent") else ""
    state = "changed" if row.get("changed") else "clean"
    return f"  {row['id']}{parent}  {row['branch']}  {state}  {row['path']}"
