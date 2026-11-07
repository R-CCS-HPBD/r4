"""Tests for the background job manager."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from r4.companion.job_manager import JobManager, _is_process_alive


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "test.db")


@pytest.fixture
def job_mgr(tmp_db):
    return JobManager(tmp_db)


def test_init_creates_jobs_table(tmp_db):
    JobManager(tmp_db)
    import sqlite3

    conn = sqlite3.connect(tmp_db)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    assert ("jobs",) in tables


def test_start_job_returns_id(job_mgr):
    job_id = job_mgr.start_job(
        session_id="s1",
        task="Test job",
        command=["true"],
    )
    assert job_id
    assert len(job_id) == 36  # UUID format


def test_get_job_returns_job_info(job_mgr):
    job_id = job_mgr.start_job(
        session_id="s1",
        task="Echo test",
        command=["echo", "hello"],
    )
    # Wait for the process to finish
    time.sleep(0.5)
    job = job_mgr.get_job(job_id)
    assert job is not None
    assert job["session_id"] == "s1"
    assert job["task_description"] == "Echo test"
    assert job["status"] in ("running", "completed", "failed")


def test_list_jobs_returns_jobs(job_mgr):
    job_mgr.start_job(
        session_id="s1",
        task="Job 1",
        command=["true"],
    )
    job_mgr.start_job(
        session_id="s1",
        task="Job 2",
        command=["true"],
    )
    jobs = job_mgr.list_jobs()
    assert len(jobs) == 2


def test_list_jobs_filters_by_session(job_mgr):
    job_mgr.start_job(
        session_id="s1",
        task="Job 1",
        command=["true"],
    )
    job_mgr.start_job(
        session_id="s2",
        task="Job 2",
        command=["true"],
    )
    s1_jobs = job_mgr.list_jobs(session_id="s1")
    assert len(s1_jobs) == 1
    assert s1_jobs[0]["session_id"] == "s1"


def test_stop_job_marks_stopped(job_mgr):
    job_id = job_mgr.start_job(
        session_id="s1",
        task="Sleep job",
        command=["sleep", "30"],
    )
    # Verify it's running
    job = job_mgr.get_job(job_id)
    assert job["status"] == "running"
    # Stop it
    stopped = job_mgr.stop_job(job_id)
    assert stopped is True
    # Verify status updated
    job = job_mgr.get_job(job_id)
    assert job["status"] == "stopped"


def test_stop_nonexistent_job_returns_false(job_mgr):
    stopped = job_mgr.stop_job("nonexistent-id")
    assert stopped is False


def test_update_job_status(job_mgr):
    job_id = job_mgr.start_job(
        session_id="s1",
        task="Test",
        command=["true"],
    )
    job_mgr.update_job_status(job_id, "completed")
    job = job_mgr.get_job(job_id)
    assert job["status"] == "completed"
    assert job["ended_at"] is not None


def test_get_job_log_returns_content(job_mgr):
    job_id = job_mgr.start_job(
        session_id="s1",
        task="Echo job",
        command=["echo", "hello world"],
    )
    time.sleep(0.5)
    log = job_mgr.get_job_log(job_id, tail_lines=10)
    assert log is not None
    assert "hello world" in log


def test_get_job_log_nonexistent_job(job_mgr):
    log = job_mgr.get_job_log("nonexistent-id")
    assert log is None


def test_is_process_alive_for_current_process():
    # Current process should be alive
    assert _is_process_alive(os.getpid()) is True


def test_is_process_alive_for_dead_process():
    # PID 0 is not a valid process to kill
    assert _is_process_alive(0) is True  # PID 0 exists (kernel)
    # Use a very high PID that definitely doesn't exist
    assert _is_process_alive(999999) is False
