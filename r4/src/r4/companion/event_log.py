"""Persistent event log for companion sessions.

Events are persisted to SQLite for replay, recovery, and audit.
This is the foundation for TUI observability — every tool call,
result, retry, and error is recorded here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .private_db import ensure_private_db
from .tool_summaries import redact_tokens


def _redacted(value: Any) -> Any:
    """``value`` with :func:`redact_tokens` applied to every string in it."""
    if isinstance(value, str):
        return redact_tokens(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(item) for item in value]
    return value


class EventLog:
    """Append-only event log backed by SQLite.

    The event log is the single source of truth for what happened during
    a session. It enables:
    - Session replay (``r4 replay <session-id>``)
    - Crash recovery (reconstruct state from events)
    - Audit trails (who did what, when)
    - Timeline display in the TUI

    Events are written via :meth:`append` and read via :meth:`query`
    or :meth:`tail`.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # ':memory:' keeps ONE connection for the object's lifetime —
        # sqlite3.connect(':memory:') per call is a fresh, table-free db.
        self._memory_conn: sqlite3.Connection | None = None
        if db_path != ":memory:":
            ensure_private_db(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL DEFAULT '{}',
                    tool_name TEXT,
                    tool_status TEXT,
                    tool_duration_ms INTEGER,
                    agent_id TEXT,
                    parent_agent_id TEXT,
                    task_id TEXT,
                    progress_pct INTEGER,
                    intent TEXT,
                    rationale TEXT
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session ON event_log(session_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_agent ON event_log(agent_id, timestamp)"
            )

    def _conn(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        message: str = "",
        data: dict[str, Any] | None = None,
        tool_name: str | None = None,
        tool_status: str | None = None,
        tool_duration_ms: int | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
        task_id: str | None = None,
        progress_pct: int | None = None,
        intent: str | None = None,
        rationale: str | None = None,
    ) -> int:
        """Append an event to the log. Returns the new event ID.

        Args:
            session_id: Session this event belongs to.
            event_type: Event type (e.g. ``tool_call``, ``tool_result``).
            message: Human-readable event message.
            data: Structured event payload.
            tool_name: Tool name (for tool events).
            tool_status: Tool status (success|failure|blocked|running).
            tool_duration_ms: Tool execution duration in milliseconds.
            agent_id: Agent that emitted this event.
            parent_agent_id: Parent agent (for sub-agents).
            task_id: Task this event belongs to.
            progress_pct: Progress percentage (0-100).
            intent: Agent intent (what it's trying to do).
            rationale: Agent rationale (why it's doing this).

        Returns:
            The event ID (auto-incremented).
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        # Tool events carry raw arguments and results (a pasted token, a
        # config file the agent read); the audit log keeps them redacted.
        message = redact_tokens(message or "")
        data = _redacted(data or {})
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO event_log
                (session_id, event_type, timestamp, message, data,
                 tool_name, tool_status, tool_duration_ms,
                 agent_id, parent_agent_id, task_id,
                 progress_pct, intent, rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    event_type,
                    timestamp,
                    message,
                    json.dumps(data or {}),
                    tool_name,
                    tool_status,
                    tool_duration_ms,
                    agent_id,
                    parent_agent_id,
                    task_id,
                    progress_pct,
                    intent,
                    rationale,
                ),
            )
            return cursor.lastrowid or 0

    def query(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query persisted events for replay/recovery.

        Args:
            session_id: Session to query.
            agent_id: Filter by agent ID.
            event_type: Filter by event type.
            since: Only return events after this ISO timestamp.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts, ordered by timestamp ascending.
        """
        sql = "SELECT * FROM event_log WHERE session_id = ?"
        params: list[Any] = [session_id]
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if since:
            sql += " AND timestamp > ?"
            params.append(since)
        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def tail(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent events for live display.

        Args:
            session_id: Session to query.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts, ordered oldest-to-newest.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM event_log WHERE session_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [_row_to_dict(row) for row in reversed(rows)]

    def count(self, session_id: str) -> int:
        """Return the total number of events for a session."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row[0] if row else 0


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a dict, parsing JSON fields."""
    result = dict(row)
    # Parse data JSON
    data_str = result.get("data")
    if isinstance(data_str, str):
        try:
            result["data"] = json.loads(data_str)
        except json.JSONDecodeError:
            result["data"] = {}
    else:
        result["data"] = {}
    return result
