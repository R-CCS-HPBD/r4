"""``r4 team plan`` (and ``r4 teamup plan``): team plans from the shell (ADR-092 D-9, D-10).

A team plan is data (D-1): phases of ``agent``/``map``/``reduce`` steps with
plan-local profiles, a hard budget and an output map. This surface lists and
shows the built-in templates, validates a plan (every problem at once), runs
one through ``TeamService`` (the CORE ``plan`` engine behind the ADR-020
seam), resumes a run as its next attempt (D-8) and reads a run's result.

The CLI holds no host PermissionManager, so plan agents fail closed: safe
tools run, risky ones are refused and named in the result (D-6).

Agent output is untrusted (D-6): every progress, log and error line, and a
result's JSON, is printed with control characters escaped, so an agent
cannot write escape sequences to the user's terminal.

Exit codes: 0 success or partial, 1 failed or cancelled, 2 invalid input.
Only argparse is imported at module load; the parser is built on every CLI
start and the plan stack loads only when a ``plan`` command runs.
"""

from __future__ import annotations

import json
import re
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2

_GOOD = frozenset({"success", "partial"})
_CLIENT_DEFAULT_MODEL = "the client's default model"  # no --model, no profile model
_FAIL_CLOSED = (
    "The CLI has no host permission manager, so plan agents run fail-closed: safe "
    "tools run; risky tools are refused and named in the result (ADR-092 D-6). Run a "
    "plan from the chat (team_plan_run) for agents under your approval ladder."
)
# C0 and C1 controls (ESC, BEL, CR, CSI ...), DEL, the Unicode line and
# paragraph separators and the bidirectional overrides: a terminal acts on
# these, so agent text printed raw could rewrite lines or set the clipboard.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
# What ``json.dumps(ensure_ascii=False)`` leaves raw: it escapes C0 inside
# strings already, and its own structure holds only spaces and newlines.
_JSON_RAW_CONTROL = re.compile(r"[\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_PLACEHOLDERS: dict[str, Any] = {
    "integer": lambda name, decl: decl.get("min", 1),
    "number": lambda name, decl: decl.get("min", 1),
    "boolean": lambda name, decl: False,
    "array": lambda name, decl: [],
    "object": lambda name, decl: {},
}


def build_team_plan_parser(subparsers) -> ArgumentParser:
    """The ``plan`` subcommand tree under ``r4 team`` / ``r4 teamup``."""
    tp = subparsers.add_parser(
        "plan",
        help="Team plans: declarative multi-agent runs (ADR-092)",
        description=(
            "Run a team plan: phases of agent fan-outs, per-item pipelines and closed "
            "reducers under a hard token budget.\n\n" + _FAIL_CLOSED
        ),
        epilog=(
            "Examples:\n"
            "  r4 team plan list\n"
            "  r4 team plan show adversarial-review\n"
            "  r4 team plan validate adversarial-review --arg target=src/solver.py\n"
            "  r4 team plan run adversarial-review --arg target=src/solver.py --dry-run\n"
            "  r4 team plan run ./my-plan.json --args-json @args.json --max-tokens 200000\n"
            "  r4 team plan run adversarial-review --arg target=src/solver.py \\\n"
            "      --venue server --source openai-main   # a sandboxed worker on UMRS\n"
            "  r4 team plan resume <run-id> --from-phase verify\n"
            "  r4 team plan result <run-id> --path output.survivors\n"
            "  r4 team plan cancel <run-id>   # a server-hosted run\n"
            "  r4 team plan clean --all-finished --dry-run"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    ps = tp.add_subparsers(dest="plan_command", title="subcommands", metavar="<plan-command>")
    ps.required = True
    p_list = ps.add_parser("list", help="List the built-in plan templates and their params")
    p_list.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
    p_show = ps.add_parser("show", help="Show a template: JSON, params, expanded agent bound")
    p_show.add_argument("name", help="Template name")
    _add_args_options(p_show)
    p_validate = ps.add_parser("validate", help="Validate a plan; print every problem")
    p_validate.add_argument("ref", help="Template name or a .json plan file")
    _add_args_options(p_validate)
    _add_run_parser(ps)
    p_resume = ps.add_parser("resume", help="Resume a run as its next attempt (journal replay)")
    p_resume.add_argument("run_id", help="Run id")
    p_resume.add_argument("--from-phase", help="Re-run this phase and every later one live")
    _add_durable_option(p_resume)
    _add_venue_options(p_resume)
    p_result = ps.add_parser("result", help="Print a run's result.json, or a slice of it")
    p_result.add_argument("run_id", help="Run id")
    p_result.add_argument("--path", help="Dotted path into result.json, e.g. output.survivors")
    p_cancel = ps.add_parser(
        "cancel", help="Cancel a server-hosted run: stop its worker, cancel its record"
    )
    p_cancel.add_argument("run_id", help="Run id")
    _add_clean_parser(ps)
    return tp


_CLEAN_HELP = (
    "Remove the isolated workspaces (git worktrees and their r4-plan/<run>/* branches, "
    "copies) that runs kept for a merge-back that was never finished. A run is cleaned "
    "when its last attempt has a terminal result, or with --older-than when its journal "
    "has not been written for that many days. A run whose journal holds a human's "
    "approval of a merge-back that was not applied is never touched: resume it."
)


def _add_clean_parser(ps) -> None:
    p_clean = ps.add_parser(
        "clean",
        help="Remove workspaces kept by finished or abandoned runs",
        description=_CLEAN_HELP,
    )
    p_clean.add_argument("run_id", nargs="?", help="Run id (or --all-finished)")
    p_clean.add_argument(
        "--all-finished", action="store_true", help="Every run under the run root that qualifies"
    )
    p_clean.add_argument(
        "--older-than",
        type=float,
        metavar="DAYS",
        help="Also clean runs with no terminal result idle for more than DAYS",
    )
    p_clean.add_argument(
        "--dry-run", action="store_true", help="List what would be removed; remove nothing"
    )


def _add_run_parser(ps) -> None:
    p_run = ps.add_parser(
        "run", help="Run a plan (fail-closed: safe tools only)", description=_FAIL_CLOSED
    )
    p_run.add_argument("ref", help="Template name or a .json plan file")
    _add_args_options(p_run)
    p_run.add_argument("--max-tokens", type=int, help="Override budget.max_tokens (hard)")
    p_run.add_argument("--concurrency", type=int, help="Override concurrency (1-8)")
    p_run.add_argument("--model", help="Model (catalog name) for profiles that name none")
    p_run.add_argument(
        "--project", help="Project the run is scoped to (default: the active project)"
    )
    p_run.add_argument(
        "--dry-run", action="store_true", help="Resolve and show the plan; run nothing"
    )
    _add_durable_option(p_run)
    _add_venue_options(p_run)


_VENUE_HELP = (
    "Where the run executes: local (this process, the default) or server (a sandboxed "
    "worker on the UMRS site's frontend venue, ADR-092 slice 8; its record is UMRS's, "
    "its models come from your pushed --source, its gates wait in the UMRS approvals "
    "inbox). A site without the venue refuses by name (plan_iteration_needs_server_venue)."
)


def _add_venue_options(parser: ArgumentParser) -> None:
    parser.add_argument("--venue", choices=("local", "server"), default="local", help=_VENUE_HELP)
    parser.add_argument(
        "--source",
        dest="venue_sources",
        metavar="NAME[,NAME]",
        help="Server venue: pushed source(s) the worker's models are served by",
    )
    parser.add_argument(
        "--default-source",
        dest="venue_default_source",
        help="Server venue: the default source (defaults to the first --source)",
    )
    parser.add_argument(
        "--web",
        dest="venue_web_policy",
        choices=["off", "none", "allowlist", "public"],
        help=(
            "The agents' web access (ADR-099): off, allowlist (with --web-domain) or "
            "public; on the server venue the worker's sandbox. Asked on a terminal "
            "when omitted"
        ),
    )
    parser.add_argument(
        "--web-domain",
        dest="venue_web_domains",
        action="append",
        metavar="DOMAIN[,DOMAIN]",
        help="An allowlist domain for --web allowlist (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Server venue: start it and return; follow with result, stop with cancel",
    )


_DURABLE_HELP = (
    "Require the run's durable record in UMRS (ADR-092 slice 7): refused when no UMRS "
    "source is configured, and the run stops if a record cannot be written. Without it, "
    "records go to UMRS when a source is configured and a failure is named, local-only."
)


def _add_durable_option(parser: ArgumentParser) -> None:
    parser.add_argument("--durable", action="store_true", default=None, help=_DURABLE_HELP)


def _add_args_options(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="K=V",
        help="A plan param (repeatable); beats --args-json",
    )
    parser.add_argument("--args-json", metavar="JSON|@FILE", help="Plan params as a JSON object")


def cmd_team_plan(args: Namespace, client: Any) -> int:
    """Dispatch ``r4 team plan <command>``; a non-zero code exits the process."""
    handler = _HANDLERS[args.plan_command]
    code = handler(args, client)
    if code:
        sys.exit(code)
    return EXIT_OK


# ── list / show ─────────────────────────────────────────────────────────────


def _cmd_list(args: Namespace, _client: Any) -> int:
    import json

    from ..teams.plan.templates import list_templates

    rows = list_templates()
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    for row in rows:
        print(f"  {row['name']:<24} {_first_sentence(row['description'])}")
        for name, decl in (row.get("params") or {}).items():
            print(f"    {_param_line(name, decl)}")
    return EXIT_OK


def _cmd_show(args: Namespace, _client: Any) -> int:
    """The template as written, its params, and the bound of its expansion."""
    import json

    from ..teams.plan_sources import load_template

    try:
        raw = load_template(args.name)
    except (KeyError, ValueError) as exc:
        return _invalid(_message(exc))
    plan_args = _plan_args(args)
    if plan_args is None:
        return EXIT_INVALID
    print(json.dumps(raw, indent=2))
    print("\nparams:")
    for name, decl in (raw.get("params") or {}).items():
        print(f"  {_param_line(name, decl)}")
    filled = _placeholder_args(raw, plan_args)
    _print_expansion(raw, {**filled, **plan_args}, sorted(filled))
    return EXIT_OK


def _print_expansion(raw: dict[str, Any], plan_args: dict[str, Any], filled: list[str]) -> None:
    from r4_contracts.team_engine.plan import plan_summary, prepare_plan

    from ..teams.plan_sources import load_template

    program, problems = prepare_plan(raw, plan_args, templates=load_template)
    if problems:
        print("\nexpansion: not bound")
        _print_problems(problems)
        return
    summary = plan_summary(program)
    note = f" (placeholders for {', '.join(filled)})" if filled else ""
    print(f"\nexpanded at these params{note}:")
    _print_phases(summary)
    print(f"  agent bound: {summary['agent_bound']}")


def _placeholder_args(raw: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    """A stand-in for each required param without a default, so ``show`` can
    expand the template: a string never changes a count (D-4: counts come
    from integer params), and a number takes its declared minimum."""
    out: dict[str, Any] = {}
    for name, decl in (raw.get("params") or {}).items():
        if name in given or not isinstance(decl, dict) or not decl.get("required"):
            continue
        maker = _PLACEHOLDERS.get(decl.get("type"), lambda n, d: f"<{n}>")
        out[name] = maker(name, decl)
    return out


# ── validate ────────────────────────────────────────────────────────────────


def _cmd_validate(args: Namespace, _client: Any) -> int:
    """Every problem at once (D-4); exit 2 when there is any."""
    loaded = _load(args)
    if loaded is None:
        return EXIT_INVALID
    from ..teams.plan.engine import spec_from_plan
    from ..teams.plan_sources import load_template

    spec, problems = spec_from_plan(*loaded, project="", templates=load_template)
    if problems or spec is None:
        print(f"invalid: {len(problems)} problem(s)")
        _print_problems(problems)
        return EXIT_INVALID
    from r4_contracts.team_engine.plan import plan_summary

    summary = plan_summary(spec.plan or {})
    steps = sum(len(phase["steps"]) for phase in summary["phases"])
    print(
        f"valid: {summary['name']}: {len(summary['phases'])} phase(s), {steps} step(s), "
        f"agent bound {summary['agent_bound']}, budget {_budget_text(summary['budget'])}"
    )
    return EXIT_OK


# ── run ─────────────────────────────────────────────────────────────────────


def _cmd_run(args: Namespace, client: Any) -> int:
    loaded = _load(args)
    if loaded is None:
        return EXIT_INVALID
    mismatch = _project_mismatch(getattr(args, "project", None))
    if mismatch:
        return _invalid(mismatch)
    from ..teams.plan.engine import spec_from_plan
    from ..teams.plan_sources import load_template
    from .teamup import _launch_project

    spec, problems = spec_from_plan(
        *loaded,
        project=_launch_project(args, client),
        max_tokens=args.max_tokens,
        concurrency=args.concurrency,
        default_model=args.model,
        templates=load_template,
    )
    if problems or spec is None:
        print(f"invalid: {len(problems)} problem(s)")
        _print_problems(problems)
        return EXIT_INVALID
    if args.dry_run:
        return _dry_run(_service(client), spec)
    if getattr(args, "venue", "local") == "server":
        return _run_on_server(args, client, _service(client), spec)
    service = _local_service(args, client)
    return _stream_to_exit(lambda: service.submit(spec, durable=args.durable))


def _project_mismatch(project: str | None) -> str | None:
    """Why ``--project`` cannot scope this run, or None.

    The ADR-062 gate and the model preflight (D-6) check the ACTIVE project
    (``R4_PROJECT_ID``, then ``r4 project switch``), never the spec's. A
    run labelled with another project was gated as the wrong one: a public
    route was not refused for a sensitive ``--project``. Until the gate takes
    the run's project, ``--project`` must name the active project.
    """
    if not project:
        return None
    from ..project import get_project_manager

    active = get_project_manager().get_current()
    if active and str(active) == project:
        return None
    return (
        f"--project {project} is not the active project ({active or 'none'}): the ADR-062 "
        "gate and the model preflight check the active project, so this run would be "
        f"gated as the wrong one. Run `r4 project switch {project}` (or set "
        f"R4_PROJECT_ID={project}) first."
    )


def _dry_run(service: Any, spec: Any) -> int:
    """What a real run would start and spend; nothing executes."""
    from r4_teamengine_spi.errors import UnknownEngineError

    try:
        resolution = service.resolve(spec)
    except UnknownEngineError as exc:
        return _invalid(str(exc))
    _print_dry_run(spec, resolution)
    issues = service.registry.get(resolution.engine_name).validate(spec)
    for issue in issues:
        print(f"  [{issue.severity}] {issue.field}: {issue.message}")
    if any(issue.severity == "error" for issue in issues):
        return EXIT_INVALID
    print("(dry-run: nothing executed)")
    return EXIT_OK


def _print_dry_run(spec: Any, resolution: Any) -> None:
    """The engine and why, the project, phases and steps, the agent bound,
    the models and tools that will run, and the budget (the D-9 card's facts).

    Agents are listed per step and stage with their EFFECTIVE model, as the
    approval card lists them (``agent_lines``): a per-profile list hid a
    step's own ``model``, so the dry run named a model that never ran.
    """
    from r4_contracts.team_engine.plan import plan_summary

    from ..companion.team_plan_tools import agent_lines

    summary = plan_summary(spec.plan or {})
    default_model = (spec.context or {}).get("default_model")
    print(f"engine:   {resolution.engine_name} ({resolution.reason})")
    print(f"project:  {spec.project or '(none)'}")
    print(f"plan:     {summary['name']}: {_first_sentence(spec.task.objective)}")
    print(f"params:   {summary['params']}")
    print("phases:")
    _print_phases(summary)
    print(f"agent bound: {summary['agent_bound']}, concurrency {summary['concurrency']}")
    models, *agents = agent_lines(spec.plan or {}, default_model, _CLIENT_DEFAULT_MODEL)
    print(models)
    print("agents:")
    for line in agents:
        print(f"  {line}")
    print(f"budget:   {_budget_text(summary['budget'])}")
    for text in summary.get("warnings") or []:
        print(f"warning:  {_printable(text)}")
    print(f"tools:    {_FAIL_CLOSED}")


# ── resume / result ─────────────────────────────────────────────────────────


def _cmd_resume(args: Namespace, client: Any) -> int:
    """Attempt N+1 in the same run directory; unchanged calls replay (D-8)."""
    if getattr(args, "venue", "local") == "server":
        return _resume_on_server(args, client)
    service = _local_service(args, client)
    durable = getattr(args, "durable", None)
    return _stream_to_exit(lambda: _resume_start(service, args.run_id, args.from_phase, durable))


async def _resume_start(
    service: Any, run_id: str, from_phase: str | None, durable: bool | None = None
) -> Any:
    """``service.resume``, with an unknown ``--from-phase`` as invalid input.

    The journal refuses a phase the plan does not have with a KeyError
    before any attempt starts (D-8). It is a mistyped argument, exit 2 as
    for an unknown run, not a failed run (exit 1).
    """
    try:
        return await service.resume(run_id, from_phase=from_phase, durable=durable)
    except KeyError as exc:
        raise ValueError(_message(exc)) from exc


def _cmd_result(args: Namespace, _client: Any) -> int:
    import json
    from pathlib import Path

    from r4_contracts.team_engine.plan import resolve_path

    run_id = args.run_id
    if not run_id or Path(run_id).name != run_id or run_id in (".", ".."):
        return _invalid(f"invalid team run id {run_id!r}")
    path = _runs_root() / run_id / "result.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        doc = _server_result_doc(_client, run_id)
        if doc is None:
            print(f"no readable result for {run_id}: {exc}", file=sys.stderr)
            return EXIT_FAILED
    value = resolve_path(doc, args.path) if args.path else doc
    print(_json_escaped(json.dumps(value, indent=2, ensure_ascii=False)))
    return EXIT_OK


# ── the server venue (slice 8) ──────────────────────────────────────────────


def _venue_kwargs(args: Namespace, client: Any) -> dict[str, Any]:
    """The server venue's start options; the web access is the user's choice
    (ADR-099 D-2: flag, R4_WEB_ACCESS, the question on a terminal, else the
    remembered one; a WebAccessError (a ValueError) is invalid input)."""
    from .web_access_prompt import cli_web_flag, remote_web_access

    sources = [s.strip() for s in str(args.venue_sources or "").split(",") if s.strip()]
    flag, domains = cli_web_flag(args, "venue_web_policy", "venue_web_domains")
    access = remote_web_access(
        flag=flag,
        domains=domains,
        client=client,
        project=str(getattr(getattr(client, "config", None), "project_id", "") or ""),
    )
    return {
        "sources": sources,
        "default_source": args.venue_default_source or "",
        "web_policy": access.mode,
        "web_allow_domains": list(access.domains),
    }


def _run_on_server(args: Namespace, client: Any, service: Any, spec: Any) -> int:
    from ..teams.server_venue import start_server_run

    return _server_start_to_exit(
        args, client, lambda: start_server_run(client, service, spec, **_venue_kwargs(args, client))
    )


def _resume_on_server(args: Namespace, client: Any) -> int:
    from ..teams.server_venue import resume_server_run

    if args.from_phase:
        return _invalid("--from-phase is not available on the server venue yet (slice 8)")
    return _server_start_to_exit(
        args, client, lambda: resume_server_run(client, args.run_id, **_venue_kwargs(args, client))
    )


def _server_start_to_exit(args: Namespace, client: Any, start: Any) -> int:
    """Start (or resume) on the server venue, then follow the run to its end."""
    from ..teams.server_venue import ServerVenueRefused

    try:
        started = start()
    except ServerVenueRefused as exc:
        print(_printable(f"refused: {exc}"), file=sys.stderr)
        return EXIT_FAILED
    except ValueError as exc:
        return _invalid(f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 — a refused start is a failed run, named
        print(_printable(f"error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return EXIT_FAILED
    run_id = started["run_id"]
    print(f"run_id: {run_id}")
    print(f"venue:  server (job {started.get('job_id')}; its worker is starting)")
    if getattr(args, "detach", False):
        print(f"follow: r4 team plan result {run_id}   stop: r4 team plan cancel {run_id}")
        return EXIT_OK
    return _follow_to_exit(client, run_id, started.get("baseline", 0))


def _follow_to_exit(client: Any, run_id: str, baseline: int) -> int:
    from ..teams.server_venue import Follower

    follower = Follower(
        client,
        run_id,
        emit=lambda line: print(_printable(line), flush=True),
        ask=_terminal_gate_answer if sys.stdin.isatty() and sys.stdout.isatty() else None,
        baseline=baseline,
    )
    try:
        done = follower.follow()
    except KeyboardInterrupt:
        print(f"\n(detached: the run goes on; r4 team plan cancel {run_id} stops it)")
        return EXIT_FAILED
    print(f"\nstatus:  {_printable(done.get('status'))}")
    print("durable: umrs (server venue)")
    if done.get("error"):
        print(_printable(f"error: {done['error']}"), file=sys.stderr)
    return EXIT_OK if done.get("status") in _GOOD else EXIT_FAILED


def _terminal_gate_answer(question: str) -> bool | None:
    """The run's owner answers a gate at the terminal (empty = leave it)."""
    for line in str(question).split("\n"):
        print(_printable(line), flush=True)
    try:
        answer = input("approve? [y/n/Enter=leave it in the inbox] ").strip().lower()
    except EOFError:
        return None
    if answer in ("y", "yes"):
        return True
    return False if answer in ("n", "no") else None


def _server_result_doc(client: Any, run_id: str) -> dict[str, Any] | None:
    """A server-hosted run's result (slice 8): the outcome its worker reported."""
    from ..teams.server_venue import server_result

    try:
        done = server_result(client, run_id)
    except Exception:  # noqa: BLE001 — no record anywhere: the caller names it
        return None
    summary = done.pop("summary", None) or {}
    return {"run_id": run_id, "venue": "server", **done, **summary}


def _cmd_cancel(args: Namespace, client: Any) -> int:
    from ..teams.server_venue import cancel_server_run

    try:
        answer = cancel_server_run(client, args.run_id)
    except Exception as exc:  # noqa: BLE001 — named
        print(_printable(f"error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return EXIT_FAILED
    state = "cancelled" if answer.get("cancelled") else f"already {answer.get('job_status')}"
    print(f"{args.run_id}: {state}; worker {((answer.get('venue') or {}).get('state')) or '-'}")
    return EXIT_OK


# ── clean (slice 5) ─────────────────────────────────────────────────────────


def _cmd_clean(args: Namespace, _client: Any) -> int:
    """``clean <run_id>`` (exit 1 when that run may not be cleaned) or
    ``clean --all-finished`` (every qualifying run; the rest are listed)."""
    problem = _clean_problem(args)
    if problem:
        return _invalid(problem)
    options = {"older_than_days": args.older_than, "dry_run": args.dry_run}
    if args.all_finished:
        return _clean_every(options)
    return _clean_one(args.run_id, options)


def _clean_problem(args: Namespace) -> str | None:
    if bool(args.run_id) == bool(args.all_finished):
        return "name one run id, or pass --all-finished (not both)"
    if args.older_than is not None and args.older_than < 0:
        return "--older-than is a number of days, 0 or more"
    return None


def _clean_every(options: dict[str, Any]) -> int:
    from ..teams.plan.clean import clean_all

    reports = [r for r in clean_all(_runs_root(), **options) if r.has_work]
    for report in reports:
        _print_clean(report)
    cleaned = sum(r.eligible for r in reports)
    verb = "would clean" if options["dry_run"] else "cleaned"
    print(f"{verb} {cleaned} run(s); left {len(reports) - cleaned} run(s) untouched")
    return EXIT_FAILED if any(r.errors for r in reports) else EXIT_OK


def _clean_one(run_id: str, options: dict[str, Any]) -> int:
    from pathlib import Path

    from ..teams.plan.clean import clean_run

    root = _runs_root()
    if Path(run_id).name != run_id or run_id in (".", "..") or not (root / run_id).is_dir():
        return _invalid(f"no team run {run_id!r} under {root}")
    report = clean_run(root / run_id, **options)
    _print_clean(report)
    return EXIT_OK if report.eligible and not report.errors else EXIT_FAILED


def _print_clean(report: Any) -> None:
    print(f"{report.run_id}: {_printable(report.reason)}")
    if not report.has_work:
        print("  nothing to clean")
        return
    if not report.eligible:
        print(f"  kept: {len(report.workspaces)} workspace(s), {len(report.branches)} branch(es)")
        return
    done = ("would remove", "would delete") if report.dry_run else ("removed", "deleted")
    for path in report.workspaces:
        print(f"  {done[0]} workspace {_printable(path)}")
    for branch in report.branches:
        print(f"  {done[1]} branch {_printable(branch)}")
    for error in report.errors:
        print(f"  error: {_printable(error)}", file=sys.stderr)


# ── streaming a run ─────────────────────────────────────────────────────────


def _stream_to_exit(start: Any) -> int:
    """Run one attempt to its end, printing progress; the exit code of its status.

    A start the service refuses is named: an unknown run, an invalid run id
    or a spec the engine cannot express is invalid input (2); anything else,
    such as an ADR-062 model refusal, is a failed run (1). The engine itself
    never raises: its failures are nulls with reasons (D-4).
    """
    import asyncio

    try:
        handle, result = asyncio.run(_stream(start))
    except (FileNotFoundError, ValueError) as exc:
        return _invalid(f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a refused start is a failed run, named
        print(_printable(f"error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return EXIT_FAILED
    print(f"\nstatus:  {_printable(result.status)}")
    print(f"run_dir: {handle.run_dir}")
    print(f"result:  {handle.run_dir / 'result.json'}")
    print(_printable(_durable_line(handle.run_dir)))
    if result.error:
        print(_printable(f"error: {result.error}"), file=sys.stderr)
    return EXIT_OK if result.status in _GOOD else EXIT_FAILED


async def _stream(start: Any) -> tuple[Any, Any]:
    handle = await start()
    print(f"run_id: {handle.run_id}")
    async for event in handle.events():
        line = _event_line(event)
        if line:
            print(line, flush=True)
    return handle, await handle.result()


def _event_line(event: Any) -> str:
    """One progress line, control characters escaped: log text and labels
    may carry agent output (``{{steps.<id>.output…}}``), untrusted (D-6)."""
    kind = getattr(event.kind, "value", event.kind)
    render = _EVENT_LINES.get(kind)
    return _printable(render(event.payload or {})) if render else ""


def _started_line(p: dict[str, Any]) -> str:
    return (
        f"[run] {p.get('plan')} {p.get('attempt')}: agent bound {p.get('agent_bound')}, "
        f"concurrency {p.get('concurrency')}, journal {p.get('journal')} "
        f"({p.get('replayable', 0)} replayable)"
    )


def _message_line(p: dict[str, Any]) -> str:
    kind = p.get("type")
    if kind == "phase":
        return f"[phase] {p.get('id')}: {p.get('title') or ''}".rstrip()
    if kind == "budget_exhausted":
        return f"[budget] {p.get('text')}"
    if kind == "log":
        return f"[log] {p.get('step')}: {p.get('text')}"
    if kind == "gate":
        return _gate_note_line(p)
    if kind == "durable":
        return f"[durable] {p.get('text')}"
    return ""


def _durable_line(run_dir: Any) -> str:
    """Where the run's records are (slice 7): UMRS and its state, or local-only."""
    try:
        durable = json.loads((run_dir / "result.json").read_text()).get("durable") or {}
    except (OSError, ValueError, AttributeError):
        return "durable: unknown (no result.json)"
    if durable.get("home") != "umrs":
        return "durable: local-only (this run directory)"
    line = f"durable: umrs {durable.get('state')} (job {durable.get('job_id') or '?'})"
    if durable.get("error"):
        line += f"; {durable['error']}"
    return line


def _gate_note_line(p: dict[str, Any]) -> str:
    if not p.get("raised"):
        return f"[gate] {p.get('gate')}: not raised"
    if p.get("approved"):
        verdict = "approved"
    elif p.get("answered"):
        verdict = "rejected: the run stops"
    else:
        verdict = f"refused ({p.get('reason') or 'no human'}): the run stops"
    replayed = " (journalled answer)" if p.get("replayed") else ""
    return f"[gate] {p.get('gate')} ({p.get('class')}): {verdict}{replayed}"


def _human_line(p: dict[str, Any]) -> str:
    return f"[gate] {p.get('gate')} ({p.get('class')}): asking a human"


def _agent_line(p: dict[str, Any]) -> str:
    """One agent's verdict, naming any tool its fork refused.

    The CLI runs agents fail-closed and promises the refused tools are
    named (D-6). An agent that still answers reads ``ok``, so without the
    engine's ``refused_tools`` on this line a run looked clean although a
    tool it relied on never ran (ADR-092 D-6, finding 10).
    """
    if p.get("replayed"):
        line = f"[agent] {p.get('label')} ok (replayed from the journal)"
    else:
        verdict = "ok" if p.get("ok") else f"null({p.get('reason') or 'unknown'})"
        line = f"[agent] {p.get('label')} {verdict} {p.get('tokens', 0)} tokens"
    refused = p.get("refused_tools")
    if isinstance(refused, list) and refused:
        line += f"; refused tools: {', '.join(str(tool) for tool in refused)}"
    return line


def _warning_line(p: dict[str, Any]) -> str:
    return (
        f"[budget] warning: {p.get('spent_tokens')} of {p.get('max_tokens')} tokens spent "
        f"({p.get('remaining')} left)"
    )


def _terminal_line(p: dict[str, Any]) -> str:
    budget = p.get("budget") or {}
    return (
        f"[budget] spent {budget.get('spent_tokens', 0)} tokens, "
        f"{budget.get('agents_started', 0)} agent(s) started; "
        f"remaining {budget.get('remaining')}"
    )


_EVENT_LINES: dict[str, Any] = {
    "run_started": _started_line,
    "agent_message": _message_line,
    "human_input_requested": _human_line,
    "agent_completed": _agent_line,
    "budget_warning": _warning_line,
    "run_completed": _terminal_line,
    "run_failed": _terminal_line,
    "run_cancelled": _terminal_line,
}


# ── helpers ─────────────────────────────────────────────────────────────────


def _load(args: Namespace) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(raw plan, plan args)`` from a template name or a plan file, or None
    after naming the problem."""
    from ..teams.plan_sources import resolve_plan_source

    try:
        raw = resolve_plan_source(args.ref)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        _invalid(_message(exc))
        return None
    plan_args = _plan_args(args)
    return None if plan_args is None else (raw, plan_args)


def _plan_args(args: Namespace) -> dict[str, Any] | None:
    """``--args-json`` (text or ``@file``) overlaid by every ``--arg K=V``."""
    values = _args_json(getattr(args, "args_json", None))
    if values is None:
        return None
    for pair in getattr(args, "arg", None) or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            _invalid(f"--arg {pair!r}: expected K=V")
            return None
        values[key.strip()] = value
    return values


def _args_json(text: str | None) -> dict[str, Any] | None:
    import json
    from pathlib import Path

    if not text:
        return {}
    try:
        if text.startswith("@"):
            text = Path(text[1:]).expanduser().read_text(encoding="utf-8")
        values = json.loads(text)
    except (OSError, ValueError) as exc:
        _invalid(f"--args-json: {exc}")
        return None
    if not isinstance(values, dict):
        _invalid("--args-json: a JSON object of param values")
        return None
    return values


def _service(client: Any, web_access: Any = None) -> Any:
    """A TeamService over the current run root and no permission manager:
    the broker then fails closed (D-6). A plan ``gate`` asks the person at
    the terminal when there is one, and is refused by name when there is not
    (slice 2): agents' tools stay fail-closed either way. ``web_access`` is
    the user's choice the agents' web tools follow (ADR-099 D-2)."""
    from ..teams.service import TeamService

    return TeamService(client, run_root=_runs_root(), gate_lane=_gate_lane(), web_access=web_access)


def _local_service(args: Namespace, client: Any) -> Any:
    """The service of a run on this machine, with the user's web choice:
    ``--web`` (or ``r4 --web``), the question on a terminal, else the
    non-interactive order (RS-1 round-2 fix W2-2: the agents refused every
    web call, and ``--web`` was read on the server venue only). The agents'
    processes run in the local sandbox ``r4 --sandbox`` (else ``R4_SANDBOX``,
    the profile default) sets (ADR-101), settled before the web question."""
    from .sandbox_prompt import cli_agent_sandbox_and_web
    from .web_access_prompt import cli_agent_web_getter

    config = getattr(client, "config", None)
    getter = cli_agent_sandbox_and_web(
        args,
        config,
        lambda: cli_agent_web_getter(
            args,
            config,
            flag_attr="venue_web_policy",
            domains_attr="venue_web_domains",
        ),
    )
    return _service(client, web_access=getter)


def _gate_lane() -> Any:
    """A terminal human for plan gates, or None (no terminal: gates refuse).

    Its own manager, asking every time and used only for the gate's
    never-auto approval, so a terminal never widens what agents may run."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    from ..companion.permissions import PermissionManager, PermissionMode

    return PermissionManager(PermissionMode.ASK_ALWAYS, approval_callback=_ask_terminal)


def _ask_terminal(_tool: str, description: str, _arguments: dict) -> bool:
    """Show the gate's question (agent-written parts escaped) and read y/N."""
    for line in str(description).split("\n"):
        print(_printable(line), flush=True)
    try:
        answer = input("approve? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _runs_root() -> Any:
    """Read at call time (the service's default is bound at import)."""
    from pathlib import Path

    from ..teams import service

    return Path(service.TEAM_RUNS_DIR)


def _print_phases(summary: dict[str, Any]) -> None:
    for phase in summary["phases"]:
        steps = ", ".join(str(step) for step in phase["steps"])
        print(f"  {phase['id']}: {phase['title']} [{steps}]")


def _print_problems(problems: list[Any]) -> None:
    """A problem may quote the plan file, which an agent may have written (D-6)."""
    for problem in problems:
        print(_printable(f"  - {problem}"))


def _param_line(name: str, decl: Any) -> str:
    if not isinstance(decl, dict):
        return f"{name}: (malformed declaration)"
    text = f"{name}: {decl.get('type', '?')}"
    if decl.get("required"):
        text += " (required)"
    elif "default" in decl:
        text += f" = {decl['default']!r}"
    if decl.get("description"):
        text += f": {decl['description']}"
    return text


def _budget_text(budget: Any) -> str:
    if not isinstance(budget, dict) or not budget:
        return "none declared"
    return ", ".join(f"{key}={value}" for key, value in budget.items())


def _first_sentence(text: str) -> str:
    head = (text or "").strip().split(". ")[0]
    return head if len(head) <= 100 else head[:99] + "…"


def _message(exc: BaseException) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _invalid(message: str) -> int:
    print(_printable(f"error: {message}"), file=sys.stderr)
    return EXIT_INVALID


def _printable(text: Any) -> str:
    """``text`` with every terminal control character shown as ``\\xNN`` /
    ``\\uNNNN``: an agent's ESC or OSC sequence (erase a line, write the
    clipboard) is displayed, never executed (D-6)."""
    return _CONTROL.sub(lambda m: _escape(m.group()), str(text))


def _json_escaped(text: str) -> str:
    """JSON text with the controls ``json.dumps`` leaves raw under
    ``ensure_ascii=False`` (C1, DEL, separators, bidi overrides) as JSON
    escapes, so it stays valid JSON and prints inert."""
    return _JSON_RAW_CONTROL.sub(lambda m: "\\u%04x" % ord(m.group()), text)


def _escape(char: str) -> str:
    code = ord(char)
    return "\\x%02x" % code if code < 0x100 else "\\u%04x" % code


_HANDLERS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "validate": _cmd_validate,
    "run": _cmd_run,
    "resume": _cmd_resume,
    "result": _cmd_result,
    "cancel": _cmd_cancel,
    "clean": _cmd_clean,
}
