"""Companion state hardening pins (chat tabs, 2026-09-30).

- r4._atomic.atomic_write_text: exact content, no tmp leftovers, rename swap.
- saved-memory facts (ADR-111) and steering._save ride the atomic writer (a torn write
  can never silently unload memory/lessons for a parallel reader).
- session_store sqlite connections carry WAL + busy_timeout so up to 8
  bridge processes can write concurrently without SQLITE_BUSY.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from r4._atomic import atomic_write_text


def test_atomic_write_content_and_no_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_swaps_existing_and_is_concurrent_reads_safe(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_failure_keeps_destination_and_cleans_tmp(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, "keep")
    # Force a failure: the destination IS a directory (rename of file→dir).
    blocker = tmp_path / "adir"
    blocker.mkdir()
    with pytest.raises(OSError):
        atomic_write_text(blocker, "x")
    assert target.read_text() == "keep"
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("**/.*.tmp"))


def test_memory_fact_uses_atomic_writer(tmp_path: Path, monkeypatch) -> None:
    """ADR-111: each saved fact (and the scope's MEMORY.md view) is written
    through the atomic writer, private to the user (0600)."""
    from r4.companion import memory_store

    calls: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(
        memory_store, "atomic_write_text", lambda p, t, mode=0o644: calls.append((p, t, mode))
    )
    monkeypatch.setenv("R4_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("R4_COMPANION_MEMORY_DIR", str(tmp_path / "legacy"))

    from r4.companion.context import ContextManager

    ContextManager().remember("goal", "compare sft vs dpo", source="test")

    names = [path.name for path, _text, _mode in calls]
    assert names == ["goal.md", "MEMORY.md"]
    assert all(mode == 0o600 for _path, _text, mode in calls)
    assert "source: test" in calls[0][1]
    assert calls[0][1].rstrip().endswith("compare sft vs dpo")


def test_steering_save_uses_atomic_writer(tmp_path: Path, monkeypatch) -> None:
    from r4.companion.harness import steering as st_mod

    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(st_mod, "atomic_write_text", lambda p, t: calls.append((p, t)))

    from r4.companion.harness.steering import SteeringLoop

    loop = SteeringLoop(lessons_dir=tmp_path)
    loop.observe_turn("q", "some answer")
    loop.observe_turn("no, use sqlite wal mode instead next time", "")
    assert calls, "atomic writer was not used"
    data = json.loads(calls[-1][1])
    assert data[0]["correction"].startswith("no, use sqlite wal")


def test_session_store_conn_pragmas(tmp_path: Path) -> None:
    from r4.companion.session_store import CompanionSessionStore

    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    with store._conn() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert int(busy) >= 5000


def test_session_store_parallel_writers_no_busy(tmp_path: Path) -> None:
    """Two store instances on one db write concurrently without SQLITE_BUSY."""
    from r4.companion.session_store import CompanionSession, CompanionSessionStore

    db = tmp_path / "sessions.db"
    errors: list[Exception] = []

    lightweight_client = type("C", (), {"config": None})()

    def writer(store: CompanionSessionStore, prefix: str) -> None:
        try:
            for i in range(8):
                session = CompanionSession(
                    f"{prefix}-{i}", f"{prefix} session {i}", lightweight_client
                )
                store.save(session)
        except Exception as exc:  # pragma: no cover - failure lands here
            errors.append(exc)

    a, b = CompanionSessionStore(db_path=db), CompanionSessionStore(db_path=db)
    threads = [threading.Thread(target=writer, args=(s, p)) for s, p in ((a, "a"), (b, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"parallel writers failed: {errors!r}"
    with sqlite3.connect(str(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 16
