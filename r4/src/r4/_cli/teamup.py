"""``r4 teamup`` command handler (bug.memo D3 Stage 1, 2026-04-25)."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from typing import Any, Callable

from ._helpers import _get_current_session
from .base import CommandHandler
from .registry import command


def build_teamup_parser(subparsers, *, name: str = "teamup") -> ArgumentParser:
    """Build the full teamup subparser tree.

    Entry point of ``r4 teamup`` (and ``r4 team``).
    """
    tp = subparsers.add_parser(
        name,
        help="Multi-agent collaboration for HPC optimization",
        description="Create and run multi-agent collaboration sessions for optimization work.",
        epilog=(
            "Examples:\n"
            f'  r4 {name} create tune-fft --code solver.c --goal "reduce runtime"\n'
            f"  r4 {name} list\n"
            f"  r4 {name} run <session-id> --iterations 3"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    ts = tp.add_subparsers(
        dest="teamup_command",
        title="subcommands",
        metavar="<teamup-command>",
    )
    ts.required = True

    t_create = ts.add_parser("create", help="Create new teamup session")
    t_create.add_argument("name", help="Session name")
    t_create.add_argument("--code", "-c", help="Path to code for optimization")
    t_create.add_argument("--goal", "-g", help="Optimization goal description")
    t_create.add_argument(
        "--hardware",
        "-H",
        default="generic",
        help="Hardware target (fugaku, nvidia_a100, generic, or custom profile path)",
    )
    t_create.add_argument("--max-iterations", type=int, default=50, help="Maximum iterations")
    t_create.add_argument(
        "--roles",
        default="pm,se,pg",
        help="Comma-separated agent roles to include (pm,se,pg,cd)",
    )
    t_create.add_argument("--auto-approve", action="store_true", help="Auto-approve optimizations")
    t_create.add_argument("--start", action="store_true", help="Start session immediately")
    t_create.add_argument(
        "--crew",
        help="Crew: hpc (this teamup session, the default) or a plan crew "
        "(research|code|paper-writing|kakenhi-report|peer-review), which runs its "
        "plan template instead: shown without --start, run with --start "
        "(ADR-092 slice 4; `r4 teamup crews` lists them)",
    )

    t_list = ts.add_parser("list", help="List teamup sessions")
    t_list.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    t_crews = ts.add_parser("crews", help="List the crews and the plan template each runs")
    t_crews.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    t_status = ts.add_parser("status", help="Show session status")
    t_status.add_argument("session_id", nargs="?", help="Session ID (default: current)")
    t_status.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
    )

    t_run = ts.add_parser("run", help="Run teamup session iterations")
    t_run.add_argument("session_id", nargs="?", help="Session ID (default: current)")
    t_run.add_argument("--iterations", "-n", type=int, default=1, help="Number of iterations")
    t_run.add_argument("--continuous", action="store_true", help="Run continuously until done")

    t_pause = ts.add_parser("pause", help="Pause running session")
    t_pause.add_argument("session_id", nargs="?", help="Session ID (default: current)")

    t_resume = ts.add_parser("resume", help="Resume paused session")
    t_resume.add_argument("session_id", nargs="?", help="Session ID (default: current)")

    t_cancel = ts.add_parser("cancel", help="Cancel session")
    t_cancel.add_argument("session_id", nargs="?", help="Session ID (default: current)")

    t_logs = ts.add_parser("logs", help="Show session logs/changelog")
    t_logs.add_argument("session_id", nargs="?", help="Session ID (default: current)")
    t_logs.add_argument("--agent", "-a", help="Filter by agent (pm, se, pg, cd)")
    t_logs.add_argument("--limit", "-l", type=int, default=20, help="Max entries to show")
    t_logs.add_argument("--follow", "-f", action="store_true", help="Follow log output")

    t_report = ts.add_parser("report", help="Generate session report")
    t_report.add_argument("session_id", nargs="?", help="Session ID (default: current)")
    t_report.add_argument("--output", "-o", help="Output file (default: stdout)")

    t_engines = ts.add_parser("engines", help="List installed team engines (ADR-020)")
    t_engines.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
    )

    t_launch = ts.add_parser(
        "launch",
        help="Spec-driven team run through the engine SPI (ADR-020); supersedes create+run",
    )
    t_launch.add_argument("name", help="Run name")
    t_launch.add_argument("--engine", "-e", help="Engine name (default: resolve automatically)")
    t_launch.add_argument("--goal", "-g", help="Objective for the team")
    t_launch.add_argument("--spec", help="TeamSpec JSON file (overrides name/goal/roles flags)")
    t_launch.add_argument("--roles", default="pm,se,pg", help="Comma-separated roles (pm,se,pg,cd)")
    t_launch.add_argument(
        "--team",
        choices=["research"],
        help="Named team: research = the research crew's plan template, research-round "
        "(planner, experimenter, analyst, reviewer; D-29, ADR-092 slice 4)",
    )
    t_launch.add_argument("--max-iterations", type=int, default=10, help="Iteration cap")
    t_launch.add_argument("--max-tokens", type=int, help="Token budget ceiling")
    t_launch.add_argument(
        "--project",
        help="Project the run is scoped to (default: the active project, then the "
        "identity project)",
    )
    t_launch.add_argument(
        "--dry-run", action="store_true", help="Validate+resolve only, don't execute"
    )

    t_hw = ts.add_parser("hardware", help="List hardware profiles")
    t_hw.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    # ADR-092 D-9: ``r4 team plan …`` / ``r4 teamup plan …`` (argparse-only import).
    from .team_plan import build_team_plan_parser

    build_team_plan_parser(ts)

    return tp


@command
class TeamupCommand(CommandHandler):
    """``r4 teamup`` — multi-agent collaboration sessions."""

    name = "teamup"
    help = "Multi-agent collaboration for HPC optimization"

    def register(self, subparsers) -> ArgumentParser:
        return build_teamup_parser(subparsers, name=self.name)

    def handle(self, args: Namespace) -> int:

        cmd_teamup(args)
        return 0


def cmd_teamup(args):
    """Handle teamup commands (A-801 to A-815)."""
    import sys

    from ..client import Client

    handler = _teamup_command_handlers().get(args.teamup_command)
    if handler is None:
        print(
            "Unknown teamup command. Use: create, list, status, run, pause, "
            "resume, cancel, logs, report, hardware",
            file=sys.stderr,
        )
        sys.exit(1)
    return handler(args, Client())


def _web_getter(args: Any, client: Any) -> Callable[[], Any]:
    """The user's web choice for this command's agents (ADR-099 D-2): ``r4
    --web``, the question on a terminal, else the non-interactive order with
    its notice (RS-1 round-2 fix W2-2: they refused every web call). The
    agents' processes run in the local sandbox ``r4 --sandbox`` (else
    ``R4_SANDBOX``, the profile default) sets, whose proxy follows it
    (ADR-101), settled before the web question."""
    from .sandbox_prompt import cli_agent_sandbox_and_web
    from .web_access_prompt import cli_agent_web_getter

    config = getattr(client, "config", None)
    return cli_agent_sandbox_and_web(args, config, lambda: cli_agent_web_getter(args, config))


def _parse_teamup_roles(raw_roles: str):
    import sys

    from ..teamup.roles import AgentKey

    roles = []
    for raw in raw_roles.split(","):
        role = AgentKey(raw).role
        if role is None:
            print(f"Unknown role: {raw.strip()}. Valid: pm, se, pg, cd", file=sys.stderr)
            sys.exit(1)
        roles.append(role)
    return roles


def _teamup_session_id(args) -> str:
    import sys

    session_id = args.session_id or _get_current_session()
    if not session_id:
        print("No session specified.", file=sys.stderr)
        sys.exit(1)
    # Accept either an exact session_id or a session name. Names are the
    # natural handle a user sees in `r4 team list`'s NAME column.
    try:
        return _session_id_by_id_or_name(session_id)
    except SystemExit:
        raise
    except Exception:  # an unknown token surfaces as-is (load reports it)
        return session_id


def _session_id_by_id_or_name(session_id: str) -> str:
    import sys

    from ..teamup.session import TEAMUP_SESSIONS_DIR, TeamupSession

    if (TEAMUP_SESSIONS_DIR / session_id).exists():
        return session_id
    matches = [s for s in TeamupSession.list_sessions() if s.get("name") == session_id]
    if len(matches) == 1:
        return matches[0]["session_id"]
    if len(matches) > 1:
        print(f"Multiple sessions named '{session_id}'. Disambiguate by ID:", file=sys.stderr)
        for s in matches:
            print(f"  {s['session_id']}  (updated {s['updated_at']})", file=sys.stderr)
        sys.exit(1)
    # Single unknown token — surface as-is so TeamupSession.load reports a
    # useful "Session not found" error.
    return session_id


def _crew_template(args) -> str | None:
    """The plan template ``--crew`` selects, or None for the HPC preset."""
    crew = getattr(args, "crew", None)
    if not crew:
        return None
    from ..teams.crews import crew_template
    from ._helpers import _die

    try:
        return crew_template(crew)
    except KeyError as exc:
        _die(str(exc.args[0]), action="teamup create", error_id="crew.unknown")
    return None


def _cmd_teamup_create(args, client):
    template = _crew_template(args)
    if template is not None:
        return _create_crew_plan(args, client, template)
    return _create_hpc_session(args, client)


def _create_crew_plan(args, client, template: str) -> int:
    """``--crew <plan crew>``: the crew is its plan template (ADR-092 slice 4).

    Without ``--start`` the plan is validated and shown (``--dry-run``);
    with it, it runs as ``r4 team plan run <template>`` runs it. The goal
    binds to the template's free-text parameter; the HPC knobs do not apply.
    """
    import sys

    from ..companion.team_plan_tools import args_with_goal
    from .team_plan import EXIT_INVALID, _print_problems

    print(f"crew {args.crew!r} runs the plan template {template!r} (ADR-092 slice 4)")
    plan_args, problem = args_with_goal(template, None, args.goal or args.name)
    if problem is not None:
        print(problem, file=sys.stderr)
        sys.exit(EXIT_INVALID)
    from ..teams.plan.engine import spec_from_plan
    from ..teams.plan_sources import load_template

    spec, problems = spec_from_plan(
        load_template(template),
        plan_args,
        project=_launch_project(args, client),
        templates=load_template,
    )
    if problems or spec is None:
        _print_problems(problems)
        sys.exit(EXIT_INVALID)
    code = _crew_plan_code(args, client, spec)
    if not args.start and not code:
        print(f"run it: r4 teamup create {args.name} --crew {args.crew} --start")
    if code:
        sys.exit(code)
    return code


def _crew_plan_code(args: Any, client: Any, spec: Any) -> int:
    """``--start`` runs the crew's plan under the user's web choice (ADR-099
    D-2); without it the plan is only shown (``--dry-run``)."""
    from .team_plan import _dry_run, _service, _stream_to_exit

    if not args.start:
        return _dry_run(_service(client), spec)
    service = _service(client, web_access=_web_getter(args, client))
    return _stream_to_exit(lambda: service.submit(spec))


def _create_hpc_session(args, client):
    import asyncio
    from pathlib import Path

    from ..teams.crews import HPC_CREW
    from ..teams.service import TeamService
    from ..teamup.session import TeamupSessionConfig

    roles = _parse_teamup_roles(args.roles)
    # 2026-10-01: stamp the active project (env/state) so the work pane can
    # scope sessions to it; None stays honest when no project is current.
    from ..project import get_project_manager

    config = TeamupSessionConfig(
        name=args.name,
        roles=roles,
        hardware_target=args.hardware,
        max_iterations=args.max_iterations,
        base_code_path=Path(args.code) if args.code else None,
        optimization_goal=args.goal,
        auto_approve=args.auto_approve,
        crew_pack=HPC_CREW,
        project_id=get_project_manager().get_current(),
    )
    # Persisted at once by TeamService (ADR-072 one seam, ADR-092 slice 3) so
    # lifecycle commands (list/status/logs/...) work even when `create` was
    # not followed by `--start`. `get_current` picks the most recently
    # updated session, which is the one just written.
    web_access = _web_getter(args, client) if args.start else None
    session = TeamService(client, web_access=web_access).open_team_session(config)
    print(f"✓ Created teamup session: {session.session_id}")
    print(f"  Name: {args.name}")
    print(f"  Hardware: {args.hardware}")
    print(f"  Roles: {', '.join(role.value for role in roles)}")
    if args.start:
        asyncio.run(session.start())
        print("\nSession started. Run iterations with:")
        print(f"  r4 teamup run {session.session_id}")


def _print_teamup_sessions(sessions: list[dict]) -> None:
    if not sessions:
        print("No teamup sessions found.")
        return
    print("Teamup Sessions:")
    print("-" * 60)
    print(f"{'ID':<10} {'NAME':<20} {'STATUS':<12} {'ITER':>5}")
    print("-" * 60)
    for session in sessions:
        print(
            f"{session['session_id']:<10} {session['name'][:20]:<20} "
            f"{session['status']:<12} {session['iteration']:>5}"
        )


def _cmd_teamup_list(args, _client):
    import json

    from ..teamup.session import TeamupSession

    sessions = TeamupSession.list_sessions()
    if args.as_json:
        print(json.dumps(sessions, indent=2))
    else:
        _print_teamup_sessions(sessions)


def _require_teamup_status_session_id(args) -> str:
    import sys

    session_id = args.session_id or _get_current_session()
    if not session_id:
        print(
            "No session specified. Provide session_id or use 'teamup create' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return session_id


def _print_teamup_status(status: dict) -> None:
    print(f"Session: {status['name']} ({status['session_id']})")
    print("=" * 50)
    print(f"Status:      {status['status']}")
    print(f"Iteration:   {status['iteration']} / {status['max_iterations']}")
    sota = status.get("current_sota")
    print(f"Best SOTA:   {sota['value'] if sota else 'N/A'}")
    print("\nAgents:")
    for agent_id, agent_state in status["agents"].items():
        print(f"  • {agent_id}: {agent_state['status']}")


def _cmd_teamup_status(args, client):
    import json

    from ..teamup.session import TeamupSession

    session = TeamupSession.load(_require_teamup_status_session_id(args), client)
    status = session.get_status()
    if args.as_json:
        print(json.dumps(status, indent=2, default=str))
    else:
        _print_teamup_status(status)


def _run_continuous_iterations(session) -> None:
    import asyncio

    print("Running continuously until complete...")
    while True:
        cont = asyncio.run(session.run_iteration())
        print(f"  Iteration {session.iteration}: {session.status.value}")
        if not cont:
            break


def _run_fixed_iterations(session, iterations: int) -> None:
    import asyncio

    for _index in range(iterations):
        cont = asyncio.run(session.run_iteration())
        print(f"  Iteration {session.iteration}: {session.status.value}")
        if not cont:
            break


def _cmd_teamup_run(args, client):
    import asyncio

    from ..teamup.session import TeamupSession

    session = TeamupSession.load(_teamup_session_id(args), client)
    session._web_access = _web_getter(args, client)
    if session.status.value == "created":
        asyncio.run(session.start())
    if args.continuous:
        _run_continuous_iterations(session)
    else:
        _run_fixed_iterations(session, args.iterations)
    status = session.get_status()
    print(f"\nSession status: {status['status']}")
    print(f"Iterations completed: {status['iteration']}")


def _cmd_teamup_state_change(args, client, *, action: str) -> None:
    from ..teamup.session import TeamupSession

    session_id = _teamup_session_id(args)
    session = TeamupSession.load(session_id, client)
    getattr(session, action)()
    labels = {"pause": "Paused", "resume": "Resumed", "cancel": "Cancelled"}
    print(f"✓ {labels[action]} session: {session_id}")


def _cmd_teamup_pause(args, client):
    _cmd_teamup_state_change(args, client, action="pause")


def _cmd_teamup_resume(args, client):
    _cmd_teamup_state_change(args, client, action="resume")


def _cmd_teamup_cancel(args, client):
    _cmd_teamup_state_change(args, client, action="cancel")


def _follow_teamup_logs(session_id: str, client, session) -> None:
    import time

    print("\n--- Following logs (Ctrl+C to stop) ---\n")
    last_check = session.updated_at
    while True:
        try:
            time.sleep(2)
            session = session.__class__.load(session_id, client)
            entries = session.comms.get_entries_since(last_check)
            for entry in entries:
                print(entry.format())
            last_check = session.updated_at
        except KeyboardInterrupt:
            break


def _cmd_teamup_logs(args, client):
    from ..teamup.session import TeamupSession

    session_id = _teamup_session_id(args)
    session = TeamupSession.load(session_id, client)
    print(session.get_logs(agent=args.agent, limit=args.limit))
    if args.follow:
        _follow_teamup_logs(session_id, client, session)


def _cmd_teamup_report(args, client):
    import asyncio
    from pathlib import Path

    from ..teamup.session import TeamupSession

    session = TeamupSession.load(_teamup_session_id(args), client)
    report = asyncio.run(session.generate_report())
    if args.output:
        Path(args.output).write_text(report)
        print(f"✓ Report saved to: {args.output}")
    else:
        print(report)


def _print_hardware_profiles(profiles: list[dict]) -> None:
    print("Available Hardware Profiles:")
    print("-" * 60)
    for profile in profiles:
        print(f"  {profile['id']:<15} - {profile['name']}")
        print(f"                  {profile['description'][:50]}")


def _cmd_teamup_hardware(args, _client):
    import json

    from ..teamup.hardware import list_hardware_profiles

    profiles = list_hardware_profiles()
    if args.as_json:
        print(json.dumps(profiles, indent=2))
    else:
        _print_hardware_profiles(profiles)


# ---------------------------------------------------------------------------
# ADR-020 engine SPI surface
# ---------------------------------------------------------------------------


def _cmd_team_engines(args, client):
    import json

    from ..teams import TeamService

    service = TeamService(client)
    registered = service.list_engines()
    if args.as_json:
        rows = []
        for r in registered:
            d = r.descriptor
            caps = d.capabilities
            rows.append(
                {
                    "name": d.name,
                    "version": d.version,
                    "trust_tier": d.trust_tier.value,
                    "available": r.available,
                    "note": r.note,
                    "workflow_styles": [s.value for s in caps.workflow_styles],
                    "supports_streaming": caps.supports_streaming,
                    "supports_checkpointing": caps.supports_checkpointing,
                    "supports_graph_execution": caps.supports_graph_execution,
                    "supports_parallel_agents": caps.supports_parallel_agents,
                    "supports_hierarchical_agents": caps.supports_hierarchical_agents,
                    "supports_distributed_execution": caps.supports_distributed_execution,
                    "supports_multi_model": caps.supports_multi_model,
                    "supports_gpu_workers": caps.supports_gpu_workers,
                    "supports_human_in_loop": caps.supports_human_in_loop,
                }
            )
        print(json.dumps(rows, indent=2))
        return
    print(f"{'engine':<14}{'version':<10}{'tier':<10}{'available':<11}styles")
    for r in registered:
        d = r.descriptor
        styles = ",".join(s.value for s in d.capabilities.workflow_styles) or "-"
        note = r.note if not r.available else ""
        print(
            f"{d.name:<14}{d.version:<10}{d.trust_tier.value:<10}"
            f"{str(r.available):<11}{styles}  {note}"
        )


def _active_project_id(client) -> str:
    """The project a CLI team run is scoped to (ADR-092 slice-1 fix).

    The active project (``R4_PROJECT_ID``, then ``r4 project switch``), else
    the caller's identity project from the client config, else "" with a
    notice. Never the old ``"cli"`` placeholder, which scoped every CLI team
    run to a project that does not exist.
    """
    import sys

    from ..project import get_project_manager

    project = get_project_manager().get_current() or _identity_project(client)
    if project:
        return str(project)
    print(
        "note: no active project (set R4_PROJECT_ID, run `r4 project switch <id>`, "
        "or pass --project)",
        file=sys.stderr,
    )
    return ""


def _identity_project(client) -> str | None:
    """The companion's own duck-typed read of ``client.config.project_id``."""
    from ..companion.tools import _identity_project_id

    return _identity_project_id(client)


def _launch_project(args, client) -> str:
    """``--project`` when given, else the active project."""
    return getattr(args, "project", None) or _active_project_id(client)


def _team_launch_spec(args, project: str = ""):
    """Build a TeamSpec from --spec JSON or CLI flags."""
    from r4_contracts.team_engine.spec import (
        TEAM_SPEC_VERSION,
        AgentSpec,
        BudgetSpec,
        TaskSpec,
        TeamSpec,
        Topology,
        WorkflowStyle,
    )

    if args.spec:
        return _launch_spec_file(args, project)
    if getattr(args, "team", None) == "research":
        return _research_crew_spec(args, project)

    roles = [r.strip().lower() for r in args.roles.split(",") if r.strip()]
    agents = tuple(
        AgentSpec(
            name=role,
            role=role,
            instructions=get_role_spec_from_name(role),
            max_iterations=args.max_iterations,
        )
        for role in roles
    )
    return TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name=args.name,
        project=project,
        task=TaskSpec(objective=args.goal or args.name),
        agents=agents,
        topology=Topology(style=WorkflowStyle.FILE_BASED),
        budgets=BudgetSpec(max_tokens=args.max_tokens),
    )


def _research_crew_spec(args, project: str):
    """``--team research``: the research crew's plan template (ADR-092 slice 4).

    The four research roles used to re-skin the HPC preset's four slots on
    the native engine; they are the ``research-round`` template now, a plan
    spec the resolver hands to the plan engine. ``--max-tokens`` overrides
    the template's budget; ``--max-iterations`` does not apply to a plan.
    """
    import sys

    from ..teams.crews import crew_template
    from ..teams.plan.engine import spec_from_plan
    from ..teams.plan_sources import load_template

    template = crew_template("research")
    spec, problems = spec_from_plan(
        load_template(template),
        {"objective": args.goal or args.name},
        project=project,
        max_tokens=args.max_tokens,
        templates=load_template,
    )
    if problems or spec is None:
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(2)
    return spec


def _launch_spec_file(args, project: str):
    """A --spec file; ``--project`` beats the file, the file beats the active one."""
    import json
    from pathlib import Path

    from r4_contracts.team_engine.spec import TEAM_SPEC_VERSION

    raw = json.loads(Path(args.spec).read_text())
    raw.setdefault("spec_version", TEAM_SPEC_VERSION)
    if getattr(args, "project", None):
        raw["project"] = args.project
    return _spec_from_dict(raw, project)


def get_role_spec_from_name(name: str):
    from ..teamup.roles import get_role_spec

    try:
        return get_role_spec(name).system_prompt
    except ValueError:
        return f"You are agent {name}. Act on the session objective and report progress."


def _spec_from_dict(raw, project: str = ""):
    """A TeamSpec from its JSON form; ``context`` and ``plan`` (ADR-092)
    pass through unchanged for the engine to validate."""
    from r4_contracts.team_engine.spec import (
        TEAM_SPEC_VERSION,
        AgentSpec,
        BudgetSpec,
        TaskSpec,
        TeamSpec,
        Topology,
        WorkflowStyle,
    )

    return TeamSpec(
        spec_version=raw.get("spec_version", TEAM_SPEC_VERSION),
        run_name=raw["run_name"],
        project=raw.get("project") or project,
        task=TaskSpec(**raw.get("task", {"objective": raw["run_name"]})),
        agents=tuple(AgentSpec(**a) for a in raw.get("agents", ())),
        topology=Topology(
            style=WorkflowStyle(raw.get("topology", {}).get("style", "file_based")),
            entry_agent=raw.get("topology", {}).get("entry_agent"),
            max_parallel=raw.get("topology", {}).get("max_parallel", 1),
        ),
        budgets=BudgetSpec(**_spec_budgets(raw)),
        context=dict(raw.get("context") or {}),
        plan=raw.get("plan"),
    )


# The plan budget keys a plan spec's ``budgets`` lowers (as spec_from_plan does).
_PLAN_BUDGET_KEYS = ("max_tokens", "max_wallclock_sec", "max_tool_calls")


def _spec_budgets(raw) -> dict:
    """The spec file's ``budgets``, else its plan's own budget (ADR-092 D-7).

    PlanEngine.validate refuses a plan spec whose budgets are not the
    plan's, because the run's meter (and so the broker's per-round guard)
    is built from ``spec.budgets``. A spec file that carries a plan and no
    ``budgets`` therefore gets the plan's, as spec_from_plan lowers them;
    budgets the file does name are kept, for validate to hold to the plan.
    """
    if "budgets" in raw:
        return dict(raw.get("budgets") or {})
    plan = raw.get("plan")
    budget = plan.get("budget") if isinstance(plan, dict) else None
    if not isinstance(budget, dict):
        return {}
    return {key: budget.get(key) for key in _PLAN_BUDGET_KEYS}


def _cmd_team_launch(args, client):
    import asyncio
    import sys

    from r4_teamengine_spi.errors import UnknownEngineError

    from ..teams import TeamService
    from .team_plan import _project_mismatch

    # The ADR-062 gate checks the ACTIVE project, so --project must name it
    # (the same rule as `r4 team plan run`, review finding 39).
    mismatch = _project_mismatch(getattr(args, "project", None))
    if mismatch:
        print(mismatch, file=sys.stderr)
        sys.exit(2)
    spec = _team_launch_spec(args, _launch_project(args, client))
    if not spec.agents and spec.plan is None:
        print("launch requires at least one agent (check --roles or --spec)", file=sys.stderr)
        sys.exit(2)
    service = TeamService(client, web_access=None if args.dry_run else _web_getter(args, client))
    try:
        resolution = service.resolve(spec, requested=args.engine)
    except UnknownEngineError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    print(f"engine: {resolution.engine_name} ({resolution.reason})")
    if args.dry_run:
        _launch_dry_run(service, spec, resolution)
        return
    result = asyncio.run(_launch_stream(service, spec, args))
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
    if result.status == "failed":
        sys.exit(1)


def _launch_dry_run(service, spec, resolution) -> None:
    """Resolve + validate only; exit 2 on a validation error."""
    import sys

    _print_launch_plan(spec, resolution)
    issues = service.registry.get(resolution.engine_name).validate(spec)
    if issues:
        print("Validation:")
        for issue in issues:
            print(f"  [{issue.severity}] {issue.field}: {issue.message}")
    else:
        print("Validation: ok (no issues)")
    if any(i.severity == "error" for i in issues):
        sys.exit(2)
    print("(dry-run — nothing executed)")


def _print_launch_plan(spec, resolution) -> None:
    """Show the resolved plan that a real launch would execute."""
    print("Launch plan:")
    print(f"  run:      {spec.run_name}")
    print(f"  project:  {spec.project or '(none)'}")
    print(f"  goal:     {spec.task.objective}")
    print(f"  style:    {spec.topology.style.value}")
    agents = ", ".join(f"{a.name}(iters≤{a.max_iterations})" for a in spec.agents)
    print(f"  agents:   {agents or '(none)'}")
    budget = _budget_parts(spec.budgets)
    if budget:
        print(f"  budget:   {', '.join(budget)}")
    print(f"  reason:   {resolution.reason}")


def _budget_parts(budgets) -> list[str]:
    if not budgets:
        return []
    parts = [
        (budgets.max_tokens, "tokens≤{}"),
        (budgets.max_wallclock_sec, "wall≤{}s"),
        (budgets.max_tool_calls, "tool_calls≤{}"),
    ]
    return [fmt.format(value) for value, fmt in parts if value]


async def _launch_stream(service, spec, args):
    from r4_contracts.team_engine.events import EventKind

    handle = await service.submit(spec, engine=args.engine)
    print(f"run_id: {handle.run_id}")
    async for event in handle.events():
        if event.kind is EventKind.AGENT_MESSAGE:
            continue  # quiet by default; stored in events.jsonl
        payload = f" {event.payload}" if event.payload else ""
        print(f"[{event.kind.value}] {event.actor.name}{payload}")
    result = await handle.result()
    print(f"\nstatus: {result.status}  run_dir: {handle.run_dir}")
    return result


def _teamup_command_handlers():
    return {
        "create": _cmd_teamup_create,
        "crews": _cmd_teamup_crews,
        "engines": _cmd_team_engines,
        "launch": _cmd_team_launch,
        "list": _cmd_teamup_list,
        "status": _cmd_teamup_status,
        "run": _cmd_teamup_run,
        "pause": _cmd_teamup_pause,
        "resume": _cmd_teamup_resume,
        "cancel": _cmd_teamup_cancel,
        "logs": _cmd_teamup_logs,
        "report": _cmd_teamup_report,
        "hardware": _cmd_teamup_hardware,
        "plan": _cmd_team_plan,
    }


def _cmd_team_plan(args, client):
    """``r4 team plan …`` (ADR-092 D-9); the plan stack loads only when used."""
    from .team_plan import cmd_team_plan

    return cmd_team_plan(args, client)


def _cmd_teamup_crews(args, _client):
    import json

    from ..teams.crews import list_crews

    crews = list_crews()
    if args.as_json:
        print(json.dumps(crews, indent=2))
        return
    for crew in crews:
        runs = f"plan template {crew['template']}" if crew["template"] else "teamup session"
        print(f"  {crew['name']:<16} {runs}: {crew['description'].split('. ')[0]}")
        if crew["phases"]:
            print(f"    phases: {' → '.join(crew['phases'])}")
        if crew["aliases"]:
            print(f"    aliases: {', '.join(crew['aliases'])}")
