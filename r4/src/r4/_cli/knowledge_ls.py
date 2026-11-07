"""``r4 knowledge ls`` — what the project knows, and who added it (K1.3).

One listing truth (knowledge plan 2026-10-24, K1.3): this reads the same
server list as the TUI Knowledge pane, ``r4 knowledge docs list`` and
``r4 knowledge stats`` (``GET /k2/artifacts``: the UMRS spine through the
graph-readable predicate). Each row names who added the item, when, its
kind, size, whether it is indexed for retrieval yet, and the graphs it sits
in. Another member's personal-graph item is never listed (the server's
rule, not a client filter).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

_LS_HELP = """\
List the project's knowledge: newest first, with who added each item.

  r4 knowledge ls                      everything you can read in the project
  r4 knowledge ls --mine               what you added
  r4 knowledge ls --by mika            what one member added
  r4 knowledge ls --since 7d           added in the last 7 days (or an ISO date)
  r4 knowledge ls --graph methods      added to one graph (name or id)
  r4 knowledge ls --kind code --json   one kind, as JSON

WHO marks how each person added it: "mika" (their upload or their own
text), "mika (kept)" (an assistant answer they kept: proposed until a
teammate establishes it in r4 knowledge review), "mika (vouched)" (an
answer they vouched for) or "mika (agent)" (written by their agent:
proposed).

INDEXED says whether retrieval can find the item yet: yes, pending (the
embed job has not finished) or stale (re-index due).
"""

#: How a contribution's origin shows beside its contributor (K-1 tiers).
_ORIGIN_MARK = {
    "agent": " (agent)",
    "agent_authored_user_kept": " (kept)",
    "agent_authored_user_vouched": " (vouched)",
}
_TITLE_MAX = 48

_INDEXED = {"current": "yes", "pending": "pending", "stale": "stale"}
_RELATIVE = re.compile(r"^(\d+)([hdw])$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def register_ls(sub: Any) -> None:
    """Add ``ls`` to the ``r4 knowledge`` subparsers."""
    p = sub.add_parser(
        "ls",
        help="List the project's knowledge with who added it (newest first)",
        description=_LS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--project", "--project-id", "-p", help="Project ID (default: current project)")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--by", dest="created_by", metavar="USER", help="Only items this user added")
    who.add_argument("--mine", action="store_true", help="Only items you added")
    p.add_argument("--graph", "-g", dest="graph", metavar="GRAPH", help="Only items in this graph")
    p.add_argument("--kind", "-k", help="Only this kind (document, code, table, …)")
    p.add_argument("--status", default=None, help="Artifact status (default: active)")
    p.add_argument("--since", help="Added since: an ISO date/time, or 24h / 7d / 2w")
    p.add_argument("--limit", "-n", type=int, default=25, help="Rows per page (max 100)")
    p.add_argument("--page", type=int, default=1, help="Page number (default 1)")
    p.add_argument("--json", "-j", action="store_true", dest="as_json", help="Print JSON")


class UsageError(ValueError):
    """A bad argument: exit 2 with the remedy (not a server failure)."""


def parse_since(raw: str | None, *, now: datetime | None = None) -> str | None:
    """``7d`` / ``24h`` / ``2w`` → an ISO 8601 UTC time; an ISO value as is."""
    if not raw:
        return None
    text = raw.strip()
    match = _RELATIVE.match(text)
    if match is None:
        try:
            datetime.fromisoformat(text)
        except ValueError:
            raise UsageError(
                f"--since takes 24h / 7d / 2w or an ISO date (2026-10-01), not {raw!r}"
            ) from None
        return text
    count, unit = int(match.group(1)), _UNITS[match.group(2)]
    moment = (now or datetime.now(timezone.utc)) - timedelta(**{unit: count})
    return moment.isoformat()


def default_graph_id(project_id: str) -> str:
    """UMRS's deterministic default-graph id (``graph_registry.default_graph_id``)."""
    return "g-" + hashlib.sha256(f"project:{project_id}".encode()).hexdigest()[:16]


def _in_project(graph: dict[str, Any], project: str) -> bool:
    """This project's graphs and the caller's personal ones (no project)."""
    return str(graph.get("project_id") or "") in ("", project)


def _graph_names(knowledge: Any, project: str) -> dict[str, str]:
    """graph id → name for the project's graphs the caller can read (and
    their personal ones); best-effort (an unreadable registry leaves ids as
    they are). Never materializes a graph."""
    names = {default_graph_id(project): "default"}
    for graph in _readable_graphs(knowledge, project):
        gid = str(graph.get("graph_id") or "")
        if gid and gid not in names:
            names[gid] = str(graph.get("name") or gid)
    return names


def _readable_graphs(knowledge: Any, project: str) -> list[dict[str, Any]]:
    """The registry's graphs in this project (and the caller's personal
    ones); [] when the registry cannot be read."""
    try:
        return [g for g in knowledge.graphs() or [] if _in_project(g, project)]
    except Exception:  # noqa: BLE001 — names are a convenience
        return []


def _resolve_graph(knowledge: Any, graph: str | None, project: str) -> str | None:
    """A graph name or id → its id (the server filters by id): first among
    this project's graphs (a name another project also uses resolved to
    that one and filtered to nothing), then the registry's own lookup."""
    if not graph:
        return None
    for gid, name in _graph_names(knowledge, project).items():
        if graph in (gid, name):
            return gid
    try:
        return str(knowledge.graph_info(graph).get("graph_id") or graph)
    except Exception:  # noqa: BLE001 — an unknown name filters to nothing
        return graph


def _created_by(args: argparse.Namespace, client: Any) -> str | None:
    if not args.mine:
        return str(args.created_by) if args.created_by else None
    user = getattr(client._config, "umrs_username", None)
    if not user:
        raise UsageError("--mine needs your user id: set UMRS_USERNAME or use --by <user>")
    return str(user)


def _size(n: Any) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _distinct(contributions: list[dict[str, Any]], key: str, names: dict[str, str]) -> str:
    """The contributions' distinct *key* values, renamed through *names*."""
    values = {str(c.get(key) or "?") for c in contributions}
    return ",".join(sorted(names.get(v, v) for v in values)) or "?"


def _who(contributions: list[dict[str, Any]]) -> str:
    """Each contributor with how they added it (``mika (kept)``)."""
    marks = {
        f"{c.get('user_id') or '?'}{_ORIGIN_MARK.get(str(c.get('origin_actor') or ''), '')}"
        for c in contributions
    }
    return ",".join(sorted(marks)) or "?"


def _title(artifact: dict[str, Any], contributions: list[dict[str, Any]]) -> str:
    """The first contribution's title, else the artifact's own name; cut to
    fit a terminal row."""
    title = next((str(c["title"]) for c in contributions if c.get("title")), "")
    if not title:
        meta = artifact.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        title = str(meta.get("source_name") or meta.get("filename") or "")
    title = " ".join(title.split())
    return title if len(title) <= _TITLE_MAX else title[: _TITLE_MAX - 1] + "…"


def _row(artifact: dict[str, Any], names: dict[str, str]) -> list[str]:
    """ADDED · WHO · KIND · SIZE · INDEXED · GRAPH · ID · TITLE."""
    contributions = list(artifact.get("contributions") or [])
    added = str(artifact.get("last_added") or artifact.get("created_at") or "")[:16]
    return [
        added.replace("T", " "),
        _who(contributions),
        str(artifact.get("kind") or ""),
        _size(artifact.get("size_bytes")),
        _INDEXED.get(str(artifact.get("embedding_status") or ""), "?"),
        _distinct(contributions, "graph_id", names),
        str(artifact.get("artifact_id") or ""),
        _title(artifact, contributions),
    ]


_HEADER = ["ADDED", "WHO", "KIND", "SIZE", "INDEXED", "GRAPH", "ID", "TITLE"]


def _print_table(rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in [_HEADER, *rows]) for i in range(len(_HEADER))]
    for row in [_HEADER, *rows]:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


def _fetch(args: argparse.Namespace, client: Any, project: str) -> dict[str, Any]:
    knowledge = client.knowledge
    payload: dict[str, Any] = knowledge.list_artifacts(
        project,
        created_by=_created_by(args, client),
        graph_id=_resolve_graph(knowledge, args.graph, project),
        kind=args.kind,
        status=args.status,
        since=parse_since(args.since),
        page=max(1, args.page),
        page_size=max(1, min(100, args.limit)),
    )
    return payload


def _print_listing(payload: dict[str, Any], names: dict[str, str], project: str) -> None:
    artifacts = payload.get("artifacts") or []
    if not artifacts:
        print(f"No knowledge in project {project} matches (r4 knowledge upload <file> adds some).")
        return
    _print_table([_row(a, names) for a in artifacts])
    total = int(payload.get("total") or len(artifacts))
    if total > len(artifacts):
        page = payload.get("page", 1)
        print(f"\n{len(artifacts)} of {total} shown (page {page}; --page N for more)")


def _failed(args: argparse.Namespace, exc: Exception) -> int:
    """Exit 2 for a usage error, 1 for a failure; JSON on stdout under
    ``--json`` (a script reads one shape), else one line on stderr."""
    code = 2 if isinstance(exc, UsageError) else 1
    if args.as_json:
        print(json.dumps({"error": str(exc), "exit_code": code}))
    elif code == 2:
        print(f"✗ {exc}", file=sys.stderr)
    else:
        print(f"✗ knowledge ls failed: {exc}", file=sys.stderr)
    return code


def handle_ls(args: argparse.Namespace) -> int:
    """``r4 knowledge ls``."""
    from ..client import Client
    from ._helpers import _current_project_id

    project = args.project or _current_project_id()
    if not project:
        print(
            "No project specified. Pass -p <project-id> or run: r4 project switch <id>",
            file=sys.stderr,
        )
        return 2
    client = Client()
    try:
        payload = _fetch(args, client, project)
    except Exception as exc:  # noqa: BLE001 — one line, the server's reason
        return _failed(args, exc)
    if args.as_json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    _print_listing(payload, _graph_names(client.knowledge, project), project)
    return 0


__all__ = ["UsageError", "default_graph_id", "handle_ls", "parse_since", "register_ls"]
