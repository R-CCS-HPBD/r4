"""``r4 provenance`` command handler (COR-005, MEM-B7, 2026-05-14).

Thin CLI wrapper for querying durable job provenance through the Gateway
endpoints implemented in ``umrs.api.provenance``.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler
from .registry import command


@command
class ProvenanceCommand(CommandHandler):
    """``r4 provenance`` — inspect durable job/workflow provenance."""

    name = "provenance"
    help = "Inspect job and workflow provenance"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Query the provenance of durable jobs and workflow runs.\n\n"
                "Provenance records answer: what inputs, model refs, attempts,\n"
                "artifacts, and policies produced a given result."
            ),
            epilog=(
                "Examples:\n"
                "  r4 provenance show job-abc123\n"
                "  r4 provenance list --status completed --type workflow_run\n"
                "  r4 provenance lineage job-abc123\n"
                "  r4 provenance diff job-abc123 job-def456\n"
                "  r4 provenance show job-abc123 --json"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="prov_command",
            title="subcommands",
            metavar="<provenance-command>",
        )
        sub.required = True

        _register_provenance_show(sub)
        _register_provenance_list(sub)
        _register_provenance_lineage(sub)
        _register_provenance_diff(sub)

        return parser

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="provenance")

        sub = getattr(args, "prov_command", None)
        if not sub:
            print("Usage: r4 provenance <show|list|lineage|diff>", file=sys.stderr)
            return 1

        if sub == "show":
            return _cmd_provenance_show(args)
        if sub == "list":
            return _cmd_provenance_list(args)
        if sub == "lineage":
            return _cmd_provenance_lineage(args)
        if sub == "diff":
            return _cmd_provenance_diff(args)

        print(f"Unknown provenance command: {sub}", file=sys.stderr)
        return 1


def _add_json_flag(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )


def _register_provenance_show(sub) -> None:
    sub_show = sub.add_parser("show", help="Show provenance for a job")
    sub_show.add_argument("job_id", help="Job ID")
    _add_json_flag(sub_show)


def _register_provenance_list(sub) -> None:
    sub_list = sub.add_parser("list", help="List provenance records")
    sub_list.add_argument("--project", "-p", help="Project ID")
    sub_list.add_argument(
        "--status",
        "-s",
        choices=["planned", "queued", "running", "completed", "failed", "cancelled"],
        help="Filter by job status",
    )
    sub_list.add_argument(
        "--type",
        "-t",
        dest="job_type",
        help="Filter by job type",
    )
    sub_list.add_argument("--limit", "-l", type=int, default=20, help="Max results")
    _add_json_flag(sub_list)


def _register_provenance_lineage(sub) -> None:
    sub_lineage = sub.add_parser("lineage", help="Show lineage graph for a job")
    sub_lineage.add_argument("job_id", help="Job ID")
    _add_json_flag(sub_lineage)


def _register_provenance_diff(sub) -> None:
    sub_diff = sub.add_parser("diff", help="Compare two job provenance records")
    sub_diff.add_argument("job_id_a", help="Job ID A (baseline)")
    sub_diff.add_argument("job_id_b", help="Job ID B (comparison)")
    _add_json_flag(sub_diff)


def _cmd_provenance_show(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    prefix = getattr(client, "_api_prefix", "/gw")
    url = f"{client.config.gateway_url}{prefix}/provenance/{args.job_id}"

    try:
        data = client.http.request("GET", url)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    _print_provenance_show(data)
    return 0


def _print_provenance_show(data: dict) -> None:
    print(f"Provenance: {data.get('job_id', 'unknown')}")
    print("=" * 60)
    _print_provenance_metadata(data)
    _print_provenance_model(data.get("model"))
    _print_provenance_workflow(data)
    _print_provenance_attempts(data.get("attempts", []))
    _print_provenance_artifacts(data.get("artifacts", []))


def _print_provenance_metadata(data: dict) -> None:
    fields = [
        ("Type", data.get("job_type", "")),
        ("Status", data.get("status", "")),
        ("Project", data.get("project_id", "")),
        ("User", data.get("user_id", "")),
        ("Created", data.get("created_at", "N/A")),
        ("Completed", data.get("completed_at", "N/A")),
    ]
    for label, value in fields:
        print(f"  {label + ':':<18} {value}")

    duration = data.get("total_duration_seconds")
    if duration is not None:
        print(f"  Duration:          {duration:.1f}s")

    more_fields = [
        ("Authority", data.get("execution_authority", "N/A")),
        ("Restart Policy", data.get("restart_policy", "N/A")),
        ("Total Attempts", data.get("total_attempts", 0)),
        ("Commit Attempt", data.get("commit_attempt_id", "N/A")),
    ]
    for label, value in more_fields:
        print(f"  {label + ':':<18} {value}")


def _print_provenance_model(model: dict | None) -> None:
    if not model:
        return
    print("\n  Model:")
    print(f"    Name:            {model.get('name', '')}")
    if model.get("path"):
        print(f"    Path:            {model['path']}")
    if model.get("version"):
        print(f"    Version:         {model['version']}")


def _print_provenance_workflow(data: dict) -> None:
    if data.get("workflow_name"):
        print(f"\n  Workflow:          {data['workflow_name']}")
    if data.get("execution_id"):
        print(f"  Execution ID:      {data['execution_id']}")


def _print_provenance_attempts(attempts: list[dict]) -> None:
    if not attempts:
        return
    print(f"\n  Attempts ({len(attempts)}):")
    for attempt in attempts:
        _print_provenance_attempt(attempt)


def _print_provenance_attempt(attempt: dict) -> None:
    status_icon = {"succeeded": "✓", "failed": "✗", "lost": "⚠", "running": "…"}.get(
        attempt.get("status", ""), "?"
    )
    duration = attempt.get("duration_seconds")
    duration_text = f" ({duration:.1f}s)" if duration is not None else ""
    print(
        f"    #{attempt.get('attempt_no', '?')} [{status_icon}] "
        f"{attempt.get('backend', '')} "
        f"ref={attempt.get('backend_ref', 'N/A')}"
        f"{duration_text}"
    )
    if attempt.get("exit_reason"):
        print(f"       reason: {attempt['exit_reason']}")


def _print_provenance_artifacts(artifacts: list[dict]) -> None:
    if not artifacts:
        return
    print(f"\n  Artifacts ({len(artifacts)}):")
    for artifact in artifacts:
        size = artifact.get("size_bytes")
        size_text = f" ({size} bytes)" if size else ""
        print(f"    {artifact.get('uri', '')}{size_text}")


def _cmd_provenance_list(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    url = f"{client.config.gateway_url}{getattr(client, '_api_prefix', '/gw')}/provenance"
    data = _request_provenance_list(client, url, _provenance_list_params(args))
    if data is None:
        return 1

    records = data.get("records", [])
    if getattr(args, "as_json", False):
        print(json.dumps(records, indent=2))
        return 0

    _print_provenance_list(records)
    return 0


def _provenance_list_params(args: Namespace) -> dict[str, str | int]:
    params: dict[str, str | int] = {"limit": args.limit}
    if args.project:
        params["project_id"] = args.project
    if args.status:
        params["status"] = args.status
    if args.job_type:
        params["job_type"] = args.job_type
    return params


def _request_provenance_list(client, url: str, params: dict[str, str | int]) -> dict | None:
    try:
        return client.http.request("GET", url, params=params)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None


def _print_provenance_list(records: list[dict]) -> None:
    if not records:
        print("No provenance records found.")
        return

    print(f"{'JOB ID':<20}  {'TYPE':<20}  {'STATUS':<12}  {'ATTEMPTS':<8}  {'DURATION':<10}  MODEL")
    print("-" * 100)

    for rec in records:
        dur = rec.get("total_duration_seconds")
        dur_str = f"{dur:.1f}s" if dur is not None else "N/A"
        model = rec.get("model", {})
        model_str = model.get("name", "") if model else ""
        print(
            f"{rec.get('job_id', ''):<20}  "
            f"{rec.get('job_type', ''):<20}  "
            f"{rec.get('status', ''):<12}  "
            f"{rec.get('total_attempts', 0):<8}  "
            f"{dur_str:<10}  "
            f"{model_str}"
        )


def _cmd_provenance_lineage(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    prefix = getattr(client, "_api_prefix", "/gw")
    url = f"{client.config.gateway_url}{prefix}/provenance/{args.job_id}/lineage"

    try:
        data = client.http.request("GET", url)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    nodes = data.get("nodes", [])
    if not nodes:
        print(f"No execution traces found for job: {args.job_id}")
        return 0

    _print_provenance_lineage(data, args.job_id)
    return 0


def _print_provenance_lineage(data: dict, job_id: str) -> None:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    metrics = data.get("metrics", {})

    print(f"Lineage Graph: {data.get('job_id', job_id)}")
    if data.get("execution_id"):
        print(f"  Execution: {data['execution_id']}")
    print("=" * 70)

    _print_lineage_metrics(metrics)
    _print_lineage_dag(nodes, edges, data.get("root_ids", []))


def _print_lineage_metrics(metrics: dict) -> None:
    print("\n  Metrics:")
    fields = [
        ("Nodes", metrics.get("total_nodes", 0)),
        ("Edges", metrics.get("total_edges", 0)),
        ("Depth", metrics.get("depth", 0)),
        ("Roots", metrics.get("root_count", 0)),
        ("Leaves", metrics.get("leaf_count", 0)),
        ("Succeeded", metrics.get("succeeded_count", 0)),
        ("Failed", metrics.get("failed_count", 0)),
        ("Running", metrics.get("running_count", 0)),
    ]
    for label, value in fields:
        print(f"    {label + ':':<16} {value}")
    total_dur = metrics.get("total_duration_seconds")
    if total_dur is not None:
        print(f"    Total Duration:  {total_dur:.1f}s")
    crit = metrics.get("critical_path_seconds")
    if crit is not None:
        print(f"    Critical Path:   {crit:.1f}s")


def _lineage_maps(
    nodes: list[dict],
    edges: list[dict],
) -> tuple[dict[str, list[str]], dict[str, dict]]:
    children_map: dict[str, list[str]] = {}
    for edge in edges:
        children_map.setdefault(edge["source"], []).append(edge["target"])
    return children_map, {node["trace_id"]: node for node in nodes}


def _lineage_status_icon(status: str) -> str:
    return {
        "succeeded": "✓",
        "failed": "✗",
        "running": "…",
        "pending": "○",
        "skipped": "⊘",
    }.get(status, "?")


def _lineage_node_suffix(node: dict) -> str:
    duration = node.get("duration_seconds")
    duration_text = f" ({duration:.1f}s)" if duration is not None else ""
    model = node.get("model_ref")
    model_text = f" model={model['name']}" if model and model.get("name") else ""
    return f"{duration_text}{model_text}"


def _render_lineage_node(
    node_id: str,
    *,
    children_map: dict[str, list[str]],
    node_map: dict[str, dict],
    indent: int = 4,
) -> None:
    node = node_map.get(node_id)
    if not node:
        return
    prefix = " " * indent
    print(
        f"{prefix}[{_lineage_status_icon(node.get('status', ''))}] "
        f"{node['step_name']} ({node.get('step_type', 'generic')})"
        f"{_lineage_node_suffix(node)}"
    )
    for child_id in children_map.get(node_id, []):
        _render_lineage_node(
            child_id,
            children_map=children_map,
            node_map=node_map,
            indent=indent + 4,
        )


def _print_lineage_dag(nodes: list[dict], edges: list[dict], root_ids: list[str]) -> None:
    print("\n  Execution DAG:")
    children_map, node_map = _lineage_maps(nodes, edges)
    for root_id in root_ids:
        _render_lineage_node(root_id, children_map=children_map, node_map=node_map)


def _cmd_provenance_diff(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    url = f"{client.config.gateway_url}{getattr(client, '_api_prefix', '/gw')}/provenance/diff"

    try:
        data = client.http.request(
            "GET",
            url,
            params={"job_a": args.job_id_a, "job_b": args.job_id_b},
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    _print_provenance_diff(data, args)
    return 0


def _print_provenance_diff(data: dict, args: Namespace) -> None:
    print("Provenance Diff")
    print(f"  Baseline:    {data.get('job_id_a', args.job_id_a)}")
    print(f"  Comparison:  {data.get('job_id_b', args.job_id_b)}")
    print("=" * 60)

    _print_diff_summary(data.get("summary", {}))
    job_diffs = _print_field_diffs(data.get("job_level_diffs", []), "Job-Level Changes")
    artifact_diffs = _print_field_diffs(data.get("artifact_diffs", []), "Artifact Changes")
    changed_steps = _print_step_diffs(data.get("step_diffs", []))

    if not job_diffs and not artifact_diffs and not changed_steps:
        print("\n  No differences found.")


def _print_diff_summary(summary: dict) -> None:
    print("\n  Summary:")
    fields = [
        ("Job-level changes", summary.get("job_field_changes", 0)),
        ("Artifact changes", summary.get("artifact_changes", 0)),
        ("Steps changed", summary.get("steps_changed", 0)),
        ("Steps added", summary.get("steps_added", 0)),
        ("Steps removed", summary.get("steps_removed", 0)),
        ("Steps unchanged", summary.get("steps_unchanged", 0)),
    ]
    for label, value in fields:
        print(f"    {label + ':':<20} {value}")


def _print_field_diffs(diffs: list[dict], title: str) -> list[dict]:
    if not diffs:
        return []
    print(f"\n  {title} ({len(diffs)}):")
    for diff in diffs:
        print(f"    {diff['field']}: {diff['old']} → {diff['new']}")
    return diffs


def _print_step_diffs(step_diffs: list[dict]) -> list[dict]:
    changed_steps = [step for step in step_diffs if step["status"] != "unchanged"]
    if changed_steps:
        print(f"\n  Step Changes ({len(changed_steps)}):")
        icons = {"added": "+", "removed": "-", "changed": "~", "unchanged": "="}
        for step in changed_steps:
            icon = icons.get(step["status"], "?")
            print(f"    [{icon}] {step['step_name']} ({step['status']})")
            for field_diff in step.get("field_diffs", []):
                print(f"        {field_diff['field']}: {field_diff['old']} → {field_diff['new']}")
    return changed_steps
