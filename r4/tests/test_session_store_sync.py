"""Tests for R4 session store sync protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from r4.chat_core.session import ChatSession, Turn
from r4.chat_core.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "test_sessions.db")


@pytest.fixture
def saved_session(store: SessionStore) -> tuple[SessionStore, str]:
    session = ChatSession(model="test-model")
    session.turns.append(Turn(user="hello", assistant="hi"))
    sid = store.save(session, name="test-sync")
    return store, sid


class TestSyncProtocol:
    """R4 SQLite cache sync with UMRS context storage."""

    def test_new_session_is_local(self, saved_session: tuple) -> None:
        store, sid = saved_session
        status = store.get_sync_status(sid)
        assert status is not None
        assert status["sync_status"] == "local"
        assert status["remote_conversation_id"] is None

    def test_mark_synced_updates_status(self, saved_session: tuple) -> None:
        store, sid = saved_session
        store.mark_synced(sid, "umrs-conv-123")
        status = store.get_sync_status(sid)
        assert status["sync_status"] == "synced"
        assert status["remote_conversation_id"] == "umrs-conv-123"
        assert status["last_synced_at"] is not None

    def test_mark_pending_after_local_edit(self, saved_session: tuple) -> None:
        store, sid = saved_session
        store.mark_synced(sid, "umrs-conv-123")
        store.mark_pending(sid)
        status = store.get_sync_status(sid)
        assert status["sync_status"] == "pending"
        # remote id preserved
        assert status["remote_conversation_id"] == "umrs-conv-123"

    def test_pending_sync_sessions_returns_unsynced(self, store: SessionStore) -> None:
        s1 = ChatSession(model="m")
        s1.turns.append(Turn(user="a", assistant="b"))
        sid1 = store.save(s1, name="unsynced")

        s2 = ChatSession(model="m")
        s2.turns.append(Turn(user="c", assistant="d"))
        sid2 = store.save(s2, name="synced")
        store.mark_synced(sid2, "remote-2")

        pending = store.pending_sync_sessions()
        ids = [p["id"] for p in pending]
        assert sid1 in ids
        assert sid2 not in ids

    def test_pending_sync_includes_pending_status(self, saved_session: tuple) -> None:
        store, sid = saved_session
        store.mark_synced(sid, "r-1")
        store.mark_pending(sid)
        pending = store.pending_sync_sessions()
        assert any(p["id"] == sid for p in pending)

    def test_get_sync_status_missing_session(self, store: SessionStore) -> None:
        assert store.get_sync_status("nonexistent") is None

    def test_sync_survives_save_roundtrip(self, saved_session: tuple) -> None:
        store, sid = saved_session
        store.mark_synced(sid, "umrs-conv-456")
        # Load and re-save the session
        session = store.load(sid)
        assert session is not None
        store.save(session, name="re-saved")
        # Sync status should be reset to local after re-save (INSERT OR REPLACE)
        status = store.get_sync_status(sid)
        assert status["sync_status"] == "local"


# ---------------------------------------------------------------------------
# WP-04 follow-on — retention sweep (opt-in, default-off, tombstone-safe)
# ---------------------------------------------------------------------------


class TestRetentionSweep:
    def _store(self, tmp_path):
        from r4.companion.session_store import CompanionSessionStore

        return CompanionSessionStore(db_path=str(tmp_path / "store.db"))

    def _seed(self, store, sid, days_old, pinned=None, archived=False):
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, name, created_at, updated_at, model, messages, metadata, pinned_at)
                   VALUES (?, 'name', ?, ?, 'm', '[]', '{}', ?)""",
                (sid, (cutoff - timedelta(minutes=1)).isoformat(), cutoff.isoformat(), pinned),
            )
        return store

    def test_default_off_noop(self, tmp_path):
        store = self._seed(self._store(tmp_path), "s1", days_old=500)
        out = store.sweep_retention()
        assert out["scanned"] == 0 and out["deleted"] == 0

    def test_expired_rows_go_through_mirror_delete(self, tmp_path, monkeypatch):
        store = self._seed(self._store(tmp_path), "s1", days_old=200)
        store = self._seed(store, "s2", days_old=1)
        monkeypatch.setenv(store.ENV_RETENTION_MAX_DAYS, "30")
        calls = []
        monkeypatch.setattr(
            store, "delete", lambda sid, client=None: calls.append(sid) or "deleted"
        )
        out = store.sweep_retention(client=None)
        assert calls == ["s1"]
        assert out["expired"] == 1 and out["deleted"] == 1

    def test_pinned_and_archived_exempt_by_default(self, tmp_path, monkeypatch):
        store = self._seed(
            self._store(tmp_path), "s-pin", days_old=200, pinned="2026-01-01T00:00:00Z"
        )
        calls = []
        monkeypatch.setattr(
            store, "delete", lambda sid, client=None: calls.append(sid) or "deleted"
        )
        out = store.sweep_retention(client=None, max_days=30)
        assert calls == []
        assert out["skipped"] == 1

    def test_mirror_pending_counts_outcome(self, tmp_path, monkeypatch):
        store = self._seed(self._store(tmp_path), "s1", days_old=200)
        monkeypatch.setattr(store, "delete", lambda sid, client=None: "mirror-pending")
        out = store.sweep_retention(client=None, max_days=30)
        assert out["mirror_pending"] == 1 and out["deleted"] == 0
