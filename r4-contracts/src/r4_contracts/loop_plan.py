"""A team plan as a research loop's iteration: the loop's side, one table
(ADR-092 slice 6; slice 8b makes it shared).

Both loop venues read this module: r4's local loop (``r4.runtime.
plan_executor``) and UMRS's server-hosted loop (``umrs.knowledge.
plan_iteration``). It holds what a loop does around one plan run, never
the run itself:

- ``LOOP_TEMPLATES``: the templates a loop iteration can run (those whose
  output carries a reviewer verdict the loop reads) and which params the
  loop owns (its goal, its own record of earlier rounds);
- ``loop_decision``: the continue / publish / stop table (a pick
  publishes; no pick or a near-tie continues; a run that did not finish,
  a gate a human rejected included, stops), decided in code, never by a
  model;
- ``plan_within_wallclock``: the plan's wall clock is the smaller of its
  own and what the loop has left (ADR-067);
- ``review_record`` / ``round_line`` / ``context_text``: the loop's own,
  code-derived record of a round (verdicts and scores, never an agent's
  words: a plan parameter renders outside the untrusted envelope);
- ``finding_operation`` / ``catalog_candidate``: the proposed-only finding
  and the Catalog candidate a published round yields (ADR-050, D-39).

Stdlib only (INV-10).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DECISION_CONTINUE = "continue"
DECISION_PUBLISH = "publish"
DECISION_STOP = "stop"
LOOP_DECISIONS = frozenset({DECISION_CONTINUE, DECISION_PUBLISH, DECISION_STOP})
LOOP_DECISION_KEY = "loop_decision"
GOOD_PLAN_STATUSES = frozenset({"success", "partial"})
MAX_REASON = 300
MAX_TEXT = 2000
MAX_CONTEXT = 1500
CONTEXT_ROUNDS = 5


@dataclass(frozen=True)
class LoopTemplate:
    """How a loop drives one plan template.

    ``goal_param`` takes the loop's goal and ``context_param`` the loop's
    own record of earlier rounds; both belong to the loop, so a caller may
    not set them. ``decision_key`` is the plan output that carries the
    reviewers' verdict (a ``rank`` result: ``picked`` with a ``winner``,
    ``competing_hypotheses`` or ``undecided``).
    """

    name: str
    goal_param: str
    context_param: str
    decision_key: str


LOOP_TEMPLATES: dict[str, LoopTemplate] = {
    "research-round": LoopTemplate(
        name="research-round",
        goal_param="objective",
        context_param="context",
        decision_key="decision",
    ),
}


def loop_template(name: str) -> LoopTemplate:
    """The loop shape of ``name``; refused by name for any other template."""
    shape = LOOP_TEMPLATES.get(str(name or "").strip())
    if shape is None:
        raise ValueError(
            f"plan template {name!r} cannot run as a loop iteration: the loop reads a "
            f"reviewer verdict from its output (loop templates: {sorted(LOOP_TEMPLATES)})"
        )
    return shape


def caller_args(shape: LoopTemplate, args: Mapping[str, Any] | None) -> dict[str, Any]:
    """The caller's plan args; the loop's own params are refused."""
    given = dict(args or {})
    owned = sorted({shape.goal_param, shape.context_param} & set(given))
    if owned:
        raise ValueError(f"plan args {owned} belong to the loop (its goal and its own record)")
    return given


def loop_decision(
    plan_status: Any, review: Mapping[str, Any] | None, error: Any = None
) -> dict[str, str]:
    """The loop's continue / publish / stop decision from one round."""
    review = review if isinstance(review, Mapping) else {}
    if plan_status not in GOOD_PLAN_STATUSES:
        why = f"plan_{plan_status or 'failed'}"
        if error:
            why += f":{error}"
        return {"decision": DECISION_STOP, "reason": why[:MAX_REASON]}
    verdict = review.get("status")
    if verdict == "picked":
        return {"decision": DECISION_PUBLISH, "reason": "the reviewers picked a design"}
    if verdict == "competing_hypotheses":
        return {
            "decision": DECISION_CONTINUE,
            "reason": "competing hypotheses: the round ended with no pick",
        }
    return {"decision": DECISION_CONTINUE, "reason": "no design passed review"}


def review_of(output: Any, shape: LoopTemplate) -> dict[str, Any]:
    """The reviewers' verdict a run's output carries ({} when none)."""
    review = output.get(shape.decision_key) if isinstance(output, Mapping) else None
    return dict(review) if isinstance(review, Mapping) else {}


def picked_winner(review: Mapping[str, Any]) -> dict[str, Any] | None:
    winner = review.get("winner") if review.get("status") == "picked" else None
    return dict(winner) if isinstance(winner, Mapping) else None


def plan_within_wallclock(raw_plan: Any, remaining_sec: float | None) -> Any:
    """The plan with ``budget.max_wallclock_sec`` = min(its own, the loop's
    remaining wall clock, ADR-067); unchanged when the loop is uncapped. An
    invalid own value is left for the validator to name."""
    if remaining_sec is None or not isinstance(raw_plan, dict):
        return raw_plan
    raw = copy.deepcopy(raw_plan)
    budget = dict(raw.get("budget") or {})
    cap = max(1, int(remaining_sec))
    own = budget.get("max_wallclock_sec")
    if own is None:
        budget["max_wallclock_sec"] = cap
    elif isinstance(own, int) and not isinstance(own, bool):
        budget["max_wallclock_sec"] = min(own, cap)
    raw["budget"] = budget
    return raw


def review_record(review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": review.get("status") or "missing",
        "winner_index": review.get("winner_index"),
        "ranking": list(review.get("ranking") or []),
        "competing": len(review.get("competing") or []),
    }


def round_line(entry: Mapping[str, Any]) -> str:
    review = entry.get("review") or {}
    scores = ", ".join(
        f"design {r.get('index')}: {r.get('score')}"
        for r in review.get("ranking") or []
        if isinstance(r, Mapping)
    )
    return f"Round {entry.get('iteration')}: review {review.get('status')}" + (
        f" ({scores})" if scores else ""
    )


def context_text(rounds: Sequence[Mapping[str, Any]]) -> str:
    """The loop's own record of its last rounds, for the next round's plan."""
    lines = [round_line(entry) for entry in list(rounds)[-CONTEXT_ROUNDS:]]
    return "\n".join(lines)[:MAX_CONTEXT]


def finding_operation(
    *, loop_id: str, iteration: int, run_id: str, winner: Mapping[str, Any]
) -> dict[str, Any]:
    """The one proposed-only finding a published round stages (ADR-050)."""
    return {
        "op": "add_finding",
        "status": "proposed",
        "confidence": 0.0,
        "producer": f"research_loop:{loop_id}/iteration:{iteration}/plan:{run_id}",
        "statement": str(winner.get("hypothesis") or "")[:MAX_TEXT],
        "protocol": str(winner.get("protocol") or "")[:MAX_TEXT],
        "refuting_outcome": str(winner.get("refuting_outcome") or "")[:MAX_TEXT],
        "untrusted": True,
    }


def catalog_candidate(
    *,
    loop_id: str,
    iteration: int,
    goal: str,
    run_id: str,
    winner: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> dict[str, Any]:
    """A Scientific Catalog candidate for a published round (U-10): recorded
    on the iteration, never written to the Catalog (review promotes, D-39)."""
    refs = [f"team_run:{run_id}"]
    if finding.get("proposal_id"):
        refs.append(f"knowledge_proposal:{finding['proposal_id']}")
    return {
        "status": "candidate",
        "written": False,
        "goal": str(goal)[:MAX_TEXT],
        "conclusion": str(winner.get("hypothesis") or "")[:MAX_TEXT],
        "evidence_refs": refs,
        "loop_id": loop_id,
        "iteration": iteration,
        "untrusted": True,
        "next": "a person reviews and promotes it; nothing is written to the Catalog",
    }


__all__ = [
    "DECISION_CONTINUE",
    "DECISION_PUBLISH",
    "DECISION_STOP",
    "GOOD_PLAN_STATUSES",
    "LOOP_DECISIONS",
    "LOOP_DECISION_KEY",
    "LOOP_TEMPLATES",
    "LoopTemplate",
    "caller_args",
    "catalog_candidate",
    "context_text",
    "finding_operation",
    "loop_decision",
    "loop_template",
    "picked_winner",
    "plan_within_wallclock",
    "review_of",
    "review_record",
    "round_line",
]
