"""``r4 knowledge ontology`` and ``r4 knowledge graph-bind`` (ADR-116 D-5).

An ontology names the classes and relations that extraction may use for a
graph. People register one from a LinkML YAML file (or the plain ontology
JSON), list and show what is registered, retire a version, and bind a graph
to one. Registration and retirement are human acts: the server refuses an
agent caller (``x-r4-actor: agent``).

Coordinates are written ``ID@VERSION``. Without ``@VERSION``, ``show`` reads
the newest live version and ``graph-bind`` / ``graph-create --ontology`` let
the server pin the newest live version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

_ONTOLOGY_HELP = """\
Register, list, show and retire ontologies (LinkML YAML or JSON).

  r4 knowledge ontology register FILE [--format linkml|json] [--global]
  r4 knowledge ontology list [--all] [--json]
  r4 knowledge ontology show ID[@VERSION] [--json]
  r4 knowledge ontology retire ID@VERSION [--yes]

Bind a graph to one:

  r4 knowledge graph-create NAME --ontology ID[@VERSION]
  r4 knowledge graph-bind GRAPH ID[@VERSION]

Without @VERSION, the newest live version is used.
"""

_PROJECT_HELP = "Project ID sent as X-Project-ID (default: current project)"
_EXT_FORMATS = {".yaml": "linkml", ".yml": "linkml", ".json": "json"}
_LIST_HEADER = ["ID", "VERSION", "SCOPE", "STATUS", "CLASSES", "RELATIONS", "DESCRIPTION"]
_DESCRIPTION_MAX = 48

#: What each server error code means for the person at the terminal.
_CODE_MESSAGES = {
    "ontology.invalid": (
        "The ontology was rejected as invalid{detail}. Check the file against "
        "the LinkML subset in the user manual (section 27, Ontologies)."
    ),
    "ontology.conflict": (
        "This ontology id and version are already registered in this project "
        "(or, with --global, globally){detail}. "
        "Raise `version` in the file, or retire the old version first."
    ),
    "ontology.forbidden": (
        "Not allowed{detail}. Ontologies are registered and retired by people, "
        "not agents. A project ontology needs write access to the project; "
        "a global one (--global) needs an admin."
    ),
    "ontology.not_found": (
        "No live ontology with that id and version{detail}. "
        "See what is registered: r4 knowledge ontology list --all"
    ),
}

_NO_ROUTE = (
    "This server has no ontology routes (UMRS before ADR-116?){detail}. "
    "Ask the operator to update the platform."
)


class OntologyUsageError(ValueError):
    """A bad argument, said before any request goes out (exit 2)."""


# ── parsing ─────────────────────────────────────────────────────────────────


def parse_coordinate(text: str, *, need_version: bool = False) -> tuple[str, Optional[str]]:
    """``ID[@VERSION]`` -> ``(id, version or None)``.

    Neither part may contain ``@``; an empty id, or an empty version after
    ``@``, is refused.
    """
    raw = (text or "").strip()
    ontology_id, sep, version = raw.partition("@")
    if not ontology_id or (sep and not version) or "@" in version:
        raise OntologyUsageError(f"expected ID[@VERSION], got {text!r}")
    if need_version and not version:
        raise OntologyUsageError(f"a version is required: {ontology_id}@VERSION")
    return ontology_id, version or None


def infer_format(path: str, explicit: Optional[str]) -> str:
    """``--format`` when given, else by extension (.yaml/.yml linkml, .json json)."""
    if explicit:
        return explicit
    fmt = _EXT_FORMATS.get(Path(path).suffix.lower())
    if fmt is None:
        raise OntologyUsageError(
            f"cannot tell the format of {path!r} from its extension; pass --format linkml|json"
        )
    return fmt


# ── server errors ───────────────────────────────────────────────────────────


def _body_json(exc: Exception) -> dict[str, Any]:
    details = getattr(exc, "details", None) or {}
    body = details.get("response_body") or getattr(exc, "response_body", None)
    try:
        data = json.loads(body) if isinstance(body, str) else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _code_in(obj: Any) -> Optional[str]:
    """``error`` or ``code`` of a dict, when it is a string."""
    if not isinstance(obj, dict):
        return None
    for key in ("error", "code"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def error_code(exc: Exception, *, ontology_route: bool = True) -> Optional[str]:
    """The server's error code: the ``x-platform-error-code`` header, else
    the body's ``detail.error`` (the knowledge routes' envelope), ``error``
    or ``code``.

    On an ontology route a 403 without a readable code reads as
    ``ontology.forbidden``: the client turns every 403 into an
    AuthenticationError, which keeps the message but not the body. On a
    graph route (graph-bind, graph-create) a 403 is the graph's own scope
    check, so it is left alone.
    """
    details = getattr(exc, "details", None) or {}
    data = _body_json(exc)
    found = details.get("code") or _code_in(data.get("detail")) or _code_in(data)
    found = found or _code_in(data.get("error"))
    if not found and ontology_route and details.get("status_code") == 403:
        return "ontology.forbidden"
    return found if isinstance(found, str) else None


def _server_text(exc: Exception) -> str:
    """The server's message, without the client's ``API error: 400:`` prefix."""
    text = str(getattr(exc, "message", "") or exc)
    text = re.sub(r"^(API error: \d+|Access denied \(403\))(: )?", "", text).strip()
    return f" ({text})" if text else ""


def ontology_error_message(exc: Exception, *, ontology_route: bool = True) -> str:
    """One clear line for an ontology failure; other errors pass through.

    ``ontology_route=False`` for the graph routes: there a 403 or a bare 404
    is about the graph, not the ontology.
    """
    code = error_code(exc, ontology_route=ontology_route)
    template = _CODE_MESSAGES.get(code or "")
    if template is not None:
        return template.format(detail=_server_text(exc))
    status = (getattr(exc, "details", None) or {}).get("status_code")
    if ontology_route and status == 404 and code is None:
        return _NO_ROUTE.format(detail=_server_text(exc))
    return str(exc)


# ── registration of the subcommands ─────────────────────────────────────────


def _add_project_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-id", "--project", "-p", dest="project", default=None, help=_PROJECT_HELP
    )
    parser.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Print the result as JSON"
    )


def register_ontology(sub: Any) -> None:
    """Add ``ontology`` to the ``r4 knowledge`` subparsers."""
    parser = sub.add_parser(
        "ontology",
        help="Register, list, show and retire ontologies (LinkML)",
        description=_ONTOLOGY_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="ontology_command", metavar="<action>")
    actions.required = True
    reg = actions.add_parser("register", help="Register an ontology from a file")
    reg.add_argument("file", help="LinkML YAML (.yaml/.yml) or ontology JSON (.json)")
    reg.add_argument(
        "--format",
        "-f",
        dest="fmt",
        choices=("linkml", "json"),
        default=None,
        help="Source format (default: from the file extension)",
    )
    reg.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Register for every project (admins only); default: the current project",
    )
    _add_project_json(reg)
    listing = actions.add_parser("list", help="List registered ontologies")
    listing.add_argument("--all", action="store_true", help="Include retired versions")
    _add_project_json(listing)
    show = actions.add_parser("show", help="Show one ontology's classes and relations")
    show.add_argument("coordinate", metavar="ID[@VERSION]", help="Default: newest live version")
    _add_project_json(show)
    retire = actions.add_parser("retire", help="Retire one ontology version")
    retire.add_argument("coordinate", metavar="ID@VERSION", help="The version is required")
    retire.add_argument("--yes", "-y", action="store_true", help="Do not ask to confirm")
    _add_project_json(retire)


def register_graph_bind(sub: Any) -> None:
    """Add ``graph-bind`` to the ``r4 knowledge`` subparsers."""
    parser = sub.add_parser(
        "graph-bind",
        help="Bind a knowledge graph to an ontology (ID[@VERSION])",
        description=(
            "Bind a graph to a registered ontology. Extraction into the graph then "
            "uses the ontology's classes and relations. Without @VERSION the server "
            "pins the newest live version."
        ),
    )
    parser.add_argument("graph_id", help="Graph id or name")
    parser.add_argument("coordinate", metavar="ID[@VERSION]", help="Ontology to bind")
    parser.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Print the result as JSON"
    )


# ── output ──────────────────────────────────────────────────────────────────


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _scope(row: dict[str, Any]) -> str:
    if row.get("builtin"):
        return "builtin"
    return str(row.get("project_id") or "global")


def _cut(text: Any, limit: int = _DESCRIPTION_MAX) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _list_row(row: dict[str, Any]) -> list[str]:
    return [
        str(row.get("ontology_id") or ""),
        str(row.get("version") or ""),
        _scope(row),
        str(row.get("status") or ("active" if row.get("builtin") else "")),
        str(row.get("n_classes", "")),
        str(row.get("n_relations", "")),
        _cut(row.get("description")),
    ]


def _print_table(header: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


def _as_map(value: Any) -> dict[str, Any]:
    """A ``{key: value}`` view of a dict or of a list of ``[key, value]`` pairs."""
    if isinstance(value, dict):
        return value
    pairs = [p for p in (value or []) if isinstance(p, (list, tuple)) and len(p) == 2]
    return {str(k): v for k, v in pairs}


def _signature(value: Any) -> str:
    """``domain -> range`` from ``[d, r]`` or ``{"domain", "range"}``."""
    if isinstance(value, dict):
        value = [value.get("domain"), value.get("range")]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0] or '*'} -> {value[1] or '*'}"
    return ""


def _signature_entry(item: Any) -> Optional[tuple[str, str]]:
    """``(relation, "domain -> range")`` from ``{"relation", "domain", "range"}``
    (what the server sends) or ``[rel, d, r]``; None for anything else."""
    if isinstance(item, dict) and item.get("relation"):
        return str(item["relation"]), _signature(item)
    if isinstance(item, (list, tuple)) and len(item) == 3:
        return str(item[0]), _signature(item[1:])
    return None


def _signatures(value: Any) -> dict[str, str]:
    """Relation -> ``domain -> range`` (several joined by ``; ``) from a dict,
    a list of ``{"relation", "domain", "range"}`` or ``[[rel, d, r], ...]``."""
    if isinstance(value, dict):
        return {str(k): _signature(v) for k, v in value.items()}
    grouped: dict[str, list[str]] = {}
    for entry in filter(None, map(_signature_entry, value or [])):
        grouped.setdefault(entry[0], []).append(entry[1])
    return {rel: "; ".join(dict.fromkeys(sigs)) for rel, sigs in grouped.items()}


def _alias_text(aliases: dict[str, Any], term: str) -> str:
    names = aliases.get(term) or []
    return f"  aliases: {', '.join(str(a) for a in names)}" if names else ""


def _print_classes(ontology: dict[str, Any]) -> None:
    buckets = _as_map(ontology.get("class_buckets"))
    parents = _as_map(ontology.get("subtype_of"))
    descriptions = _as_map(ontology.get("descriptions"))
    aliases = _as_map(ontology.get("aliases"))
    abstract = set(ontology.get("abstract_types") or [])
    classes = sorted(ontology.get("entity_types") or [])
    print(f"Classes ({len(classes)}):")
    for name in classes:
        parent = f" is_a {parents[name]}" if parents.get(name) else ""
        flag = " (abstract)" if name in abstract else ""
        bucket = f" [{buckets[name]}]" if buckets.get(name) else ""
        print(f"  {name}{parent}{bucket}{flag}")
        if descriptions.get(name):
            print(f"      {_cut(descriptions[name], 100)}")
        if _alias_text(aliases, name):
            print(f"    {_alias_text(aliases, name)}")


def _print_relations(ontology: dict[str, Any]) -> None:
    signatures = _signatures(ontology.get("relation_signatures"))
    transitive = set(ontology.get("transitive_relations") or [])
    aliases = _as_map(ontology.get("aliases"))
    relations = sorted(ontology.get("relation_types") or [])
    print(f"Relations ({len(relations)}):")
    for name in relations:
        sig = f": {signatures[name]}" if signatures.get(name) else ""
        flag = " (transitive)" if name in transitive else ""
        print(f"  {name}{sig}{flag}{_alias_text(aliases, name)}")


def _print_show(payload: dict[str, Any]) -> None:
    ontology = payload.get("ontology") or {}
    record = payload.get("record") or {}
    print(f"{ontology.get('ontology_id', '?')}@{ontology.get('version', '?')}")
    fields = (
        ("description", ontology.get("description")),
        ("scope", _scope(record) if record else "builtin"),
        ("status", record.get("status")),
        ("format", payload.get("source_format") or ontology.get("source_format")),
        ("source sha256", record.get("source_sha256")),
    )
    for label, value in fields:
        if value:
            print(f"  {label:<14}: {value}")
    _print_classes(ontology)
    _print_relations(ontology)


# ── handlers ────────────────────────────────────────────────────────────────


def _knowledge() -> Any:
    from ..client import Client

    return Client().knowledge


def _confirm(prompt: str) -> bool:
    """Interactive yes/no; no terminal answer (EOF) is a no."""
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _registered_line(payload: dict[str, Any]) -> str:
    ontology = payload.get("ontology") or {}
    record = payload.get("record") or {}
    n_classes = len(ontology.get("entity_types") or [])
    n_relations = len(ontology.get("relation_types") or [])
    return (
        f"✓ Registered ontology {ontology.get('ontology_id', '?')}@"
        f"{ontology.get('version', '?')}: {n_classes} classes, {n_relations} relations "
        f"(scope: {_scope(record) if record else '?'})"
    )


def _do_register(args: argparse.Namespace) -> int:
    fmt = infer_format(args.file, args.fmt)
    try:
        source = Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OntologyUsageError(f"cannot read {args.file}: {exc}") from exc
    payload = _knowledge().ontology_register(
        source,
        fmt=fmt,
        scope="global" if args.global_scope else "project",
        project_id=args.project,
    )
    if args.as_json:
        _print_json(payload)
    else:
        print(_registered_line(payload))
    return 0


def _do_list(args: argparse.Namespace) -> int:
    rows = _knowledge().ontology_list(include_retired=args.all, project_id=args.project)
    if args.as_json:
        _print_json({"ontologies": rows})
    elif not rows:
        print("No ontologies registered (r4 knowledge ontology register FILE adds one).")
    else:
        _print_table(_LIST_HEADER, [_list_row(r) for r in rows])
    return 0


def _version_key(version: str) -> tuple[tuple[int, Any], ...]:
    """The server's key (umrs ``ontology_registry._version_key``): numbers
    compare as numbers (``10`` after ``9``, ``1.10`` after ``1.9``), and a
    word part ranks above a number part (``1.0-rc`` after ``1.0.1``)."""
    parts = re.split(r"[.+_-]", version)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def newest_live_version(rows: list[dict[str, Any]], ontology_id: str) -> Optional[str]:
    """The newest live version of *ontology_id* in a listing, or None.

    The same rule the server pins by (``latest_live_version``): the highest
    version, registration time only breaking ties, so ``show ID`` reads the
    version ``graph-bind GRAPH ID`` binds.
    """
    live = [
        r
        for r in rows
        if r.get("ontology_id") == ontology_id and r.get("status", "active") != "retired"
    ]
    if not live:
        return None
    best = max(
        live, key=lambda r: (_version_key(str(r["version"])), str(r.get("created_at") or ""))
    )
    return str(best["version"])


def _do_show(args: argparse.Namespace) -> int:
    ontology_id, version = parse_coordinate(args.coordinate)
    knowledge = _knowledge()
    if version is None:
        version = newest_live_version(knowledge.ontology_list(project_id=args.project), ontology_id)
    if version is None:
        raise OntologyUsageError(
            f"no live ontology named {ontology_id!r} (r4 knowledge ontology list --all)"
        )
    payload = knowledge.ontology_show(ontology_id, version, project_id=args.project)
    if args.as_json:
        _print_json(payload)
    else:
        _print_show(payload)
    return 0


def _do_retire(args: argparse.Namespace) -> int:
    ontology_id, version = parse_coordinate(args.coordinate, need_version=True)
    coordinate = f"{ontology_id}@{version}"
    if not args.yes and not _confirm(
        f"Retire ontology {coordinate}? No graph can bind it afterwards; "
        "graphs already bound to it keep using it until you bind them to "
        "another version. [y/N] "
    ):
        print("Aborted.")
        return 1
    payload = _knowledge().ontology_retire(ontology_id, str(version), project_id=args.project)
    if args.as_json:
        _print_json(payload)
    else:
        print(f"✓ Retired ontology {coordinate}")
    return 0


_ACTIONS: dict[str, Callable[[argparse.Namespace], int]] = {
    "register": _do_register,
    "list": _do_list,
    "show": _do_show,
    "retire": _do_retire,
}


def run_guarded(
    args: argparse.Namespace,
    action: Callable[[argparse.Namespace], int],
    *,
    ontology_route: bool = True,
) -> int:
    """Run *action*: exit 2 on a usage error, 1 with a clear line on a
    server error (JSON on stdout under ``--json``)."""
    try:
        return action(args)
    except OntologyUsageError as exc:
        return _fail(args, str(exc), 2)
    except Exception as exc:  # noqa: BLE001 — one line, the server's reason
        return _fail(args, ontology_error_message(exc, ontology_route=ontology_route), 1)


def _fail(args: argparse.Namespace, message: str, code: int) -> int:
    if getattr(args, "as_json", False):
        print(json.dumps({"error": message, "exit_code": code}))
    else:
        print(f"✗ {message}", file=sys.stderr)
    return code


def handle_ontology(args: argparse.Namespace) -> int:
    """``r4 knowledge ontology <action>``."""
    action = _ACTIONS.get(getattr(args, "ontology_command", None) or "")
    if action is None:
        print(_ONTOLOGY_HELP, file=sys.stderr)
        return 2
    return run_guarded(args, action)


def _graph_id(knowledge: Any, graph: str) -> str:
    """The id of a graph given by id or by name: ``GET graphs/{graph}``
    resolves either, while the PATCH that binds takes an id only."""
    return str(knowledge.graph_info(graph).get("graph_id") or graph)


def _do_graph_bind(args: argparse.Namespace) -> int:
    ontology_id, version = parse_coordinate(args.coordinate)
    knowledge = _knowledge()
    graph = knowledge.graph_bind(_graph_id(knowledge, args.graph_id), ontology_id, version)
    if args.as_json:
        _print_json(graph)
        return 0
    pinned = graph.get("ontology_version") or version or "newest live"
    name = graph.get("name") or args.graph_id
    print(
        f"✓ Graph {graph.get('graph_id', args.graph_id)} ({name}) bound to {ontology_id}@{pinned}"
    )
    return 0


def handle_graph_bind(args: argparse.Namespace) -> int:
    """``r4 knowledge graph-bind GRAPH ID[@VERSION]``."""
    return run_guarded(args, _do_graph_bind, ontology_route=False)


__all__ = [
    "OntologyUsageError",
    "error_code",
    "handle_graph_bind",
    "handle_ontology",
    "infer_format",
    "newest_live_version",
    "ontology_error_message",
    "parse_coordinate",
    "register_graph_bind",
    "register_ontology",
    "run_guarded",
]
