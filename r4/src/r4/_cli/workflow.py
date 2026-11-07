"""``r4 workflow`` command handler (Phase 2k, 2026-04-25).

Thin CLI wrapper around :class:`r4.workflows.WorkflowClient`, exposing
``r4 workflow list|trigger|upload|status|cancel`` against the Gateway
endpoints implemented in ``gateway/gateway/routes/workflows.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import CommandHandler
from .registry import command

if TYPE_CHECKING:
    from ..workflows import WorkflowClient


@command
class WorkflowCommand(CommandHandler):
    """``r4 workflow`` — UMRS workflow registry pass-through (A-1106)."""

    name = "workflow"
    help = "List, upload, and trigger UMRS workflows"

    def register(self, subparsers) -> ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help,
            description="List, upload, trigger, inspect, and cancel UMRS workflows.",
            epilog=(
                "Examples:\n"
                "  r4 workflow list\n"
                "  r4 workflow upload ./workflow.zip\n"
                "  r4 workflow export my-workflow -o ./exports/ --extract ./my-workflow-src\n"
                "  r4 workflow trigger my-workflow --param foo=bar\n"
                "  r4 workflow trigger my-workflow --params-file params.json\n"
                "  r4 workflow run ./workflow_dir --params-json '{\"threshold\": 0.8}' --wait"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub = parser.add_subparsers(
            dest="workflow_command",
            title="subcommands",
            metavar="<workflow-command>",
        )
        sub.required = True

        sub_list = sub.add_parser("list", help="List registered workflows")
        sub_list.add_argument("--scope", choices=["user", "project", "system"])
        sub_list.add_argument("--name", help="Filter by name substring")
        sub_list.add_argument(
            "--all",
            action="store_true",
            dest="all_users",
            help="List every owner's workflows (admin only; ignored otherwise)",
        )
        sub_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_trigger = sub.add_parser("trigger", help="Trigger a workflow run")
        sub_trigger.add_argument("workflow_name")
        sub_trigger.add_argument(
            "--param",
            "-p",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Workflow parameter (repeatable)",
        )
        sub_trigger.add_argument(
            "--params-json",
            metavar="JSON",
            help="Workflow input parameters as a JSON object string (as in the Studio view)",
        )
        sub_trigger.add_argument(
            "--params-file",
            metavar="FILE",
            help="Path to a JSON file with the workflow input definition",
        )
        sub_trigger.add_argument(
            "--input-file",
            action="append",
            default=[],
            help="Input file ID or S3 key (repeatable)",
        )
        sub_trigger.add_argument("--project", dest="project_id")
        sub_trigger.add_argument("--wait", action="store_true", help="Block until run finishes")
        sub_trigger.add_argument(
            "--mcp-tool",
            action="append",
            default=[],
            metavar="TOOL_NAME",
            help="MCP tool to inject into workflow context (repeatable)",
        )
        sub_trigger.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_run = sub.add_parser(
            "run",
            help="Upload a workflow definition (dir or .zip) and immediately trigger it",
        )
        sub_run.add_argument("path", help="Workflow definition directory or .zip bundle")
        sub_run.add_argument("--name", help="Override workflow name")
        sub_run.add_argument(
            "--param",
            "-p",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Workflow parameter (repeatable)",
        )
        sub_run.add_argument(
            "--params-json",
            metavar="JSON",
            help="Workflow input parameters as a JSON object string",
        )
        sub_run.add_argument(
            "--params-file",
            metavar="FILE",
            help="Path to a JSON file with the workflow input definition",
        )
        sub_run.add_argument(
            "--input-file",
            action="append",
            default=[],
            help="Input file ID or S3 key (repeatable)",
        )
        sub_run.add_argument("--project", dest="project_id")
        sub_run.add_argument("--wait", action="store_true", help="Block until run finishes")
        sub_run.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_upload = sub.add_parser("upload", help="Upload a workflow ZIP bundle")
        sub_upload.add_argument("path", help="Path to workflow.zip or workflow dir")
        sub_upload.add_argument("--name", help="Override workflow name")
        sub_upload.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_delete = sub.add_parser("delete", help="Delete a registered workflow (owner or admin)")
        sub_delete.add_argument("workflow_name")
        sub_delete.add_argument(
            "--yes",
            "-y",
            action="store_true",
            help="Skip the confirmation prompt",
        )
        sub_delete.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_status = sub.add_parser("status", help="Show workflow execution status")
        sub_status.add_argument("execution_id")
        sub_status.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_results = sub.add_parser(
            "results",
            help="Show an execution's per-step outputs (keys, extras) and where to download them",
        )
        sub_results.add_argument("execution_id")
        sub_results.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_cancel = sub.add_parser("cancel", help="Cancel a running execution")
        sub_cancel.add_argument("execution_id")

        sub_approve = sub.add_parser("approve", help="Approve a waiting manual-gate step (W5)")
        sub_approve.add_argument("execution_id")
        sub_approve.add_argument("step_name")
        sub_approve.add_argument("--approver", help="Approver identity for the audit trail")
        sub_approve.add_argument("--comment", help="Decision comment for the audit trail")

        sub_reject = sub.add_parser("reject", help="Reject a waiting manual-gate step (W5)")
        sub_reject.add_argument("execution_id")
        sub_reject.add_argument("step_name")
        sub_reject.add_argument("--comment", help="Rejection reason for the audit trail")

        sub_triggers = sub.add_parser(
            "triggers", help="List a workflow's declared event triggers (W4)"
        )
        sub_triggers.add_argument("workflow_name")
        sub_triggers.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_access = sub.add_parser(
            "access",
            help="Show or set who may run a workflow (user/project/group/system)",
        )
        sub_access.add_argument("workflow_name")
        sub_access.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        sub_access.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owner may run the workflow",
        )

        sub_export = sub.add_parser(
            "export",
            help="Download a workflow you can see (own, system, shared) as a verified zip",
        )
        sub_export.add_argument("workflow_name")
        sub_export.add_argument(
            "--output", "-o", metavar="PATH", help="File or directory (default: ./NAME.zip)"
        )
        sub_export.add_argument(
            "--force", action="store_true", help="Overwrite an existing file (and extracted files)"
        )
        sub_export.add_argument(
            "--extract", metavar="DIR", help="Also unpack the verified zip into DIR"
        )
        sub_export.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_show = sub.add_parser("show", help="Describe a workflow; --graph renders the step DAG")
        sub_show.add_argument("workflow_name")
        sub_show.add_argument(
            "--graph",
            action="store_true",
            help="Render step dependency graph as ASCII layers",
        )
        sub_show.add_argument(
            "--mermaid",
            action="store_true",
            help="With --graph: emit Mermaid flowchart instead of ASCII",
        )
        sub_show.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_watch = sub.add_parser(
            "watch", help="Live re-render an execution's step DAG until terminal"
        )
        sub_watch.add_argument("execution_id")
        sub_watch.add_argument(
            "--interval",
            type=float,
            default=2.0,
            help="Poll interval seconds (default 2)",
        )
        sub_watch.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_lint = sub.add_parser(
            "lint",
            help=(
                "Check a workflow bundle against the upload contract (description, every "
                "step's depends_on), or lint a knowledge-indexing draft JSON file"
            ),
        )
        sub_lint.add_argument(
            "file", help="Bundle directory, its workflow.json, or a workflow draft JSON file"
        )
        sub_lint.add_argument(
            "--server",
            action="store_true",
            default=False,
            help="Merge with server-side diagnostics",
        )
        sub_lint.add_argument("--json", "-j", action="store_true", dest="as_json")

        sub_def = sub.add_parser(
            "definition",
            help="Persisted run templates: save once, replay exactly (no LLM needed)",
        )
        def_sub = sub_def.add_subparsers(
            dest="definition_command", title="actions", metavar="<action>"
        )
        def_sub.required = True

        d_save = def_sub.add_parser("save", help="Save/update a named run definition")
        d_save.add_argument("name", help="Run definition name")
        d_save.add_argument("--workflow", required=True, help="Registered workflow name")
        d_save.add_argument("--param", "-p", action="append", default=[], metavar="KEY=VALUE")
        d_save.add_argument("--params-json", metavar="JSON")
        d_save.add_argument("--params-file", metavar="FILE")
        d_save.add_argument("--description", default="")
        d_save.add_argument("--json", "-j", action="store_true", dest="as_json")

        d_list = def_sub.add_parser("list", help="List run definitions (own; all if admin)")
        d_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        d_show = def_sub.add_parser("show", help="Show one run definition")
        d_show.add_argument("name")
        d_show.add_argument("--json", "-j", action="store_true", dest="as_json")

        d_delete = def_sub.add_parser("delete", help="Delete a run definition")
        d_delete.add_argument("name")
        d_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

        d_run = def_sub.add_parser("run", help="Replay a run definition (exact params)")
        d_run.add_argument("name")
        d_run.add_argument("--wait", action="store_true", help="Block until run finishes")
        d_run.add_argument("--json", "-j", action="store_true", dest="as_json")

        return parser

    def handle(self, args: Namespace) -> int:
        sub = getattr(args, "workflow_command", None)
        if not sub:
            print(
                "Usage: r4 workflow "
                "<list|show|triggers|access|export|trigger|run|upload|delete|definition|status|watch|approve|reject|cancel|lint>",
                file=sys.stderr,
            )
            return 1

        from ._helpers import _preflight_check

        _preflight_check(action="workflow")

        from ..client import Client

        client = Client()
        wf = client.workflows

        handler = _subcommands().get(sub)
        if handler is None:
            print(f"Unknown workflow subcommand: {sub}", file=sys.stderr)
            return 1
        return handler(wf, args)


def _subcommands() -> dict:
    """Dispatch table (built lazily so forward defs resolve at call time)."""
    return {
        "list": _cmd_list,
        "trigger": _cmd_trigger,
        "run": _cmd_run,
        "upload": _cmd_upload,
        "delete": _cmd_delete,
        "status": _cmd_status,
        "results": _cmd_results,
        "cancel": _cmd_cancel,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "triggers": _cmd_triggers,
        "access": _cmd_access,
        "export": _cmd_export,
        "show": _cmd_show,
        "watch": _cmd_watch,
        "lint": lambda _wf, args: _cmd_lint(args),
        "definition": _cmd_definition,
    }


def _cmd_definition(wf, args: Namespace) -> int:
    """Dispatch for the nested `definition` subcommand group."""
    action = getattr(args, "definition_command", None)
    handlers = {
        "save": _cmd_def_save,
        "list": _cmd_def_list,
        "show": _cmd_def_show,
        "delete": _cmd_def_delete,
        "run": _cmd_def_run,
    }
    handler = handlers.get(action or "")
    if handler is None:
        print("Usage: r4 workflow definition <save|list|show|delete|run>", file=sys.stderr)
        return 1
    return handler(wf, args)


def _cmd_def_save(wf, args: Namespace) -> int:
    params, err = _parse_parameters(args)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    try:
        data = wf.save_run_definition(
            args.name,
            args.workflow,
            params=params,
            description=getattr(args, "description", "") or "",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    print(f"run definition '{args.name}' saved (workflow={args.workflow})")
    return 0


def _cmd_def_list(wf, args: Namespace) -> int:
    try:
        data = wf.list_run_definitions()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    items = (data or {}).get("definitions", [])
    if args.as_json:
        print(json.dumps(items, indent=2, default=str))
        return 0
    if not items:
        print("No run definitions.")
        return 0
    print(f"{'NAME':<28} {'WORKFLOW':<28} UPDATED")
    print("-" * 90)
    for d in items:
        print(
            f"{(d.get('name') or '')[:28]:<28} "
            f"{(d.get('workflow_name') or '')[:28]:<28} "
            f"{d.get('updated_at') or ''}"
        )
    return 0


def _cmd_def_show(wf, args: Namespace) -> int:
    try:
        data = wf.get_run_definition(args.name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    definition = (data or {}).get("definition", {})
    if args.as_json:
        print(json.dumps(definition, indent=2, default=str))
        return 0
    print(json.dumps(definition, indent=2, default=str))
    return 0


def _cmd_def_delete(wf, args: Namespace) -> int:
    if not args.yes:
        answer = input(f"Delete run definition '{args.name}'? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 0
    try:
        data = wf.delete_run_definition(args.name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(data.get("message", f"run definition '{args.name}' deleted"))
    return 0


def _cmd_def_run(wf, args: Namespace) -> int:
    try:
        result = wf.trigger_run_definition(args.name, wait=bool(getattr(args, "wait", False)))
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(_status_to_dict(result), indent=2, default=str))
        return 0
    print(f"execution accepted: {getattr(result, 'execution_id', '') or '(unknown)'}")
    state = getattr(result, "status", None) or "submitted"
    print(f"status: {state}")
    return 0


def _cmd_delete(wf, args: Namespace) -> int:
    """Delete a registered workflow after confirmation."""
    if not args.yes:
        answer = input(f"Delete workflow '{args.workflow_name}'? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 0
    try:
        data = wf.delete(args.workflow_name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    print(data.get("message", f"workflow '{args.workflow_name}' deleted"))
    return 0


def _cmd_approve(wf, args: Namespace) -> int:
    return _submit_gate_decision(wf, args, approved=True)


def _cmd_reject(wf, args: Namespace) -> int:
    return _submit_gate_decision(wf, args, approved=False)


def _submit_gate_decision(wf, args: Namespace, *, approved: bool) -> int:
    """Submit an approve/reject decision for a waiting gate step (W5)."""
    try:
        status_obj = wf.approve_gate(
            args.execution_id,
            args.step_name,
            approved=approved,
            approver=getattr(args, "approver", None),
            comment=getattr(args, "comment", None),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    verdict = "approved" if approved else "rejected"
    print(f"gate {verdict}: {args.step_name} (execution {args.execution_id})")
    state = getattr(status_obj, "status", None) or "unknown"
    print(f"execution status: {state}")
    return 0


def _cmd_triggers(wf, args: Namespace) -> int:
    try:
        data = wf.list_workflow_triggers(args.workflow_name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    triggers = data.get("triggers") or []
    if not triggers:
        print(f"{args.workflow_name}: no triggers declared")
        return 0
    print(f"{args.workflow_name}: {len(triggers)} trigger(s) declared")
    for index, trig in enumerate(triggers, 1):
        matchers = ", ".join(f"{k}={v}" for k, v in trig.items() if k != "on") or "(any)"
        print(f"  {index}. on={trig.get('on', '?')} {matchers}")
    return 0


def _cmd_access(wf, args: Namespace) -> int:
    """Show or replace a workflow's ADR-080 access list (via the parent Client)."""
    client = wf._parent
    try:
        if args.private or args.access_set:
            data = client.set_workflow_access(
                args.workflow_name, [] if args.private else args.access_set
            )
        else:
            data = client.get_workflow_access(args.workflow_name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    scopes = data.get("access") or []
    owner = data.get("owner") or "?"
    print(f"{args.workflow_name}  owner: {owner}")
    print("  access: " + (", ".join(scopes) if scopes else "owner only"))
    return 0


def _cmd_export(wf: WorkflowClient, args: Namespace) -> int:
    """Download (and optionally unpack) a workflow export zip."""
    from ..workflows import extract_export, read_export_manifest

    try:
        path = wf.export(args.workflow_name, args.output, overwrite=args.force)
        manifest = read_export_manifest(path)
        extracted = (
            extract_export(path, args.extract, overwrite=args.force) if args.extract else None
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    summary = {
        "workflow": args.workflow_name,
        "path": str(path),
        "files": len(manifest.get("files") or []),
        "excluded": len(manifest.get("excluded") or []),
        "extracted_to": str(extracted) if extracted else None,
    }
    if args.as_json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"exported {args.workflow_name} -> {path}")
    print(f"  files: {summary['files']}  excluded: {summary['excluded']}  (images not included)")
    if extracted:
        print(f"  extracted to: {extracted}")
    return 0


def _cmd_show(wf, args: Namespace) -> int:
    try:
        desc = wf.describe(args.workflow_name)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(desc, indent=2, default=str))
        return 0

    steps = desc.get("steps") or []

    if args.graph or args.mermaid:
        from ..workflow_graph import WorkflowGraphError, render_ascii, render_mermaid

        try:
            if args.mermaid:
                print(render_mermaid(steps, title=args.workflow_name))
            else:
                print(render_ascii(steps))
        except WorkflowGraphError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    shared = _shared_with_caller(wf, desc.get("name", args.workflow_name), desc.get("user_id"))
    _print_description(desc, args.workflow_name, len(steps), shared=shared)
    return 0


def _shared_with_caller(wf: WorkflowClient, name: str, owner: object) -> bool:
    """Whether the caller's catalog lists ``name`` from ``owner`` as shared.

    The describe body carries no shared flag; the list does (UMRS marks rows
    another user shared with the caller). Both name and owner must match, and
    a list error only leaves the label as the owner's scope.
    """
    if not name or not owner:
        return False
    try:
        rows = wf.list_workflows()
    except Exception:  # noqa: BLE001 - the label only; show still prints
        return False
    return any(
        getattr(row, "shared", False) and row.name == name and getattr(row, "owner", "") == owner
        for row in rows
    )


def _print_description(
    desc: dict, fallback_name: str, step_count: int, *, shared: bool = False
) -> None:
    """Human-readable describe payload (non-graph branch of ``show``)."""
    print(f"name        : {_described_name(desc, fallback_name)}")
    print(f"version     : {desc.get('version', '-')}")
    scope = f"shared · {desc['user_id']}" if shared else desc.get("scope", "-")
    print(f"scope       : {scope}")
    if desc.get("user_id"):
        print(f"owner       : {desc['user_id']}")
    description = desc.get("description", "")
    if description:
        print(f"description : {description}")
    print(f"steps       : {step_count} (use --graph to visualise)")
    rows = _schema_parameter_rows(desc.get("parameter_schema") or {})
    if rows:
        print("parameters  :")
        for pname, default, required in rows:
            flag = " (required)" if required else ""
            print(f"  - {pname:<24} default={default}{flag}")


def _described_name(desc: dict[str, Any], fallback_name: str) -> str:
    """UMRS describe answers ``workflow_name`` (``name`` on older servers)."""
    return desc.get("workflow_name") or desc.get("name") or fallback_name


def _schema_parameter_rows(schema: dict[str, Any]) -> list[tuple[str, object, bool]]:
    """(name, default, required) per parameter of a describe ``parameter_schema``.

    The schema is JSON Schema (``properties`` + ``required``); the loop used to
    walk its top-level keys and printed ``type`` / ``properties`` as parameter
    names (ADR-105 S1). A legacy flat ``{name: spec}`` map still prints.
    """
    if not isinstance(schema, dict):
        return []
    is_json_schema = "properties" in schema or schema.get("type") == "object"
    props = schema.get("properties", {}) if is_json_schema else schema
    required = set(schema.get("required") or []) if is_json_schema else set()
    if not isinstance(props, dict):
        return []
    return [
        (name, spec.get("default", "-") if isinstance(spec, dict) else "-", name in required)
        for name, spec in props.items()
    ]


def _parse_kv_pairs(raw: list[str]) -> dict:
    out: dict = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def _parse_parameters(args: Namespace) -> tuple[dict, str | None]:
    """Merge a JSON parameter object with --param KEY=VALUE pairs.

    The JSON object (--params-json / --params-file) is loaded first — this is
    the same ``parameters`` object the Studio view sends — then any
    ``--param KEY=VALUE`` overrides are applied on top.

    Returns ``(params, error)``; ``error`` is a user-facing message or None.
    """
    params: dict = {}
    raw = getattr(args, "params_json", None)
    fpath = getattr(args, "params_file", None)
    if raw and fpath:
        return {}, "Use only one of --params-json and --params-file."
    if fpath:
        raw, err = _read_params_file(fpath)
        if err is not None:
            return {}, err
    if raw:
        obj, err = _load_params_json_object(raw, "--params-json")
        if err is not None:
            return {}, err
        params.update(obj)
    try:
        params.update(_parse_kv_pairs(getattr(args, "param", []) or []))
    except ValueError as exc:
        return {}, str(exc)
    return params, None


def _read_params_file(fpath) -> tuple[str | None, str | None]:
    """Read a --params-file path; (text, error)."""
    try:
        return Path(fpath).expanduser().read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"Could not read --params-file {fpath}: {exc}"


def _load_params_json_object(raw: str, source: str) -> tuple[dict | None, str | None]:
    """Parse the --params-json/-file object; (obj, error)."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{source} is not valid JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "--params-json / --params-file must contain a JSON object."
    return obj, None


def _cmd_list(wf, args: Namespace) -> int:
    items = _filter_wfs(
        wf.list_workflows(all_users=getattr(args, "all_users", False)),
        getattr(args, "name", None),
    )

    if args.as_json:
        print(json.dumps([_workflow_to_dict(w) for w in items], indent=2))
        return 0
    if not items:
        print("No workflows registered.")
        return 0
    print(f"{'NAME':<32} {'DESCRIPTION':<48} TAGS")
    print("-" * 100)
    for row in (_wf_row(w) for w in items):
        print(row)
    return 0


def _filter_wfs(items: list, needle: str | None) -> list:
    if not needle:
        return items
    needle = needle.lower()
    return [w for w in items if needle in (getattr(w, "name", "") or "").lower()]


def _wf_row(w) -> str:
    name = (getattr(w, "name", "") or "")[:32]
    desc = (getattr(w, "description", "") or "")[:48]
    tags = ", ".join(getattr(w, "tags", []) or [])
    return f"{name:<32} {desc:<48} {tags}"


def _cmd_trigger(wf, args: Namespace) -> int:
    params, err = _parse_parameters(args)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    mcp_tools = getattr(args, "mcp_tool", []) or []
    if mcp_tools:
        params["mcp_tools"] = mcp_tools

    wait = bool(getattr(args, "wait", False))

    try:
        result = wf.trigger(
            workflow_id=args.workflow_name,
            parameters=params,
            input_files=args.input_file or None,
            project_id=getattr(args, "project_id", None),
            wait=wait,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(_status_to_dict(result), indent=2, default=str))
        return 0

    print(f"execution_id : {getattr(result, 'execution_id', '-')}")
    print(f"status       : {getattr(result, 'status', '-')}")
    if getattr(result, "error", None):
        print(f"error        : {result.error}")
        return 1
    return 0


def _cmd_run(wf, args: Namespace) -> int:
    """Upload a workflow definition (dir/.zip), then trigger it immediately.

    This is the CLI counterpart (and superset) of the Studio view's
    runWorkflow: the view requires a pre-registered name, while ``run`` also
    packs a local definition into a bundle first.
    """
    params, err = _parse_parameters(args)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    workflow_id, err = _run_upload(wf, args)
    if err is not None:
        print(f"upload failed : {err}", file=sys.stderr)
        return 1

    print(f"upload accepted: {workflow_id}")

    try:
        result = wf.trigger(
            workflow_id=workflow_id,
            parameters=params,
            input_files=args.input_file or None,
            project_id=getattr(args, "project_id", None),
            wait=bool(getattr(args, "wait", False)),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error (trigger): {exc}", file=sys.stderr)
        return 1

    return _print_trigger_summary(result, args)


def _run_upload(wf, args: Namespace) -> tuple[str | None, str | None]:
    """Upload the definition for ``run``; (workflow_id, error)."""
    try:
        result = wf.upload(workflow_path=args.path, workflow_name=args.name)
    except Exception as exc:  # noqa: BLE001
        return None, f"Error (upload): {exc}"

    upload_error = getattr(result, "error", None)
    if upload_error or not getattr(result, "accepted", False):
        detail = upload_error or getattr(result, "message", None) or "not accepted"
        return None, str(detail)

    workflow_id = getattr(result, "workflow_id", None) or args.name
    if not workflow_id:
        return None, "upload accepted but no workflow name resolved; pass --name explicitly."
    return workflow_id, None


def _print_trigger_summary(result, args: Namespace) -> int:
    """Shared plain/JSON summary for trigger-family command results."""
    if args.as_json:
        print(json.dumps(_status_to_dict(result), indent=2, default=str))
        return 0

    print(f"execution_id : {getattr(result, 'execution_id', '-')}")
    print(f"status       : {getattr(result, 'status', '-')}")
    if getattr(result, "error", None):
        print(f"error        : {result.error}")
        return 1
    return 0


def _cmd_upload(wf, args: Namespace) -> int:
    # F-001: SDK uses ``workflow_path`` (not ``path``).
    result = wf.upload(workflow_path=args.path, workflow_name=args.name)
    if args.as_json:
        print(json.dumps(_status_to_dict(result), indent=2, default=str))
        return 0
    print(f"workflow_id : {getattr(result, 'workflow_id', '-')}")
    print(f"status      : {getattr(result, 'status', '-')}")
    if getattr(result, "message", None):
        print(f"message     : {result.message}")
    if getattr(result, "error", None):
        print(f"error       : {result.error}")
        return 1
    return 0 if getattr(result, "accepted", False) else 1


_TERMINAL_STATES = {"completed", "succeeded", "failed", "cancelled", "error"}


def _cmd_watch(wf, args: Namespace) -> int:
    """Poll the execution; re-render status + step DAG until terminal."""
    import time

    interval = max(0.5, float(getattr(args, "interval", 2.0) or 2.0))
    is_tty = sys.stdout.isatty()
    try:
        while True:
            detail = wf.get_execution_detail(args.execution_id)
            frame = _watch_frame(wf, detail, args.execution_id)
            if is_tty:
                print("\033[2J\033[H", end="")
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
            if str(detail.get("status", "")).lower() in _TERMINAL_STATES:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nwatch stopped.", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return _watch_exit_code(detail)


def _watch_frame(wf, detail: dict, execution_id: str) -> str:
    """Render one watch frame: summary line + step DAG with statuses."""
    lines = [
        f"watch {execution_id}",
        f"status: {detail.get('status', 'unknown')}  "
        f"workflow: {detail.get('workflow_name') or '-'}",
    ]
    steps = _execution_step_defs(wf, detail)
    if steps:
        from ..workflow_graph import render_ascii

        statuses = _step_status_map(detail)
        lines.append(render_ascii(steps, statuses=statuses, show_action=False))
    else:
        progress = detail.get("progress")
        if progress is not None:
            lines.append(f"progress: {float(progress) * 100:.1f}%")
    return "\n".join(lines)


def _execution_step_defs(wf, detail: dict) -> list:
    """Step definitions from the workflow describe (best effort)."""
    name = detail.get("workflow_name")
    if not name:
        return []
    try:
        desc = wf.describe(name)
    except Exception:  # noqa: BLE001 — describe may 404 for ad-hoc runs
        return []
    return desc.get("steps") or []


def _step_status_map(detail: dict) -> dict:
    """Map step-id -> status from execution results payload."""
    results = detail.get("results") or {}
    steps = results.get("steps") or {}
    out: dict = {}
    for sid, info in steps.items():
        if isinstance(info, dict):
            out[str(sid)] = str(info.get("status", "pending"))
    return out


def _watch_exit_code(detail: dict) -> int:
    status = str(detail.get("status", "")).lower()
    return 0 if status in {"completed", "succeeded"} else 1


def _cmd_status(wf, args: Namespace) -> int:
    result = wf.status(args.execution_id)
    if args.as_json:
        print(json.dumps(_status_to_dict(result), indent=2, default=str))
        return 0
    print(f"execution_id : {getattr(result, 'execution_id', '-')}")
    print(f"status       : {getattr(result, 'status', '-')}")
    progress = getattr(result, "progress", None)
    if progress is not None:
        print(f"progress     : {progress * 100:.1f}%")
    return 0


def _cmd_cancel(wf, args: Namespace) -> int:
    ok = wf.cancel(args.execution_id)
    if ok:
        print(f"Cancelled {args.execution_id}")
        return 0
    print(f"Failed to cancel {args.execution_id}", file=sys.stderr)
    return 1


def _cmd_lint(args: Namespace) -> int:
    """Lint a workflow draft JSON file (client-side, optionally merged with server)."""
    from ..workflow_lint import (
        format_diagnostics,
        lint_json_file,
        merge_client_server_diagnostics,
    )

    filepath = args.file
    use_server = getattr(args, "server", False)

    # 1. Client-side lint
    client_diag = lint_json_file(filepath)
    if use_server:
        # 2. Server-side lint — call UMRS lint endpoint if available
        try:
            from ..client import Client

            client = Client()
            server_diag = client.workflows.lint_file(filepath)
        except Exception:  # noqa: BLE001
            # Server lint not available — emit warning, proceed with client only
            print(
                "Warning: server-side lint unavailable, showing client diagnostics only.",
                file=sys.stderr,
            )
            server_diag = []
        merged = merge_client_server_diagnostics(client_diag, server_diag)
        output = format_diagnostics(merged)
    else:
        merged = client_diag
        output = format_diagnostics(client_diag)

    # JSON output mode
    if getattr(args, "as_json", False):
        print(json.dumps([d.to_dict() for d in merged], indent=2))
        return 0

    print(output)
    return 1 if any(d.severity == "error" for d in merged) else 0


def _workflow_to_dict(w) -> dict:
    return {
        "workflow_id": getattr(w, "workflow_id", None),
        "name": getattr(w, "name", None),
        "description": getattr(w, "description", None),
        "parameters": getattr(w, "parameters", []),
        "tags": getattr(w, "tags", []),
        **_ownership_fields(w),
    }


def _ownership_fields(w: object) -> dict[str, object]:
    """Who owns a catalog row and whether it is shared with the caller
    (2026-10-23): the tags show the label, these keep the raw fields."""
    return {
        "scope": getattr(w, "scope", "") or "",
        "owner": getattr(w, "owner", "") or "",
        "shared": getattr(w, "shared", False) is True,
        "access": list(getattr(w, "access", None) or []),
    }


def _cmd_results(wf, args: Namespace) -> int:
    """Per-step outputs of an execution (GET /gw/workflows/executions/<id>/results)."""
    result = wf.get_results(args.execution_id)
    execution_id, steps, artifacts = _results_view(result, args.execution_id)
    if args.as_json:
        payload = {
            "execution_id": execution_id,
            "status": getattr(result, "status", None),
            "error": getattr(result, "error", None),
            "steps": steps,
            "artifacts": artifacts,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"execution_id : {execution_id}")
    print(f"status       : {getattr(result, 'status', '-')}")
    if getattr(result, "error", None):
        print(f"error        : {result.error}")
    for name, step in steps.items():
        _print_step_outputs(name, step if isinstance(step, dict) else {})
    _print_download_hint(execution_id, artifacts)
    return 0


def _results_view(result, fallback_id: str) -> tuple[str, dict, list]:
    """(execution id, per-step outputs, artifact refs) of a WorkflowResult."""
    execution_id = getattr(result, "execution_id", None) or fallback_id
    outputs = getattr(result, "outputs", None)
    steps = outputs.get("steps") if isinstance(outputs, dict) else None
    return execution_id, steps or {}, list(getattr(result, "artifacts", None) or [])


def _print_step_outputs(name: str, step: dict) -> None:
    keys = step.get("output_keys") or []
    # Cluster paths (slurm_job.workdir) stay out of the human summary.
    extra = {k: v for k, v in (step.get("extra") or {}).items() if k != "slurm_job"}
    print(f"\n[{name}] {len(keys)} output key(s)")
    for key in keys[:20]:
        print(f"  {key}")
    if len(keys) > 20:
        print(f"  ... {len(keys) - 20} more (use --json)")
    if extra:
        text = json.dumps(extra, default=str)
        print(f"  extra: {text[:400]}{'…' if len(text) > 400 else ''}")


def _print_download_hint(execution_id: str, artifacts: list) -> None:
    if not artifacts:
        return
    bucket = str(artifacts[0]).split("/", 1)[0]
    print(f"\nfiles: {len(artifacts)} artifact(s) in {bucket}")
    print(
        f"  download all: r4 files download runs/{execution_id}/ --bucket {bucket} "
        "--recursive -o results/"
    )


def _status_to_dict(s) -> dict:
    keys = (
        "execution_id",
        "workflow_id",
        "status",
        "started_at",
        "finished_at",
        "error",
        "progress",
        "accepted",
        "message",
        "source_path",
    )
    return {k: getattr(s, k, None) for k in keys if hasattr(s, k)}
