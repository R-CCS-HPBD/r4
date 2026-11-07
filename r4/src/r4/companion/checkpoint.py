"""Checkpoint manager for companion sessions.

Lightweight state snapshots for crash recovery. Checkpoints are
stored in the ``checkpoints`` table within the same SQLite database
as the session store.

A checkpoint captures:
- ``session_id``: The session being checkpointed
- ``agent_id``: The agent (or ``__supervisor__`` for session-level)
- ``state``: JSON blob with messages, tool history, progress
- ``checkpoint_type``: auto|manual|pre_tool|post_turn
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class CheckpointManager:
    """Periodic state snapshots for crash recovery.

    Usage::

        cm = CheckpointManager(db_path)
        cm.save_checkpoint(
            session_id="s1",
            agent_id="agent-1",
            state={"messages": [...], "progress": 42},
        )
        # After crash...
        state = cm.load_latest_checkpoint("s1")
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # ':memory:' keeps ONE connection for the object's lifetime —
        # sqlite3.connect(':memory:') per call is a fresh, table-free db.
        self._memory_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT '__supervisor__',
                    timestamp TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '{}',
                    checkpoint_type TEXT NOT NULL DEFAULT 'auto'
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_session ON checkpoints(session_id, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_agent ON checkpoints(agent_id, timestamp)"
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

    def save_checkpoint(
        self,
        *,
        session_id: str,
        agent_id: str = "__supervisor__",
        state: dict[str, Any],
        checkpoint_type: str = "auto",
    ) -> str:
        """Save a checkpoint. Returns the checkpoint ID.

        Args:
            session_id: Session this checkpoint belongs to.
            agent_id: Agent this checkpoint belongs to.
            state: Serializable state dict.
            checkpoint_type: auto|manual|pre_tool|post_turn.

        Returns:
            The checkpoint UUID.
        """
        checkpoint_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO checkpoints
                   (id, session_id, agent_id, timestamp, state, checkpoint_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint_id,
                    session_id,
                    agent_id,
                    timestamp,
                    json.dumps(state),
                    checkpoint_type,
                ),
            )
        return checkpoint_id

    def load_latest_checkpoint(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Load the latest checkpoint for a session.

        Args:
            session_id: Session to load.
            agent_id: Optional filter by agent ID.

        Returns:
            The checkpoint state dict, or None if no checkpoint exists.
        """
        sql = "SELECT state FROM checkpoints WHERE session_id = ?"
        params: list[Any] = [session_id]
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY timestamp DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["state"])
        except (json.JSONDecodeError, KeyError):
            return None

    def list_checkpoints(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List checkpoints for a session, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, agent_id, timestamp, checkpoint_type
                   FROM checkpoints WHERE session_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint. Returns True if deleted."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM checkpoints WHERE id = ?",
                (checkpoint_id,),
            )
            return cursor.rowcount > 0

    def count_checkpoints(self, session_id: str) -> int:
        """Return the total number of checkpoints for a session."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row[0] if row else 0
