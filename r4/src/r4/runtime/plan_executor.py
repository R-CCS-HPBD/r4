"""A team plan as the research loop's per-iteration executor (ADR-092 slice 6).

The D-29 tail: the loop engine runs a team inside an iteration. There is no
new loop and no new team engine (CONSTITUTION §1.6, D-29, D-32): the local
runtime's one driver thread runs the iteration, and the iteration is one
bounded plan run through ``TeamService`` and the plan engine, governed as
every plan run is (ADR-092 D-3..D-8).

What the loop adds around the plan:

- **The plan run is an action the loop gates (ADR-066).** ``iteration_action``
  declares ``plan_run``, classed ``cost`` (UMRS ``ask_policy`` registers the
  same kind). The runtime gates it before ``run_iteration`` is called: an
  ask-mode loop parks on its own approvals list until a human decides it
  through the existing decide path; auto runs it under the loop's caps.
  Called any other way (``__call__``) the executor refuses: a plan iteration
  never runs outside the gate.
- **Gates go to the loop's inbox.** A gate the plan raises
  (``HUMAN_INPUT_REQUESTED``) is asked through the loop's approvals list
  (``loop_gate_lane``), whatever the loop's mode: only a human answer passes
  it, the answer flows back to the gate, and the engine journals it (D-8)
  while the loop journals the question and the answer on its record. A loop
  stopped while a gate waits withdraws the question: the gate is refused,
  never answered.
- **The ADR-067 wall clock is passed down.** The plan's own
  ``budget.max_wallclock_sec`` becomes the smaller of its own and what the
  loop has left (``plan_within_loop_budget``).
- **The loop's own decision.** The research crew's former "decision" phase
  is the loop's, not a plan step (a bounded plan has no driver, ADR-075
  D-2): code reads the round's reviewer verdict and decides continue,
  publish or stop (``loop_decision_for``); no model decides it.
- **Reviewer gating feeds proposals only (U-10, ADR-050, migration 0069).**
  A design the reviewers picked is staged as a *proposed* knowledge
  operation (confidence 0, producer-tagged) on the project's graph; nothing
  is established, and the proposal waits for a person's decision. A round
  the reviewers did not pick proposes nothing. A published round records a
  Scientific Catalog *candidate* on the iteration; nothing is written to the
  Catalog (review promotes it, D-39's construction path).

A server-hosted loop cannot run a plan until slice 8's server venue: the UMRS
runtime and UMRS itself refuse a plan iteration by name
(``plan_iteration_needs_server_venue``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from r4_contracts import loop_plan as _shared
from r4_contracts.loop_plan import (
    LOOP_TEMPLATES,
    LoopTemplate,
    loop_decision,
    plan_within_wallclock,
)

from .api import (
    DECISION_PUBLISH,
    DECISION_STOP,
    LOOP_DECISION_KEY,
    IterationContext,
)
from .local_policy import PLAN_GATE, PLAN_RUN

logger = logging.getLogger(__name__)

_GOOD_STATUSES = frozenset({"success", "partial"})
_MAX_TEXT = 2000
_MAX_REASON = 300
_MAX_CONTEXT = 1500


# The templates a loop iteration can run, the loop's params, its decision
# table and its wall-clock rule are one shared table (ADR-092 slice 8b):
# ``r4_contracts.loop_plan``, which UMRS's server-hosted loop reads too.


class PlanIterationExecutor:
    """Run one plan template per loop iteration (see the module docstring).

    ``service_factory(gate_lane)`` builds the ``TeamService`` of one run
    (tests pass a scripted one). The plan's agents get no lead permission
    manager, so they fail closed as a CLI run's do: safe tools run, risky
    ones are refused and named (ADR-092 D-6). The human of the loop is
    asked twice at most per round: the run itself (by the loop's mode) and
    any gate the plan raises.
    """

    def __init__(
        self,
        client: Any,
        project_id: str,
        *,
        template: str = "research-round",
        args: Optional[Mapping[str, Any]] = None,
        service_factory: Optional[Callable[[Any], Any]] = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._shape = _loop_template(template)
        self._args = _caller_args(self._shape, args)
        self._client = client
        self._project_id = project_id
        self._service_factory = service_factory or self._team_service
        self._poll_seconds = poll_seconds
        self.rounds: List[Dict[str, Any]] = []

    @property
    def template(self) -> str:
        return self._shape.name

    def __call__(self, loop_id: str, iteration_number: int, research_goal: str) -> Dict[str, Any]:
        raise RuntimeError(
            "a plan iteration runs only under a loop that gates it (ADR-092 slice 6): "
            "start the loop on the local runtime, which calls run_iteration"
        )

    # ── the loop iteration executor surface ──────────────────────────────

    def iteration_action(self, ctx: IterationContext) -> Dict[str, Any]:
        """The action the loop gates: one plan run, classed ``cost``."""
        return {
            "kind": PLAN_RUN,
            "template": self._shape.name,
            "loop_id": ctx.loop_id,
            "iteration": ctx.iteration_number,
            "goal": ctx.research_goal[:_MAX_REASON],
            "args": dict(self._args),
            "remaining_wallclock_sec": ctx.remaining_wallclock_sec,
        }

    def run_iteration(self, ctx: IterationContext) -> Dict[str, Any]:
        remaining = ctx.remaining_wallclock_sec
        if remaining is not None and remaining < 1:
            return self._not_run(ctx, "budget_exhausted:wall_clock")
        spec, problems = self._spec(ctx)
        if problems:
            return self._not_run(ctx, "plan_invalid:" + "; ".join(problems))
        try:
            doc = asyncio.run(self._drive(spec, ctx))
        except Exception as exc:  # noqa: BLE001 — a refused or broken run stops the loop, named
            logger.warning("loop %s: plan iteration failed: %s", ctx.loop_id, exc)
            return self._not_run(ctx, f"plan_refused:{type(exc).__name__}: {exc}")
        return self._conclude(ctx, spec, doc)

    # ── one run ──────────────────────────────────────────────────────────

    def _spec(self, ctx: IterationContext) -> tuple:
        from r4.teams.plan.engine import spec_from_plan
        from r4.teams.plan_sources import load_template

        raw = plan_within_loop_budget(load_template(self._shape.name), ctx.remaining_wallclock_sec)
        args = {
            **self._args,
            self._shape.goal_param: ctx.research_goal,
            self._shape.context_param: self._context_text(),
        }
        spec, problems = spec_from_plan(raw, args, project=ctx.project_id, templates=load_template)
        return spec, [str(problem) for problem in problems]

    async def _drive(self, spec: Any, ctx: IterationContext) -> Dict[str, Any]:
        """Submit, drain the events (a plan runs only while they are read),
        and cancel the run if the loop is stopped meanwhile."""
        service = self._service_factory(loop_gate_lane(ctx))
        handle = await service.submit(spec, caller=f"research_loop:{ctx.loop_id}")
        watcher = asyncio.ensure_future(self._watch_stop(handle, ctx))
        try:
            async for _event in handle.events():
                pass
            await handle.result()
        finally:
            watcher.cancel()
        doc = _read_json(Path(handle.run_dir) / "result.json")
        doc.setdefault("run_id", handle.run_id)
        doc["result_path"] = str(Path(handle.run_dir) / "result.json")
        return doc

    async def _watch_stop(self, handle: Any, ctx: IterationContext) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            if await asyncio.to_thread(ctx.stop_requested):
                await handle.cancel("the research loop was stopped")
                return

    def _team_service(self, gate_lane: Any) -> Any:
        from r4.teams import service

        return service.TeamService(
            self._client, run_root=Path(service.TEAM_RUNS_DIR), gate_lane=gate_lane
        )

    # ── the loop's decision and the observe hooks ────────────────────────

    def _conclude(self, ctx: IterationContext, spec: Any, doc: Dict[str, Any]) -> Dict[str, Any]:
        review = _shared.review_of(doc.get("output"), self._shape)
        decision = loop_decision_for(doc.get("status"), review, doc.get("error"))
        summary = {
            "iteration": ctx.iteration_number,
            "phase": "plan_iteration",
            "plan": _plan_record(self._shape.name, spec, doc),
            "review": _review_record(review),
            LOOP_DECISION_KEY: decision,
        }
        winner = _shared.picked_winner(review)
        if winner is not None and decision["decision"] == DECISION_PUBLISH:
            finding = propose_finding(self._client, ctx, doc, winner)
            summary["finding"] = finding
            summary["catalog_candidate"] = catalog_candidate(ctx, doc, winner, finding)
        self.rounds.append({"iteration": ctx.iteration_number, "review": summary["review"]})
        return summary

    def _not_run(self, ctx: IterationContext, reason: str) -> Dict[str, Any]:
        return {
            "iteration": ctx.iteration_number,
            "phase": "plan_iteration",
            "plan": {"template": self._shape.name, "status": "not_run"},
            LOOP_DECISION_KEY: {"decision": DECISION_STOP, "reason": reason[:_MAX_REASON]},
        }

    def _context_text(self) -> str:
        """The loop's own record of earlier rounds, for the next round.

        Code-derived facts only (the reviewers' verdict and scores per
        round), never an agent's words: a plan parameter renders outside the
        untrusted envelope (ADR-092 D-4, D-6).
        """
        # TODO(ADR-092 slice 6, reviewer): carrying earlier rounds' agent-written
        # hypotheses into the next round needs the untrusted-argument rule that
        # slice 2 deferred for nested plans; until then only verdicts are carried.
        return _shared.context_text(self.rounds)


# ── pure pieces (pinned by tests) ───────────────────────────────────────────


def plan_within_loop_budget(raw_plan: Any, remaining_sec: Optional[float]) -> Any:
    """min(the plan's own wall clock, the loop's remaining one): the shared rule."""
    return plan_within_wallclock(raw_plan, remaining_sec)


def loop_decision_for(
    plan_status: Any, review: Mapping[str, Any], error: Any = None
) -> Dict[str, str]:
    """The loop's continue / publish / stop decision: the shared table
    (``r4_contracts.loop_plan.loop_decision``), which UMRS's loop reads too."""
    return loop_decision(plan_status, review, error)


def loop_gate_lane(ctx: IterationContext) -> Any:
    """The plan's gate lane inside a loop: the loop's approvals list.

    Its own manager, asking every time and used only for the gate's
    never-auto approval (as the CLI's terminal lane is), so it never widens
    what agents may run. The question is parked on the loop's record; the
    human's answer comes back as the gate's answer; a stopped loop raises
    ``LoopHalted``, which the engine records as a refusal, not an answer.
    """
    from r4.companion.permissions import PermissionManager, PermissionMode

    def _ask(_tool: str, _description: str, arguments: dict) -> bool:
        action = {
            "kind": PLAN_GATE,
            "gate": str(arguments.get("gate") or ""),
            "class": str(arguments.get("class") or ""),
            "prompt": str(arguments.get("prompt") or "")[:_MAX_TEXT],
            "run_id": str(arguments.get("run_id") or ""),
            "untrusted": True,
        }
        return bool(ctx.ask_human(action))

    return PermissionManager(PermissionMode.ASK_ALWAYS, approval_callback=_ask)


def propose_finding(
    client: Any, ctx: IterationContext, doc: Mapping[str, Any], winner: Mapping[str, Any]
) -> Dict[str, Any]:
    """Stage the reviewers' pick as a PROPOSED knowledge operation (U-10).

    One graph-extension proposal on the project's first graph (migration
    0069's propose lane): pending, confidence 0, producer-tagged, marked
    untrusted (an agent wrote it). A person decides it; nothing here
    decides, ingests or establishes anything (ADR-050). Any refusal or
    failure is recorded as ``not_proposed`` with its reason.
    """
    try:
        graph, refusal = _proposal_target(client, ctx.project_id)
        if refusal:
            return {"status": "not_proposed", "reason": refusal[:_MAX_REASON]}
        run_id = str(doc.get("run_id") or "")
        proposal = client.knowledge.graph_propose(
            graph,
            reason=(
                f"Research loop {ctx.loop_id}, iteration {ctx.iteration_number}: the "
                f"reviewers picked this design for the goal {ctx.research_goal[:200]!r}. "
                "Proposed only; a person decides (ADR-050)."
            ),
            source_artifacts=[f"team_run:{run_id}"],
            proposed_operations=[_finding_operation(ctx, run_id, winner)],
        )
    except Exception as exc:  # noqa: BLE001 — a failed proposal never fails the loop
        return {"status": "not_proposed", "reason": f"propose failed: {exc}"[:_MAX_REASON]}
    proposal = proposal if isinstance(proposal, dict) else {}
    return {
        "status": "proposed",
        "graph_id": graph,
        "proposal_id": str(proposal.get("proposal_id") or proposal.get("id") or ""),
    }


def catalog_candidate(
    ctx: IterationContext,
    doc: Mapping[str, Any],
    winner: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> Dict[str, Any]:
    """A Scientific Catalog candidate for a published round (U-10): recorded
    on the iteration, never written to the Catalog (review promotes, D-39)."""
    return _shared.catalog_candidate(
        loop_id=ctx.loop_id,
        iteration=ctx.iteration_number,
        goal=ctx.research_goal,
        run_id=str(doc.get("run_id") or ""),
        winner=winner,
        finding=finding,
    )


# ── helpers ─────────────────────────────────────────────────────────────────


def _loop_template(name: str) -> LoopTemplate:
    return _shared.loop_template(name)


def _caller_args(shape: LoopTemplate, args: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _shared.caller_args(shape, args)


def _proposal_target(client: Any, project_id: str) -> tuple:
    """``(graph id, None)`` or ``(None, why no proposal can be staged)``."""
    from r4.companion.knowledge_write_gate import check_propose
    from r4.teams.project_knowledge import project_graph_ids

    graphs = project_graph_ids(client, project_id)
    if not graphs:
        return None, f"project {project_id!r} has no knowledge graph to propose into"
    return graphs[0], check_propose(client, graphs[0])


def _finding_operation(
    ctx: IterationContext, run_id: str, winner: Mapping[str, Any]
) -> Dict[str, Any]:
    return _shared.finding_operation(
        loop_id=ctx.loop_id, iteration=ctx.iteration_number, run_id=run_id, winner=winner
    )


def _plan_record(template: str, spec: Any, doc: Mapping[str, Any]) -> Dict[str, Any]:
    budgets = getattr(spec, "budgets", None)
    record = {
        "template": template,
        "run_id": doc.get("run_id"),
        "status": doc.get("status"),
        "result_path": doc.get("result_path"),
        "max_wallclock_sec": getattr(budgets, "max_wallclock_sec", None),
        "budget": doc.get("budget"),
        "gates": doc.get("gates"),
        "journal": doc.get("journal"),
    }
    if doc.get("error"):
        record["error"] = str(doc["error"])[:_MAX_REASON]
    return record


def _review_record(review: Mapping[str, Any]) -> Dict[str, Any]:
    return _shared.review_record(review)


def _round_line(entry: Mapping[str, Any]) -> str:
    return _shared.round_line(entry)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "failed", "error": f"no readable result at {path}"}
    return data if isinstance(data, dict) else {"status": "failed", "error": "result not an object"}


__all__ = [
    "LOOP_TEMPLATES",
    "LoopTemplate",
    "PlanIterationExecutor",
    "catalog_candidate",
    "loop_decision_for",
    "loop_gate_lane",
    "plan_within_loop_budget",
    "propose_finding",
]
