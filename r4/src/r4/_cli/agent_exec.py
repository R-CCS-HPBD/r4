"""``r4 agent-exec`` command handler (Phase 4 — Agent Runtime tracking).

Thin wrapper over the A2L ``/gw/a2l/agent-executions*`` routes.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction

from ._helpers import _current_project_id
from .base import CommandHandler


class AgentExecCommand(CommandHandler):
    """``r4 agent-exec`` — A2L agent execution tracking."""

    name = "agent-exec"
    help = "Track agent executions in A2L"

    def register(self, subparsers: _SubParsersAction[ArgumentParser]) -> ArgumentParser:
        from argparse import RawDescriptionHelpFormatter

        p = subparsers.add_parser(
            self.name,
            help="A2L agent execution tracking",
            description="Create, list, and update agent execution records.",
            epilog=(
                "Examples:\n"
                "  r4 agent-exec list\n"
                "  r4 agent-exec show <execution-id>\n"
                "  r4 agent-exec create --agent segmenter-v2\n"
                "  r4 agent-exec update <execution-id> --status completed"
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        add_agent_exec_subcommands(p)
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="agent-exec")

        cmd_agent_exec(args)
        return 0


def add_agent_exec_subcommands(p: ArgumentParser, *, artifact_link_flags: bool = True) -> None:
    """Build the list/show/create/update/ledger tree that cmd_agent_exec reads.

    ``r4 agent-exec`` and ``r4 run`` share it so their dests cannot drift.
    ``artifact_link_flags=False`` leaves out --input/--output (the ledger
    records no artifact links); their dests still default to [].
    """
    sub = p.add_subparsers(
        dest="exec_command",
        title="subcommands",
        metavar="<exec-command>",
    )
    sub.required = True

    # list
    exec_list = sub.add_parser("list", help="List agent executions")
    exec_list.add_argument("--agent", help="Filter by agent name")
    exec_list.add_argument(
        "--status",
        choices=["pending", "running", "completed", "failed", "cancelled"],
        help="Filter by status",
    )
    exec_list.add_argument("--project", help="Filter by project ID")
    exec_list.add_argument("--user", help="Filter by user ID")
    exec_list.add_argument("--limit", type=int, default=50)
    exec_list.add_argument("--offset", type=int, default=0)
    exec_list.add_argument("--json", "-j", action="store_true", dest="as_json")

    # show
    exec_show = sub.add_parser("show", help="Show a single execution")
    exec_show.add_argument("exec_id")
    exec_show.add_argument("--json", "-j", action="store_true", dest="as_json")

    # create
    exec_create = sub.add_parser("create", help="Create an agent execution record")
    exec_create.add_argument("--agent", required=True, help="Agent name")
    exec_create.add_argument("--version", default="", help="Agent version")
    exec_create.add_argument("--model", default="", help="Model reference")
    exec_create.add_argument("--prompt-hash", default="", help="SHA-256 of prompts")
    exec_create.add_argument("--params", default="{}", help="JSON params")
    _add_artifact_link_flag(exec_create, "--input", "inputs", artifact_link_flags)
    exec_create.add_argument("--project", help="Project ID")

    # update
    exec_update = sub.add_parser("update", help="Update an execution")
    exec_update.add_argument("exec_id")
    exec_update.add_argument(
        "--status",
        choices=["running", "completed", "failed", "cancelled"],
        help="New status",
    )
    _add_artifact_link_flag(exec_update, "--output", "outputs", artifact_link_flags)
    exec_update.add_argument("--error", help="Error message")
    exec_update.add_argument("--trace", help="JSON execution trace")

    # ledger — compositional job history (ADR-034 UMRS-side ledger)
    exec_ledger = sub.add_parser(
        "ledger",
        help="Conserved history for one job (spine + artifacts + decisions)",
    )
    exec_ledger.add_argument("job_id")
    exec_ledger.add_argument("--json", "-j", action="store_true", dest="as_json")


def _add_artifact_link_flag(parser: ArgumentParser, flag: str, dest: str, enabled: bool) -> None:
    if not enabled:
        parser.set_defaults(**{dest: []})
        return
    kind = "Input" if dest == "inputs" else "Output"
    parser.add_argument(
        flag,
        action="append",
        dest=dest,
        default=[],
        help=f"{kind} artifact ID (refused: the ledger records no artifact links yet)",
    )


def cmd_agent_exec(args):
    """Handle `r4 agent-exec` subcommands."""
    from ..client import Client
    from ..errors import APIError, R4ConnectionError, R4Error

    if not args.exec_command:
        print("Usage: r4 agent-exec {list,show,create,update}", file=sys.stderr)
        sys.exit(1)

    client = Client()
    http = client.http
    base = _a2l_url(client, "agent-executions")
    if args.exec_command == "ledger":
        # Ledger job views: /gw/a2l/* reaches UMRS's a2l-ledger router since
        # the ADR-034 cutover; /umrs/... is not served through the gateway.
        base = _a2l_url(client, "jobs")
    else:
        _check_a2l_available(client, http, APIError, R4ConnectionError, R4Error)
    project_id = _agent_exec_project_id(args, client)

    handler = _agent_exec_handlers().get(args.exec_command)
    if handler is not None:
        handler(args, http, base, project_id)
        return

    print(f"Unknown agent-exec subcommand: {args.exec_command}", file=sys.stderr)
    sys.exit(1)


def _a2l_url(client, path: str) -> str:
    """A2L ledger URL in the client's mode (a2l_client.a2l_base_url, H-10.2)."""
    from ..a2l_client import a2l_base_url

    return f"{a2l_base_url(client)}/{path}"


def _check_a2l_available(client, http, api_error, connection_error, r4_error) -> None:
    url = _a2l_url(client, "health")
    try:
        http.get(url, timeout=5)
    except (connection_error, r4_error) as exc:
        if isinstance(exc, connection_error):
            # Nothing answered — the gateway itself is down or unreachable,
            # which says nothing about whether A2L is deployed (c2-cli).
            print(f"Error: cannot reach {url}: {exc}", file=sys.stderr)
            print("  hint: check the gateway with `r4 health`.", file=sys.stderr)
            sys.exit(1)
        status_code = exc.status_code if isinstance(exc, api_error) else None
        if status_code in (502, 503, 504):
            print(
                "Error: A2L artifact service is not available on this deployment.",
                file=sys.stderr,
            )
            sys.exit(1)


def _agent_exec_project_id(args: Namespace, client) -> str | None:
    return getattr(args, "project", None) or _current_project_id() or client.config.project_id


def _agent_exec_handlers():
    return {
        "list": _agent_exec_list,
        "show": _agent_exec_show,
        "create": _agent_exec_create,
        "update": _agent_exec_update,
        "ledger": _agent_exec_ledger,
    }


def _agent_exec_ledger(args: Namespace, http, base: str, _project_id: str | None) -> None:
    data = http.get(f"{base}/{args.job_id}/view")
    if args.as_json:
        _print_json(data)
        return
    _print_ledger_view(data)


def _print_ledger_view(data: dict) -> None:
    spine = data.get("spine") or {}
    print(f"job {data.get('job_id', '')}")
    if spine:
        print(
            f"  spine: status={spine.get('status', '?')} "
            f"type={spine.get('job_type', '?')} commit_attempt={spine.get('commit_attempt_id', '')}"
        )
    else:
        print("  spine: (no job row joined — conserved data only)")
    _print_ledger_artifacts(data.get("artifacts") or [])
    _print_ledger_executions(data.get("executions") or [])
    _print_ledger_decisions(data.get("decisions") or [])


def _print_ledger_artifacts(artifacts: list) -> None:
    print(f"  artifacts ({len(artifacts)}):")
    for item in artifacts[:50]:
        print(
            f"    {item.get('id', '')[:16]}  {(item.get('type') or '')[:12]:<12} "
            f"{(item.get('status') or '')[:9]:<9}  {item.get('uri', '')}"
        )


def _print_ledger_executions(executions: list) -> None:
    print(f"  agent executions ({len(executions)}):")
    for item in executions[:50]:
        print(
            f"    {item.get('id', '')[:16]}  {(item.get('agent_name') or '')[:20]:<20} "
            f"{(item.get('status') or '')[:10]:<10}  attempt={item.get('attempt_id', '')}"
        )


def _print_ledger_decisions(decisions: list) -> None:
    print(f"  decisions ({len(decisions)}):")
    for item in decisions[:50]:
        print(
            f"    {(item.get('decision') or '')[:14]:<14}  by {item.get('decided_by', '')}  "
            f"{item.get('rationale', '')[:60]}"
        )


def _agent_exec_list(args: Namespace, http, base: str, project_id: str | None) -> None:
    data = _filter_listed_executions(
        http.get(base, params=_agent_exec_list_params(args, project_id)), args
    )
    if args.as_json:
        _print_json(data)
        return
    _print_agent_exec_list(data)


def _agent_exec_list_params(args: Namespace, project_id: str | None) -> dict[str, object]:
    params: dict[str, object] = {"offset": args.offset, "limit": args.limit}
    if args.agent:
        params["agent_name"] = args.agent
    if args.status:
        params["status"] = args.status
    if project_id:
        params["project_id"] = project_id
    if args.user:
        params["user_id"] = args.user
    return params


def _filter_listed_executions(data: dict, args: Namespace) -> dict:
    """Apply --status / --project to the fetched page.

    GET /umrs/a2l/ledger/agent-executions takes neither filter and ignores
    both params, so without this the list came back unfiltered.
    """
    wanted = {"status": args.status, "project_id": getattr(args, "project", None)}
    wanted = {key: value for key, value in wanted.items() if value}
    key = "executions" if "executions" in data else "items"
    if not wanted or not isinstance(data.get(key), list):
        return data
    kept = [row for row in data[key] if all(row.get(k) == v for k, v in wanted.items())]
    return {**data, key: kept}


def _print_agent_exec_list(data: dict) -> None:
    # The a2l ledger answers {"executions": [...]}; older surfaces used "items".
    items = data.get("executions") or data.get("items") or []
    total = data.get("total", len(items))
    if not items:
        print("No agent executions found.")
        return
    print(f"{'ID':<38}  {'AGENT':<20}  {'STATUS':<10}  CREATED")
    for item in items:
        print(
            f"{item.get('id', '')[:38]:<38}  "
            f"{(item.get('agent_name') or '')[:20]:<20}  "
            f"{(item.get('status') or '')[:10]:<10}  "
            f"{item.get('created_at', '')}"
        )
    print(f"\n{len(items)}/{total} execution(s)")


def _agent_exec_show(args: Namespace, http, base: str, _project_id: str | None) -> None:
    _print_json(http.get(f"{base}/{args.exec_id}"))


def _refuse_artifact_links(artifact_ids: list[str], flag_name: str) -> None:
    """The ledger's execution bodies have no artifact-id field and drop it silently."""
    if artifact_ids:
        print(
            f"Error: {flag_name} is not supported: the A2L ledger does not record "
            "artifact links on agent executions yet. Record artifact lineage "
            "with `r4 artifact derive --parent <id>` instead.",
            file=sys.stderr,
        )
        sys.exit(2)


def _agent_exec_create(args: Namespace, http, base: str, project_id: str | None) -> None:
    _refuse_artifact_links(args.inputs, "--input")
    body = _agent_exec_create_body(args, project_id)
    _print_json(http.post(base, json_body=body))


def _agent_exec_create_body(args: Namespace, project_id: str | None) -> dict[str, object]:
    body = {
        "agent_name": args.agent,
        "agent_version": args.version,
        "model_ref": args.model,
        "prompt_hash": args.prompt_hash,
        "params": _parse_json_option(args.params, "--params"),
    }
    if project_id:
        body["project_id"] = project_id
    return body


def _agent_exec_update(args: Namespace, http, base: str, _project_id: str | None) -> None:
    _refuse_artifact_links(args.outputs, "--output")
    body = _agent_exec_update_body(args)
    _print_json(http.request("PATCH", f"{base}/{args.exec_id}", json_body=body))


def _agent_exec_update_body(args: Namespace) -> dict[str, object]:
    body: dict[str, object] = {}
    if args.status:
        body["status"] = args.status
    if args.error:
        body["error"] = args.error
    if args.trace:
        body["execution_trace"] = _parse_json_option(args.trace, "--trace")
    return body


def _parse_json_option(value: str, flag_name: str) -> object:
    try:
        return json.loads(value) if value else {}
    except json.JSONDecodeError as exc:
        print(f"Error: {flag_name} must be valid JSON ({exc})", file=sys.stderr)
        sys.exit(2)


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))
