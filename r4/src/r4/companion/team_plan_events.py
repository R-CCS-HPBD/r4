"""The live ``team_event`` stream of team plan runs (ADR-092 slice 3, ADR-087).

A plan run started in this process (by the lead's ``team_plan_run`` tool or
the bridge's ``team.plan_run``) reports what happens as it happens, keyed by
the run id and by the agent id (``<step>[<item>]#<replica>``):

==================  ============================================================
kind                when
==================  ============================================================
``plan_started``    the engine started an attempt
``phase``           a phase began
``agent_started``   an agent started (or was replayed from the journal)
``agent_round``     one model round of an agent ended (tokens, model)
``agent_tool``      an agent called a tool, or the tool returned
``agent_result``    an agent answered (``ok``; a bounded ``preview``)
``agent_failed``    an agent ended as a null with a reason
``agent_cancelled`` an agent in flight was stopped by a cancel
``gate_requested``  a gate asks the human (``class``, ``prompt``)
``gate_answered``   a gate was answered, refused or replayed
``budget_warning``  80% of the token budget is spent
``budget_exhausted`` a ceiling is spent; the remaining agents are refused
``plan_log``        a ``log`` line of the plan
``plan_finished``   the run ended (``status``, spent tokens, result path)
==================  ============================================================

Every event carries ``run_id``, ``plan``, ``kind``, ``agent`` ("" for a run
or phase fact) and ``emitted_at``. Payloads are bounded and never invent a
field the engine did not report. The stream is observation only: nothing a
listener does can change a run, and a failing listener is logged and
skipped. The listener list is process-local and is not a job store
(CONSTITUTION §6); each run's ``events.jsonl`` and ``result.json`` stay the
record.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 400
MAX_TEXT_CHARS = 400
MAX_PREVIEW_CHARS = 600

Listener = Callable[[dict[str, Any]], None]

_LISTENERS: list[Listener] = []
_LISTENERS_LOCK = threading.Lock()

_AGENT_KEYS = ("step", "phase", "item_index", "replica", "stage", "model", "replayed")
_RESULT_KEYS = ("ok", "reason", "tokens", "refused_tools")


def add_run_listener(listener: Listener) -> None:
    """Receive every plan event of this process (idempotent per listener)."""
    with _LISTENERS_LOCK:
        if listener not in _LISTENERS:
            _LISTENERS.append(listener)


def remove_run_listener(listener: Listener) -> None:
    with _LISTENERS_LOCK:
        if listener in _LISTENERS:
            _LISTENERS.remove(listener)


def publish(event: dict[str, Any] | None) -> None:
    """Hand one event to every listener; a listener's failure stays its own."""
    if not event:
        return
    with _LISTENERS_LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener(event)
        except Exception:  # noqa: BLE001 — a watcher never breaks a run
            logger.debug("team plan listener failed", exc_info=True)


def from_engine_event(run_id: str, plan: str, event: Any) -> dict[str, Any] | None:
    """The ``team_event`` of one engine ``TeamEvent``, or None when it has none."""
    kind = getattr(getattr(event, "kind", None), "value", getattr(event, "kind", ""))
    payload = dict(getattr(event, "payload", None) or {})
    actor = getattr(event, "actor", None)
    agent = str(getattr(actor, "name", "") or "") if getattr(actor, "kind", "") == "agent" else ""
    mapper = _ENGINE_MAPPERS.get(str(kind))
    body = mapper(payload) if mapper is not None else None
    if body is None:
        return None
    return _envelope(run_id, plan, agent, getattr(event, "ts", None), body)


def from_agent_fact(run_id: str, plan: str, fact: str, agent: str, payload: dict[str, Any]):
    """The ``team_event`` of one broker fact (``round`` or ``tool``), or None."""
    if fact == "round":
        body = {
            "kind": "agent_round",
            "round": payload.get("round"),
            "input_tokens": payload.get("input_tokens"),
            "output_tokens": payload.get("output_tokens"),
            "model": payload.get("model"),
        }
    elif fact == "tool":
        body = _tool_body(payload)
    else:
        return None
    return _envelope(run_id, plan, agent, None, body)


def _envelope(run_id: str, plan: str, agent: str, ts: Any, body: dict[str, Any]):
    return {
        "run_id": run_id,
        "plan": plan,
        "agent": agent,
        "emitted_at": str(ts or datetime.now(timezone.utc).isoformat()),
        **body,
    }


def _tool_body(payload: dict[str, Any]) -> dict[str, Any]:
    called = payload.get("phase") == "call"
    return {
        "kind": "agent_tool",
        "tool": str(payload.get("name") or ""),
        "state": "started" if called else str(payload.get("status") or "done"),
        "duration_ms": None if called else payload.get("duration_ms"),
    }


def _started(payload: dict[str, Any]) -> dict[str, Any]:
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    return {
        "kind": "plan_started",
        "attempt": payload.get("attempt"),
        "resumed_from": payload.get("resumed_from"),
        "agent_bound": payload.get("agent_bound"),
        "concurrency": payload.get("concurrency"),
        "max_tokens": budget.get("max_tokens"),
    }


def _agent_started(payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "agent_started", **{key: payload.get(key) for key in _AGENT_KEYS}}


def _agent_completed(payload: dict[str, Any]) -> dict[str, Any]:
    """``agent_result`` for an answer, ``agent_cancelled`` for an agent a
    cancel stopped, ``agent_failed`` for any other null."""
    reason = str(payload.get("reason") or "")
    if payload.get("ok"):
        kind = "agent_result"
    elif reason.startswith("cancelled"):
        kind = "agent_cancelled"
    else:
        kind = "agent_failed"
    body = {"kind": kind, **{key: payload.get(key) for key in _AGENT_KEYS + _RESULT_KEYS}}
    body["preview"] = _clip(payload.get("preview"), MAX_PREVIEW_CHARS)
    return body


def _gate_requested(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "gate_requested",
        "gate": payload.get("gate"),
        "step": payload.get("step"),
        "phase": payload.get("phase"),
        "class": payload.get("class"),
        "prompt": _clip(payload.get("prompt"), MAX_PROMPT_CHARS),
    }


def _message(payload: dict[str, Any]) -> dict[str, Any] | None:
    mapper = _MESSAGE_MAPPERS.get(str(payload.get("type") or ""))
    return mapper(payload) if mapper is not None else None


def _phase(payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "phase", "phase": payload.get("id"), "title": payload.get("title") or ""}


def _gate_note(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A raised gate's outcome; a gate whose ``when`` was empty was never asked."""
    if not payload.get("raised"):
        return None
    return {
        "kind": "gate_answered",
        "gate": payload.get("gate"),
        "class": payload.get("class"),
        "approved": bool(payload.get("approved")),
        "answered": bool(payload.get("answered")),
        "reason": payload.get("reason"),
        "replayed": bool(payload.get("replayed")),
    }


def _budget_exhausted(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "budget_exhausted",
        "ceiling": payload.get("kind"),
        "text": _clip(payload.get("text"), MAX_TEXT_CHARS),
    }


def _log(payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "plan_log", "step": payload.get("step"), "text": _clip(payload.get("text"))}


def _budget_warning(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "budget_warning",
        "ceiling": payload.get("kind"),
        "spent_tokens": payload.get("spent_tokens"),
        "max_tokens": payload.get("max_tokens"),
    }


def _finished(payload: dict[str, Any]) -> dict[str, Any]:
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    return {
        "kind": "plan_finished",
        "status": payload.get("status"),
        "error": _clip(payload.get("error")) or None,
        "reason": payload.get("reason"),
        "spent_tokens": budget.get("spent_tokens"),
        "agents_started": budget.get("agents_started"),
        "result_path": payload.get("result_path"),
    }


def _clip(value: Any, cap: int = MAX_TEXT_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= cap else text[:cap] + "…"


_MESSAGE_MAPPERS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    "phase": _phase,
    "gate": _gate_note,
    "budget_exhausted": _budget_exhausted,
    "log": _log,
}
_ENGINE_MAPPERS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    "run_started": _started,
    "agent_message": _message,
    "agent_started": _agent_started,
    "agent_completed": _agent_completed,
    "human_input_requested": _gate_requested,
    "budget_warning": _budget_warning,
    "run_completed": _finished,
    "run_failed": _finished,
    "run_cancelled": _finished,
}
