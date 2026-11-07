"""Task graph for companion sessions.

A task graph is a DAG of tasks. Each task has:
- ``id``: UUID
- ``parent_id``: Parent task (for hierarchy)
- ``title``: Human-readable title
- ``status``: pending | active | done | failed
- ``progress``: 0-100
- ``depends_on``: List of task IDs that must complete first
- ``child_ids``: List of child task IDs

The graph is stored in SQLite and can be queried for:
- Ready tasks (all dependencies met)
- Root tasks (no parent)
- Children of a task
- Full tree serialization

This replaces the flat timeline with a structured DAG view:
    Fix NaN issue [52%]
    ├── Search papers ✓
    ├── Find bug ✓
    ├── Patch code ●
    └── Run tests ○
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Task:
    """A task in the task graph."""

    id: str = field(default_factory=lambda: str(uuid4()))
    parent_id: str | None = None
    title: str = ""
    status: str = "pending"  # pending | active | done | failed
    progress: int = 0  # 0-100
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    notes: str = ""
    depends_on: list[str] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "notes": self.notes,
            "depends_on": self.depends_on,
            "child_ids": self.child_ids,
        }


class TaskGraph:
    """A DAG of tasks backed by SQLite.

    Usage::

        tg = TaskGraph(db_path)
        root = tg.add_task("Fix NaN issue")
        search = tg.add_task("Search papers", parent_id=root.id)
        patch = tg.add_task("Patch code", parent_id=root.id, depends_on=[search.id])
        tg.update_task_status(search.id, "done")
        ready = tg.get_ready_tasks()  # [patch]
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # ':memory:' must be ONE connection for the object's lifetime —
        # every sqlite3.connect(':memory:') is a brand-new, table-free
        # database (mock-store bridge sessions ride this path; per-call
        # connects made add-then-list see 'no such table').
        self._memory_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    depends_on TEXT NOT NULL DEFAULT '[]',
                    child_ids TEXT NOT NULL DEFAULT '[]'
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            self._ensure_session_column(conn)

    @staticmethod
    def _ensure_session_column(conn: sqlite3.Connection) -> None:
        """The owning companion session of a task (NULL for rows written
        before 2026-10-17 and for tasks added without one). Every session's
        bridge shares one sqlite file, so a session's plan is the rows that
        carry its id (TUI audit r2 #27)."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "session_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN session_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)")

    def _conn(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_task(
        self,
        title: str,
        *,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
        notes: str = "",
        session_id: str | None = None,
    ) -> Task:
        """Add a task to the graph. Returns the new Task.

        ``session_id`` scopes it to one companion session (see
        ``list_all_tasks``)."""
        task = Task(
            title=title,
            parent_id=parent_id,
            depends_on=depends_on or [],
            notes=notes,
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id, parent_id, title, status, progress,
                    created_at, updated_at, completed_at, notes,
                    depends_on, child_ids, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.parent_id,
                    task.title,
                    task.status,
                    task.progress,
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                    task.notes,
                    json.dumps(task.depends_on),
                    json.dumps(task.child_ids),
                    session_id,
                ),
            )
            # Update parent's child_ids
            if parent_id:
                parent = self._get_task(conn, parent_id)
                if parent:
                    new_child_ids = parent.child_ids + [task.id]
                    conn.execute(
                        "UPDATE tasks SET child_ids = ? WHERE id = ?",
                        (json.dumps(new_child_ids), parent_id),
                    )
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        with self._conn() as conn:
            return self._get_task(conn, task_id)

    def _get_task(self, conn: sqlite3.Connection, task_id: str) -> Task | None:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def update_task_status(self, task_id: str, status: str) -> bool:
        """Update the status of a task. Returns True if updated."""
        now = datetime.now(timezone.utc).isoformat()
        completed_at = now if status in ("done", "failed") else None
        progress = 100 if status == "done" else None
        with self._conn() as conn:
            cursor = conn.execute(
                """UPDATE tasks
                   SET status = ?, updated_at = ?, completed_at = ?,
                       progress = COALESCE(?, progress)
                   WHERE id = ?""",
                (status, now, completed_at, progress, task_id),
            )
            return cursor.rowcount > 0

    def update_task_progress(self, task_id: str, progress: int) -> bool:
        """Update the progress of a task. Returns True if updated."""
        safe_progress = max(0, min(100, progress))
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                """UPDATE tasks SET progress = ?, updated_at = ? WHERE id = ?""",
                (safe_progress, now, task_id),
            )
            return cursor.rowcount > 0

    def get_ready_tasks(self) -> list[Task]:
        """Get tasks that are ready to execute (pending + all deps done)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
        ready: list[Task] = []
        for row in rows:
            task = self._row_to_task(row)
            if not task.depends_on:
                ready.append(task)
                continue
            # Check all dependencies are done
            all_done = True
            for dep_id in task.depends_on:
                dep = self.get_task(dep_id)
                if not dep or dep.status != "done":
                    all_done = False
                    break
            if all_done:
                ready.append(task)
        return ready

    def get_root_tasks(self) -> list[Task]:
        """Get tasks that have no parent."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id IS NULL ORDER BY created_at"
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_children(self, parent_id: str) -> list[Task]:
        """Get the children of a task."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_all_tasks(self, session_id: str | None = None) -> list[Task]:
        """List all tasks; with ``session_id``, only that session's."""
        with self._conn() as conn:
            if session_id is None:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entire task graph."""
        tasks = self.list_all_tasks()
        return {task.id: task.to_dict() for task in tasks}

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            parent_id=row["parent_id"],
            title=row["title"],
            status=row["status"],
            progress=row["progress"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            notes=row["notes"],
            depends_on=json.loads(row["depends_on"]),
            child_ids=json.loads(row["child_ids"]),
        )
