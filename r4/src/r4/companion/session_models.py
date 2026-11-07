"""Session message and usage data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SessionMessage:
    """A single message in the conversation."""

    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageRecord:
    """A single LLM usage record captured during a session."""

    call_type: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    model: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_type": self.call_type,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
            "model": self.model,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UsageRecord":
        return cls(
            call_type=data.get("call_type", "turn"),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            estimated=bool(data.get("estimated", False)),
            model=data.get("model", ""),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            metadata=data.get("metadata", {}),
        )


@dataclass
class UsageSummary:
    """Aggregate usage totals across a session."""

    llm_calls: int = 0
    compaction_calls: int = 0
    subagent_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_calls: int = 0

    def record(self, usage: UsageRecord) -> None:
        self.llm_calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        if usage.estimated:
            self.estimated_calls += 1
        if usage.call_type == "compaction":
            self.compaction_calls += 1
        if usage.call_type == "subagent":
            self.subagent_calls += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "compaction_calls": self.compaction_calls,
            "subagent_calls": self.subagent_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_calls": self.estimated_calls,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UsageSummary":
        return cls(
            llm_calls=int(data.get("llm_calls", 0)),
            compaction_calls=int(data.get("compaction_calls", 0)),
            subagent_calls=int(data.get("subagent_calls", 0)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            estimated_calls=int(data.get("estimated_calls", 0)),
        )


class BudgetLimitExceededError(RuntimeError):
    """Raised when a session-level LLM budget limit is exceeded."""


# Where a conflict was found, for clients (cycle 3 review): "before_turn" —
# the message was refused and nothing ran; "after_turn" — the turn (model and
# tools) already ran and only its save lost, so re-sending it would run it
# twice; "" — any other save (a slash command, a toggle).
CONFLICT_STAGES = ("before_turn", "after_turn", "")


def _conflict_hint(session_id: str, copy_id: str) -> str:
    """How to go on, true on every console: the c3 TUI reloads the tab that
    holds the session; older ones answer "already open in a tab" instead, and
    the oldest have no tabs at all (cycle 3 review tui-runtime#1)."""
    hint = "in the console, "
    if copy_id:
        hint += f"/sessions resume {copy_id} opens the unsaved copy; "
    return hint + (
        f"/sessions resume {session_id} reloads this session (if the console answers "
        "'already open in a tab', close that tab first with /tab close — after /tab new "
        "when it is the last one — then resume it)"
    )


def _conflict_message(session_id: str, copy_id: str, stage: str) -> str:
    short = session_id[:8]
    if stage == "after_turn":
        kept = (
            f"; the turn and its reply are kept in session {copy_id} (unsaved copy)"
            if copy_id
            else ""
        )
        return (
            f"your turn ran, but this session ({short}) changed in another console meanwhile, "
            f"so the turn was not saved to it{kept} — do not send it again"
        )
    what = "your message was not run" if stage == "before_turn" else "this change was not saved"
    message = f"this session ({short}) changed in another console, so {what}"
    message += " — reload it to continue"
    if copy_id:
        message += f"; what this console had not saved is kept in session {copy_id}"
    return message


class SessionConflictError(RuntimeError):
    """The stored row moved on since this copy was loaded (cycle 3, T1).

    Two bridges on one session (two consoles, or a console and ``r4 chat``)
    used to save last-writer-wins, so one side's turns vanished. A save is now
    compare-and-swap on the row's revision; the losing writer gets this error,
    and its unsaved conversation is kept as a separate session (``copy_id``).
    ``stage`` (one of :data:`CONFLICT_STAGES`) says whether the turn ran.
    """

    def __init__(self, session_id: str, *, copy_id: str = "", stage: str = "") -> None:
        super().__init__(_conflict_message(session_id, copy_id, stage))
        self.session_id = session_id
        self.copy_id = copy_id
        self.stage = stage
        self.hint = _conflict_hint(session_id, copy_id)
        self.data = {
            "code": "session_conflict",
            "session_id": session_id,
            "copy_id": copy_id,
            "stage": stage,
        }

    def at_stage(self, stage: str) -> "SessionConflictError":
        """The same conflict, named for where the bridge met it."""
        return SessionConflictError(self.session_id, copy_id=self.copy_id, stage=stage)


def metadata_project_id(metadata: dict[str, Any]) -> str:
    """The project a session's metadata names ("" when none)."""
    raw = metadata.get("config")
    config: dict[str, Any] = raw if isinstance(raw, dict) else {}
    for candidate in (
        metadata.get("project_id"),
        metadata.get("project"),
        config.get("project_id"),
        config.get("project"),
    ):
        if candidate:
            return str(candidate)
    return ""
