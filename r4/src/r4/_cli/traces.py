"""``r4 traces`` command handler (COR-006, MEM-B4).

Thin CLI wrapper for querying execution traces through Gateway.
Traces record step-level execution within durable jobs, forming a DAG
of operations with timing, model refs, artifacts, and routing decisions.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from .base import CommandHandler
from .registry import command


@command
class TracesCommand(CommandHandler):
    """``r4 traces`` — inspect execution traces for jobs and workflows."""

    name = "traces"
    help = "Inspect execution traces for jobs and workflows"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Query step-level execution traces for durable jobs.\n\n"
                "Traces form a DAG showing what steps ran, what models and\n"
                "artifacts they used, how long they took, and how they relate."
            ),
            epilog=(
                "Examples:\n"
                "  r4 traces list --job job-abc123\n"
                "  r4 traces show trc-abc123def456\n"
                "  r4 traces list --execution exec-xyz --json\n"
                "  r4 traces children trc-abc123def456"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="traces_command",
            title="subcommands",
            metavar="<traces-command>",
        )
        sub.required = True

        _register_traces_list(sub)
        _register_trace_id_command(sub, "show", "Show a single trace")
        _register_trace_id_command(sub, "lineage", "Show ancestor chain for a trace")
        _register_trace_id_command(sub, "children", "Show child traces")

        return parser

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="traces")

        sub = getattr(args, "traces_command", None)
        if not sub:
            print("Usage: r4 traces <list|show|lineage|children>", file=sys.stderr)
            return 1

        handlers = {
            "list": _cmd_traces_list,
            "show": _cmd_traces_show,
            "lineage": _cmd_traces_lineage,
            "children": _cmd_traces_children,
        }
        handler = handlers.get(sub)
        if not handler:
            print(f"Unknown traces command: {sub}", file=sys.stderr)
            return 1

        return handler(args)


def _add_json_flag(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )


def _register_traces_list(sub) -> None:
    sub_list = sub.add_parser("list", help="List traces for a job or execution")
    group = sub_list.add_mutually_exclusive_group(required=True)
    group.add_argument("--job", "-j", dest="job_id", help="Job ID")
    group.add_argument(
        "--execution",
        "-e",
        dest="execution_id",
        help="Workflow execution ID",
    )
    _add_json_flag(sub_list)


def _register_trace_id_command(sub, name: str, help_text: str) -> None:
    parser = sub.add_parser(name, help=help_text)
    parser.add_argument("trace_id", help="Trace ID")
    _add_json_flag(parser)


def _gw_request(path: str, params: dict | None = None) -> dict:
    from ..client import Client

    client = Client()
    url = f"{client.config.gateway_url}{getattr(client, '_api_prefix', '/gw')}/traces/{path}"
    return client.http.request("GET", url, params=params)


def _format_trace(trace: dict, indent: int = 2) -> None:
    """Print a single trace in human-readable form."""
    prefix = " " * indent
    status_icon = _trace_status_icon(trace.get("status", ""))

    print(
        f"{prefix}[{status_icon}] {trace.get('trace_id', '')}  "
        f"{trace.get('step_name', '')} ({trace.get('step_type', 'generic')})"
    )
    _print_trace_timestamps(trace, prefix)
    _print_trace_optional_summary(trace, prefix)


def _trace_status_icon(status: str) -> str:
    return {
        "succeeded": "✓",
        "failed": "✗",
        "running": "…",
        "pending": "○",
        "skipped": "⊘",
    }.get(status, "?")


def _print_trace_timestamps(trace: dict, prefix: str) -> None:
    started = trace.get("started_at", "")
    finished = trace.get("finished_at", "")
    if started:
        print(f"{prefix}    Started:  {started[:19]}")
    if finished:
        print(f"{prefix}    Finished: {finished[:19]}")


def _print_trace_optional_summary(trace: dict, prefix: str) -> None:
    model = trace.get("model_ref")
    if model and model.get("name"):
        print(f"{prefix}    Model:    {model['name']}")

    artifacts = trace.get("artifact_refs", [])
    if artifacts:
        print(f"{prefix}    Artifacts: {len(artifacts)}")

    tool_calls = trace.get("tool_calls", [])
    if tool_calls:
        names = [tc.get("tool_name", "?") for tc in tool_calls]
        print(f"{prefix}    Tools:    {', '.join(names)}")

    error = trace.get("error")
    if error:
        print(f"{prefix}    Error:    {error}")


def _cmd_traces_list(args: Namespace) -> int:
    try:
        if args.job_id:
            data = _gw_request(f"job/{args.job_id}")
        else:
            data = _gw_request(f"execution/{args.execution_id}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    traces = data.get("traces", [])

    if getattr(args, "as_json", False):
        print(json.dumps(traces, indent=2))
        return 0

    if not traces:
        target = args.job_id or args.execution_id
        print(f"No traces found for: {target}")
        return 0

    target_label = f"job={args.job_id}" if args.job_id else f"execution={args.execution_id}"
    print(f"Execution Traces ({target_label}): {len(traces)} steps")
    print("=" * 60)

    for trace in traces:
        _format_trace(trace)

    return 0


def _cmd_traces_show(args: Namespace) -> int:
    try:
        data = _gw_request(args.trace_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(data, indent=2))
        return 0

    _print_trace_show(data)
    return 0


def _print_trace_show(data: dict) -> None:
    print(f"Trace: {data.get('trace_id', '')}")
    print("=" * 60)
    _print_trace_show_metadata(data)
    _print_mapping_section("Model", data.get("model_ref"))
    _print_trace_routing(data.get("routing_decision"))
    _print_trace_artifacts(data.get("artifact_refs", []))
    _print_trace_tool_calls(data.get("tool_calls", []))
    _print_trace_error(data)


def _print_trace_show_metadata(data: dict) -> None:
    print(f"  Job:           {data.get('job_id', '')}")
    print(f"  Step:          {data.get('step_name', '')} ({data.get('step_type', 'generic')})")
    print(f"  Status:        {data.get('status', '')}")

    if data.get("execution_id"):
        print(f"  Execution:     {data['execution_id']}")
    if data.get("parent_trace_id"):
        print(f"  Parent:        {data['parent_trace_id']}")

    print(f"  Started:       {data.get('started_at', 'N/A')}")
    print(f"  Finished:      {data.get('finished_at', 'N/A')}")
    print(f"  Progress:      {data.get('progress', 0):.0%}")
    print(f"  Inputs Hash:   {data.get('inputs_hash', 'N/A')}")
    print(f"  Outputs Hash:  {data.get('outputs_hash', 'N/A')}")


def _print_mapping_section(title: str, values: dict | None) -> None:
    if not values:
        return
    print(f"\n  {title}:")
    for key, value in values.items():
        if value:
            print(f"    {key}: {value}")


def _print_trace_routing(routing: dict | None) -> None:
    if routing:
        print(
            f"\n  Routing: target={routing.get('selected_target', '?')}"
            f" reason={routing.get('reason', '?')}"
        )


def _print_trace_artifacts(artifacts: list[dict]) -> None:
    if not artifacts:
        return
    print(f"\n  Artifacts ({len(artifacts)}):")
    for artifact in artifacts:
        print(f"    {artifact.get('uri', '')}")


def _print_trace_tool_calls(tool_calls: list[dict]) -> None:
    if not tool_calls:
        return
    print(f"\n  Tool Calls ({len(tool_calls)}):")
    for tool_call in tool_calls:
        print(f"    {tool_call.get('tool_name', '?')}")


def _print_trace_error(data: dict) -> None:
    error = data.get("error")
    if error:
        print(f"\n  Error: {error}")
        detail = data.get("error_detail")
        if detail:
            print(f"  Detail: {json.dumps(detail, indent=4)}")


def _cmd_traces_lineage(args: Namespace) -> int:
    try:
        data = _gw_request(f"{args.trace_id}/lineage")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    traces = data.get("traces", [])

    if getattr(args, "as_json", False):
        print(json.dumps(traces, indent=2))
        return 0

    if not traces:
        print(f"No lineage found for trace: {args.trace_id}")
        return 0

    print(f"Ancestor Chain for {args.trace_id}: {len(traces)} steps (root first)")
    print("=" * 60)

    for i, trace in enumerate(traces):
        arrow = "└─" if i == len(traces) - 1 else "├─"
        indent = "  " * i
        status_icon = {
            "succeeded": "✓",
            "failed": "✗",
            "running": "…",
            "pending": "○",
            "skipped": "⊘",
        }.get(trace.get("status", ""), "?")
        print(
            f"  {indent}{arrow} [{status_icon}] {trace.get('step_name', '')}"
            f" ({trace.get('step_type', 'generic')})"
        )

    return 0


def _cmd_traces_children(args: Namespace) -> int:
    try:
        data = _gw_request(f"{args.trace_id}/children")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    traces = data.get("traces", [])

    if getattr(args, "as_json", False):
        print(json.dumps(traces, indent=2))
        return 0

    if not traces:
        print(f"No child traces for: {args.trace_id}")
        return 0

    print(f"Children of {args.trace_id}: {len(traces)}")
    print("=" * 60)

    for trace in traces:
        _format_trace(trace)

    return 0
