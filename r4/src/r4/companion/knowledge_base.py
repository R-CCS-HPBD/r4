"""Knowledge base for the companion TUI.

Tracks patterns learned and anti-patterns encountered during autonomous
research loops.  This is the "knowledge building up" layer — each loop
iteration can contribute patterns or anti-patterns.

See: docs/autonomous-science/PLAN.md §3 (Knowledge Acquisition Layer)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class KnowledgeBase:
    """Persistent knowledge base for patterns and anti-patterns.

    All state is stored in SQLite (same DB as the session store).
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
                """CREATE TABLE IF NOT EXISTS knowledge_patterns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    evidence TEXT,
                    confidence REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_lessons (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    context TEXT,
                    category TEXT DEFAULT 'general',
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kp_session ON knowledge_patterns(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kl_session ON knowledge_lessons(session_id)"
            )

    def _conn(self) -> sqlite3.Connection:
        # 2026-10-18 (H-4 contract find): row_factory was never set, so every
        # dict(row) listing raised ValueError as soon as a row existed.
        if self._db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_pattern(
        self,
        *,
        session_id: str,
        title: str,
        description: str = "",
        evidence: str = "",
        confidence: float = 0.5,
    ) -> str:
        """Add a successful pattern."""
        pattern_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO knowledge_patterns
                   (id, session_id, pattern_type, title, description,
                    evidence, confidence, created_at)
                   VALUES (?, ?, 'pattern', ?, ?, ?, ?, ?)""",
                (pattern_id, session_id, title, description, evidence, confidence, timestamp),
            )
        return pattern_id

    def add_anti_pattern(
        self,
        *,
        session_id: str,
        title: str,
        description: str = "",
        evidence: str = "",
        confidence: float = 0.5,
    ) -> str:
        """Add an anti-pattern (something that failed)."""
        pattern_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO knowledge_patterns
                   (id, session_id, pattern_type, title, description,
                    evidence, confidence, created_at)
                   VALUES (?, ?, 'anti_pattern', ?, ?, ?, ?, ?)""",
                (pattern_id, session_id, title, description, evidence, confidence, timestamp),
            )
        return pattern_id

    def add_lesson(
        self,
        *,
        session_id: str,
        lesson: str,
        context: str = "",
        category: str = "general",
    ) -> str:
        """Add a distilled lesson."""
        lesson_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO knowledge_lessons
                   (id, session_id, lesson, context, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (lesson_id, session_id, lesson, context, category, timestamp),
            )
        return lesson_id

    def list_patterns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """List patterns for a session."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, pattern_type, title, description, evidence,
                          confidence, created_at
                   FROM knowledge_patterns WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_lessons(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """List lessons for a session."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, lesson, context, category, created_at
                   FROM knowledge_lessons WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self, session_id: str) -> dict[str, Any]:
        """Get knowledge base statistics for TUI display."""
        with self._conn() as conn:
            patterns = conn.execute(
                (
                    "SELECT COUNT(*) FROM knowledge_patterns WHERE session_id = ?"
                    " AND pattern_type = 'pattern'"
                ),
                (session_id,),
            ).fetchone()
            anti_patterns = conn.execute(
                (
                    "SELECT COUNT(*) FROM knowledge_patterns WHERE session_id = ?"
                    " AND pattern_type = 'anti_pattern'"
                ),
                (session_id,),
            ).fetchone()
            lessons = conn.execute(
                "SELECT COUNT(*) FROM knowledge_lessons WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            avg_confidence = conn.execute(
                "SELECT AVG(confidence) FROM knowledge_patterns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return {
            "patterns": patterns[0] if patterns else 0,
            "anti_patterns": anti_patterns[0] if anti_patterns else 0,
            "lessons": lessons[0] if lessons else 0,
            "avg_confidence": avg_confidence[0] if avg_confidence and avg_confidence[0] else 0.0,
        }
