"""Team plan chat tools: the lead authors, runs and reads plans (ADR-092 D-9).

The chat agent is the lead (ADR-075 D-1). It gets four tools:

- ``team_plan_templates`` (safe): the built-in templates and their params.
- ``team_plan_run`` (RISKY): validates first, so an invalid plan returns
  every problem and spends nothing; otherwise it runs the plan through
  ``TeamService`` on a daemon thread, waits up to ``wait_sec`` and returns a
  bounded structured result (D-9).
- ``team_plan_result`` (safe): progress while a run is going, or a slice of
  its ``result.json``.
- ``team_plan_cancel`` (RISKY): cancels a run this process started, by id.
  The engine stops the run as a unit (D-3): agents in flight get the
  cancellation and are journalled as ``cancelled`` nulls, the rest never
  start, and the run ends ``cancelled``. A later ``resume`` continues it by
  the journal's rules (D-8): ok calls replay, the cancelled ones run live.

Plan agents never see these tools: ``TEAM_AGENT_DENY`` lists every one of
them, so a plan cannot start a plan (D-6; ADR-075 D-2 keeps one driver).

Taint (ADR-039, D-6): whenever a finished result comes back, the run's
excerpts are imported into the lead's registry, so plan output cannot
launder untrusted content into the lead's later tool arguments. Results are
pretty-printed one value per line so the fence's per-line excerpts of this
tool's own output see each value.

Runs are client-local daemon threads until slice 7 (MD-4). The registry
below is process-local and is not a job store (CONSTITUTION §6): it maps a
run id to its thread and live progress only, and every finished answer is
read back from the run's own ``result.json``.

Slice 3 (surfaces): every run publishes its events, keyed by run id and
agent id, to ``team_plan_events`` listeners (the console bridge writes them
as ``team_event`` lines), and its agents' rounds and tool calls reach them
through the broker's observer. A plan started in a chat turn is cancelled
with that turn (``turn_cancel``, ``cancel_from_turn``). The console's
``team.plan_*`` RPCs run this same executor (``executor_of``).

The approval card (D-9) comes from the same ``_PlanTools`` that runs the
call: ``build_team_plan_tools`` registers ``describe_run`` on the lead's
permission manager, and the card and ``run`` both go through ``prepare``.
The card therefore validates what the tool will run, with its run root and
the session's current model, and names the model and tools of every agent
step and stage (``agent_lines``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r4_contracts.team_engine.plan import plan_summary, resolve_path

from . import team_plan_events as plan_events

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 12 * 1024  # D-9: the declared outputs, up to 12 KB
DEFAULT_WAIT_SEC = 240
MAX_WAIT_SEC = 600
MAX_REASON_CHARS = 240
MAX_ERROR_CHARS = 400
MAX_KEPT_RUNS = 64
CANCEL_WAIT_SEC = 30.0  # how long team_plan_cancel waits for the run to settle
SESSION_MODEL_LABEL = "the session's model"  # an agent whose plan names no model

_TERMINAL = frozenset({"success", "partial", "failed", "cancelled"})
_GOOD = frozenset({"success", "partial"})
_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_NULL_NOTE = (
    "A null is 'unknown', never 'passed': read its null_reasons, then narrow, re-run "
    "or resolve it before you rely on that step."
)
_POLL_NOTE = (
    "The run continues in the background, bounded by its own budget; poll "
    "team_plan_result(run_id) for progress and the result."
)
_INVALID_NOTE = "Nothing ran and nothing was spent. Fix every problem and call again."
INVALID_NOTE = _INVALID_NOTE  # the bridge's team.plan_run answers with it too
_RESUME_ONLY = "resume takes only the run_id: the attempt reruns the plan it recorded"

TEMPLATES_DESCRIPTION = (
    "List the built-in team plan templates (ADR-092): name, description and params "
    "(type, default, required). Run one with team_plan_run(template=<name>, args={...}); "
    "prefer a template to an inline plan when one fits."
)
RUN_DESCRIPTION = (
    "Run a team plan: a declarative multi-agent orchestration you author as data "
    "(ADR-092). Give a built-in `template` (team_plan_templates lists them) with "
    "`args`, OR an inline `plan` object: {plan_version: 1, name, description, params, "
    "agents: {profile: {instructions, tools, model, max_tool_rounds}}, concurrency, "
    "budget: {max_tokens (required), max_agents, max_wallclock_sec, max_tool_calls}, "
    "phases: [{id, title, steps: [agent | map | reduce | pattern]}], output: "
    "{name: 'steps.<id>.output…'}}. The plan is validated first: an invalid plan "
    "returns every problem and spends nothing. A valid plan runs in the background; "
    "this call waits up to wait_sec and returns status (success|partial|failed|"
    "cancelled|running), the declared outputs (up to 12 KB), per-step ok/null counts "
    "with null reasons, budget spent and remaining, and the run_id. A null is "
    "'unknown', never 'passed'. `resume`=<run_id> starts the next attempt of a run and "
    "replays its unchanged agent calls at zero model cost. Author ONE plan per phase "
    "(understand, design, implement, review) and read each result before writing the "
    "next."
)
CANCEL_DESCRIPTION = (
    "Cancel a running team plan by its run_id. Agents in flight are stopped and recorded "
    "as cancelled nulls; agents not yet started never start; the run ends 'cancelled'. "
    "Returns the run's final result view. team_plan_run(resume=<run_id>) later continues "
    "it: unchanged ok agent calls replay, the cancelled ones run again."
)
RESULT_DESCRIPTION = (
    "Read a team plan run: live progress while it runs (phase, agents ok/null, tokens), "
    "or its finished result. `path` slices result.json by a dotted path (e.g. "
    "'output.survivors', 'output.findings.3', 'steps.verify', 'budget') to read an "
    "entry that team_plan_run truncated."
)


@dataclass
class _LiveRun:
    """One run started in this process: its thread's live progress."""

    run_id: str
    run_dir: Path
    plan: str
    status: str = "starting"
    attempt: str = ""
    phase: str = ""
    agents_ok: int = 0
    agents_null: int = 0
    tokens: int = 0
    budget_warning: bool = False
    error: str | None = None
    result_path: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    # The run's handle and the event loop it runs on (its own thread), once
    # started; a cancel asked before that is held here and applied at start.
    handle: Any = None
    loop: Any = None
    cancel_requested: str | None = None


_LOCK = threading.Lock()
_RUNS: dict[str, _LiveRun] = {}


def build_team_plan_tools(
    client: Any,
    *,
    session_model: str | Callable[[], str | None] | None = None,
    permission_manager: Any = None,
    usage_hooks: Any = None,
    run_root: Path | None = None,
    turn_cancel: Callable[[], Any] | None = None,
    web_access: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """The lead's three team plan tools (ADR-092 D-9).

    ``permission_manager``, ``session_model`` and ``usage_hooks`` are the
    lead's governance handles, handed to ``TeamService`` so its agent
    broker forks the lead's manager per agent and charges every agent round
    to this chat session's usage records and caps (D-6, D-7).

    ``session_model`` may be the session's model accessor, read at each
    call: a name fixed at build time outlived a ``/model`` switch (D-6).
    The manager is handed this executor's ``describe_run``, so its
    ``team_plan_run`` card is rendered by the code the call runs (D-9).

    ``turn_cancel`` is the session's accessor for the running chat turn's
    cancellation token (slice 3): a plan a turn starts is cancelled through
    the engine's own ``cancel`` when that turn is cancelled.

    ``web_access`` is the session's web-access accessor: the plan's agents'
    web tools follow the lead's choice (ADR-099 D-9, RS-1 fix W3).
    """
    tools = _PlanTools(
        client, session_model, permission_manager, usage_hooks, run_root, turn_cancel, web_access
    )
    register = getattr(permission_manager, "set_tool_describer", None)
    if callable(register):
        register("team_plan_run", tools.describe_run)
    return [
        _tool(
            "team_plan_templates",
            TEMPLATES_DESCRIPTION,
            {"type": "object", "properties": {}},
            tools.templates,
            concurrency_safe=True,
        ),
        _tool("team_plan_run", RUN_DESCRIPTION, _RUN_PARAMETERS, tools.run),
        _tool(
            "team_plan_result",
            RESULT_DESCRIPTION,
            _RESULT_PARAMETERS,
            tools.result,
            concurrency_safe=True,
        ),
        _tool("team_plan_cancel", CANCEL_DESCRIPTION, _CANCEL_PARAMETERS, tools.cancel),
    ]


def _tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    execute: Callable[..., str],
    *,
    concurrency_safe: bool = False,
) -> dict[str, Any]:
    """A companion tool dict (the shape ``tools._tool`` builds; imported from
    there it would be circular, as tools.py imports this module)."""
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
        "concurrency_safe": concurrency_safe,
    }


_RUN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "object",
            "description": "An inline plan (plan_version 1). Give this or template.",
        },
        "template": {
            "type": "string",
            "description": "A built-in template name (team_plan_templates). Give this or plan.",
        },
        "args": {
            "type": "object",
            "description": "Values for the plan's declared params, {name: value}.",
        },
        "max_tokens": {
            "type": "integer",
            "description": "Override the plan's budget.max_tokens (a hard ceiling).",
        },
        "concurrency": {
            "type": "integer",
            "description": "Override the plan's concurrency (1-8 agents at a time).",
        },
        "wait_sec": {
            "type": "integer",
            "description": f"Seconds to wait for the result (0-{MAX_WAIT_SEC}); then 'running'.",
            "default": DEFAULT_WAIT_SEC,
        },
        "resume": {
            "type": "string",
            "description": "A run_id to resume as its next attempt (no plan or template).",
        },
        "durable": {
            "type": "boolean",
            "description": (
                "true: require the run's durable record in UMRS (refused without a UMRS "
                "source; the run stops if a record cannot be written). Omitted: records go "
                "to UMRS when a source is configured, a failure is named and the run "
                "continues local-only."
            ),
        },
        "venue": {
            "type": "string",
            "enum": ["local", "server"],
            "description": (
                "local (default): run here. server: a sandboxed worker on the UMRS site's "
                "frontend venue runs it (ADR-092 slice 8); its record is UMRS's, its gates "
                "wait in the UMRS approvals inbox for the user, and a site without the "
                "venue refuses by name (plan_iteration_needs_server_venue)."
            ),
        },
        "sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": "venue=server: the user's pushed source(s) serving the models.",
        },
    },
}
# The keywords ``_PlanTools.run`` takes; any other non-null argument fails
# the call before anything runs (the executor takes keywords only).
RUN_ARGUMENTS: frozenset[str] = frozenset(_RUN_PARAMETERS["properties"])
_RESULT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "The run_id team_plan_run returned"},
        "path": {
            "type": "string",
            "description": "Optional dotted path into result.json, e.g. output.survivors",
        },
    },
    "required": ["run_id"],
}


_CANCEL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "The run_id team_plan_run returned"},
        "reason": {"type": "string", "description": "Why, recorded with the run"},
    },
    "required": ["run_id"],
}


@dataclass(frozen=True)
class _PlanCall:
    """One ``team_plan_run`` call as ``run`` will execute it (D-9).

    ``given`` is the call without its null arguments, as the executor gets
    it; ``spec`` is the bound plan a fresh run starts, or the recorded plan a
    resume reruns; ``problems`` is why the tool refuses it before any spend.
    """

    given: dict[str, Any]
    problems: tuple[str, ...] = ()
    spec: Any = None
    run_dir: Path | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def resume(self) -> str:
        return str(self.given.get("resume") or "")


class _PlanTools:
    """The tool executors, bound to one chat session's handles."""

    def __init__(
        self,
        client: Any,
        session_model: str | Callable[[], str | None] | None,
        permission_manager: Any,
        usage_hooks: Any,
        run_root: Path | None,
        turn_cancel: Callable[[], Any] | None = None,
        web_access: Callable[[], Any] | None = None,
    ) -> None:
        self._client = client
        self._web_access = web_access
        self._session_model = session_model
        self._permissions = permission_manager
        self._usage_hooks = usage_hooks
        self._run_root = Path(run_root) if run_root is not None else None
        self._turn_cancel = turn_cancel

    def _model_now(self) -> str | None:
        """The session's model at this call (ADR-092 D-6, D-9).

        Read per call, not at build time: the tools are built once per
        session, so a ``/model`` switch to a local model did not reach the
        next plan, whose agents, preflight and card kept the old route.
        ``session_model`` is the session's accessor, or a fixed name.
        """
        source = self._session_model
        value = source() if callable(source) else source
        return str(value) if value else None

    # ── team_plan_templates ────────────────────────────────────────────────

    def templates(self) -> str:
        from ..teams.plan.templates import list_templates

        return _dump(
            {
                "templates": list_templates(),
                "next": "team_plan_run(template=<name>, args={<param>: <value>})",
            }
        )

    # ── team_plan_run ──────────────────────────────────────────────────────

    def run(
        self,
        plan: Any = None,
        template: str | None = None,
        args: Any = None,
        max_tokens: Any = None,
        concurrency: Any = None,
        wait_sec: Any = DEFAULT_WAIT_SEC,
        resume: str | None = None,
        durable: Any = None,
        venue: str | None = None,
        sources: Any = None,
    ) -> str:
        """Validate, then run and wait (D-9). Validation spends nothing."""
        if self._client is None:
            return _dump({"ok": False, "error": "team plans need a connected client"})
        call = self.prepare(
            {
                "plan": plan,
                "template": template,
                "args": args,
                "max_tokens": max_tokens,
                "concurrency": concurrency,
                "wait_sec": wait_sec,
                "resume": resume,
                "durable": durable,
                "venue": venue,
                "sources": sources,
            }
        )
        if call.problems or call.spec is None:
            return _dump({"ok": False, "problems": list(call.problems), "note": _INVALID_NOTE})
        grants = self._card_grants(call)  # one-shot: consumed whatever the venue
        if call.given.get("venue") == "server":
            return _dump(self._on_server(call, wait_sec))
        if call.resume:
            return self._start_resume(call, wait_sec, grants)
        return self._start(call.spec, wait_sec, grants, _durable_arg(call.given))

    def _card_grants(self, call: _PlanCall) -> frozenset[str]:
        """The SAFE tools this call's approved card listed (ADR-092 MD-6).

        Only when a human approved this exact call's card on the lead's
        manager (``take_card_approval``, one-shot): a run nobody approved
        (no manager, no callback) grants nothing, and its agents ask as the
        lead's mode says. The grant reaches this run's agents only.
        """
        take = getattr(self._permissions, "take_card_approval", None)
        if not callable(take) or not take("team_plan_run", call.given):
            return frozenset()
        return card_safe_tools(call.spec.plan or {})

    def prepare(self, arguments: dict[str, Any]) -> _PlanCall:
        """The call ``run`` makes of ``arguments``, validated (D-9).

        The approval card and the executor both come through here, so the
        card never summarises a call the tool refuses nor refuses one it
        runs. A null argument is dropped first, as the companion drops it
        before the executor sees it (``agent.py`` ``_without_null_optionals``;
        this tool has no required parameter): ``"run_id": null`` once made
        the card say the call fails while the executor ran the whole plan.
        """
        given = {key: value for key, value in arguments.items() if value is not None}
        unknown = sorted(set(given) - RUN_ARGUMENTS)
        if unknown:
            return _PlanCall(given, (_unknown_arguments(unknown),))
        if given.get("resume"):
            return self._prepare_resume(given)
        spec, problems = self._spec(
            given.get("plan"),
            given.get("template"),
            given.get("args"),
            given.get("max_tokens"),
            given.get("concurrency"),
        )
        return _PlanCall(given, tuple(problems), spec=spec)

    def _spec(
        self, plan: Any, template: Any, args: Any, max_tokens: Any, concurrency: Any
    ) -> tuple[Any, list[str]]:
        """The bound, expanded TeamSpec v2, or every problem (D-4)."""
        raw, problems = _plan_source(plan, template)
        plan_args, arg_problems = _json_object(args, "args")
        problems += arg_problems
        if problems or raw is None:
            return None, problems
        from ..teams.plan.engine import spec_from_plan
        from ..teams.plan_sources import load_template

        spec, plan_problems = spec_from_plan(
            raw,
            plan_args,
            project=self._project(),
            max_tokens=_maybe_int(max_tokens),
            concurrency=_maybe_int(concurrency),
            default_model=self._model_now(),
            tool_capabilities=self._mcp_capabilities(raw),
            templates=load_template,
        )
        return spec, [str(problem) for problem in plan_problems]

    def _mcp_capabilities(self, raw: dict[str, Any]) -> dict[str, Any]:
        """The declared capabilities of the MCP tools a plan lists (D-6), read
        from the client's MCP registry only when a profile lists one: an MCP
        tool not declared read-only is a writer, refused in parallel steps."""
        if self._client is None or not _lists_mcp_tool(raw):
            return {}
        from .mcp_first_class import mcp_tool_capabilities

        return {name: list(flags) for name, flags in mcp_tool_capabilities(self._client).items()}

    def _prepare_resume(self, given: dict[str, Any]) -> _PlanCall:
        """A resume reruns the plan its run recorded (D-8).

        The recorded plan is rebuilt here exactly as ``TeamService.resume``
        rebuilds it, so a run that cannot resume is refused before any
        spend, and the card shows the plan it reruns: a resume card that
        named no budget, bound, tools or models asked the user to approve
        a plan they could not see.
        """
        if given.get("plan") is not None or given.get("template"):
            return _PlanCall(given, (_RESUME_ONLY,))
        run_id = str(given["resume"])
        run_dir, problem = _safe_run_dir(self._root(), run_id)
        if problem is None and not (run_dir / "resolution.json").is_file():
            problem = self._hydrate(run_id)
        if problem is not None:
            return _PlanCall(given, (problem,))
        manifest, spec, problem = _recorded_spec(run_dir)
        problems = (problem,) if problem else ()
        return _PlanCall(given, problems, spec=spec, run_dir=run_dir, manifest=manifest)

    def _hydrate(self, run_id: str) -> str | None:
        """Rebuild a run this machine lacks from its durable record (slice 7),
        so the card shows the recorded plan; why not, when it cannot."""
        try:
            self._service().hydrate(run_id)
        except (FileNotFoundError, ValueError) as exc:
            return str(exc)
        return None

    def _start(
        self,
        spec: Any,
        wait_sec: Any,
        grants: frozenset[str] = frozenset(),
        durable: bool | None = None,
    ) -> str:
        run_id = _fresh_run_id(spec.run_name)
        live = _LiveRun(run_id=run_id, run_dir=self._root() / run_id, plan=spec.run_name)
        _register(live)
        self._bind_turn(live)
        service = self._service(grants, live)
        _spawn(live, lambda: service.submit(spec, run_id=run_id, durable=durable))
        return self._answer(live, wait_sec)

    def _start_resume(
        self, call: _PlanCall, wait_sec: Any, grants: frozenset[str] = frozenset()
    ) -> str:
        """The next attempt of a run, rebuilt by the service from its plan.json (D-8)."""
        run_id = call.resume
        plan_name = str(call.manifest.get("run_name") or run_id)
        live = _LiveRun(run_id=run_id, run_dir=call.run_dir or self._root(), plan=plan_name)
        if not _register(live):
            return _dump(
                {"ok": False, "error": f"run {run_id} is still running", "next": _POLL_NOTE}
            )
        self._bind_turn(live)
        service = self._service(grants, live)
        durable = _durable_arg(call.given)
        _spawn(live, lambda: service.resume(run_id, durable=durable))
        return self._answer(live, wait_sec)

    # ── the server venue (slice 8) ─────────────────────────────────────────

    def _on_server(self, call: _PlanCall, wait_sec: Any) -> dict[str, Any]:
        """Start (or resume) the run on the server venue, then wait up to
        ``wait_sec`` for its outcome. The human approved this call's card;
        its gates wait in the UMRS inbox for the user (this tool never
        answers them), and no SAFE-tool grant crosses to the worker (its
        agents run fail-closed there, as a CLI run's do)."""
        from ..teams.server_venue import (
            ServerVenueRefused,
            resume_server_run,
            start_server_run,
        )

        web = self._server_web_fields()
        if web is None:
            from .web_access import REMOTE_UNCHOSEN, WEB_CHOICE_REQUIRED

            return {
                "ok": False,
                "venue": "server",
                "code": WEB_CHOICE_REQUIRED,
                "error": REMOTE_UNCHOSEN,
            }
        venue = {"sources": _source_list(call.given.get("sources")), **web}
        try:
            if call.resume:
                started = resume_server_run(self._client, call.resume, **venue)
            else:
                started = start_server_run(self._client, self._service(), call.spec, **venue)
        except ServerVenueRefused as exc:
            return {"ok": False, "venue": "server", "code": exc.code, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — a refused start is named
            return {"ok": False, "venue": "server", "error": f"{type(exc).__name__}: {exc}"}
        return self._server_view(started["run_id"], started.get("baseline", 0), wait_sec)

    def _server_web_fields(self) -> dict[str, Any] | None:
        """The session's web choice for the server worker (ADR-099 D-2, M-4):
        ``web_policy`` and its domains, None when the user chose nothing (the
        start is then refused here: the server would refuse it, or widen it
        to the site's default; RS-1 round-2 fix W2-1)."""
        from .web_access import ambient_web_access, remote_start_choice

        access = remote_start_choice((self._web_access or ambient_web_access)())
        if access is None:
            return None
        return {"web_policy": access.mode, "web_allow_domains": list(access.domains)}

    def _server_view(self, run_id: str, baseline: int, wait_sec: Any) -> dict[str, Any]:
        from ..teams.server_venue import Follower

        follower = Follower(self._client, run_id, emit=lambda _line: None, baseline=baseline)
        done = follower.follow(timeout_sec=_clamp_wait(wait_sec))
        return self._server_outcome(run_id, done)

    def _server_outcome(self, run_id: str, done: dict[str, Any]) -> dict[str, Any]:
        """The bounded result of a server-hosted run; its output is untrusted
        and absorbed whole as generic taint (D-6: no per-excerpt taint
        crosses from the worker)."""
        summary = done.get("summary") or {}
        output, notes = _capped(summary.get("output"), "output")
        view: dict[str, Any] = {
            "ok": done.get("status") in ("success", "partial"),
            "run_id": run_id,
            "venue": "server",
            "status": done.get("status"),
            "journal": "umrs",
        }
        if done.get("status") == "running":
            view["next"] = f"team_plan_result(run_id={run_id!r}) (gates wait for the user)"
            return view
        view.update(output=output, error=done.get("error") or None, **notes)
        if summary.get("output_truncated"):
            view["output_truncated"] = True
        view["taint_segments"] = self._absorb_taint(
            {"output": summary.get("output"), "taint_truncated": True}
        )
        return view

    def _server_record(self, run_id: str) -> dict[str, Any] | None:
        """A server-hosted run's outcome, when UMRS has its record (slice 8)."""
        from ..teams.server_venue import server_result

        try:
            done = server_result(self._client, run_id)
        except Exception:  # noqa: BLE001 — no venue record: the caller names it
            return None
        return self._server_outcome(run_id, done)

    def _bind_turn(self, live: _LiveRun) -> None:
        """Cancel the run with the chat turn that started it (slice 3).

        The turn's token calls back when the user stops the turn; the run is
        then cancelled through the engine's own ``cancel`` (agents in flight
        are journalled as ``cancelled`` nulls, the rest never start). A run
        started outside a turn (the bridge's ``team.plan_run``, the CLI) has
        no token and is cancelled only by ``team_plan_cancel``. A token that
        is already cancelled cancels the run at once.
        """
        accessor = self._turn_cancel
        token = accessor() if callable(accessor) else None
        register = getattr(token, "register_close", None)
        if callable(register):
            register(functools.partial(cancel_from_turn, live.run_id))

    def _answer(self, live: _LiveRun, wait_sec: Any) -> str:
        live.done.wait(_clamp_wait(wait_sec))
        return _dump(self._view(live.run_id))

    # ── the approval card ──────────────────────────────────────────────────

    def describe_run(self, arguments: dict[str, Any]) -> str:
        """The ``team_plan_run`` approval card (D-9), built from ``prepare``.

        Registered on the lead's permission manager, so the card is computed
        with this executor's run root and the session's current model: it
        shows what the call will start and spend, or why the tool refuses
        it. Never raises: a card never fails the approval.
        """
        try:
            call = self.prepare(arguments if isinstance(arguments, dict) else {})
            if call.problems or call.spec is None:
                return _refused_card(call)
            if call.resume:
                return _resume_card(call, self._model_now())
            return _run_card(call)
        except Exception as exc:  # noqa: BLE001 - a card never fails the approval
            return f"Run team plan: {_card_ref(arguments)} (summary unavailable: {str(exc)[:160]})"

    # ── team_plan_cancel ───────────────────────────────────────────────────

    def cancel(self, run_id: str, reason: str | None = None) -> str:
        """Cancel a run this process started, then wait for it to settle (D-3).

        The engine's own ``cancel`` stops the run as a unit: in-flight agents
        get the cancellation (the broker requests every live turn's token)
        and settle as ``cancelled`` nulls in the journal; the rest never
        start; result.json says ``cancelled`` with the reason. A run that is
        not running here (finished, or started by another process) is not
        touched: the answer says which.
        """
        return self._cancel(str(run_id or ""), _cancel_reason(reason))

    def cancel_by_user(self, run_id: str, reason: str | None = None) -> str:
        """The same cancel, asked for by the human in the console (slice 3).

        Not a tool, and no tool calls it: only the bridge's ``team.plan_cancel``
        RPC (the console's Work-pane ``c``) does. A human's own click needs no
        approval card; the agent-called ``team_plan_cancel`` tool (``cancel``
        above) keeps its risky-tool rule. The reason records who cancelled.
        """
        return self._cancel(str(run_id or ""), _user_cancel_reason(reason))

    def _cancel(self, run_id: str, why: str) -> str:
        _run_dir, problem = _safe_run_dir(self._root(), run_id)
        if problem is not None:
            return _dump({"ok": False, "error": problem})
        live, handle, loop = _request_cancel(run_id, why)
        if live is None:
            server = self._cancel_on_server(run_id) if self._client else None
            return _dump(server or self._not_running_view(run_id))
        if handle is not None:
            _cancel_on_loop(handle, loop, why)
        live.done.wait(CANCEL_WAIT_SEC)
        view = self._view(run_id)
        view["cancel_requested"] = why
        return _dump(view)

    def _cancel_on_server(self, run_id: str) -> dict[str, Any] | None:
        """Cancel a server-hosted run (slice 8): UMRS stops its worker and
        cancels its record. None when UMRS hosts no such run."""
        from ..teams.server_venue import cancel_server_run

        try:
            answer = cancel_server_run(self._client, run_id)
        except Exception:  # noqa: BLE001 — not a server-hosted run
            return None
        return {
            "ok": bool(answer.get("cancelled")),
            "run_id": run_id,
            "venue": "server",
            "status": answer.get("job_status"),
            "worker": (answer.get("venue") or {}).get("state"),
        }

    # ── a crew is its template (slice 4) ───────────────────────────────────

    def start_crew(
        self, crew: str, goal: str, models: Any = None, run: bool = True
    ) -> dict[str, Any]:
        """Run a crew's plan template, exactly as ``team_plan_run`` would (slice 4).

        ``crew`` (or an alias) names a template crew (``r4.teams.crews``); the
        goal binds to the template's free-text parameter and ``models``
        (``{role: model}``) to its ``<role>_model`` parameters. The call is
        validated first, so an invalid one spends nothing. Then the lead's
        manager asks the human with the plan's card (``team_plan_run`` is
        never auto-approved, MD-5) and only a yes starts the run, granting
        the card's SAFE tools to its agents (MD-6). ``run=False`` validates
        and describes the plan only. The reply lands once the run started.
        """
        head, arguments, call, problems = self._crew_call(crew, goal, models)
        if problems:
            return {"ok": False, **head, "problems": problems, "note": _INVALID_NOTE}
        if not run:
            return {"ok": True, **head, "started": False, **_plan_preview(call.spec.plan or {})}
        if not self._card_approved(arguments):
            note = "team_plan_run was not approved; nothing ran and nothing was spent"
            return {"ok": False, **head, "status": "rejected", "error": note}
        return {**json.loads(self.run(**arguments, wait_sec=0)), **head, "started": True}

    def _crew_call(
        self, crew: str, goal: str, models: Any
    ) -> tuple[dict[str, str], dict[str, Any], _PlanCall | None, list[str]]:
        """(crew and template, the call's arguments, the prepared call, problems)."""
        from ..teams.crews import crew_name, crew_template

        name = crew_name(crew)
        template = crew_template(name)
        if template is None:
            raise ValueError(f"crew {name!r} is the HPC teamup preset, not a plan template")
        head = {"crew": name, "template": template}
        arguments, problems = crew_arguments(template, goal, models)
        if problems:
            return head, arguments, None, problems
        call = self.prepare(arguments)
        if call.problems or call.spec is None:
            return head, arguments, None, list(call.problems) or ["the plan did not bind"]
        return head, arguments, call, []

    def _card_approved(self, arguments: dict[str, Any]) -> bool:
        """The lead's manager on this plan's card, as for the chat tool (MD-5).

        With no manager there is no human to ask, so nothing starts."""
        check = getattr(self._permissions, "check", None)
        return bool(check("team_plan_run", arguments)) if callable(check) else False

    def _not_running_view(self, run_id: str) -> dict[str, Any]:
        view = self._view(run_id)
        status = view.get("status")
        note = (
            f"run {run_id} is not running in this session (status {status}); nothing was cancelled"
        )
        return {"ok": False, "run_id": run_id, "status": status, "error": note}

    # ── team_plan_result ───────────────────────────────────────────────────

    def result(self, run_id: str, path: str | None = None) -> str:
        """Progress while running, else the result or a slice of it."""
        return _dump(self._view(str(run_id or ""), path or None))

    def _view(self, run_id: str, path: str | None = None) -> dict[str, Any]:
        run_dir, problem = _safe_run_dir(self._root(), run_id)
        if problem is not None:
            return {"ok": False, "error": problem}
        live = _snapshot(run_id)
        if live is not None and not live.done.is_set():
            return _running_view(live)
        if live is not None and live.result_path is None:
            return _start_failed_view(live)
        return self._finished_view(run_id, run_dir, path)

    def _finished_view(self, run_id: str, run_dir: Path, path: str | None) -> dict[str, Any]:
        doc = _read_result(run_dir)
        server = self._server_record(run_id) if doc is None and self._client else None
        if server is not None:
            return server
        if doc is None:
            return {
                "ok": False,
                "run_id": run_id,
                "status": "unknown",
                "error": f"no result.json in {run_dir}: the run never started in this "
                "process, or it ended without writing one",
            }
        absorbed = self._absorb_taint(doc)
        view = _slice_view(doc, path) if path else _result_view(doc)
        view.update(result_path=str(run_dir / "result.json"), taint_segments=absorbed)
        if doc.get("taint_truncated") is True:
            # The engine cut excerpts at its caps; the output was absorbed
            # whole as untrusted instead (D-6), and the lead is told so.
            view["taint_truncated"] = True
        return view

    def _absorb_taint(self, doc: dict[str, Any]) -> int:
        """Import the run's excerpts into the lead's registry (D-6).

        Never caught: if the import fails, the tool call fails and the lead
        sees no plan output, rather than output its fence cannot see
        (fail-closed, ADR-039).

        When the engine cut excerpts at its caps (``taint_truncated``), the
        carried ones no longer cover the output: an excerpt past the cap is
        untrusted text the fence would not see. So every string of the
        output is imported as generic taint too (``_output_segments``):
        the lead's fence then asks before any argument that copies it
        (ADR-092 D-6, finding 5).
        """
        registry = getattr(self._permissions, "taint_registry", None)
        if registry is None:
            return 0
        raw = doc.get("taint")
        segments = list(raw) if isinstance(raw, list) else []
        if doc.get("taint_truncated") is True:
            segments += _output_segments(doc.get("output"))
        if not segments:
            return 0
        return int(registry.import_segments(segments))

    # ── wiring ─────────────────────────────────────────────────────────────

    def _service(self, grants: frozenset[str] = frozenset(), live: _LiveRun | None = None) -> Any:
        from ..teams.service import TeamService

        return TeamService(
            self._client,
            run_root=self._root(),
            permission_manager=self._permissions,
            session_model=self._model_now(),
            usage_hooks=self._usage_hooks,
            web_access=self._web_access,
            run_grants=grants,
            observer=_agent_observer(live) if live is not None else None,
        )

    def run_root(self) -> Path:
        """Where this executor's runs live (the bridge lists them, slice 3)."""
        return self._root()

    def _root(self) -> Path:
        """The run root, read at call time so a test or an env change applies."""
        if self._run_root is not None:
            return self._run_root
        from ..teams import service

        return Path(service.TEAM_RUNS_DIR)

    def _project(self) -> str:
        """The active project, else the caller's identity project, else "".

        Never a placeholder: a plan run is scoped to a real project or to
        none (the ADR-092 slice-1 fix of the hard-coded ``cli``).
        """
        from ..project import get_project_manager
        from .tools import _identity_project_id

        try:
            current = get_project_manager().get_current()
        except Exception:  # noqa: BLE001 - no project state is "no active project"
            current = None
        return str(current or _identity_project_id(self._client) or "")


def describe_run_call(arguments: dict[str, Any]) -> str:
    """The ``team_plan_run`` card of a manager with no plan tools bound (D-9).

    The same ``prepare`` and renderers over the shared run root, with no
    session model: an agent naming no model is shown on "the session's
    model". A session's own card comes from its bound ``describe_run``.
    """
    return _PlanTools(None, None, None, None, None).describe_run(arguments)


def _lists_mcp_tool(raw: Any) -> bool:
    """True when a profile of the raw plan lists an MCP tool."""
    from r4_contracts.team_engine.plan import is_mcp_tool

    profiles = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(profiles, dict):
        return False
    return any(
        is_mcp_tool(tool)
        for profile in profiles.values()
        if isinstance(profile, dict) and isinstance(profile.get("tools"), list)
        for tool in profile["tools"]
    )


def _unknown_arguments(unknown: list[str]) -> str:
    """An argument ``run`` has no keyword for: the executor call itself fails
    (D-9), so the card says so rather than summarising a plan."""
    return (
        f"the tool does not take {', '.join(unknown)}; it takes {', '.join(sorted(RUN_ARGUMENTS))}"
    )


def _durable_arg(given: dict[str, Any]) -> bool | None:
    """``durable`` as the service takes it: True requires, None is the default."""
    return True if given.get("durable") is True else None


def _recorded_spec(run_dir: Path) -> tuple[dict[str, Any], Any, str | None]:
    """``(manifest, spec, None)`` for a recorded run, or why it cannot resume.

    Through the service's own ``_rebuild_plan_spec``, so the plan a resume
    is validated and shown with is the one ``TeamService.resume`` runs (D-8).
    """
    from r4_contracts.team_engine.plan import PlanInvalid

    from ..teams.service import _read_json, _rebuild_plan_spec

    try:
        manifest = _read_json(run_dir / "resolution.json")
        return manifest, _rebuild_plan_spec(run_dir, manifest), None
    except PlanInvalid as exc:
        return {}, None, f"the recorded plan no longer validates: {exc}"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {}, None, f"the recorded run cannot be rebuilt: {exc}"


# ── the plan source ─────────────────────────────────────────────────────────


def _plan_source(plan: Any, template: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """The raw plan: an inline object, or a built-in template by name.

    A template is a built-in name only, never a path: the approval card
    renders what it reads from the call's own arguments or the built-in
    templates, so a plan file would run unseen (D-9).
    """
    if (plan is None) == (not template):
        return None, [
            "give exactly one of plan (an inline plan object) or template (a built-in "
            "name; team_plan_templates lists them)"
        ]
    if template:
        return _template_plan(str(template))
    raw, problems = _json_object(plan, "plan")
    return (raw if not problems else None), problems


def _template_plan(name: str) -> tuple[dict[str, Any] | None, list[str]]:
    from ..teams.plan_sources import load_template

    try:
        return load_template(name), []
    except KeyError as exc:
        return None, [str(exc.args[0] if exc.args else exc)]
    except ValueError as exc:
        return None, [str(exc)]


def _json_object(value: Any, name: str) -> tuple[dict[str, Any], list[str]]:
    """``value`` as a JSON object: a dict as is, JSON text parsed, None as {}.

    Models sometimes send an object argument as JSON text; that is accepted,
    anything else is a named problem rather than a crash.
    """
    if value is None:
        return {}, []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            return {}, [f"{name}: not valid JSON ({exc})"]
    if not isinstance(value, dict):
        return {}, [f"{name}: must be a JSON object, got {type(value).__name__}"]
    return value, []


def _maybe_int(value: Any) -> Any:
    """A digit string as an int; anything else unchanged, for the validator
    to accept or refuse by name (budgets are held to the plan limits, D-7)."""
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _clamp_wait(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_WAIT_SEC
    return max(0.0, min(float(MAX_WAIT_SEC), seconds))


def _fresh_run_id(name: str) -> str:
    """``<name>-<8 hex>``, a single safe path component: a plan name is free
    text, and the run id names a directory under the run root."""
    stem = _RUN_ID_RE.sub("-", name or "plan").strip("-")[:24] or "plan"
    return f"{stem}-{uuid.uuid4().hex[:8]}"


def _safe_run_dir(root: Path, run_id: str) -> tuple[Path, str | None]:
    """``root/run_id``, refusing an id that would leave the root: run ids
    come from the lead's tool calls."""
    if not run_id or run_id in (".", "..") or Path(run_id).name != run_id or "\\" in run_id:
        return root, f"invalid team plan run id {run_id!r}"
    return root / run_id, None


def _read_result(run_dir: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


# ── the run thread and its process-local registry ───────────────────────────


def _register(live: _LiveRun) -> bool:
    """Add a run; False while a run with the same id is still going."""
    with _LOCK:
        current = _RUNS.get(live.run_id)
        if current is not None and not current.done.is_set():
            return False
        _prune_locked()
        _RUNS[live.run_id] = live
        return True


def _prune_locked() -> None:
    """Forget the oldest finished runs past ``MAX_KEPT_RUNS`` (their
    result.json stays; only the live-progress entry goes)."""
    finished = [run_id for run_id, live in _RUNS.items() if live.done.is_set()]
    for run_id in finished[: max(0, len(_RUNS) - MAX_KEPT_RUNS + 1)]:
        del _RUNS[run_id]


def _snapshot(run_id: str) -> _LiveRun | None:
    """A consistent copy of one run's progress, ``done`` included (D-9).

    ``dataclasses.replace`` alone shares the ``done`` Event: a run that
    finished between the copy and the caller's ``done.is_set()`` read as
    done with the copy's stale ``result_path`` of None, and a successful
    run was reported failed, "ended without a result". The copy gets its
    own Event, set iff the run was done under the lock. Every write the
    run thread makes (``_on_terminal``, a crash) is under this lock and
    before it sets ``done``, so a copy that says done has them all.
    """
    with _LOCK:
        live = _RUNS.get(run_id)
        if live is None:
            return None
        done = threading.Event()
        if live.done.is_set():
            done.set()
        return dataclasses.replace(live, done=done)


def _cancel_reason(reason: Any, who: str = "the lead") -> str:
    text = " ".join(str(reason or "").split())[:MAX_REASON_CHARS]
    return f"cancelled by {who}: {text}" if text else f"cancelled by {who}"


def _user_cancel_reason(reason: Any) -> str:
    return _cancel_reason(reason, "the user from the console")


def _request_cancel(run_id: str, why: str) -> tuple[_LiveRun | None, Any, Any]:
    """The live run, its handle and loop; None when it is not running here.

    A run whose engine has not started yet (no handle) keeps the request,
    and ``_drive`` applies it the moment the handle exists.
    """
    with _LOCK:
        live = _RUNS.get(run_id)
        if live is None or live.done.is_set():
            return None, None, None
        if live.handle is None:
            live.cancel_requested = why
        return live, live.handle, live.loop


def _cancel_on_loop(handle: Any, loop: Any, why: str) -> None:
    """``handle.cancel`` on the run's own event loop (its thread), awaited."""
    try:
        future = asyncio.run_coroutine_threadsafe(handle.cancel(why), loop)
        future.result(timeout=CANCEL_WAIT_SEC)
    except Exception as exc:  # noqa: BLE001 - a run that ended meanwhile needs no cancel
        logger.info("team plan cancel: %s", exc)


def _spawn(live: _LiveRun, start: Callable[[], Awaitable[Any]]) -> None:
    """Run one attempt on a daemon thread with its own event loop. The plan
    runs only while its events are drained (ADR-020), so the thread drains
    them to the end whatever the tool call's wait. The sandbox context the
    run starts under is pinned for its thread and every task and
    ``to_thread`` call under it: a session switch never reroutes the plan's
    spawns (ADR-101, review ESC-3, LC-5)."""
    from .sandbox import bind_sandbox

    thread = threading.Thread(
        target=bind_sandbox(_thread_main),
        args=(live, start),
        name=f"team-plan-{live.run_id}",
        daemon=True,
    )
    thread.start()


def _thread_main(live: _LiveRun, start: Callable[[], Awaitable[Any]]) -> None:
    try:
        asyncio.run(_drive(live, start))
    except Exception as exc:  # noqa: BLE001 - a crash is reported to the lead, never lost
        logger.warning("team plan run %s failed: %s", live.run_id, exc)
        with _LOCK:
            if live.result_path is None:
                live.status = "failed"
                live.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
    finally:
        live.done.set()


async def _drive(live: _LiveRun, start: Callable[[], Awaitable[Any]]) -> None:
    handle = await start()
    with _LOCK:
        live.handle, live.loop = handle, asyncio.get_running_loop()
        pending = live.cancel_requested
    if pending:
        await handle.cancel(pending)
    async for event in handle.events():
        _observe(live, event)
    result = await handle.result()
    with _LOCK:
        if live.result_path is None:
            live.status = result.status if result.status in _TERMINAL else "failed"
            live.error = result.error


def _observe(live: _LiveRun, event: Any) -> None:
    kind = getattr(event.kind, "value", event.kind)
    observer = _OBSERVERS.get(kind)
    if observer is not None:
        with _LOCK:
            observer(live, event.payload or {})
    # Slice 3: every surface watching this process sees the run live,
    # keyed by agent id (ADR-087). Outside the lock: a listener may block.
    plan_events.publish(plan_events.from_engine_event(live.run_id, live.plan, event))


def _agent_observer(live: _LiveRun) -> Callable[[str, str, dict[str, Any]], None]:
    """The broker's observer for one run: its agents' rounds and tool calls."""

    def _observe_agent(fact: str, agent: str, payload: dict[str, Any]) -> None:
        plan_events.publish(
            plan_events.from_agent_fact(live.run_id, live.plan, fact, agent, payload)
        )

    return _observe_agent


TURN_CANCEL_REASON = "cancelled with the chat turn that started it"


def cancel_from_turn(run_id: str) -> None:
    """The turn that started ``run_id`` was cancelled: cancel the run (slice 3).

    Called from the thread that cancels the turn, so it never waits: the
    engine's ``cancel`` is scheduled on the run's own loop, or held for the
    start when the engine has not started yet. A run that already ended is
    left alone.
    """
    _live, handle, loop = _request_cancel(run_id, TURN_CANCEL_REASON)
    if handle is None or loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(handle.cancel(TURN_CANCEL_REASON), loop)
    except RuntimeError as exc:  # the loop closed: the run has ended meanwhile
        logger.info("team plan cancel with the turn: %s", exc)


def live_runs() -> list[dict[str, Any]]:
    """Progress of every run still going in this process (the Work pane)."""
    with _LOCK:
        running = [live for live in _RUNS.values() if not live.done.is_set()]
        return [
            {
                "run_id": live.run_id,
                "plan": live.plan,
                "status": "running",
                "phase": live.phase,
                "agents_ok": live.agents_ok,
                "agents_null": live.agents_null,
                "tokens": live.tokens,
                "cancel_requested": live.cancel_requested,
            }
            for live in running
        ]


def executor_of(tools: Any) -> _PlanTools | None:
    """The ``_PlanTools`` behind a session's ``team_plan_run`` tool, or None.

    The bridge's ``team.plan_*`` RPCs run the session's own executor, so its
    card, its grants (MD-6) and its handles are the ones the chat tool uses.
    """
    for tool in tools or ():
        if isinstance(tool, dict) and tool.get("name") == "team_plan_run":
            owner = getattr(tool.get("execute"), "__self__", None)
            if isinstance(owner, _PlanTools):
                return owner
    return None


def args_with_goal(template: str, args: Any, goal: str) -> tuple[dict[str, Any], str | None]:
    """``args`` with ``goal`` bound to the template's free-text parameter.

    ``/teamup on plan:<template>`` arms the next chat message as the goal
    (slice 3). The goal goes to the template's first required string
    parameter that ``args`` does not already set; a template with none is a
    named problem, never a guess.
    """
    from ..teams.plan_sources import load_template

    given, problems = _json_object(args, "args")
    if problems:
        return given, problems[0]
    try:
        params = load_template(str(template)).get("params") or {}
    except (KeyError, ValueError) as exc:
        return given, str(exc.args[0] if exc.args else exc)
    name = _goal_param(params, given)
    if name is None:
        return given, f"template {template!r} has no free-text parameter left for the goal"
    return {**given, name: goal}, None


def crew_arguments(
    template: str, goal: str, models: Any = None
) -> tuple[dict[str, Any], list[str]]:
    """The ``team_plan_run`` arguments of a crew's run, or every problem (slice 4).

    The goal binds as ``/teamup on plan:`` binds it (``args_with_goal``).
    ``models`` maps a role to a model: ``{"reviewer": "m"}`` sets the
    template's ``reviewer_model``. A role the template has no model
    parameter for is a named problem, never dropped.
    """
    args, problem = args_with_goal(template, None, goal)
    problems = [problem] if problem else []
    given, model_problems = _json_object(models, "models")
    problems += model_problems
    if given:
        from ..teams.plan_sources import load_template

        params = load_template(template).get("params") or {}
        knobs = sorted(name[: -len("_model")] for name in params if name.endswith("_model"))
        for role, model in given.items():
            if f"{role}_model" in params:
                args[f"{role}_model"] = model
            else:
                problems.append(
                    f"models: template {template!r} has no model for {role!r} "
                    f"(it takes: {', '.join(knobs) or 'none'})"
                )
    return {"template": template, "args": args}, problems


def _plan_preview(program: dict[str, Any]) -> dict[str, Any]:
    """What a crew run would start, for ``run=False``: nothing is spent."""
    summary = plan_summary(program)
    return {
        "agent_bound": summary.get("agent_bound"),
        "phases": [phase.get("title") or phase.get("id") for phase in summary.get("phases") or []],
        "budget": summary.get("budget"),
        "warnings": summary.get("warnings") or [],
    }


def _goal_param(params: dict[str, Any], given: dict[str, Any]) -> str | None:
    """The first required string parameter the call has not set, or None."""
    for key, spec in params.items():
        if not isinstance(spec, dict) or key in given:
            continue
        if spec.get("required") and spec.get("type") == "string":
            return key
    return None


def _on_started(live: _LiveRun, payload: dict[str, Any]) -> None:
    live.status = "running"
    live.attempt = str(payload.get("attempt") or "")


def _on_message(live: _LiveRun, payload: dict[str, Any]) -> None:
    if payload.get("type") == "phase":
        live.phase = f"{payload.get('id')}: {payload.get('title') or ''}".strip()


def _on_agent(live: _LiveRun, payload: dict[str, Any]) -> None:
    if payload.get("ok"):
        live.agents_ok += 1
    else:
        live.agents_null += 1
    live.tokens += int(payload.get("tokens") or 0)


def _on_warning(live: _LiveRun, _payload: dict[str, Any]) -> None:
    live.budget_warning = True


def _on_terminal(live: _LiveRun, payload: dict[str, Any]) -> None:
    live.status = str(payload.get("status") or "failed")
    live.error = payload.get("error")
    live.result_path = str(payload.get("result_path") or live.run_dir / "result.json")


_OBSERVERS: dict[str, Callable[[_LiveRun, dict[str, Any]], None]] = {
    "run_started": _on_started,
    "agent_message": _on_message,
    "agent_completed": _on_agent,
    "budget_warning": _on_warning,
    "run_completed": _on_terminal,
    "run_failed": _on_terminal,
    "run_cancelled": _on_terminal,
}


# ── views ───────────────────────────────────────────────────────────────────


def _running_view(live: _LiveRun) -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": live.run_id,
        "plan": live.plan,
        "status": "running",
        "progress": {
            "stage": live.status,
            "attempt": live.attempt,
            "phase": live.phase,
            "agents_ok": live.agents_ok,
            "agents_null": live.agents_null,
            "tokens": live.tokens,
            "budget_warning": live.budget_warning,
            "elapsed_sec": round(time.monotonic() - live.started_at, 1),
        },
        "result_path": str(live.run_dir / "result.json"),
        "next": _POLL_NOTE,
    }


def _start_failed_view(live: _LiveRun) -> dict[str, Any]:
    """The run ended before the engine wrote a result: a refused model, an
    unknown run, a spec the engine could not express. Nothing ran."""
    return {
        "ok": False,
        "run_id": live.run_id,
        "plan": live.plan,
        "status": live.status if live.status in _TERMINAL else "failed",
        "error": str(live.error or "the run ended without a result")[:MAX_ERROR_CHARS],
    }


def _result_view(doc: dict[str, Any]) -> dict[str, Any]:
    """The bounded structured result of D-9."""
    status = doc.get("status")
    output, more = _capped(doc.get("output"), "output")
    view: dict[str, Any] = {
        "ok": status in _GOOD,
        "run_id": doc.get("run_id"),
        "plan": doc.get("plan"),
        "attempt": doc.get("attempt"),
        "status": status,
        "output": output,
        "steps": _step_counts(doc.get("steps")),
        "budget": doc.get("budget"),
        "journal": doc.get("journal"),
        # Slice 7: where the run's records are, and a failure of that home by name.
        "durable": doc.get("durable"),
        "note": _NULL_NOTE,
    }
    view.update(more)
    if doc.get("error"):
        view["error"] = str(doc["error"])[:MAX_ERROR_CHARS]
    return view


def _slice_view(doc: dict[str, Any], path: str) -> dict[str, Any]:
    value, more = _capped(resolve_path(doc, path.strip()), path.strip())
    view = {
        "ok": True,
        "run_id": doc.get("run_id"),
        "status": doc.get("status"),
        "path": path,
        "value": value,
    }
    view.update(more)
    return view


def _output_segments(output: Any) -> list[dict[str, str]]:
    """Every string of a run's output as generic excerpts (fail closed, D-6).

    For a result whose taint the engine truncated. The fence matches an
    excerpt by its first ``_MATCH_EXCERPT_CHARS`` characters inside a
    normalised argument, so a long string is cut into windows of that
    length: an argument that copies any run of two windows' length from
    the output contains one whole window. A window shorter than the
    fence's floor (``MIN_TAINT_EXCERPT_LEN``) would match ordinary text and
    is left out, as the registry leaves out short results.
    """
    from r4_contracts.taint import MIN_TAINT_EXCERPT_LEN

    from .taint import _MATCH_EXCERPT_CHARS as width

    segments: list[dict[str, str]] = []
    for text in _string_leaves(output):
        flat = " ".join(text.split())
        segments += [
            {"text_excerpt": window, "taint_class": "generic", "source_tool": "team_plan_run"}
            for window in (flat[i : i + width] for i in range(0, len(flat), width))
            if len(window) >= MIN_TAINT_EXCERPT_LEN
        ]
    return segments


def _string_leaves(value: Any) -> list[str]:
    """Every string inside a JSON value, at any depth (no recursion limit:
    a fail-closed import must not stop short of a deep string)."""
    leaves: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            leaves.append(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return leaves


def _step_counts(steps: Any) -> dict[str, Any]:
    """Per-step ok and null counts with their (bounded) null reasons."""
    if not isinstance(steps, dict):
        return {}
    out: dict[str, Any] = {}
    for step_id, tally in steps.items():
        if not isinstance(tally, dict):
            continue
        row = dict(tally)
        row["null_reasons"] = [
            str(reason)[:MAX_REASON_CHARS] for reason in tally.get("null_reasons") or []
        ]
        out[step_id] = row
    return out


# ── the approval card (D-9) ─────────────────────────────────────────────────


def agent_lines(
    program: dict[str, Any], default_model: str | None, unnamed: str = SESSION_MODEL_LABEL
) -> list[str]:
    """``models: …``, then one line per agent step or map stage (D-9).

    From ``r4.teams.plan.summary.effective_agents``, which applies the
    engine's precedence (the step's model, else its profile's, else the
    default). A per-profile list hid a step's own ``model`` and the default
    profile a step with no ``use`` runs on, so the card and the dry run named
    a model that never ran and left out the ones that did. ``unnamed`` names
    the model an agent runs on when the plan and the default name none.
    Shared by the card and ``r4 team plan run --dry-run``.
    """
    from ..teams.plan.summary import effective_agents

    rows = effective_agents(program, default_model)
    models = list(dict.fromkeys(_one_line(row.get("model") or unnamed) for row in rows))
    return [f"models: {', '.join(models) or 'none (no agent step)'}"] + [
        _agent_line(row, unnamed) for row in rows
    ]


def _agent_line(row: dict[str, Any], unnamed: str) -> str:
    where = f"step {row.get('step')}"
    if row.get("stage") is not None:
        where = f"map {row.get('step')}, stage {row.get('stage')}"
    tools = ", ".join(str(tool) for tool in row.get("tools") or []) or "none"
    isolation = row.get("isolation") or "none"
    works = "the project root" if isolation == "none" else f"its own {isolation}"
    return _one_line(
        f"agent {row.get('label')} ({where}): profile {row.get('profile')}; "
        f"model {row.get('model') or unnamed}; tools {tools}; {_agent_count(row)}; "
        f"works in {works}"
    )


def _agent_count(row: dict[str, Any]) -> str:
    replicas = row.get("replicas") or 1
    rounds = f", up to {row['iterations']} iteration(s)" if row.get("iterations") else ""
    if row.get("stage") is None:
        return f"{replicas} agent(s){rounds}"
    items = row.get("max_items")
    per_item = f"{replicas} agent(s) per item"
    return (f"{per_item}, up to {items} item(s)" if items else per_item) + rounds


def _gate_lines(summary: dict[str, Any]) -> list[str]:
    """Where the run will stop and ask you (slice 2): never auto-approved."""
    lines = []
    for gate in summary.get("gates") or []:
        when = f" (only if {gate['when']} is set)" if gate.get("when") else ""
        lines.append(
            _one_line(
                f"  asks you at gate {gate.get('gate')} ({gate.get('class')}){when}: "
                "never auto-approved; a no stops the run"
            )
        )
    return lines


def _merge_back_lines(summary: dict[str, Any]) -> list[str]:
    """Where the run will stop and ask before writing to the project (slice 5)."""
    return [
        _one_line(
            f"  asks you at merge_back {row.get('step')} before writing the workspaces of "
            f"{', '.join(str(s) for s in row.get('from') or [])} into the project root "
            "(text diffs only; a conflict is refused, never resolved): never auto-approved; "
            "a no stops the run"
        )
        for row in summary.get("merge_backs") or []
    ]


def _warning_lines(summary: dict[str, Any]) -> list[str]:
    return [_one_line(f"  warning: {text}") for text in summary.get("warnings") or []]


def _run_card(call: _PlanCall) -> str:
    """What a fresh run may start and spend: bound, budget, phases, and the
    model and tools of every agent step, with the session's model as the
    default the spec was bound with."""
    program = call.spec.plan or {}
    summary = plan_summary(program)
    default_model = (call.spec.context or {}).get("default_model")
    lines = [
        f"Run team plan: {_one_line(summary.get('name') or _card_ref(call.given))}, at most "
        f"{summary.get('agent_bound', '?')} agents, {summary.get('concurrency', '?')} at a time",
        f"  budget: {_budget_text(summary.get('budget'))}",
        *_phase_lines(summary.get("phases")),
        *_indented(agent_lines(program, default_model)),
        *_gate_lines(summary),
        *_merge_back_lines(summary),
        *_warning_lines(summary),
        *_grant_lines(program),
        *_override_lines(call.given),
        *_durable_lines(call.given),
        *_wait_lines(call.given),
    ]
    return "\n".join(lines)


def _durable_lines(given: dict[str, Any]) -> list[str]:
    lines = []
    if given.get("durable") is True:
        lines.append("  durable: required: its records must reach UMRS, or the run stops")
    if given.get("venue") == "server":
        sources = ", ".join(_source_list(given.get("sources"))) or "(none given: refused)"
        lines.append(
            "  venue: server: a sandboxed worker on the UMRS site runs it (models from "
            f"{sources}; agents fail-closed: safe tools only; gates wait in the UMRS "
            "approvals inbox for you)"
        )
    return lines


def _source_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value or () if str(v).strip()]


def _resume_card(call: _PlanCall, session_model: str | None) -> str:
    """What a resume reruns (D-8): the recorded plan's facts, as a run card.

    Each attempt gets a fresh token meter, so the recorded budget can be
    spent again by the calls that run live; an agent whose recorded plan
    names no model runs on the session's model, as the broker falls back.
    """
    program = call.spec.plan or {}
    summary = plan_summary(program)
    default_model = (call.spec.context or {}).get("default_model") or session_model
    lines = [
        f"Resume team plan run {_one_line(call.resume)[:120]}: plan "
        f"{_one_line(summary.get('name') or '?')}, attempt {_attempt(call.manifest) + 1}; "
        "it replays every unchanged ok agent call from its journal at zero model cost, "
        "and changed and failed calls run live under a fresh budget for this attempt",
        f"  at most {summary.get('agent_bound', '?')} agents, "
        f"{summary.get('concurrency', '?')} at a time",
        f"  budget (again, per attempt): {_budget_text(summary.get('budget'))}",
        *_phase_lines(summary.get("phases")),
        *_indented(agent_lines(program, default_model)),
        *_gate_lines(summary),
        *_merge_back_lines(summary),
        *_warning_lines(summary),
        *_grant_lines(program),
        *_ignored_lines(call.given),
        *_durable_lines(call.given),
        *_wait_lines(call.given),
    ]
    return "\n".join(lines)


def card_safe_tools(program: dict[str, Any]) -> frozenset[str]:
    """The SAFE tools the card lists for a plan's agents (ADR-092 MD-6):
    every agent step's and map stage's effective tools, the safe ones only."""
    from ..teams.plan.summary import effective_agents
    from .permissions import SAFE_TOOLS, TEAM_AGENT_DENY

    listed = {str(tool) for row in effective_agents(program) for tool in row.get("tools") or ()}
    return frozenset(listed & SAFE_TOOLS) - TEAM_AGENT_DENY


def _grant_lines(program: dict[str, Any]) -> list[str]:
    """What approving this card grants (MD-6), and what it does not."""
    granted = sorted(card_safe_tools(program))
    if not granted:
        return ["  approving grants no tool: every agent tool call follows your mode"]
    return [
        f"  approving grants its agents, for this run only: {', '.join(granted)} "
        "(safe tools; every other tool follows your permission mode, per call)"
    ]


def _refused_card(call: _PlanCall) -> str:
    """One line: why the tool refuses this call; nothing runs or spends."""
    problems = call.problems or ("the plan could not be bound",)
    why = (
        f"invalid: {len(problems)} problem(s), first {_one_line(problems[0])}; "
        "the tool refuses it before any spend"
    )
    if call.resume:
        return f"Resume team plan run {_one_line(call.resume)[:120]} ({why})"
    return f"Run team plan: {_card_ref(call.given)} ({why})"


def _card_ref(arguments: Any) -> str:
    """The plan a call names: its template, else its inline plan's name."""
    given = arguments if isinstance(arguments, dict) else {}
    template = given.get("template")
    if isinstance(template, str) and template:
        return _one_line(template)[:120]
    plan, _problems = _json_object(given.get("plan"), "plan")
    return _one_line(plan.get("name") or "inline plan")[:120]


def _attempt(manifest: dict[str, Any]) -> int:
    value = manifest.get("attempt")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 1


def _budget_text(budget: Any) -> str:
    if not isinstance(budget, dict) or not budget:
        return "none declared"
    return ", ".join(f"{key}={value}" for key, value in budget.items())


def _phase_lines(phases: Any) -> list[str]:
    lines: list[str] = []
    for phase in phases if isinstance(phases, list) else []:
        steps = ", ".join(str(step) for step in phase.get("steps") or [])
        lines.append(
            "  " + _one_line(f"phase {phase.get('id')}: {phase.get('title') or ''} [{steps}]")
        )
    return lines


def _override_lines(given: dict[str, Any]) -> list[str]:
    """The overrides this call asks for (already applied to the summary)."""
    asked = [
        f"{key}={_maybe_int(given[key])}" for key in ("max_tokens", "concurrency") if key in given
    ]
    return [f"  this call sets {', '.join(asked)}"] if asked else []


def _ignored_lines(given: dict[str, Any]) -> list[str]:
    """A resume keeps its recorded params and budget; the tool ignores these."""
    ignored = [key for key in ("args", "max_tokens", "concurrency") if key in given]
    return [f"  ignored on resume: {', '.join(ignored)}"] if ignored else []


def _wait_lines(given: dict[str, Any]) -> list[str]:
    if "wait_sec" not in given:
        return []
    return [f"  waits up to {_clamp_wait(given['wait_sec']):g}s for the result"]


def _indented(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def _one_line(text: Any) -> str:
    """Whitespace folded: a card line is one line, whatever a plan writes."""
    return " ".join(str(text).split())


# ── the 12 KB cap ───────────────────────────────────────────────────────────

# Measured as the value is returned: ``_dump`` (indent=1), one level inside
# the view. A container of n >= 1 entries then takes 3 bytes plus each
# entry's size as a one-entry container less 3, which ``_entry_size`` gives.
_CONTAINER_BYTES = 3
_NO_ROOM = object()


def _capped(value: Any, path: str) -> tuple[Any, dict[str, Any]]:
    """``value`` whole when it fits ``MAX_OUTPUT_BYTES``; otherwise a bounded
    view of it, plus ``truncated: true`` and how to read the rest by path.
    Nothing is cut silently: every dropped part names its path or its count.

    Every shape is bounded as it is returned (D-9: up to 12 KB), and the
    result stays whole JSON. A stub reserved per remaining key once made an
    object of many small keys come back as stubs larger than its data, past
    the companion's 40 000-character cut, which returned a non-JSON
    fragment; and compact bytes under-counted the indented text.
    """
    if _shown_size(value) <= MAX_OUTPUT_BYTES:
        return value, {}
    if isinstance(value, dict):
        kept = _capped_object(value, path)
        more = _more(f"{path}.<key>")
        more.update(shown_keys=len(kept), total_keys=len(value))
        return kept, more
    if isinstance(value, list):
        items = _capped_list(value)
        more = _more(f"{path}.{len(items)}")
        more.update(shown_items=len(items), total_items=len(value))
        return items, more
    return _clip_text(value), _more(path)


def _capped_object(value: dict[str, Any], path: str) -> dict[str, Any]:
    """The leading entries, each whole or as a stub naming its path, while
    the object fits; the keys after the first that fits neither way are
    left out (the caller reports ``shown_keys`` of ``total_keys``)."""
    kept: dict[str, Any] = {}
    used = _CONTAINER_BYTES
    for key, entry in value.items():
        placed, size = _fitting(key, entry, path, MAX_OUTPUT_BYTES - used)
        if placed is _NO_ROOM:
            break
        kept[key] = placed
        used += size
    while kept and _shown_size(kept) > MAX_OUTPUT_BYTES:
        kept.popitem()
    return kept


def _fitting(key: str, entry: Any, path: str, room: int) -> tuple[Any, int]:
    """``(entry, size)`` if it fits ``room``, else its stub if that fits and
    is smaller, else ``(_NO_ROOM, 0)``: a stub never outgrows its data."""
    size = _entry_size({key: entry})
    if size <= room:
        return entry, size
    stub = _stub(entry, f"{path}.{key}")
    stub_size = _entry_size({key: stub})
    if stub_size <= room and stub_size < size:
        return stub, stub_size
    return _NO_ROOM, 0


def _capped_list(value: list[Any]) -> list[Any]:
    kept: list[Any] = []
    used = _CONTAINER_BYTES
    for item in value:
        size = _entry_size([item])
        if used + size > MAX_OUTPUT_BYTES:
            break
        kept.append(item)
        used += size
    while kept and _shown_size(kept) > MAX_OUTPUT_BYTES:
        kept.pop()
    return kept


def _stub(value: Any, path: str) -> dict[str, Any]:
    stub: dict[str, Any] = {"truncated": True, "bytes": _size(value), "path": path}
    if isinstance(value, (list, dict)):
        stub["items"] = len(value)
    return stub


def _clip_text(value: Any) -> str:
    """A prefix that fits once JSON-escaped: quotes and control characters
    grow under escaping, so the clip is measured on the returned form."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    limit = MAX_OUTPUT_BYTES
    while True:
        clipped = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "…"
        if limit <= 0 or _shown_size(clipped) <= MAX_OUTPUT_BYTES:
            return clipped
        limit = limit * 3 // 4


def _more(path_hint: str) -> dict[str, Any]:
    return {
        "truncated": True,
        "read_more": f"team_plan_result(run_id, path='{path_hint}') reads the rest",
    }


def _size(value: Any) -> int:
    """Compact UTF-8 bytes: the size a stub reports for what it stands for."""
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _shown_size(value: Any) -> int:
    """UTF-8 bytes of ``value`` as ``_dump`` returns it one level inside the
    view: indent=1, plus the one-space indent each later line gains there."""
    text = json.dumps(value, ensure_ascii=False, indent=1, default=str)
    return len(text.encode("utf-8")) + text.count("\n")


def _entry_size(single: Any) -> int:
    """What one entry adds to its container (``single`` holds just it)."""
    return _shown_size(single) - _CONTAINER_BYTES


def _dump(view: dict[str, Any]) -> str:
    """indent=1: one value per line, so the taint fence's per-line excerpts
    of this tool's own output (ADR-039) see each value."""
    return json.dumps(view, ensure_ascii=False, indent=1, default=str)


__all__ = ["agent_lines", "build_team_plan_tools", "describe_run_call"]
