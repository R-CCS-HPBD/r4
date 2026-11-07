"""archive / set_state on a copy that trails the journal (review 2026-09-25).

A state change stamps a fresh marker and mirrors the whole row. When a remote
turn finished while nobody adopted it here, the local row trails the journal,
and that mirror overwrote the newer journal copy with the stale one. The store
now adopts the journal copy first when it is ahead, then applies the change.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import r4.companion.session_store as session_store
from r4.companion.session_store import CompanionSessionStore


class _Journal:
    """Snapshot journal with the server's `<=` rule and a detail GET."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.get_error: Exception | None = None

    def post(self, path: str, json: Any = None, **_kw: Any) -> dict[str, Any]:
        sid = path.rsplit("/", 2)[-2]
        stored = self.rows.get(sid)
        if stored and _instant(stored["updated_at"]) > _instant(json["updated_at"]):
            return {"status": "ok", "applied": False}
        self.rows[sid] = {**json, "session_id": sid}
        return {"status": "ok", "applied": True}

    def get(self, path: str, **_kw: Any) -> dict[str, Any]:
        if self.get_error is not None:
            raise self.get_error
        sid = path.rsplit("/", 1)[-1]
        row = self.rows.get(sid)
        return {**row, "venue": row.get("venue") or "frontend"} if row else {}

    def advance_remotely(self, sid: str, reply: str) -> None:
        """A remote turn lands in the journal only (nobody adopts it here)."""
        row = self.rows[sid]
        blob = json.loads(row["payload"])
        rows = json.loads(blob["messages"]) + [
            {"role": "assistant", "content": reply, "timestamp": "", "metadata": {}}
        ]
        newer = (_instant(row["updated_at"]) + timedelta(minutes=5)).isoformat()
        blob.update(messages=json.dumps(rows), updated_at=newer)
        self.rows[sid] = {**row, "payload": json.dumps(blob), "updated_at": newer}

    def assistant(self, sid: str) -> list[str]:
        blob = json.loads(self.rows[sid]["payload"])
        return [r["content"] for r in json.loads(blob["messages"]) if r["role"] == "assistant"]


def _instant(marker: str) -> datetime:
    return datetime.fromisoformat(marker.replace("Z", "+00:00")).astimezone()


def _store(tmp_path, monkeypatch) -> tuple[CompanionSessionStore, _Journal, str]:
    monkeypatch.setattr(session_store, "_SNAPSHOT_ENABLED", True)
    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    journal = _Journal()
    client = MagicMock()
    client.post.side_effect = journal.post
    client.get.side_effect = journal.get
    store.set_mirror_poster(journal.post, client)
    session = store.create_session(client, name="Trailing")
    return store, journal, session.session_id


def test_archive_on_a_trailing_copy_keeps_the_remote_turn(tmp_path, monkeypatch):
    store, journal, sid = _store(tmp_path, monkeypatch)
    journal.advance_remotely(sid, "REMOTE-DONE")
    assert store.archive(sid)
    assert journal.assistant(sid) == ["REMOTE-DONE"]
    record = store.get_session_record(sid)
    assert record["metadata"].get("archived_at")
    assert [m["content"] for m in record["messages"]] == ["REMOTE-DONE"]


def test_set_state_on_a_trailing_copy_keeps_the_remote_turn(tmp_path, monkeypatch):
    store, journal, sid = _store(tmp_path, monkeypatch)
    journal.advance_remotely(sid, "REMOTE-DONE")
    assert store.set_state(sid, "completed")
    assert journal.assistant(sid) == ["REMOTE-DONE"]
    record = store.get_session_record(sid)
    assert record["metadata"]["state"] == "completed"
    assert json.loads(json.loads(journal.rows[sid]["payload"])["metadata"])["state"] == "completed"


def test_state_change_with_an_unreachable_journal_proceeds_as_before(tmp_path, monkeypatch):
    store, journal, sid = _store(tmp_path, monkeypatch)
    journal.get_error = ConnectionError("journal down")
    assert store.set_state(sid, "completed")
    assert store.get_session_record(sid)["metadata"]["state"] == "completed"


def test_state_change_on_a_current_copy_adopts_nothing(tmp_path, monkeypatch):
    store, journal, sid = _store(tmp_path, monkeypatch)
    before = store.get_session_record(sid)["updated_at"]
    assert store.set_state(sid, "completed")
    record = store.get_session_record(sid)
    assert record["messages"] == []
    assert _instant(record["updated_at"]) > _instant(before)
