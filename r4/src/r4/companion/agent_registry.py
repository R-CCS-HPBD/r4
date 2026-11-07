"""In-memory agent registry for the companion TUI.

Tracks the live tree of agents (main + subagents) with stable IDs,
parent-child links, status, current tool, started/ended timestamps.

Thread-safe; evicts completed agents after a TTL so the registry does
not grow unbounded across a long session.

The registry is the backend for the ``AgentsPanel`` in the JS TUI and is
exposed via the bridge's RPC methods:

- ``list_active_agents`` → flat list of active agents
- ``get_agent_tree``     → nested tree of all agents (active + recently completed)
- ``get_agent_status``   → single agent record by id
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# How long to keep completed/errored agents in memory before eviction.
DEFAULT_EVICT_TTL_SEC = 300.0  # 5 minutes

# Valid agent statuses. ``thinking`` = waiting on LLM; ``running`` = executing
# a tool; ``idle`` = registered but not yet started; ``completed`` /
# ``errored`` = terminal.
_VALID_STATUSES = {"idle", "thinking", "running", "completed", "errored"}


@dataclass
class AgentRecord:
    """One agent in the live tree."""

    agent_id: str
    parent_id: str | None
    name: str
    task: str
    status: str = "idle"
    # ``current_tool`` is ``None`` when the agent is between tools.
    current_tool: dict[str, Any] | None = None
    # ``last_tool`` is the most recently completed tool (name + status + duration).
    last_tool: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    tool_count: int = 0
    # agent_ids of direct children (for tree rendering).
    children: list[str] = field(default_factory=list)

    def is_active(self) -> bool:
        """True while the agent has started but not yet ended."""
        return self.started_at is not None and self.ended_at is None

    def elapsed_sec(self) -> float:
        """Wall-clock seconds since the agent started (or 0 if not started)."""
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for RPC."""
        return {
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "task": self.task,
            "status": self.status,
            "current_tool": self.current_tool,
            "last_tool": self.last_tool,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "tool_count": self.tool_count,
            "children": list(self.children),
            "elapsed_sec": round(self.elapsed_sec(), 2),
        }


class AgentRegistry:
    """Thread-safe in-memory tree of active agents.

    The registry is intentionally ephemeral — it tracks the live state of
    agents for UI rendering. Persistence of agent events (for replay/audit)
    is handled separately by the :class:`EventLog`.
    """

    def __init__(self, *, evict_ttl_sec: float = DEFAULT_EVICT_TTL_SEC) -> None:
        self._records: dict[str, AgentRecord] = {}
        self._lock = threading.Lock()
        self._evict_ttl_sec = evict_ttl_sec

    # -- registration ------------------------------------------------------

    def register(
        self,
        *,
        agent_id: str,
        parent_id: str | None = None,
        name: str = "agent",
        task: str = "",
    ) -> AgentRecord:
        """Register a new agent. Returns the new record.

        Re-registering an existing ``agent_id`` is a no-op (idempotent) —
        this makes the registry safe to call from ``__init__`` even if the
        bridge has already pre-registered the main agent.
        """
        with self._lock:
            existing = self._records.get(agent_id)
            if existing is not None:
                return existing
            record = AgentRecord(
                agent_id=agent_id,
                parent_id=parent_id,
                name=name,
                task=task,
            )
            self._records[agent_id] = record
            if parent_id is not None:
                parent = self._records.get(parent_id)
                if parent is not None and agent_id not in parent.children:
                    parent.children.append(agent_id)
            return record

    def start(self, agent_id: str) -> None:
        """Mark an agent as started (first turn beginning)."""
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            if record.started_at is None:
                record.started_at = time.time()
            if record.status in {"idle"}:
                record.status = "thinking"

    def update_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status. Silently ignores unknown agents."""
        if status not in _VALID_STATUSES:
            return
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            record.status = status
            if status in {"completed", "errored"} and record.ended_at is None:
                record.ended_at = time.time()
                record.current_tool = None

    def set_current_tool(
        self,
        agent_id: str,
        *,
        tool_name: str,
        target: str = "",
        started_at: float | None = None,
    ) -> None:
        """Set the tool currently being executed by ``agent_id``."""
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            record.current_tool = {
                "name": tool_name,
                "target": target,
                "started_at": started_at or time.time(),
            }
            if record.status == "thinking":
                record.status = "running"

    def record_tool_result(
        self,
        agent_id: str,
        *,
        tool_name: str,
        status: str,
        duration_ms: float = 0.0,
    ) -> None:
        """Record a completed tool's result; clears ``current_tool``."""
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            record.last_tool = {
                "name": tool_name,
                "status": status,
                "duration_ms": round(duration_ms, 1),
            }
            record.current_tool = None
            record.tool_count += 1
            if record.status == "running":
                record.status = "thinking"

    # -- queries -----------------------------------------------------------

    def get(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            return self._records.get(agent_id)

    def active_count(self) -> int:
        """Number of agents that have started but not yet ended."""
        with self._lock:
            self._evict_locked()
            return sum(1 for r in self._records.values() if r.is_active())

    def list_active(self) -> list[dict[str, Any]]:
        """Flat list of currently-active agents (started, not ended)."""
        with self._lock:
            self._evict_locked()
            return [r.to_dict() for r in self._records.values() if r.is_active()]

    def tree(self) -> dict[str, Any]:
        """Nested tree of all non-evicted agents, rooted at parent-less nodes."""
        with self._lock:
            self._evict_locked()
            roots = [
                r
                for r in self._records.values()
                if not r.parent_id or r.parent_id not in self._records
            ]
            return {
                "agents": [self._subtree_locked(r.agent_id) for r in roots],
                "active_count": sum(1 for r in self._records.values() if r.is_active()),
                "total_count": len(self._records),
            }

    # -- eviction ----------------------------------------------------------

    def _evict_locked(self) -> None:
        """Drop terminal agents older than the TTL. Caller holds the lock."""
        now = time.time()
        evict_ids = [
            agent_id
            for agent_id, r in self._records.items()
            if r.ended_at is not None and (now - r.ended_at) > self._evict_ttl_sec
        ]
        for agent_id in evict_ids:
            record = self._records.pop(agent_id, None)
            if record is None:
                continue
            # Detach from parent's children list to avoid dangling refs.
            if record.parent_id is not None:
                parent = self._records.get(record.parent_id)
                if parent is not None and agent_id in parent.children:
                    parent.children.remove(agent_id)

    def _subtree_locked(self, agent_id: str) -> dict[str, Any]:
        """Recursive serialization. Caller holds the lock."""
        record = self._records.get(agent_id)
        if record is None:
            return {}
        data = record.to_dict()
        data["children"] = [
            self._subtree_locked(child_id)
            for child_id in record.children
            if child_id in self._records
        ]
        return data
