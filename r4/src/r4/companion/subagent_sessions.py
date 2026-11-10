"""Durable local recording for ordinary synchronous and async subagents.

One recorder binds an owning product session to the existing session store and
EventLog. Executions are short-lived recording views, not live session objects,
schedulers, registries, or a second persistence authority. Child rows never use
``CompanionSessionStore.save`` because its snapshot mirror is parent-bound until
S5 defines an explicit child-aware venue rail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .cancellation import TurnCancelledError
from .event_log import EventLog
from .session import _now_marker
from .transcript import _clip


def _declared_tool_names(tools: list[dict[str, Any]] | None) -> list[str] | None:
    if tools is None:
        return None
    return [str(tool.get("name") or "<unnamed>") for tool in tools]


def _message(role: str, content: str, turn: int) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "timestamp": _now_marker(),
        "metadata": {"subagent_turn": turn},
    }


@dataclass
class SubagentExecution:
    """One durable child row and its event-log subscription."""

    recorder: "SubagentSessionRecorder"
    agent_id: str
    child_session_id: str
    task: str
    system_prompt: str
    model: str
    execution_kind: str
    declared_tools: list[str] | None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    _listener: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._listener = self._record_event

    @property
    def turn(self) -> int:
        return int(self.metadata.get("follow_up_count", 0))

    def event_data(self, **extra: Any) -> dict[str, Any]:
        """Stable lineage for every parent lifecycle event."""
        return {
            "agent_id": self.agent_id,
            "child_session_id": self.child_session_id,
            "parent_session_id": self.recorder.parent_session_id,
            "parent_agent_id": self.recorder.parent_agent_id,
            "subagent_turn": self.turn,
            **extra,
        }

    def attach(self, child: Any) -> None:
        """Give a real child its backend identity and nested recording binding."""
        child.agent_id = self.agent_id
        child._subagent_sessions = self.recorder.nested(self)
        on_event = getattr(child, "on_event", None)
        if callable(on_event):
            on_event(self._listener)

    def detach(self, child: Any) -> None:
        off_event = getattr(child, "off_event", None)
        if callable(off_event):
            off_event(self._listener)

    def follow_up(self, message: str) -> None:
        previous_messages = list(self.messages)
        previous_metadata = dict(self.metadata)
        self.metadata["follow_up_count"] = self.turn + 1
        self.metadata["subagent_status"] = "running"
        self.metadata["ended_at"] = ""
        self.metadata["terminal_error"] = ""
        self.messages.append(_message("user", message, self.turn))
        preview = {
            "follow_up": True,
            "message_chars": len(message),
            "message_truncated": len(message) > 200,
            "message_preview_cap": 200,
        }
        try:
            self._save()
            self._append("session_resume", message[:200], preview)
        except Exception:
            self.messages = previous_messages
            self.metadata = previous_metadata
            self._save()
            raise

    def complete(self, reply: str) -> None:
        self.messages.append(_message("assistant", reply, self.turn))
        self.metadata["subagent_status"] = "completed"
        self.metadata["ended_at"] = _now_marker(str(self.metadata.get("started_at") or ""))
        self.metadata["terminal_error"] = ""
        self._save()
        self._append_terminal("session_end", "Subagent completed", {"status": "completed"})

    def fail(self, error: BaseException) -> None:
        status = "cancelled" if isinstance(error, TurnCancelledError) else "failed"
        self.metadata["subagent_status"] = status
        self.metadata["ended_at"] = _now_marker(str(self.metadata.get("started_at") or ""))
        self.metadata["terminal_error"] = str(error)
        self._save()
        self._append_terminal("error", str(error), {"status": status, "terminal_error": str(error)})

    def registration_failed(self, error: BaseException) -> None:
        """Compensate a pre-acceptance start-event failure without deleting history."""
        self.metadata["subagent_status"] = "failed"
        self.metadata["ended_at"] = _now_marker(str(self.metadata.get("started_at") or ""))
        self.metadata["terminal_error"] = str(error)
        self.metadata["event_log_error"] = str(error)[:200]
        self._save()

    def _append_terminal(self, kind: str, message: str, data: dict[str, Any]) -> None:
        """A terminal row stays authoritative if its optional boundary event is unavailable."""
        try:
            self._append(kind, message, data)
        except Exception as exc:  # noqa: BLE001 — preserve the child result and stored lifecycle
            self.metadata["event_log_error"] = str(exc)[:200]
            self._save()

    def _save(self) -> None:
        self.recorder.store.save_subagent(
            session_id=self.child_session_id,
            name=f"Subagent {self.agent_id}",
            model=self.model,
            messages=self.messages,
            metadata=self.metadata,
        )

    def _record_event(self, event: Any) -> None:
        raw_type = getattr(event, "event_type", "event")
        kind = getattr(raw_type, "value", None) or str(raw_type)
        data = dict(getattr(event, "data", None) or {})
        if kind == "tool_result" and data.get("result_truncated"):
            data["result_preview_cap"] = 2000
        message = str(getattr(event, "message", "") or "")
        self._append(kind, message, data, include_lineage_columns=True)

    def _append(self, kind: str, message: str, data: dict[str, Any], **options: bool) -> None:
        tool_name = data.get("name") if kind.startswith("tool_") else None
        status = data.get("status") if kind == "tool_result" else None
        duration = data.get("duration_ms")
        self.recorder.event_log.append(
            session_id=self.child_session_id,
            event_type=kind,
            message=_clip(message),
            data=_clip(self.event_data(**data)),
            tool_name=str(tool_name) if tool_name else None,
            tool_status=str(status) if status is not None else None,
            tool_duration_ms=duration
            if isinstance(duration, int) and not isinstance(duration, bool)
            else None,
            agent_id=self.agent_id if options.get("include_lineage_columns") else None,
            parent_agent_id=(
                self.recorder.parent_agent_id if options.get("include_lineage_columns") else None
            ),
        )


class SubagentSessionRecorder:
    """Bind children to one owning session's existing store and event log."""

    def __init__(
        self,
        store: Any,
        parent_session_id: str,
        parent_agent_id: str,
        *,
        event_log: EventLog | None = None,
        venue: Any = "local",
    ) -> None:
        self.store = store
        self.parent_session_id = parent_session_id
        self.parent_agent_id = parent_agent_id
        self.event_log = event_log or EventLog(str(store.db_path))
        self._venue = venue

    def venue(self) -> str:
        """Resolve at begin time: venue workers annotate their parent after load."""
        return str(self._venue() if callable(self._venue) else self._venue)

    def nested(self, execution: SubagentExecution) -> "SubagentSessionRecorder":
        return SubagentSessionRecorder(
            self.store,
            execution.child_session_id,
            execution.agent_id,
            event_log=self.event_log,
            venue=self._venue,
        )

    def begin(
        self,
        *,
        agent_id: str,
        task: str,
        system_prompt: str,
        model: str,
        execution_kind: str,
        tools: list[dict[str, Any]] | None,
    ) -> SubagentExecution:
        """Persist the initial task before any child construction or thread start."""
        child_session_id = str(uuid.uuid4())
        started_at = _now_marker()
        metadata = {
            "session_kind": "subagent",
            "venue": self.venue(),
            "parent_session_id": self.parent_session_id,
            "parent_agent_id": self.parent_agent_id,
            "agent_id": agent_id,
            "task": task,
            "system_prompt": system_prompt,
            "declared_tools": _declared_tool_names(tools),
            "execution_kind": execution_kind,
            "subagent_status": "running",
            "started_at": started_at,
            "ended_at": "",
            "terminal_error": "",
            "follow_up_count": 0,
        }
        execution = SubagentExecution(
            recorder=self,
            agent_id=agent_id,
            child_session_id=child_session_id,
            task=task,
            system_prompt=system_prompt,
            model=model,
            execution_kind=execution_kind,
            declared_tools=_declared_tool_names(tools),
            metadata=metadata,
            messages=[_message("user", task, 0)],
        )
        execution._save()
        try:
            execution._append(
                "session_start", "Subagent started", {"execution_kind": execution_kind}
            )
        except Exception as exc:
            execution.registration_failed(exc)
            raise
        return execution


def bind_subagent_recorder(session: Any, store: Any) -> SubagentSessionRecorder:
    """Install one idempotent root binding without adding a store dependency to agents."""
    agent = session.agent
    agent_id = str(
        session.metadata.get("agent_id") or getattr(agent, "agent_id", "") or uuid.uuid4().hex[:12]
    )
    session.metadata["agent_id"] = agent_id
    agent.agent_id = agent_id
    recorder = SubagentSessionRecorder(
        store,
        session.session_id,
        agent_id,
        venue=lambda: session.metadata.get("venue") or "local",
    )
    agent._subagent_sessions = recorder
    return recorder
