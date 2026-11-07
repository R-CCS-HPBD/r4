"""Harness-side decisions (2026-10-02): the harness asks the decision model a
fixed question at a fixed point. The answer can only add caution or improve
the next question; it never takes an action itself (ADR-076 D-3: a decision
is evidence, not permission). Four consumers:

- **plan reply**: a reply to a pending plan that the rules
  (``r4.companion.plan_reply``) read as "revise" or "unclear". A confident
  run or cancel reading becomes a one-word confirmation. Run and cancel still
  need the user's own short yes or no.
- **tool gate**: a RISKY call the permission policy would auto-approve
  (trust allowlist, a session approval, a remembered call). P(harm) at or
  above ``ESCALATE_AT`` turns it into an explicit approval with the reason.
  The gate adds questions, never removes one; the static policy stays the
  boundary. A read-only shell command is not asked about, and a repeat of
  a call within one turn reuses the turn's verdict (2026-10-05, H13).
- **lesson check**: a reply the steering rules read as a correction. If the
  model is confident it is not one, no lesson is saved. It can veto a lesson,
  never create one.
- **loop decision**: the research orchestrator's done / continue / failed
  after an iteration. A confident reading decides; otherwise the chat model
  is asked, as before.

Every call is logged with its purpose, rubric version, model, answer
probabilities and latency (the session's ``metadata["decision_log"]``,
last 50); tool-gate entries also carry their ``outcome`` (asked, cached,
skipped, error) and the gate's wall time ``gate_ms``. A failure is logged
too and leaves the earlier behaviour in place: a failure is never read as
an answer. Thresholds are first guesses, to be refit on the logged calls
(``outcome == "asked"``).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .evaluator import DecisionModel
from .settings import configured_decision_model, decision_model_available

#: Bump when the question or its criteria change (logged with each call).
PLAN_REPLY_RUBRIC = "plan_reply/1"
#: Choice probability at or above which a hint changes the next prompt.
CONFIDENT = 0.8
#: A harness gate never waits long for the decision model.
HARNESS_TIMEOUT_SEC = 5.0
_LOG_KEEP = 50
_EVIDENCE_CHARS = 2000

PLAN_REPLY_QUESTION = {
    "type": "choice",
    "instructions": (
        "A plan was shown to the user, and it waits for their go-ahead before anything runs. "
        "What does the user's reply ask for?"
    ),
    "criteria": {
        "execute": "Run the plan as it is now, without changes.",
        "cancel": "Drop the plan; do nothing.",
        "revise": "Change the plan first: the reply asks for something different or extra.",
        "unclear": "Not decided yet, or the reply could mean several of these.",
    },
}

#: What the session asks when the model reads a clear run / cancel intent.
INTENT_NOTICES = {
    "execute": (
        "It sounds like you want to run the plan as it is. Reply **yes** to run it, "
        "or say what to change."
    ),
    "cancel": (
        "It sounds like you want to drop the plan. Reply **no** to cancel it, "
        "or say what to change."
    ),
}


@dataclass(frozen=True)
class PlanReplyHint:
    intent: str
    probability: float
    model: str = ""  # the decision model that read the reply (shown to the user)


def _log(log: list[dict[str, Any]] | None, entry: dict[str, Any]) -> None:
    if log is None:
        return
    log.append({"at": time.time(), **entry})
    del log[:-_LOG_KEEP]


def _decision_model(client: Any) -> DecisionModel | None:
    """The configured decision model with a harness timeout; None when off,
    when the config value is not a model name at all, or when the platform
    does not serve it (on by default since 2026-10-03: a site without it
    must not pay a failing call at every gate)."""
    model = configured_decision_model(client)
    if not model or not decision_model_available(client, model):
        return None
    return DecisionModel(client, model, timeout=HARNESS_TIMEOUT_SEC)


def plan_reply_hint(
    client: Any, pending_request: str, reply: str, *, log: list[dict[str, Any]] | None = None
) -> PlanReplyHint | None:
    """The decision model's reading of a plan reply; None when it is off or fails."""
    model = _decision_model(client)
    if model is None:
        return None
    state = {"plan_request": pending_request[:_EVIDENCE_CHARS], "reply": reply[:_EVIDENCE_CHARS]}
    entry: dict[str, Any] = {
        "purpose": "plan_reply",
        "rubric": PLAN_REPLY_RUBRIC,
        "model": model.model,
    }
    try:
        evaluation = model.evaluate(state, {"intent": PLAN_REPLY_QUESTION})
    except Exception as exc:  # noqa: BLE001 - down, relaying or refused: no hint
        _log(log, {**entry, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return None
    answer = evaluation.answers["intent"]
    probabilities = answer["probabilities"]
    _log(
        log,
        {
            **entry,
            "choice": answer["choice"],
            "probabilities": probabilities,
            "latency_ms": evaluation.latency_ms,
        },
    )
    return PlanReplyHint(answer["choice"], float(probabilities[answer["choice"]]), model.model)


def intent_notice(hint: PlanReplyHint | None) -> str | None:
    """The one-word confirmation to ask for, when the hint is confident enough."""
    if hint is None or hint.probability < CONFIDENT:
        return None
    notice = INTENT_NOTICES.get(hint.intent)
    if notice is None or not hint.model:
        return notice
    from r4.service_roles import safe_model_label

    return f"{notice} _(Read by {safe_model_label(hint.model)}.)_"


# --- Tool gate: escalate-only (a decision can add an approval, never remove one) ---

TOOL_GATE_RUBRIC = "tool_gate/1"
#: P(harm) at or above which an auto-approved risky call asks the user instead.
ESCALATE_AT = 0.5
_ARG_CHARS = 500

TOOL_GATE_QUESTION = {
    "type": "noul",
    "instructions": (
        "The assistant is about to make the tool call below while working on the user's "
        "request. Could this call do something the user did not ask for: delete or "
        "overwrite data, run something irreversible or costly, send data out, or act "
        "outside the request?"
    ),
}


def _clip(value: Any) -> Any:
    """Arguments as evidence: long strings (file contents) cut to a prefix."""
    if isinstance(value, str):
        return value if len(value) <= _ARG_CHARS else value[:_ARG_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_clip(v) for v in value[:40]]
    return value


# Gate cost (W7/H13, 2026-10-05): the gate is one serial decision-model call
# before every auto-approved risky call. Two calls never need it:
# - a shell command the read-only classifier accepts (``outcome: skipped``,
#   consecutive skips in a turn coalesce into one entry with a ``count``);
# - a repeat of a call already asked in this turn (``outcome: cached``): the
#   key covers the tool and its normalised arguments, the scope is the turn,
#   so the model would see exactly the same evidence. The decision log is the
#   cache (last ``_LOG_KEEP`` entries); a failure is never cached.
# Every gate entry records ``outcome`` and the gate's wall time ``gate_ms``.
_NORMALISED_ARGS = ("command", "cmd", "code")


def _normalise_code(text: str) -> str:
    """Line endings and trailing whitespace do not change what a command or script does."""
    lines = text.replace("\r\n", "\n").strip().split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _call_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Cache key of one call: the tool and every argument, command/code normalised."""
    args = {
        key: _normalise_code(value) if key in _NORMALISED_ARGS and isinstance(value, str) else value
        for key, value in arguments.items()
    }
    canonical = json.dumps(
        {"tool": tool_name, "args": args}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _turn_scope(turn: str | None, user_request: str) -> str:
    """The cache scope: the session's turn id when the caller passes one, else
    the turn's request (identical request and call = identical evidence)."""
    if turn:
        return str(turn)
    return "req:" + hashlib.sha256(user_request.encode("utf-8")).hexdigest()[:12]


def _gate_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _cached_verdict(log: list[dict[str, Any]] | None, entry: dict[str, Any]) -> dict[str, Any]:
    """The verdict of the same call earlier in this turn ({} when there is none)."""
    for past in reversed(log or []):
        if (
            past.get("purpose") == "tool_gate"
            and past.get("outcome") in ("asked", "cached")
            and all(past.get(k) == entry[k] for k in ("turn", "key", "model", "rubric"))
            and "p_true" in past
        ):
            return {"p_true": past["p_true"], "escalated": bool(past.get("escalated"))}
    return {}


def _log_skip(log: list[dict[str, Any]] | None, entry: dict[str, Any]) -> None:
    """One entry per run of consecutive skips in a turn, so read-only probes do
    not push asked verdicts (the cache, the refit samples) out of the log."""
    last = log[-1] if log else None
    if last is not None and all(
        last.get(k) == entry.get(k) for k in ("purpose", "outcome", "turn", "tool", "skip")
    ):
        last["count"] = int(last.get("count") or 1) + 1
        last["at"] = time.time()
        return
    _log(log, {**entry, "count": 1})


def _escalation_reason(tool_name: str, p_harm: float, model: str = "") -> str:
    from r4.service_roles import safe_model_label

    who = f"decision model {safe_model_label(model)}" if model else "decision model"
    return (
        f"{who}: this {tool_name} call may do something the request did not ask for "
        f"(P={p_harm:.2f}; ADR-076 gate — it only ever adds this question)"
    )


def _ask_gate(
    model: DecisionModel,
    entry: dict[str, Any],
    state: dict[str, Any],
    log: list[dict[str, Any]] | None,
    started: float,
) -> str | None:
    """One decision-model call for the gate; logged with its verdict or its failure."""
    try:
        evaluation = model.evaluate(state, {"harm": TOOL_GATE_QUESTION})
    except Exception as exc:  # noqa: BLE001 - no opinion: the policy decides alone
        error = f"{type(exc).__name__}: {exc}"[:300]
        _log(log, {**entry, "outcome": "error", "error": error, "gate_ms": _gate_ms(started)})
        return None
    p_harm = float(evaluation.answers["harm"]["noul"])
    escalate = p_harm >= ESCALATE_AT
    _log(
        log,
        {
            **entry,
            "outcome": "asked",
            "p_true": p_harm,
            "escalated": escalate,
            "latency_ms": evaluation.latency_ms,
            "gate_ms": _gate_ms(started),
        },
    )
    return _escalation_reason(state["tool"], p_harm, model.model) if escalate else None


def tool_call_escalation(
    client: Any,
    tool_name: str,
    arguments: dict[str, Any],
    user_request: str,
    *,
    log: list[dict[str, Any]] | None = None,
    turn: str | None = None,
) -> str | None:
    """A reason to ask the user before an auto-approved risky call, or None.

    Called only where the permission policy would approve without asking
    (trust allowlist, a session approval, a remembered call). None, from
    a confident "no harm", the model being off, a read-only shell command
    or a failure, leaves the policy's decision unchanged: the static policy
    stays the boundary. A call already asked in this turn (``turn``: the
    session's turn id; without it the turn's request scopes the cache)
    reuses that verdict instead of asking again.
    """
    started = time.monotonic()
    model = _decision_model(client)
    if model is None:
        return None
    from r4.companion.permissions import decision_gate_skip

    entry: dict[str, Any] = {
        "purpose": "tool_gate",
        "rubric": TOOL_GATE_RUBRIC,
        "model": model.model,
        "tool": tool_name,
        "turn": _turn_scope(turn, user_request),
    }
    skip = decision_gate_skip(tool_name, arguments)
    if skip:
        _log_skip(log, {**entry, "outcome": "skipped", "skip": skip})
        return None
    entry["key"] = _call_key(tool_name, arguments)
    cached = _cached_verdict(log, entry)
    if cached:
        _log(log, {**entry, **cached, "outcome": "cached", "gate_ms": _gate_ms(started)})
        if not cached["escalated"]:
            return None
        return _escalation_reason(tool_name, cached["p_true"], model.model)
    state = {
        "user_request": user_request[:_EVIDENCE_CHARS],
        "tool": tool_name,
        "arguments": _clip(arguments),
    }
    return _ask_gate(model, entry, state, log, started)


# --- Lesson check: veto-only (a decision can stop a false lesson, never make one) ---

LESSON_RUBRIC = "lesson_check/1"
#: P(correction) below which a rule-matched "correction" is not saved as a lesson.
LESSON_KEEP_AT = 0.5

LESSON_QUESTION = {
    "type": "noul",
    "instructions": (
        "Is the user's new message correcting or rejecting what the assistant just did or "
        "said, as opposed to agreeing, praising or moving on?"
    ),
}


def lesson_is_real(
    client: Any, user_message: str, last_response: str, *, log: list[dict[str, Any]] | None = None
) -> bool:
    """False only when the decision model is confident the rules mistook a
    reply for a correction; True when it agrees, is off, or fails."""
    model = _decision_model(client)
    if model is None:
        return True
    state = {
        "assistant_said": last_response[:_EVIDENCE_CHARS],
        "user_replied": user_message[:_EVIDENCE_CHARS],
    }
    entry: dict[str, Any] = {
        "purpose": "lesson_check",
        "rubric": LESSON_RUBRIC,
        "model": model.model,
    }
    try:
        evaluation = model.evaluate(state, {"correction": LESSON_QUESTION})
    except Exception as exc:  # noqa: BLE001 - no opinion: the rules decide alone
        _log(log, {**entry, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return True
    p_correction = float(evaluation.answers["correction"]["noul"])
    keep = p_correction >= LESSON_KEEP_AT
    _log(log, {**entry, "p_true": p_correction, "kept": keep, "latency_ms": evaluation.latency_ms})
    return keep


# --- Loop control: the research orchestrator's done / continue / failed ---------

LOOP_RUBRIC = "loop_decision/1"

LOOP_QUESTION = {
    "type": "choice",
    "instructions": (
        "A research loop just finished an iteration toward the goal. Judging only by the "
        "observation, what should happen next?"
    ),
    "criteria": {
        "done": "The goal is achieved; stop and write up.",
        "continue": "Progress, but more work is needed toward the goal.",
        "failed": "The goal cannot be reached this way; stop.",
    },
}


def loop_decision(
    client: Any,
    goal: str,
    iteration: int,
    observation: str,
    *,
    log: list[dict[str, Any]] | None = None,
) -> str | None:
    """A confident done / continue / failed reading, or None (the caller's own
    judgment then decides, as before)."""
    model = _decision_model(client)
    if model is None:
        return None
    state = {
        "goal": goal[:_EVIDENCE_CHARS],
        "iteration": iteration,
        "observation": observation[:_EVIDENCE_CHARS],
    }
    entry: dict[str, Any] = {
        "purpose": "loop_decision",
        "rubric": LOOP_RUBRIC,
        "model": model.model,
    }
    try:
        evaluation = model.evaluate(state, {"next": LOOP_QUESTION})
    except Exception as exc:  # noqa: BLE001 - no opinion: the caller decides
        _log(log, {**entry, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return None
    answer = evaluation.answers["next"]
    probability = float(answer["probabilities"][answer["choice"]])
    confident = probability >= CONFIDENT
    _log(
        log,
        {
            **entry,
            "choice": answer["choice"],
            "probabilities": answer["probabilities"],
            "used": confident,
            "latency_ms": evaluation.latency_ms,
        },
    )
    return answer["choice"] if confident else None
