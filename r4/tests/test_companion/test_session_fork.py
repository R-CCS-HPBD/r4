"""Session fork (G-P9-2): duplicate a session row under a fresh id."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from r4.companion.session_store import CompanionSessionStore


def _store(tmp_path) -> CompanionSessionStore:
    return CompanionSessionStore(db_path=tmp_path / "sessions.db")


def _seed_session(store: CompanionSessionStore, session_id: str) -> None:
    session = MagicMock()
    session.session_id = session_id
    session.name = "original"
    session.created_at = "2026-09-06T10:00:00+00:00"
    session.updated_at = "2026-09-06T10:00:00+00:00"
    session.client = MagicMock()
    session.client.config = MagicMock(profile="default", gateway_url="", project_id="")
    message = MagicMock(role="user", content="hello", timestamp="t", metadata={})
    session.messages = [message]
    session.skills._active = {"pdf"}
    memory_item = MagicMock(key="k1", value="v1", created_at="t", source="user")
    session.context._memory = {"k1": memory_item}
    session.metadata = {"config": {}}
    session.agent.model = "m"
    session._sync_metadata_usage_state = lambda: None
    store.save(session)


class TestSessionFork:
    def test_fork_duplicates_row_with_fresh_id(self, tmp_path):
        store = _store(tmp_path)
        _seed_session(store, "sess-src")
        summary = store.fork("sess-src", "sess-fork")
        assert summary["forked_from"] == "sess-src"
        assert summary["messages"] == 1
        assert summary["name"] == "original (fork)"
        forked = store.load("sess-fork", client=MagicMock())
        assert forked is not None
        assert forked.messages[0].content == "hello"
        assert forked.metadata["forked_from"] == "sess-src"

    def test_fork_custom_name(self, tmp_path):
        store = _store(tmp_path)
        _seed_session(store, "sess-src")
        summary = store.fork("sess-src", "sess-f2", new_name="experiment B")
        assert summary["name"] == "experiment B"

    def test_fork_absent_source_returns_none(self, tmp_path):
        store = _store(tmp_path)
        assert store.fork("sess-missing", "sess-f3") is None

    def test_fork_does_not_touch_source(self, tmp_path):
        store = _store(tmp_path)
        _seed_session(store, "sess-src")
        store.fork("sess-src", "sess-f4")
        source = store.load("sess-src", client=MagicMock())
        assert source is not None
        assert "forked_from" not in json.loads(
            store._conn()
            .execute("SELECT metadata FROM sessions WHERE id = ?", ("sess-src",))
            .fetchone()[0]
        )


class TestForkFromTurn:
    """ADR-057 D-4: fork(from_turn) slices the copied prefix at the named
    turn; unknown ids raise the named fork.turn_not_found class (KeyError)."""

    def _seed_turns(self, tmp_path, session_id="turn-src"):
        store = _store(tmp_path)
        session = MagicMock()
        session.session_id = session_id
        session.name = "original"
        session.created_at = "2026-09-06T10:00:00+00:00"
        session.updated_at = "2026-09-06T10:00:00+00:00"
        session.client = MagicMock()
        session.client.config = MagicMock(profile="default", gateway_url="", project_id="")

        def _msg(role, content, tid=""):
            return MagicMock(
                role=role,
                content=content,
                timestamp="t",
                metadata={"turn_id": tid} if tid else {},
            )

        session.messages = [
            _msg("user", "m1", "t1"),
            _msg("assistant", "r1", "t1"),
            _msg("user", "m2", "t2"),
            _msg("assistant", "r2", "t2"),
            _msg("user", "m3", "t3"),
            _msg("assistant", "r3", "t3"),
        ]
        session.skills._active = {"pdf"}
        memory_item = MagicMock(key="k1", value="v1", created_at="t", source="user")
        session.context._memory = {"k1": memory_item}
        session.metadata = {"config": {}, "turn_seq": 3}
        session.agent.model = "m"
        session._sync_metadata_usage_state = lambda: None
        store.save(session)
        return store

    def test_fork_at_t2_keeps_first_two_turns(self, tmp_path):
        store = self._seed_turns(tmp_path)
        summary = store.fork("turn-src", "turn-fork", from_turn="t2")
        assert summary["messages"] == 4
        forked = store.load("turn-fork", client=MagicMock())
        contents = [m.content for m in forked.messages]
        assert contents == ["m1", "r1", "m2", "r2"]
        assert forked.metadata["forked_from_turn"] == "t2"
        # F6 (2026-09-21): the fork continues turn_seq from the last slice turn
        # (duplicates per forked session never possible — accessor uniqueness inside one room).
        assert forked.metadata["turn_seq"] == 2

    def test_fork_at_last_turn_keeps_everything(self, tmp_path):
        store = self._seed_turns(tmp_path)
        summary = store.fork("turn-src", "turn-fork-all", from_turn="t3")
        assert summary["messages"] == 6

    def test_fork_unknown_turn_raises_named_error(self, tmp_path):
        store = self._seed_turns(tmp_path)
        import pytest

        with pytest.raises(KeyError, match="t99"):
            store.fork("turn-src", "turn-fork-x", from_turn="t99")
