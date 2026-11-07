"""Async background subagents with round-boundary notification (G9).

``spawn_subagent`` is synchronous — the parent blocks on the child, so the
fan-out shape Claude Code's ``run_in_background`` AgentTool enables
(parallel read-heavy research workers, results delivered as
``<task-notification>`` messages at a round boundary) was impossible. The
AsyncAgentPool runs children on daemon threads, caps concurrency, and hands
the parent loop a per-round drain of completion notifications.

The pool is deliberately agent-agnostic: the companion supplies runner
closures; notification text is as close to the ``<task-notification>``
convention as makes sense here (XML shells parse cleanly for models).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_MAX_RUNNING = 4
DEFAULT_RESULT_HEAD = 4_000


def _max_running() -> int:
    raw = os.environ.get("R4_COMPANION_ASYNC_AGENTS_MAX", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_RUNNING
    except ValueError:
        return DEFAULT_MAX_RUNNING


@dataclass
class AsyncAgentRecord:
    agent_id: str
    task: str
    status: str = "running"  # running | completed | failed | cancelled
    result: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    delivered: bool = False
    thread: threading.Thread | None = None
    # G17b: resume state — the child companion and the reconstructed
    # conversation it holds (user task + assistant result per finished turn).
    child: object | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    system_prompt: str = ""

    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


def _notification(record: AsyncAgentRecord, head: int) -> str:
    """One bounded ``<subagent-notification>`` block for the parent's rounds."""
    body = record.result if record.status == "completed" else (record.error or record.status)
    body = body[:head]
    return (
        "<subagent-notification>\n"
        f"  <agent-id>{record.agent_id}</agent-id>\n"
        f"  <status>{record.status}</status>\n"
        f"  <task>{record.task[:300]}</task>\n"
        f"  <duration-ms>{record.duration_ms()}</duration-ms>\n"
        f"  <result>{body}</result>\n"
        "</subagent-notification>\n"
        "This is a background agent's result — not a user message. Fold it "
        "into the work it was spawned for; do not answer it directly."
    )


class AsyncAgentPool:
    """Threaded subagent tracker: spawn (capped), complete, drain, status."""

    def __init__(self, max_running: int | None = None, head_chars: int = DEFAULT_RESULT_HEAD):
        self._max = max_running
        self._head = head_chars
        self._records: dict[str, AsyncAgentRecord] = {}
        self._condition = threading.Condition()

    # -- lifecycle -----------------------------------------------------------

    def spawn(
        self, task: str, runner: Callable[[AsyncAgentRecord], str]
    ) -> AsyncAgentRecord | dict[str, str]:
        """Start a background agent, or return an error dict over the cap."""
        with self._condition:
            running = sum(1 for r in self._records.values() if r.status == "running")
            limit = self._max if self._max is not None else _max_running()
            if running >= limit:
                return {
                    "error": f"{running} background agents already running (cap {limit}); "
                    "wait for one to finish or check async_agent_status"
                }
            record = AsyncAgentRecord(agent_id=uuid.uuid4().hex[:12], task=task)
            record.thread = self._thread(record, runner)
            self._records[record.agent_id] = record
            record.thread.start()
            return record

    def _thread(
        self, record: AsyncAgentRecord, runner: Callable[[AsyncAgentRecord], str]
    ) -> threading.Thread:
        """The agent's daemon thread, its sandbox pinned now (ADR-101, review ESC-2).

        The thread outlives the turn that started it; a session switch must
        not move its later shell and Python commands to another session's
        sandbox setting (an off one included).
        """
        from .sandbox import bind_sandbox

        return threading.Thread(
            target=bind_sandbox(self._run),
            args=(record, runner),
            daemon=True,
            name=f"subagent-{record.agent_id}",
        )

    def _run(self, record: AsyncAgentRecord, runner: Callable[[AsyncAgentRecord], str]) -> None:
        try:
            result = runner(record)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed record, never lost
            from .cancellation import TurnCancelledError

            status = "cancelled" if isinstance(exc, TurnCancelledError) else "failed"
            with self._condition:
                record.status, record.error = status, str(exc)
                self._condition.notify_all()
            return
        with self._condition:
            record.status, record.result = "completed", result
            self._condition.notify_all()

    # -- delivery ------------------------------------------------------------

    def drain(self) -> list[str]:
        """Undelivered completion notifications (each marked delivered)."""
        with self._condition:
            fresh = [r for r in self._records.values() if self._deliverable(r)]
            for record in fresh:
                record.delivered = True
        return [_notification(record, self._head) for record in fresh]

    @staticmethod
    def _deliverable(record: AsyncAgentRecord) -> bool:
        return record.status != "running" and not record.delivered

    def get(self, agent_id: str) -> AsyncAgentRecord | None:
        with self._condition:
            return self._records.get(agent_id)

    def resume(
        self, agent_id: str, runner: Callable[[AsyncAgentRecord], str]
    ) -> dict[str, str] | AsyncAgentRecord:
        """G17b: a follow-up turn on a finished agent's accumulated context."""
        with self._condition:
            record = self._records.get(agent_id)
            if record is None:
                return {"error": f"unknown background agent {agent_id!r}"}
            if record.status == "running":
                return {"error": f"{agent_id} is still running; wait for its notification"}
            record.status = "running"
            record.error = ""
            record.started_at = time.monotonic()
            record.thread = self._thread(record, runner)
            record.thread.start()
            return record

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Tests and turn boundaries: True when nothing is running."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while any(r.status == "running" for r in self._records.values()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def status(self) -> list[dict[str, Any]]:
        with self._condition:
            return [
                {
                    "agent_id": r.agent_id,
                    "task": r.task[:120],
                    "status": r.status,
                    "duration_ms": r.duration_ms(),
                    "delivered": r.delivered,
                    **({"result_head": r.result[:500]} if r.status == "completed" else {}),
                    **({"error": r.error[:500]} if r.error else {}),
                }
                for r in self._records.values()
            ]
