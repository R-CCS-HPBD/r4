"""``r4 project`` command handler (Phase 2m, 2026-04-28).

Owns the argparse subparser tree. Handler logic still lives in
:func:`r4.cli.cmd_project` for now.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import _print_active_context
from .base import CommandHandler
from .project_data_class import add_data_class_parser, project_data_class
from .project_knowledge import (
    project_approve,
    project_ask,
    project_ask_policy,
    project_emit_workflow,
    project_export,
    project_findings,
    project_import,
    project_init,
    project_lineage,
    project_loop,
    project_plan,
    project_readiness,
    project_sessions,
    project_status,
    project_transition,
    project_verify,
    project_verify_job,
)
from .project_storage import add_storage_parser, project_storage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..project import ProjectConfig, ProjectManager
from .registry import command


def _deprecated_handler(name: str, new_name: str, handler):
    """Return a wrapper that prints deprecation warning then delegates to handler."""

    def wrapper(*args, **kwargs):
        print(
            f"WARNING: `r4 project {name}` is deprecated. Use `r4 project {new_name}` instead.",
            file=sys.stderr,
        )
        return handler(*args, **kwargs)

    return wrapper


@command
class ProjectCommand(CommandHandler):
    name = "project"
    help = "Project management"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description="Manage the local project registry and choose the active project context.",
            epilog=(
                "Examples:\n"
                "  r4 project list\n"
                '  r4 project create demo --name "Demo Project"\n'
                "  r4 project switch demo\n"
                "  r4 project import ./papers ./code\n"
                "  r4 project init\n"
                '  r4 project verify "Explain my project"\n'
                '  r4 project plan "What research should we perform next?"\n'
                '  r4 project ask "Literature on a topic"\n'
                "  r4 project loop start --approve\n"
                "  r4 project data-class get\n"
                "\n"
                "Note: <project-id> is optional on all subcommands. When omitted, "
                "the command uses the project set by 'r4 project switch'."
            ),
            formatter_class=RawDescriptionHelpFormatter,
        )
        sub = p.add_subparsers(
            dest="project_command",
            title="subcommands",
            metavar="<project-command>",
        )
        sub.required = True

        p_list = sub.add_parser("list", help="List projects")
        p_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )
        project_create = sub.add_parser("create", help="Create project")
        project_create.add_argument("project_id", help="Project ID")
        project_create.add_argument("--name", "-n", help="Project name")
        project_create.add_argument("--gateway", "-g", help="Gateway URL")
        project_create.add_argument("--model", "-m", help="Default model")
        project_create.add_argument(
            "--security-level",
            choices=["standard", "high"],
            help=(
                "Project-wide security level. 'high' enforces secure mode "
                "for all chat sessions (equivalent to --secure)."
            ),
        )

        project_register = sub.add_parser(
            "register",
            help="Register a project on the platform (server): loops, jobs and knowledge need it",
            description=(
                "Create the project on the platform this profile is connected to "
                "(POST /gw/projects). Any signed-in user may; you become its owner. "
                "Safe to repeat. `r4 project create` alone keeps a project on this machine."
            ),
        )
        project_register.add_argument("project_id", help="Project ID")
        project_register.add_argument("--name", "-n", default="", help="Project name")

        project_switch = sub.add_parser("switch", help="Switch to project")
        project_switch.add_argument("project_id", help="Project ID")

        sub.add_parser("current", help="Show current project")

        project_delete = sub.add_parser("delete", help="Delete project")
        project_delete.add_argument("project_id", help="Project ID")

        project_import_parser = sub.add_parser(
            "import",
            help="Ingest papers, code, notebooks, and data into the knowledge store",
        )
        project_import_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_import_parser.add_argument(
            "paths",
            nargs="+",
            help="Directories to ingest",
        )
        project_import_parser.add_argument(
            "--local",
            action="store_true",
            dest="local",
            help=(
                "Legacy in-process simulation mode (deprecated, V-02): runs the "
                "in-memory store locally instead of the durable UMRS ingest chain"
            ),
        )
        project_import_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_init_parser = sub.add_parser(
            "init",
            help="Extract structured knowledge and build the knowledge graph",
        )
        project_init_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_init_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        # Deprecated aliases (route to same handlers, print warning via _deprecated_handler)
        ingest_sub = sub.add_parser(
            "ingest",
            help="[DEPRECATED] Use 'import' instead",
        )
        ingest_sub.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        ingest_sub.add_argument(
            "paths",
            nargs="+",
            help="Directories to ingest (deprecated)",
        )
        ingest_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )
        understand_sub = sub.add_parser(
            "understand",
            help="[DEPRECATED] Use 'init' instead",
        )
        understand_sub.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        understand_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_verify_parser = sub.add_parser(
            "verify",
            help="Answer a knowledge verification question with evidence",
        )
        project_verify_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_verify_parser.add_argument("question", help="Verification question")
        project_verify_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_verify_job_parser = sub.add_parser(
            "verify_job",
            help="Submit a knowledge verification durable job (k2 layer)",
        )
        project_verify_job_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_verify_job_parser.add_argument(
            "--verifier-names",
            nargs="*",
            dest="verifier_names",
            help="Specific verifier names to run",
        )
        project_verify_job_parser.add_argument(
            "--detach",
            "-d",
            action="store_true",
            help="Do not wait for job completion",
        )
        project_verify_job_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        transition_parser = sub.add_parser(
            "transition",
            help="Transition a finding status (acknowledged, resolved, waived)",
        )
        transition_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        transition_parser.add_argument("finding_key", help="Finding identifier")
        transition_parser.add_argument(
            "--from-status",
            dest="from_status",
            default="open",
            help="Current status (default: open)",
        )
        transition_parser.add_argument(
            "new_status",
            choices=["acknowledged", "resolved", "waived"],
            help="New status",
        )
        transition_parser.add_argument(
            "--evidence",
            "-e",
            default="",
            help="Evidence for the transition",
        )
        transition_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_readiness_parser = sub.add_parser(
            "readiness",
            help="Generate a research readiness report",
        )
        project_readiness_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_readiness_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_ask_parser = sub.add_parser(
            "ask",
            help="Ask a question about the project's knowledge (with citations)",
        )
        project_ask_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_ask_parser.add_argument("question", help="Question to ask")
        project_ask_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        ask_policy_parser = sub.add_parser(
            "ask_policy",
            aliases=["ask-policy"],
            help=(
                "Show or set when research loops pause for the scientist "
                "(maturity stage preset or explicit profile; D-33)"
            ),
        )
        ask_policy_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        ask_group = ask_policy_parser.add_mutually_exclusive_group()
        ask_group.add_argument(
            "--stage",
            choices=["bootstrapped", "learning", "competent", "autonomous"],
            help="Maturity-stage preset (pauses fall as the catalog matures)",
        )
        ask_group.add_argument(
            "--mode", choices=["auto", "ask", "profile"], help="Explicit permission mode"
        )
        ask_group.add_argument("--clear", action="store_true", help="Remove the project default")
        ask_policy_parser.add_argument(
            "--triggers",
            default="",
            metavar="A,B",
            help=(
                "With --mode profile: comma-separated trigger classes (cost, "
                "competing_hypotheses, direction_change, physical_action)"
            ),
        )
        ask_policy_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        add_storage_parser(sub)
        add_data_class_parser(sub)

        project_lineage_parser = sub.add_parser(
            "lineage",
            help="Query lineage for a knowledge artifact (A2L links)",
        )
        project_lineage_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_lineage_parser.add_argument("artifact_id", help="Artifact ID to query lineage for")
        project_lineage_parser.add_argument(
            "--depth",
            "-d",
            type=int,
            default=3,
            help="Max chain depth (default: 3)",
        )
        project_lineage_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_plan_parser = sub.add_parser(
            "plan",
            help="Generate a graph-grounded research plan",
        )
        project_plan_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_plan_parser.add_argument("research_goal", help="Research goal")
        project_plan_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )
        project_plan_parser.add_argument(
            "--emit-workflow",
            metavar="FILE",
            dest="emit_workflow",
            help="Emit workflow draft to FILE (yaml)",
        )
        project_plan_parser.add_argument(
            "--plan-file",
            metavar="PLAN",
            help="Path to plan JSON file (skip generation)",
        )

        emit_sub = sub.add_parser(
            "emit_workflow",
            help="Emit a workflow draft from a research plan",
        )
        emit_sub.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        emit_sub.add_argument(
            "--plan-file",
            metavar="PLAN",
            help="Path to plan JSON file (skip generation)",
        )
        emit_sub.add_argument(
            "--emit-workflow",
            metavar="FILE",
            dest="emit_workflow",
            help="Output workflow draft YAML file",
        )
        emit_sub.add_argument("--model", "-m", help="Model name for project context")
        emit_sub.add_argument("--partition", help="Slurm partition")
        emit_sub.add_argument("--account", help="Slurm account")
        emit_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        status_sub = sub.add_parser(
            "status",
            help="Show project status: artifacts, extraction, verifications",
        )
        status_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )
        status_sub.add_argument(
            "--watch",
            "-w",
            action="store_true",
            dest="watch",
            help="Poll continuously (5s interval)",
        )
        status_sub.add_argument(
            "--interval",
            "-i",
            type=float,
            default=5.0,
            help="Poll interval in seconds (default: 5)",
        )

        loop_parser = sub.add_parser(
            "loop",
            help=(
                "Control server-side autonomous research loops "
                "(start/status/stop/pause/resume; approvals/decide for ask-mode gates)"
            ),
        )
        loop_parser.add_argument(
            "loop_action",
            choices=[
                "start",
                "status",
                "list",
                "iterations",
                "watch",
                "stop",
                "pause",
                "resume",
                "approvals",
                "decide",
                "replay",
            ],
            help="Loop action",
        )
        loop_parser.add_argument(
            "target",
            nargs="?",
            default=None,
            help="Project ID for start; approval id for decide; loop_id otherwise",
        )
        loop_parser.add_argument(
            "--decision",
            choices=["once", "always", "no"],
            default=None,
            help="Decision for `decide` (once | always | no)",
        )
        loop_parser.add_argument(
            "goal",
            nargs="?",
            default=None,
            help="Research goal (for start; defaults to saved plan goal)",
        )
        loop_parser.add_argument(
            "--max-iterations",
            type=int,
            default=10,
            help="Maximum loop iterations (default: 10)",
        )
        loop_parser.add_argument(
            "--runtime",
            choices=["umrs", "local", "hybrid"],
            default=None,
            help="Research runtime: umrs (default), local (SQLite, offline), or hybrid",
        )
        loop_parser.add_argument(
            "--requires",
            action="append",
            default=None,
            metavar="KEY=[VAL]",
            help="Resource requirement hint for venue routing (repeatable, e.g. gpu=1)",
        )
        loop_parser.add_argument(
            "--session",
            default="",
            metavar="SESSION_ID",
            help="Durable research session to attach the loop to (ADR-025)",
        )
        loop_parser.add_argument(
            "--approve",
            action="store_true",
            help="Certify readiness approval is in place before start",
        )
        loop_parser.add_argument(
            "--workflow",
            default="",
            metavar="NAME",
            help=(
                "Registered UMRS workflow every experiment runs (for start). "
                "Omit when exactly one workflow is registered — UMRS uses it; "
                "discover names with `r4 workflow list`"
            ),
        )
        loop_parser.add_argument(
            "--tracks",
            type=int,
            default=1,
            choices=range(1, 5),
            metavar="N",
            help=(
                "Pursue up to N hypotheses in parallel tracks (for start; 1–4, "
                "UMRS engine): a question pending on one track never idles the "
                "others, and a rejected track closes without failing the loop"
            ),
        )
        loop_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )
        loop_parser.add_argument(
            "--interval",
            "-i",
            type=float,
            default=5.0,
            help="Watch poll interval seconds (default: 5)",
        )

        sessions_parser = sub.add_parser(
            "sessions",
            help="List or inspect durable research sessions (ADR-025)",
        )
        sessions_parser.add_argument(
            "session_action",
            choices=["list", "show"],
            help="Sessions action (default: list)",
        )
        sessions_parser.add_argument(
            "target",
            nargs="?",
            default=None,
            help="Project ID (list) or session ID (show)",
        )
        sessions_parser.add_argument(
            "--status",
            choices=["active", "paused", "completed", "archived"],
            default=None,
            help="Filter sessions by lifecycle status",
        )
        sessions_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        findings_sub = sub.add_parser(
            "findings",
            help="List verification findings with severity and status",
        )
        findings_sub.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        findings_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        project_approve_parser = sub.add_parser(
            "approve",
            help="Approve a project for autonomous research",
        )
        project_approve_parser.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        project_approve_parser.add_argument(
            "--force",
            action="store_true",
            help="Approve despite audited findings (override)",
        )
        project_approve_parser.add_argument(
            "--reason",
            default="",
            help="Why the project is approved (recorded with the approval)",
        )
        project_approve_parser.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )

        export_sub = sub.add_parser(
            "export",
            help="Export project knowledge bundle (.kb format)",
        )
        export_sub.add_argument(
            "--detach", "-d", action="store_true", help="Run export in background"
        )
        export_sub.add_argument(
            "--json", "-j", action="store_true", dest="json", help="Output as JSON"
        )
        export_sub.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero on partial export (some artifacts not bundled)",
        )
        export_sub.add_argument(
            "--output",
            "-o",
            type=Path,
            default=None,
            help="Write .kb bundle to path (defaults to current dir)",
        )
        export_sub.add_argument(
            "--format",
            "-f",
            default="kb",
            choices=["kb", "raw"],
            help="Output format (kb: server-side bundle, raw: client-side fallback)",
        )
        export_sub.add_argument(
            "--with-raw",
            action="store_true",
            dest="with_raw",
            help="Also carry the artifacts' raw bytes (owner/admin; the catalog must "
            "state a licence)",
        )
        export_sub.add_argument(
            "--acknowledge-sensitive",
            action="store_true",
            dest="acknowledge_sensitive",
            help="With --with-raw on a sensitive project: explicit, audited release",
        )
        export_sub.add_argument(
            "--chunk-text",
            action="store_true",
            dest="chunk_text",
            help="Carry each chunk's full md5-verified text (r4-kb v1.3); a "
            "sensitive project: its owner only, audited",
        )
        export_sub.add_argument(
            "--allow-unsigned",
            action="store_true",
            dest="allow_unsigned",
            help="Explicit opt-out for a server with no bundle signing key "
            "(UMRS_SECRET_KEY): produce an UNSIGNED bundle (manifest unsigned: true) "
            "instead of refusing; importing it needs --allow-unsigned too. A server "
            "with a key still signs",
        )

        export_sub.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project ID (optional; defaults to current project)",
        )
        return p

    def handle(self, args: Namespace) -> int:

        return cmd_project(args)


def cmd_project(args):
    """Handle project commands."""
    from ..config import get_config, reload_config
    from ..project import ProjectConfig, get_project_manager

    pm = get_project_manager()
    active_profile = get_config().profile
    handler = _project_handlers().get(args.project_command)
    if handler is not None:
        handler(args, pm, active_profile, reload_config, ProjectConfig)
        return

    print(
        "Unknown project command. Use: list, create, register, switch, current, delete, "
        "import, init, verify, verify_job, status, findings, approve, transition, "
        "readiness, plan, ask, export, loop, sessions, data-class "
        "(research runs: loop start)",
        file=sys.stderr,
    )
    sys.exit(1)


def _project_handlers():
    return {
        "list": _project_list,
        "create": _project_create,
        "register": _project_register,
        "switch": _project_switch,
        "current": _project_current,
        "delete": _project_delete,
        "ingest": _deprecated_handler("ingest", "import", project_import),
        "import": project_import,
        "understand": _deprecated_handler("understand", "init", project_init),
        "init": project_init,
        "verify": project_verify,
        "verify_job": project_verify_job,
        "readiness": project_readiness,
        "plan": project_plan,
        "emit_workflow": project_emit_workflow,
        "status": project_status,
        "loop": project_loop,
        "sessions": project_sessions,
        "findings": project_findings,
        "approve": project_approve,
        "transition": project_transition,
        "ask": project_ask,
        "export": project_export,
        "lineage": project_lineage,
        "ask_policy": project_ask_policy,
        "ask-policy": project_ask_policy,
        "storage": project_storage,
        "data-class": project_data_class,
        "data_class": project_data_class,
    }


def _project_list(args: Namespace, pm, _active_profile, _reload_config, _project_config) -> None:
    projects = pm.list()
    current = pm.get_current()
    if getattr(args, "as_json", False):
        print(json.dumps(_project_list_payload(projects, current), indent=2, default=str))
        return
    if not projects:
        print("No projects configured. Create one with: r4 project create <id>")
        return
    _print_project_list(projects, current)


def _project_list_payload(projects, current: str | None) -> list[dict]:
    return [
        {
            "project_id": project.project_id,
            "name": project.name,
            "active": project.project_id == current,
            "gateway_url": project.gateway_url,
            "model": project.model,
            "metadata": project.metadata,
        }
        for project in projects
    ]


def _print_project_list(projects, current: str | None) -> None:
    print("Projects:")
    for project in projects:
        marker = "*" if project.project_id == current else " "
        name = f" ({project.name})" if project.name else ""
        print(f"  {marker} {project.project_id}{name}")


def _project_create(
    args: Namespace,
    pm,
    active_profile: str,
    reload_config,
    project_config,
) -> None:
    existing = pm.get(args.project_id)
    project = _project_config_from_args(args, existing, project_config)
    pm.create(project)
    print(f"✓ Created project: {args.project_id}")
    _refresh_created_project(pm, args.project_id, active_profile, reload_config)
    _print_local_only_note(project)


def _print_local_only_note(project: "ProjectConfig") -> None:
    """A connected profile's new project with no gateway of its own exists on
    this machine only: say how to put it on the platform (2026-11-03)."""
    from ..config import get_config
    from ..project_register import LOCAL_ONLY_NOTE

    if project.gateway_url or not getattr(get_config(), "gateway_url", None):
        return
    print(LOCAL_ONLY_NOTE.format(project_id=project.project_id))


def _project_register(
    args: Namespace,
    pm: "ProjectManager",
    _active_profile: str,
    _reload_config: object,
    _project_config: object,
) -> None:
    """r4 project register <id> [--name N]: create the server row (ADR-051 D-1)."""
    from ..client import Client
    from ..project_register import RegisterError, register_project, registered_text

    try:
        result = register_project(Client(), args.project_id, name=args.name or "", pm=pm)
    except RegisterError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
    print(registered_text(result))
    if result["local"] == "added":
        print(
            f"  Added to this machine's projects too (switch: r4 project switch {args.project_id})"
        )


def _project_config_from_args(args: Namespace, existing, project_config):
    return project_config(
        project_id=args.project_id,
        name=getattr(args, "name", None) or _existing_project_attr(existing, "name"),
        gateway_url=getattr(args, "gateway", None)
        or _existing_project_attr(existing, "gateway_url"),
        model=getattr(args, "model", None) or _existing_project_attr(existing, "model"),
        metadata=_project_metadata(args, existing),
    )


def _existing_project_attr(existing, name: str):
    return getattr(existing, name) if existing else None


def _project_metadata(args: Namespace, existing) -> dict:
    metadata = dict(existing.metadata) if existing else {}
    sec_level = getattr(args, "security_level", None)
    if sec_level:
        metadata["security_level"] = sec_level
    return metadata


def _refresh_created_project(pm, project_id: str, active_profile: str, reload_config) -> None:
    if not pm.get_current():
        pm.set_current(project_id)
        reload_config(profile=active_profile)
        print("  Set as current project")
    elif pm.get_current() == project_id:
        reload_config(profile=active_profile)


def _project_switch(
    args: Namespace,
    pm,
    active_profile: str,
    reload_config,
    _project_config,
) -> None:
    if not pm.get(args.project_id):
        print(f"✗ Project not found: {args.project_id}. Run 'r4 project list'.", file=sys.stderr)
        sys.exit(1)
    pm.set_current(args.project_id)
    _persist_project_context(active_profile, args.project_id)
    runtime_config = reload_config(profile=active_profile)
    print(f"✓ Switched to project: {args.project_id}")
    _print_env_override_note()
    _print_bound_source_project_note(runtime_config, args.project_id)
    _print_active_context(runtime_config)


def _print_bound_source_project_note(config, project_id: str) -> None:
    """Say so when the switched project is not what the active source is sent.

    The active source is another deployment than the profile's login
    (onboarding plan S1): it is sent ``R4_PROJECT_ID``, a switched project
    recorded for its deployment (``r4 project create --gateway``) or its own
    ``project`` — never the profile's. A switch that changes nothing there
    says how to give the source this project (2026-10-15).
    """
    source = getattr(config, "bound_source", None)
    if not source or config.project_id == project_id:
        return
    print(
        f"  Note: the active source '{source}' is another deployment than your profile's "
        f"login, so it is not sent project '{project_id}'. To send it there: "
        f"r4 source set {source} --project {project_id}",
        file=sys.stderr,
    )


def _persist_project_context(profile: str, project_id: str) -> None:
    """Persist the switched project as the runtime default for the active profile.

    Mirrors ``r4 config context --project <id>`` so ``project switch`` alone is
    sufficient — users must not have to export R4_PROJECT_ID to align the two.
    """
    from ..config import ProfileConfigStore

    ProfileConfigStore().save(profile, project_id=project_id)


def _print_env_override_note() -> None:
    """Warn when env vars would still mask the saved project default."""
    import os

    env_project = os.environ.get("R4_PROJECT_ID")
    if env_project:
        print(
            f"  Note: R4_PROJECT_ID={env_project} is set and overrides the saved project "
            "(unset it to use the switched project).",
            file=sys.stderr,
        )


def _project_current(
    args: Namespace,
    pm,
    active_profile: str,
    reload_config,
    _project_config,
) -> None:
    current = pm.get_current()
    if not current:
        print("No current project set. Create one with: r4 project create <id>")
        return
    config = pm.get(current)
    runtime_config = reload_config(profile=active_profile)
    _print_current_project(current, config)
    _print_active_context(runtime_config, heading="Runtime defaults")


def _print_current_project(current: str, config) -> None:
    print(f"Current project: {current}")
    if not config:
        return
    for label, value in _current_project_fields(config):
        print(f"  {label}: {value}")


def _current_project_fields(config) -> list[tuple[str, object]]:
    fields = [
        ("Name", config.name),
        ("Gateway", config.gateway_url),
        ("Model", config.model),
    ]
    if config.security_level != "standard":
        fields.append(("Security level", config.security_level))
    return [(label, value) for label, value in fields if value]


def _project_delete(
    args: Namespace,
    pm,
    active_profile: str,
    reload_config,
    _project_config,
) -> None:
    if pm.delete(args.project_id):
        reload_config(profile=active_profile)
        print(f"✓ Deleted project: {args.project_id}")
        return
    print(f"✗ Project not found: {args.project_id}", file=sys.stderr)
    sys.exit(1)
