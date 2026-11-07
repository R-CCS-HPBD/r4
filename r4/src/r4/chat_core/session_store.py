"""Unified session persistence for all chat surfaces.

Stores sessions from TUI, ChatApp, and companion REPL in a single
SQLite database at ``~/.config/r4/sessions.db``.

Schema v2 normalizes messages into a dedicated table and adds sync
metadata for UMRS context storage integration.

Migrates transparently from the legacy companion store if it exists.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from r4.chat_types import ChatMessage

from .session import ChatSession, Turn

DB_PATH = Path(os.environ.get("R4_SESSION_DB") or os.path.expanduser("~/.config/r4/sessions.db"))

LEGACY_DB_PATH = Path(
    os.environ.get("R4_COMPANION_DB") or os.path.expanduser("~/.config/r4/companion/sessions.db")
)

_SCHEMA_VERSION = 2


class SessionStore:
    """Unified SQLite session store for all R4 chat surfaces."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._maybe_migrate_legacy()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    surface TEXT NOT NULL DEFAULT 'tui',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    turns TEXT NOT NULL DEFAULT '[]',
                    system_prompt TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    remote_conversation_id TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'local',
                    last_synced_at TEXT,
                    _schema_version INTEGER NOT NULL DEFAULT 1
                )"""
            )
            # Normalized messages table for per-message indexing and sync
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    token_count INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_session_messages_session_seq
                   ON session_messages(session_id, seq)"""
            )
            # Usage records for token tracking per session
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_usage (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_session_usage_session
                   ON session_usage(session_id)"""
            )
            # Schema version tracking
            conn.execute(
                """CREATE TABLE IF NOT EXISTS _schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            conn.execute(
                """INSERT OR REPLACE INTO _schema_info (key, value)
                   VALUES ('version', ?)""",
                (str(_SCHEMA_VERSION),),
            )
            # Migrate existing sessions table if columns missing
            self._upgrade_sessions_table(conn)

    def _upgrade_sessions_table(self, conn: sqlite3.Connection) -> None:
        """Add v2 columns to sessions table if missing (idempotent)."""
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        new_cols = {
            "remote_conversation_id": "TEXT",
            "sync_status": "TEXT NOT NULL DEFAULT 'local'",
            "last_synced_at": "TEXT",
            "_schema_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for col_name, col_type in new_cols.items():
            if col_name not in columns:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")

    def _maybe_migrate_legacy(self) -> None:
        """One-time migration from companion sessions.db."""
        if not LEGACY_DB_PATH.exists():
            return
        marker = self.db_path.parent / ".migrated_companion"
        if marker.exists():
            return
        try:
            legacy = sqlite3.connect(str(LEGACY_DB_PATH))
            rows = legacy.execute(
                "SELECT id, name, created_at, updated_at, messages, model FROM sessions"
            ).fetchall()
            legacy.close()

            with self._conn() as conn:
                for row in rows:
                    sid, name, created, updated, messages_json, model = row
                    # Convert companion messages to turns
                    turns = self._convert_legacy_messages(messages_json)
                    conn.execute(
                        """INSERT OR IGNORE INTO sessions
                           (id, name, model, surface, created_at, updated_at, turns)
                           VALUES (?, ?, ?, 'companion', ?, ?, ?)""",
                        (sid, name, model or "", created, updated, json.dumps(turns)),
                    )
            marker.touch()
        except Exception:
            pass  # migration is best-effort

    @staticmethod
    def _convert_legacy_messages(messages_json: str) -> list[dict]:
        """Convert companion messages list to turn pairs."""
        messages = json.loads(messages_json)
        turns = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                assistant_text = ""
                if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                    assistant_text = messages[i + 1].get("content", "")
                    i += 1
                turns.append({"user": user_text, "assistant": assistant_text})
            i += 1
        return turns

    def save(self, session: ChatSession, *, name: str | None = None, surface: str = "tui") -> str:
        """Save a session. Returns the session ID."""
        session_id = getattr(session, "_store_id", None) or str(uuid.uuid4())
        session._store_id = session_id  # type: ignore[attr-defined]
        now = datetime.now().isoformat()
        created = getattr(session, "_store_created", now)
        session._store_created = created  # type: ignore[attr-defined]

        session_name = name or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        turns_json = json.dumps(
            [
                {
                    "user": t.user,
                    "assistant": t.assistant,
                    "model": t.model or "",
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                }
                for t in session.turns
            ]
        )

        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id, name, model, surface, created_at, updated_at, turns,
                    system_prompt, metadata, _schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    session_name,
                    session.model or "",
                    surface,
                    created,
                    now,
                    turns_json,
                    session.system_prompt or "",
                    "{}",
                    _SCHEMA_VERSION,
                ),
            )
            # Write normalized messages (replace all for simplicity)
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            seq = 0
            for turn in session.turns:
                # User message
                conn.execute(
                    """INSERT INTO session_messages
                       (id, session_id, role, content, seq, model, token_count, created_at)
                       VALUES (?, ?, 'user', ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        turn.user,
                        seq,
                        turn.model or "",
                        turn.prompt_tokens,
                        now,
                    ),
                )
                seq += 1
                # Assistant message
                if turn.assistant:
                    conn.execute(
                        """INSERT INTO session_messages
                           (id, session_id, role, content, seq, model, token_count, created_at)
                           VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            session_id,
                            turn.assistant,
                            seq,
                            turn.model or "",
                            turn.completion_tokens,
                            now,
                        ),
                    )
                    seq += 1
            # Write usage record
            total_prompt = sum(t.prompt_tokens for t in session.turns)
            total_completion = sum(t.completion_tokens for t in session.turns)
            if total_prompt or total_completion:
                conn.execute(
                    """INSERT OR REPLACE INTO session_usage
                       (id, session_id, prompt_tokens, completion_tokens, model, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        session_id,
                        total_prompt,
                        total_completion,
                        session.model or "",
                        now,
                    ),
                )
        return session_id

    def load(self, session_id: str) -> Optional[ChatSession]:
        """Load a session by ID."""
        with self._conn() as conn:
            row = conn.execute(
                (
                    "SELECT id, name, model, surface, created_at, updated_at, turns,"
                    " system_prompt FROM sessions WHERE id = ?"
                ),
                (session_id,),
            ).fetchone()

        if not row:
            return None

        sid, name, model, surface, created, updated, turns_json, system_prompt = row
        session = ChatSession(model=model, system_prompt=system_prompt or None)
        session._store_id = sid  # type: ignore[attr-defined]
        session._store_created = created  # type: ignore[attr-defined]

        for t in json.loads(turns_json):
            turn = Turn(
                user=t["user"],
                assistant=t["assistant"],
                model=t.get("model"),
                prompt_tokens=t.get("prompt_tokens", 0),
                completion_tokens=t.get("completion_tokens", 0),
            )
            session.turns.append(turn)
            session.messages.append(ChatMessage.user(turn.user))
            if turn.assistant:
                session.messages.append(ChatMessage.assistant(turn.assistant))
            session.total_prompt_tokens += int(turn.prompt_tokens or 0)
            session.total_completion_tokens += int(turn.completion_tokens or 0)

        return session

    def list_sessions(self, limit: int = 50, surface: str | None = None) -> list[dict[str, Any]]:
        """List sessions ordered by most recently updated."""
        query = "SELECT id, name, model, surface, created_at, updated_at FROM sessions"
        params: list[Any] = []
        if surface:
            query += " WHERE surface = ?"
            params.append(surface)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "id": r[0],
                "name": r[1],
                "model": r[2],
                "surface": r[3],
                "created_at": r[4],
                "updated_at": r[5],
            }
            for r in rows
        ]

    def delete(self, session_id: str) -> bool:
        """Delete a session by ID."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Sync protocol: R4 SQLite is a cache; UMRS context storage is truth.
    # ------------------------------------------------------------------

    def mark_synced(
        self,
        session_id: str,
        remote_conversation_id: str,
    ) -> None:
        """Mark a local session as synced with UMRS context storage."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE sessions
                   SET remote_conversation_id = ?,
                       sync_status = 'synced',
                       last_synced_at = ?
                   WHERE id = ?""",
                (remote_conversation_id, now, session_id),
            )

    def mark_pending(self, session_id: str) -> None:
        """Mark a session as pending sync (modified locally after last sync)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET sync_status = 'pending' WHERE id = ?",
                (session_id,),
            )

    def pending_sync_sessions(self) -> list[dict[str, Any]]:
        """Return sessions that need to be pushed to UMRS."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, name, model, surface, updated_at,
                          remote_conversation_id, sync_status
                   FROM sessions
                   WHERE sync_status IN ('local', 'pending')
                   ORDER BY updated_at ASC""",
            ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "model": r[2],
                "surface": r[3],
                "updated_at": r[4],
                "remote_conversation_id": r[5],
                "sync_status": r[6],
            }
            for r in rows
        ]

    def get_sync_status(self, session_id: str) -> dict[str, Any] | None:
        """Return sync metadata for a session."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT sync_status, remote_conversation_id, last_synced_at
                   FROM sessions WHERE id = ?""",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "sync_status": row[0],
            "remote_conversation_id": row[1],
            "last_synced_at": row[2],
        }


__all__ = ["SessionStore", "DB_PATH"]
