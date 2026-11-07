"""ADR-111 M0: a session row never carries saved memory.

Before ADR-111 every save copied the in-process memory dict into the row and
every load wrote it back over the live one, so a restored session brought
back facts deleted since; its next ``remember`` then wrote them to disk for
every session. Memory now lives only in the store's files.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from r4.companion.session_store import CompanionSessionStore


@pytest.fixture(autouse=True)
def _scratch_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("R4_COMPANION_MEMORY_DIR", str(tmp_path / "legacy"))


def _seed(store: CompanionSessionStore, session_id: str) -> None:
    session = MagicMock()
    session.session_id = session_id
    session.name = "original"
    session.created_at = "2026-11-07T10:00:00+00:00"
    session.updated_at = "2026-11-07T10:00:00+00:00"
    session.client = MagicMock()
    session.client.config = MagicMock(profile="default", gateway_url="", project_id="")
    session.messages = [MagicMock(role="user", content="hello", timestamp="t", metadata={})]
    session.skills._active = set()
    stale = MagicMock(key="stale", value="deleted since", created_at="t", source="user")
    session.context._memory = {"stale": stale}
    session.metadata = {"config": {}}
    session.agent.model = "m"
    session._sync_metadata_usage_state = lambda: None
    store.save(session)


def _memory_column(db_path, session_id: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT memory FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return json.loads(row[0])


def test_save_writes_no_memory_into_the_row(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    _seed(store, "sess-a")
    assert _memory_column(tmp_path / "sessions.db", "sess-a") == {}


def test_an_old_row_snapshot_never_comes_back_on_restore(tmp_path):
    db = tmp_path / "sessions.db"
    store = CompanionSessionStore(db_path=db)
    _seed(store, "sess-old")
    # A row written by an older r4 still carries a snapshot.
    snapshot = {"stale": {"key": "stale", "value": "deleted since", "source": "user"}}
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE sessions SET memory = ? WHERE id = ?", (json.dumps(snapshot), "sess-old")
        )
    session = store.load("sess-old", client=MagicMock())
    assert session is not None
    assert session.context.get_memory("stale") is None
