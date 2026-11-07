"""``r4 artifact`` command handler (bug.memo D3 Phase 2i, 2026-04-25).

Ported from ``r4.cli.cmd_artifact``. Behaviour is byte-identical; the
handler function still lives in ``r4.cli`` and is imported lazily
inside :meth:`handle` to avoid bootstrap-time circular imports.

The argparse parser stays in ``_build_parser`` in ``r4.cli``; this
handler only owns the dispatch.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, _SubParsersAction

from ._helpers import _current_project_id
from .base import CommandHandler
from .registry import command


@command
class ArtifactCommand(CommandHandler):
    """``r4 artifact`` — A2L artifact management."""

    name = "artifact"
    help = "Manage A2L artifacts"

    def register(self, subparsers: _SubParsersAction[ArgumentParser]) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help="A2L artifact registry (lineage-tracked outputs)",
            description="Inspect artifacts and record lineage-preserving derivations.",
            epilog=(
                "Examples:\n"
                "  r4 artifact list --project demo\n"
                "  r4 artifact show <artifact-id>\n"
                "  r4 artifact derive --type report --uri s3://bucket/report.md "
                "--parent <artifact-id>"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="artifact_command",
            title="subcommands",
            metavar="<artifact-command>",
        )
        sub.required = True

        art_list = sub.add_parser("list", help="List artifacts")
        art_list.add_argument("--type", help="Filter by artifact type")
        art_list.add_argument(
            "--status",
            choices=["pending", "running", "completed", "failed"],
            help="Filter by status",
        )
        art_list.add_argument("--project", help="Filter by project ID")
        art_list.add_argument("--user", help="Filter by user ID")
        art_list.add_argument("--limit", type=int, default=50)
        art_list.add_argument("--offset", type=int, default=0)
        art_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        art_show = sub.add_parser("show", help="Show a single artifact")
        art_show.add_argument("artifact_id")
        art_show.add_argument("--json", "-j", action="store_true", dest="as_json")

        art_create = sub.add_parser(
            "create",
            help=(
                "Create an artifact (admin/ingestion only — agents MUST use "
                "`r4 artifact derive` to preserve lineage)."
            ),
        )
        art_create.add_argument("--type", required=True)
        art_create.add_argument("--uri", required=True)
        art_create.add_argument("--metadata", help="JSON object with metadata", default="{}")
        art_create.add_argument("--project", help="Project ID")

        art_derive = sub.add_parser(
            "derive",
            help="Derive a new artifact from parent artifact(s) (preferred write path)",
        )
        art_derive.add_argument("--type", required=True)
        art_derive.add_argument("--uri", required=True)
        art_derive.add_argument(
            "--parent",
            action="append",
            dest="parents",
            required=True,
            help="Parent artifact ID (may be repeated)",
        )
        art_derive.add_argument("--metadata", default="{}")
        art_derive.add_argument("--project", help="Project ID")

        art_status = sub.add_parser("status", help="Update artifact status")
        art_status.add_argument("artifact_id")
        art_status.add_argument(
            "new_status",
            choices=["running", "completed", "failed"],
            help="Target status (FSM-enforced by the server)",
        )
        art_status.add_argument("--error", help="Error message (for 'failed')")

        art_lineage = sub.add_parser(
            "lineage",
            help="Show lineage graph for an artifact",
        )
        art_lineage.add_argument("artifact_id")
        art_lineage.add_argument(
            "--direction",
            choices=["up", "down", "both"],
            default="both",
            help="Traversal direction (default: both)",
        )
        art_lineage.add_argument(
            "--depth",
            type=int,
            default=10,
            help="Max traversal depth (default: 10)",
        )
        art_lineage.add_argument("--json", "-j", action="store_true", dest="as_json")
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="artifact")

        cmd_artifact(args)
        return 0


def cmd_artifact(args):
    """Handle `r4 artifact` (bug.memo A2L1, 2026-04-25).

    Thin wrapper over the gateway's ``/gw/a2l/artifacts*`` routes.
    """
    import sys

    if not args.artifact_command:
        print(
            "Usage: r4 artifact {list,show,create,derive,status}",
            file=sys.stderr,
        )
        sys.exit(1)

    from ..client import Client

    client = Client()
    http = client.http
    _check_a2l_available(client, http)
    base = _a2l_url(client, "artifacts")
    project_id = _artifact_project_id(args, client)
    handler = _artifact_command_handlers().get(args.artifact_command)
    if handler is None:
        print(f"Unknown artifact subcommand: {args.artifact_command}", file=sys.stderr)
        sys.exit(1)
    return handler(args, http, base, project_id)


def _a2l_url(client, path: str) -> str:
    """A2L ledger URL in the client's mode (a2l_client.a2l_base_url, H-10.2)."""
    from ..a2l_client import a2l_base_url

    return f"{a2l_base_url(client)}/{path}"


def _check_a2l_available(client, http) -> None:
    from ..errors import APIError, R4ConnectionError, R4Error

    url = _a2l_url(client, "health")
    try:
        http.get(url, timeout=5)
    except (R4ConnectionError, R4Error) as exc:
        if isinstance(exc, R4ConnectionError):
            # Nothing answered: that is not evidence A2L is undeployed (c2-cli).
            _die_a2l_unreachable(url, exc)
        status_code = exc.status_code if isinstance(exc, APIError) else None
        if status_code in (502, 503, 504):
            _die_a2l_unavailable()


def _die_a2l_unreachable(url: str, exc: Exception) -> None:
    import sys

    print(f"Error: cannot reach {url}: {exc}", file=sys.stderr)
    print("  hint: check the gateway with `r4 health`.", file=sys.stderr)
    sys.exit(1)


def _die_a2l_unavailable() -> None:
    import sys

    print("Error: A2L artifact service is not available on this deployment.", file=sys.stderr)
    print(
        "  hint: The `r4 artifact` commands require the A2L sidecar. "
        "Contact your administrator to deploy A2L, or use "
        "`r4 rag` / `r4 model` for non-artifact workflows.",
        file=sys.stderr,
    )
    sys.exit(1)


def _parse_metadata(raw: str) -> dict:
    import json
    import sys

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        print(f"Error: --metadata must be valid JSON ({error})", file=sys.stderr)
        sys.exit(2)
    if not isinstance(parsed, dict):
        print("Error: --metadata must be a JSON object", file=sys.stderr)
        sys.exit(2)
    return parsed


def _artifact_project_id(args, client) -> str | None:
    return getattr(args, "project", None) or _current_project_id() or client.config.project_id


def _json_print(data) -> None:
    import json

    print(json.dumps(data, indent=2, default=str))


def _artifact_list_params(args, project_id: str | None) -> dict:
    params = {"offset": args.offset, "limit": args.limit}
    optional_filters = [
        ("type", args.type),
        ("status", args.status),
        ("user_id", args.user),
    ]
    for key, value in optional_filters:
        if value:
            params[key] = value
    if args.project:
        params["project_id"] = args.project
    elif project_id:
        params["project_id"] = project_id
    return params


def _print_artifact_list(data: dict) -> None:
    # The a2l ledger answers {"artifacts": [...]}; older surfaces used "items".
    items = data.get("artifacts") or data.get("items") or []
    total = data.get("total", len(items))
    if not items:
        print("No artifacts found.")
        return
    print(f"{'ID':<40}  {'TYPE':<20}  {'STATUS':<10}  URI")
    for item in items:
        print(
            f"{item.get('id', '')[:40]:<40}  "
            f"{(item.get('type') or '')[:20]:<20}  "
            f"{(item.get('status') or '')[:10]:<10}  "
            f"{item.get('uri', '')}"
        )
    print(f"\n{len(items)}/{total} artifact(s)")


def _cmd_artifact_list(args, http, base: str, project_id: str | None):
    data = http.get(base, params=_artifact_list_params(args, project_id))
    if args.as_json:
        _json_print(data)
    else:
        _print_artifact_list(data)


def _cmd_artifact_show(args, http, base: str, _project_id: str | None):
    _json_print(http.get(f"{base}/{args.artifact_id}"))


def _artifact_create_body(args, project_id: str | None) -> dict:
    body = {
        "type": args.type,
        "uri": args.uri,
        "metadata": _parse_metadata(args.metadata),
    }
    if project_id:
        body["project_id"] = project_id
    return body


def _cmd_artifact_create(args, http, base: str, project_id: str | None):
    _json_print(http.post(base, json_body=_artifact_create_body(args, project_id)))


def _artifact_derive_body(args, project_id: str | None) -> dict:
    body = _artifact_create_body(args, project_id)
    body["parents"] = args.parents
    return body


def _cmd_artifact_derive(args, http, base: str, project_id: str | None):
    _json_print(http.post(f"{base}/derive", json_body=_artifact_derive_body(args, project_id)))


def _cmd_artifact_status(args, http, base: str, _project_id: str | None):
    body = {"status": args.new_status}
    if args.error:
        body["error"] = args.error
    data = http.request("PATCH", f"{base}/{args.artifact_id}/status", json_body=body)
    _json_print(data)


def _lineage_children_map(edges: list[dict]) -> dict[str, list[str]]:
    children_map: dict[str, list[str]] = {}
    for edge in edges:
        children_map.setdefault(edge["parent_id"], []).append(edge["child_id"])
    return children_map


def _lineage_roots(nodes: list[dict], edges: list[dict], fallback_root: str) -> list[str]:
    all_children = {edge["child_id"] for edge in edges}
    roots = [node["id"] for node in nodes if node["id"] not in all_children]
    return roots or [fallback_root]


def _lineage_node_label(node_id: str, node_map: dict[str, dict]) -> str:
    info = node_map.get(node_id, {})
    artifact_class = info.get("artifact_class") or ""
    extra = f" [{artifact_class}]" if artifact_class else ""
    return f"{node_id[:12]}  {info.get('type', '?')}  {info.get('status', '?')}{extra}"


def _print_lineage_tree(
    node_id: str,
    node_map: dict[str, dict],
    children_map: dict[str, list[str]],
    *,
    prefix: str = "",
    is_last: bool = True,
) -> None:
    marker = "└── " if is_last else "├── "
    print(f"{prefix}{marker}{_lineage_node_label(node_id, node_map)}")
    kids = children_map.get(node_id, [])
    for index, child_id in enumerate(kids):
        child_prefix = prefix + ("    " if is_last else "│   ")
        _print_lineage_tree(
            child_id,
            node_map,
            children_map,
            prefix=child_prefix,
            is_last=index == len(kids) - 1,
        )


def _print_lineage(data: dict, fallback_root: str) -> None:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    root = data.get("root_id", fallback_root)
    if not nodes:
        print(f"Artifact {root} has no lineage.")
        return
    children_map = _lineage_children_map(edges)
    node_map = {node["id"]: node for node in nodes}
    roots = _lineage_roots(nodes, edges, root)
    for index, root_id in enumerate(roots):
        _print_lineage_tree(
            root_id,
            node_map,
            children_map,
            is_last=index == len(roots) - 1,
        )


def _cmd_artifact_lineage(args, http, base: str, _project_id: str | None):
    params = {"direction": args.direction, "max_depth": args.depth}
    data = http.get(f"{base}/{args.artifact_id}/lineage", params=params)
    if args.as_json:
        _json_print(data)
    else:
        _print_lineage(data, args.artifact_id)


def _artifact_command_handlers():
    return {
        "list": _cmd_artifact_list,
        "show": _cmd_artifact_show,
        "create": _cmd_artifact_create,
        "derive": _cmd_artifact_derive,
        "status": _cmd_artifact_status,
        "lineage": _cmd_artifact_lineage,
    }
