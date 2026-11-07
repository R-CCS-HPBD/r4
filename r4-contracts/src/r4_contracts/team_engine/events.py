"""Canonical team run events (ADR-020).

Engines translate native callbacks into TeamEvent drafts; the runtime's
EventSink assigns ``seq`` (single-writer responsibility), persists, and
fans out. Engines never assign sequence numbers themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

TEAM_EVENT_VERSION = 1


class EventKind(str, Enum):
    RUN_STARTED = "run_started"
    AGENT_STARTED = "agent_started"
    AGENT_MESSAGE = "agent_message"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    ARTIFACT_PRODUCED = "artifact_produced"
    HUMAN_INPUT_REQUESTED = "human_input_requested"
    CHECKPOINT_CREATED = "checkpoint_created"
    BUDGET_WARNING = "budget_warning"
    AGENT_COMPLETED = "agent_completed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


TERMINAL_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {EventKind.RUN_COMPLETED, EventKind.RUN_FAILED, EventKind.RUN_CANCELLED}
)


@dataclass(frozen=True)
class ActorRef:
    """Who caused an event."""

    kind: str  # "engine" | "agent" | "runtime" | "human"
    name: str


@dataclass(frozen=True)
class TeamEvent:
    """Canonical run event. Payload shapes per kind are engine-neutral."""

    seq: int
    run_id: str
    ts: str  # ISO-8601 UTC
    kind: EventKind
    actor: ActorRef
    payload: dict[str, Any]
