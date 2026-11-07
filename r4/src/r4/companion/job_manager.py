"""Background job manager for companion sessions.

Manages detached background agent workflows. Agent work survives
terminal closure — similar to tmux/screen/Slurm.

Jobs are stored in the ``jobs`` table within the same SQLite database
as the session store. Each job has:
- ``id``: UUID
- ``session_id``: Associated session
- ``pid``: Process ID of the detached worker
- ``status``: running|completed|failed|stopped
- ``log_path``: Path to the job's log file
- ``task_description``: Human-readable task summary
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class JobManager:
    """Manage detached background agent workflows.

    Usage::

        jm = JobManager(db_path)
        job_id = jm.start_job(
            session_id="s1",
            task="Run benchmark suite",
            command=["python", "-m", "r4", "--yolo"],
        )
        # Later...
        status = jm.get_job(job_id)
        jm.stop_job(job_id)
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
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    pid INTEGER,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    log_path TEXT,
                    task_description TEXT
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id)")

    def _conn(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start_job(
        self,
        *,
        session_id: str,
        task: str,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> str:
        """Start a detached background job. Returns the job ID.

        Args:
            session_id: Session this job belongs to.
            task: Human-readable task description.
            command: Command to execute (argv list).
            env: Additional environment variables.
            cwd: Working directory for the job.

        Returns:
            The job UUID.
        """
        job_id = str(uuid4())
        log_dir = Path.home() / ".config" / "r4" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}.log"

        merged_env = {**os.environ, **(env or {})}
        with open(log_path, "w") as log_file:
            proc = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                env=merged_env,
                cwd=cwd,
                start_new_session=True,  # detach from parent process group
            )

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, session_id, pid, status, started_at, log_path, task_description)
                   VALUES (?, ?, ?, 'running', ?, ?, ?)""",
                (
                    job_id,
                    session_id,
                    proc.pid,
                    datetime.now(timezone.utc).isoformat(),
                    str(log_path),
                    task,
                ),
            )
        return job_id

    def list_jobs(
        self,
        *,
        include_completed: bool = False,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List background jobs.

        Args:
            include_completed: If False, only return running jobs.
            session_id: Filter by session ID.

        Returns:
            List of job dicts, newest first.
        """
        sql = "SELECT * FROM jobs"
        conditions: list[str] = []
        params: list[Any] = []
        if not include_completed:
            conditions.append("status = 'running'")
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY started_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get the status of a background job.

        Also checks if the process is still alive and updates the
        status if the process has exited.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        job = dict(row)
        # Check if process is still alive
        if job.get("status") == "running" and job.get("pid"):
            if not _is_process_alive(job["pid"]):
                # Process has exited — check log for success/failure
                exit_code = _get_exit_code_from_log(job.get("log_path", ""))
                status = "failed" if exit_code != 0 else "completed"
                self.update_job_status(job_id, status)
                job["status"] = status
        return job

    def stop_job(self, job_id: str) -> bool:
        """Stop a running background job.

        Returns True if the job was stopped, False if it wasn't running
        or didn't exist.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT pid FROM jobs WHERE id = ? AND status = 'running'",
                (job_id,),
            ).fetchone()
        if not row or not row["pid"]:
            return False
        try:
            os.kill(row["pid"], signal.SIGTERM)
            # Give it a moment to exit gracefully
            import time

            time.sleep(0.5)
            if _is_process_alive(row["pid"]):
                os.kill(row["pid"], signal.SIGKILL)
            self.update_job_status(job_id, "stopped")
            return True
        except ProcessLookupError:
            self.update_job_status(job_id, "stopped")
            return True
        except OSError:
            return False

    def update_job_status(self, job_id: str, status: str) -> None:
        """Update the status of a background job.

        Args:
            job_id: The job UUID.
            status: New status (running|completed|failed|stopped).
        """
        ended_at = datetime.now(timezone.utc).isoformat() if status not in ("running",) else None
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, ended_at = ? WHERE id = ?""",
                (status, ended_at, job_id),
            )

    def get_job_log(self, job_id: str, *, tail_lines: int = 50) -> str | None:
        """Read the tail of a job's log file.

        Args:
            job_id: The job UUID.
            tail_lines: Number of lines to read from the end.

        Returns:
            The log content as a string, or None if the job doesn't exist.
        """
        job = self.get_job(job_id)
        if not job:
            return None
        log_path = job.get("log_path", "")
        if not log_path or not Path(log_path).exists():
            return ""
        try:
            with open(log_path) as f:
                lines = f.readlines()
            return "".join(lines[-tail_lines:])
        except OSError:
            return ""


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # signal 0 = check if process exists
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _get_exit_code_from_log(log_path: str) -> int:
    """Best-effort extraction of exit code from a job log.

    Looks for common patterns like "exit code: N" or shell exit markers.
    Returns 0 if no error is found, 1 otherwise.
    """
    if not log_path or not Path(log_path).exists():
        return 0
    try:
        with open(log_path) as f:
            content = f.read()
        # Check for common failure markers
        for marker in ("Traceback", "Error:", "FATAL", "exit code: 1"):
            if marker in content:
                return 1
        return 0
    except OSError:
        return 0
