"""``r4 knowledge review`` — the minimal review inbox (knowledge plan K1.5).

Agents and research loops write knowledge as *proposed* (D-35, ADR-084 D-2);
so do the entities and relations a model extracts from your own uploads
(K-1). A person establishes it. This command lists what waits, grouped by
the run that wrote it, and promotes it through the existing UMRS routes (graph
proposals, the human-only establish routes, the read-only proposed-rows
listing). The decision and the promotion are always sent as the person's act;
from a Companion tool's shell they are refused (humans establish).

Review of K1 part A (2026-10-24): the inbox groups rows by run (``--rows``
lists each one), names every row's origin, says when a graph has more rows
than it shows, reaches the caller's personal graphs, finds a row or a whole
run past the newest window, and approve/reject need no current project.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

_REVIEW_HELP = """\
List what waits for review and decide it.

  r4 knowledge review                       what waits, grouped by run
  r4 knowledge review --rows                every proposed row
  r4 knowledge review approve <proposal>    approve an agent's graph proposal
  r4 knowledge review reject <proposal>     decline it
  r4 knowledge review establish <row-id>    promote one proposed node or edge
  r4 knowledge review establish --run <run> promote every proposed row of a run

Rows come from agents, research loops, and the entities a model extracted
from your uploads (origin: agent, loop, extracted).
"""

_PROJECT_HELP = "Project ID (default: current project)"
_ROWS_HELP = "List every row, not one line per run"
_TEXT_WIDTH = 80
_PERSONAL_KINDS = ("personal", "user")
#: Rows a run's establish reads per request (the server's maximum).
_RUN_PAGE = 1000


def _add_common(parser: argparse.ArgumentParser, *, nested: bool) -> None:
    """-p / --graph / --json on ``review`` and on each action.

    On an action parser the defaults are suppressed, so a flag given before
    the action is not reset by the action parser's own default.
    """
    default: Any = argparse.SUPPRESS if nested else None
    parser.add_argument(
        "--project-id", "--project", "-p", dest="project", default=default, help=_PROJECT_HELP
    )
    parser.add_argument(
        "--graph",
        "-g",
        dest="review_graph",
        metavar="GRAPH",
        default=default,
        help="One graph (id or name); default: the project's graphs and your personal ones",
    )
    flag_default: Any = argparse.SUPPRESS if nested else False
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        dest="as_json",
        default=flag_default,
        help="Print the result as JSON",
    )


def register_review(sub: Any) -> None:
    """Add ``review`` to the ``r4 knowledge`` subparsers."""
    review = sub.add_parser(
        "review",
        help="Review proposed knowledge: list, approve, reject, establish",
        description=_REVIEW_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(review, nested=False)
    review.add_argument("--rows", action="store_true", dest="show_rows", help=_ROWS_HELP)
    actions = review.add_subparsers(dest="review_command", metavar="<action>")
    listing = actions.add_parser("list", help="List what waits for review")
    _add_common(listing, nested=True)
    listing.add_argument(
        "--rows", action="store_true", dest="show_rows", default=argparse.SUPPRESS, help=_ROWS_HELP
    )
    for verb, text in (("approve", "Approve"), ("reject", "Decline")):
        p = actions.add_parser(verb, help=f"{text} a pending graph proposal")
        p.add_argument("proposal_id", help="Proposal id (from `r4 knowledge review`)")
        _add_common(p, nested=True)
    est = actions.add_parser("establish", help="Promote proposed rows to established")
    est.add_argument(
        "row_id", nargs="?", help="Node or edge id (from `r4 knowledge review --rows`)"
    )
    est.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="Promote every proposed row of this run (loop, job or artifact id)",
    )
    _add_common(est, nested=True)


# ── reading the inbox ───────────────────────────────────────────────────────


def _unique(graphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for graph in graphs:
        gid = str(graph.get("graph_id") or "")
        if gid and gid not in seen:
            seen.add(gid)
            out.append(graph)
    return out


def _review_graphs(knowledge: Any, project: str, graph: str | None) -> list[dict[str, Any]]:
    """The graphs to review: the named one, else the project's own graphs and
    the personal graphs the caller can read (stored with no project, so the
    project listing never returned them: K-1 rows of a personal upload and
    an agent's personal-graph upload were invisible without ``-g``)."""
    if graph:
        return [knowledge.graph_info(graph)]
    own = [g for g in knowledge.graphs(project_id=project) if g.get("project_id") == project]
    personal = [g for g in knowledge.graphs() if g.get("kind") in _PERSONAL_KINDS]
    return _unique(own + personal)


def _keyed(payload: dict[str, Any], graph_id: str) -> list[dict[str, Any]]:
    """The payload's nodes then edges, each keyed to the graph it was read
    under (a legacy row stores ``graph_id = ''`` and establish needs one)."""
    rows = (payload.get("nodes") or []) + (payload.get("edges") or [])
    return [{**row, "graph_id": graph_id} for row in rows]


def _graph_inbox(
    knowledge: Any, graph_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """One graph's pending proposals, proposed rows, and whether it has more."""
    proposals = [
        {**p, "graph_id": graph_id} for p in knowledge.graph_proposals(graph_id, status="pending")
    ]
    payload = knowledge.graph_proposed(graph_id) or {}
    return proposals, _keyed(payload, graph_id), bool(payload.get("truncated"))


def collect_inbox(knowledge: Any, project: str, graph: str | None = None) -> dict[str, Any]:
    """Pending proposals and proposed rows of the graphs to review, plus the
    graphs whose rows the listing cut at its window (``truncated``)."""
    inbox: dict[str, Any] = {"project": project, "proposals": [], "rows": [], "truncated": []}
    for graph_meta in _review_graphs(knowledge, project, graph):
        graph_id = str(graph_meta["graph_id"])
        proposals, rows, more = _graph_inbox(knowledge, graph_id)
        inbox["proposals"].extend(proposals)
        inbox["rows"].extend(rows)
        if more:
            inbox["truncated"].append(graph_id)
    return inbox


def run_rows(rows: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """One run's proposed rows (ADR-084 D-2): its nodes, then its edges.

    An edge belongs to the run when it names the run itself or leaves one of
    the run's nodes (a loop finding's ``produced_by`` / evidence links).
    """
    nodes = [r for r in rows if r.get("kind") == "node" and r.get("run_id") == run_id]
    ids = {r.get("id") for r in nodes}
    edges = [
        r
        for r in rows
        if r.get("kind") == "edge" and (r.get("run_id") == run_id or r.get("source_id") in ids)
    ]
    return nodes + edges


def _group_for(
    groups: dict[tuple[str, str], dict[str, Any]], row: dict[str, Any]
) -> dict[str, Any]:
    key = (str(row.get("graph_id") or ""), str(row.get("run_id") or ""))
    if key not in groups:
        groups[key] = {"graph_id": key[0], "run_id": key[1], "rows": 0, "origins": [], "sample": ""}
    return groups[key]


def run_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (graph, run): row count, origins, a sample summary."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        group = _group_for(groups, row)
        group["rows"] += 1
        origin = str(row.get("origin") or "")
        if origin and origin not in group["origins"]:
            group["origins"].append(origin)
        if not group["sample"] and row.get("kind") == "node":
            group["sample"] = str(row.get("summary") or "")
    return list(groups.values())


# ── printing ────────────────────────────────────────────────────────────────


def _clip(text: Any, width: int = _TEXT_WIDTH) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _print_proposals(proposals: list[dict[str, Any]]) -> None:
    print(f"Pending proposals ({len(proposals)}):")
    for p in proposals:
        print(
            f"  {p.get('proposal_id')}  graph={p['graph_id']}  "
            f"by={p.get('created_by') or '-'}  {_clip(p.get('reason'))}"
        )


def _print_rows(rows: list[dict[str, Any]]) -> None:
    print(f"Proposed rows ({len(rows)}):")
    for r in rows:
        print(
            f"  {r.get('kind')} {r.get('id')}  graph={r['graph_id']}  "
            f"origin={r.get('origin') or '-'}  run={r.get('run_id') or '-'}  "
            f"{_clip(r.get('summary'))}"
        )


def _print_groups(rows: list[dict[str, Any]]) -> None:
    groups = run_groups(rows)
    print(f"Proposed rows ({len(rows)}) in {len(groups)} run(s):")
    for g in groups:
        origins = ",".join(g["origins"]) or "-"
        print(
            f"  run {g['run_id'] or '-'}  {g['rows']} row(s)  origin={origins}  "
            f"graph={g['graph_id']}  {_clip(g['sample'], 48)}"
        )


def _print_inbox(inbox: dict[str, Any], *, show_rows: bool) -> None:
    proposals, rows = inbox["proposals"], inbox["rows"]
    if not proposals and not rows:
        print(f"Nothing waits for review in project {inbox['project']}.")
        return
    _print_proposals(proposals)
    (_print_rows if show_rows else _print_groups)(rows)
    for graph_id in inbox.get("truncated") or []:
        print(
            f"  Graph {graph_id} has more proposed rows than shown (newest first); "
            "establish --run <run> reaches all of a run."
        )
    print(
        "Next: r4 knowledge review approve|reject <proposal-id>; "
        "r4 knowledge review establish --run <run> | <row-id> (ids: --rows)"
    )


# ── actions ─────────────────────────────────────────────────────────────────


def _list(knowledge: Any, args: argparse.Namespace) -> int:
    inbox = collect_inbox(knowledge, args.project, args.review_graph)
    if args.as_json:
        print(json.dumps(inbox, indent=2, default=str))
    else:
        _print_inbox(inbox, show_rows=bool(getattr(args, "show_rows", False)))
    return 0


def _decide(knowledge: Any, args: argparse.Namespace, *, approved: bool) -> int:
    result = knowledge.graph_decide(args.proposal_id, approved=approved)
    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    status = result.get("status") or ("approved" if approved else "declined")
    print(f"✓ proposal {args.proposal_id}: {status}")
    return 0


def _establish_one(knowledge: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Promote one row; the failure is reported on the row (``error``,
    ``status``: the HTTP status when the platform refused it)."""
    from ..client import denial_status

    try:
        knowledge.establish(row["graph_id"], row["id"], kind=row.get("kind", "node"))
        return {**row, "established": True}
    except Exception as exc:  # noqa: BLE001 — reported per row
        return {**row, "established": False, "error": str(exc), "status": denial_status(exc)}


def _establish_all(knowledge: Any, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote each target; one failure does not stop the rest."""
    return [_establish_one(knowledge, row) for row in targets]


def _run_page(knowledge: Any, graph_id: str, run_id: str) -> tuple[list[dict[str, Any]], bool]:
    payload = knowledge.graph_proposed(graph_id, limit=_RUN_PAGE, run_id=run_id) or {}
    return run_rows(_keyed(payload, graph_id), run_id), bool(payload.get("truncated"))


def _establish_run_in(knowledge: Any, graph_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every proposed row of the run in one graph, page after page; a row
    tried once is not tried again (a failure cannot loop)."""
    outcomes: list[dict[str, Any]] = []
    tried: set[str] = set()
    more = True
    while more:
        rows, more = _run_page(knowledge, graph_id, run_id)
        pending = [r for r in rows if str(r.get("id")) not in tried]
        if not pending:
            break
        tried.update(str(r.get("id")) for r in pending)
        outcomes.extend(_establish_all(knowledge, pending))
    return outcomes


def _establish_by_id(
    knowledge: Any, graphs: list[dict[str, Any]], row_id: str
) -> list[dict[str, Any]]:
    """The row named, promoted through each graph's establish route (node,
    then edge) until one takes it: the inbox window cannot hide it. A 404
    means "not this graph / not this kind" and moves on."""
    for graph in graphs:
        for kind in ("node", "edge"):
            outcome = _establish_one(
                knowledge, {"kind": kind, "id": row_id, "graph_id": str(graph["graph_id"])}
            )
            if outcome["established"] or outcome["status"] != 404:
                return [outcome]
    return []


def _establish_targets(knowledge: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    graphs = _review_graphs(knowledge, args.project, args.review_graph)
    if not args.run_id:
        return _establish_by_id(knowledge, graphs, args.row_id)
    outcomes: list[dict[str, Any]] = []
    for graph in graphs:
        outcomes.extend(_establish_run_in(knowledge, str(graph["graph_id"]), args.run_id))
    return outcomes


def _establish(knowledge: Any, args: argparse.Namespace) -> int:
    if not (args.row_id or args.run_id):
        print("Name a row id or --run <run-id> (see: r4 knowledge review)", file=sys.stderr)
        return 2
    outcomes = _establish_targets(knowledge, args)
    if not outcomes:
        what = f"run {args.run_id}" if args.run_id else args.row_id
        print(f"✗ No proposed row for {what} in project {args.project}.", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps({"established": outcomes}, indent=2, default=str))
    else:
        _print_outcomes(outcomes)
    return 0 if all(row["established"] for row in outcomes) else 1


def _print_outcomes(outcomes: list[dict[str, Any]]) -> None:
    for row in outcomes:
        where = f"{row.get('kind')} {row['id']} (graph {row['graph_id']})"
        if row["established"]:
            print(f"✓ established {where}")
        else:
            print(f"✗ failed {where}: {row['error']}")


#: Actions that read or write a project's graphs (approve/reject are keyed by
#: the proposal id alone and need no project).
_PROJECT_ACTIONS = frozenset({"list", "establish"})


def _refused_in_companion_shell(action: str) -> bool:
    """Humans establish: a Companion tool's shell may list, never decide."""
    from ..knowledge import companion_shell

    if action == "list" or not companion_shell():
        return False
    print(
        f"✗ knowledge review {action} is the person's decision: run it in your own "
        "terminal, not through the assistant's shell.",
        file=sys.stderr,
    )
    return True


def handle_review(args: argparse.Namespace) -> int:
    """Dispatch ``r4 knowledge review [action]``."""
    from ..client import Client
    from .knowledge import _resolve_project_arg

    action = getattr(args, "review_command", None) or "list"
    if _refused_in_companion_shell(action):
        return 2
    if action in _PROJECT_ACTIONS and not _resolve_project_arg(args):
        return 1
    knowledge = Client().knowledge
    handlers = {
        "list": lambda: _list(knowledge, args),
        "approve": lambda: _decide(knowledge, args, approved=True),
        "reject": lambda: _decide(knowledge, args, approved=False),
        "establish": lambda: _establish(knowledge, args),
    }
    try:
        return handlers[action]()
    except Exception as exc:  # noqa: BLE001 — one line for the person
        print(f"✗ knowledge review {action} failed: {exc}", file=sys.stderr)
        return 1
