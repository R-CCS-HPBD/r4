"""``r4 evidence`` command handler (V-07: evidence / scientific-memory plane).

Thin wrapper over ``/gw/evidence*`` and ``/gw/scientific-memory*``: the
retraction / negative-results / confidence plane that the server always
mounted but no R4 code called. Surfaces claim → evidence lookups (D-45),
status transitions (retract / supersede / reactivate) and memory search
(failed experiments, retractions) for the loop and the scientist.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class EvidenceCommand(CommandHandler):
    """``r4 evidence`` — evidence + scientific-memory plane."""

    name = "evidence"
    help = "Evidence plane: claims, retractions, negative results (V-07)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Evidence and scientific-memory plane (V-07). Claims carry "
                "evidence maps (D-45); status transitions are append-only "
                "(retract/supersede); scientific memory holds failed "
                "experiments and negative results."
            ),
        )
        sub = p.add_subparsers(dest="evidence_command", metavar="<evidence-command>")
        sub.required = True

        ls = sub.add_parser("list", help="List evidence with filters")
        ls.add_argument("--project", default=None)
        ls.add_argument("--status", default=None)
        ls.add_argument("--source-type", default=None)
        ls.add_argument("--claim", default=None, dest="claim_id")
        ls.add_argument("--limit", type=int, default=50)
        ls.add_argument("--json", "-j", action="store_true", dest="as_json")

        claim = sub.add_parser("claim", help="All evidence for one claim (D-45)")
        claim.add_argument("claim_id")
        claim.add_argument("--json", "-j", action="store_true", dest="as_json")

        st = sub.add_parser("status", help="Retract/supersede one evidence row")
        st.add_argument("evidence_id")
        st.add_argument("status", help="retracted | superseded | active …")
        st.add_argument("--json", "-j", action="store_true", dest="as_json")

        mem = sub.add_parser("memory", help="Scientific-memory entries")
        mem_sub = mem.add_subparsers(dest="memory_command", metavar="<memory-command>")
        mem_sub.required = True

        m_ls = mem_sub.add_parser("list", help="List memory entries")
        m_ls.add_argument("--project", default=None)
        m_ls.add_argument("--status", default=None)
        m_ls.add_argument("--limit", type=int, default=50)
        m_ls.add_argument("--json", "-j", action="store_true", dest="as_json")

        m_search = mem_sub.add_parser("search", help="Search memory entries")
        m_search.add_argument("query")
        m_search.add_argument("--project", default=None)
        m_search.add_argument("--json", "-j", action="store_true", dest="as_json")

        m_st = mem_sub.add_parser("status", help="Update one memory entry's status")
        m_st.add_argument("entry_id")
        m_st.add_argument("status")
        m_st.add_argument("--json", "-j", action="store_true", dest="as_json")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="evidence")
        return cmd_evidence(args)


def cmd_evidence(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    verb = getattr(args, "evidence_command", None) or ""
    handler = _VERBS.get(verb)
    if handler is None:
        print("Usage: r4 evidence <list|claim|status|memory>")
        return 1
    return handler(client, args)


def _emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(human)


def _evidence_rows(items: list) -> str:
    if not items:
        return "No evidence rows."
    return "\n".join(
        f"{e.get('evidence_id') or e.get('id') or '?'}  "
        f"[{e.get('status', '?')}] {e.get('claim_id', '')}  "
        f"{(e.get('content') or e.get('summary') or '')[:80]}"
        for e in items
    )


def _list(client, args) -> int:
    result = client.list_evidence(
        project_id=args.project,
        status=args.status,
        source_type=args.source_type,
        claim_id=args.claim_id,
        limit=args.limit,
    )
    items = result.get("evidence", [])
    _emit(result, args.as_json, _evidence_rows(items))
    return 0


def _claim(client, args) -> int:
    result = client.get_evidence_claim(args.claim_id)
    items = result.get("evidence", [])
    _emit(result, args.as_json, _evidence_rows(items))
    return 0


def _status(client, args) -> int:
    row = client.update_evidence_status(args.evidence_id, args.status)
    _emit(row, args.as_json, f"Evidence {args.evidence_id} -> {args.status}.")
    return 0


def _memory(client, args) -> int:
    sub = getattr(args, "memory_command", None) or ""
    if sub == "list":
        result = client.list_memory_entries(
            project_id=args.project, status=args.status, limit=args.limit
        )
        items = result.get("entries", [])
        _emit(result, args.as_json, _evidence_rows(items))
        return 0
    if sub == "search":
        result = client.search_memory(args.query, project_id=args.project)
        items = result.get("entries", result.get("results", []))
        _emit(result, args.as_json, _evidence_rows(items))
        return 0
    if sub == "status":
        row = client.update_memory_status(args.entry_id, args.status)
        _emit(row, args.as_json, f"Entry {args.entry_id} -> {args.status}.")
        return 0
    print("Usage: r4 evidence memory <list|search|status>")
    return 1


_VERBS = {
    "list": _list,
    "claim": _claim,
    "status": _status,
    "memory": _memory,
}
