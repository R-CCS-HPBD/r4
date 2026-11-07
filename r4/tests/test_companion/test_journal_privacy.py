"""The companion's local journal is owner-only and its audit rows redacted
(security lane F7, 2026-10-21).

``~/.config/r4/companion/sessions.db`` (and ``-wal`` / ``-shm``) holds raw
user messages and tool results; it was 0644 in a 0755 directory. The event
log stored tool arguments and results verbatim (a pasted JWT, the contents
of a config file), and a transcript kept a value under an ``api_key`` key
whenever the token patterns did not recognise it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat

import pytest

from r4.companion.event_log import EventLog
from r4.companion.session_store import CompanionSessionStore
from r4.companion.transcript import _clip

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJhbGljZSIsInByb2plY3RfaWQiOiJwMSJ9."
    "c2lnbmF0dXJlLXNpZ25hdHVyZS1zaWduYXR1cmU"
)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture(autouse=True)
def _wide_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_session_store_creates_an_owner_only_db_and_directory(tmp_path) -> None:
    db = tmp_path / "companion" / "sessions.db"
    CompanionSessionStore(db)
    assert _mode(db.parent) == 0o700
    assert _mode(db) == 0o600
    with sqlite3.connect(db) as conn:  # WAL side files take the db's mode
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        wal = db.with_name("sessions.db-wal")
        if wal.exists():
            assert _mode(wal) == 0o600


def test_an_existing_world_readable_journal_is_narrowed(tmp_path) -> None:
    directory = tmp_path / "companion"
    directory.mkdir(mode=0o755)
    db = directory / "sessions.db"
    sqlite3.connect(db).close()
    os.chmod(db, 0o644)
    CompanionSessionStore(db)
    assert _mode(directory) == 0o700
    assert _mode(db) == 0o600


def test_event_log_file_is_owner_only(tmp_path) -> None:
    db = tmp_path / "fresh" / "events.db"
    EventLog(str(db))
    assert _mode(db) == 0o600
    assert _mode(db.parent) == 0o700


def test_event_log_redacts_tokens_in_message_and_data(tmp_path) -> None:
    db = tmp_path / "events.db"
    log = EventLog(str(db))
    log.append(
        session_id="s",
        event_type="tool_result",
        message=f"read config: {_JWT}",
        data={"result": f'{{"api_key": "{_JWT}"}}', "nested": [{"arguments": _JWT}], "n": 3},
    )
    with sqlite3.connect(db) as conn:
        message, data = conn.execute("SELECT message, data FROM event_log").fetchone()
    assert _JWT not in message
    assert _JWT not in data
    assert json.loads(data)["n"] == 3


def test_transcript_redacts_values_under_credential_keys() -> None:
    clipped = _clip({"api_key": "opaque-key-123", "Token": "x9", "path": "a.txt"})
    assert "opaque-key-123" not in json.dumps(clipped)
    assert "x9" not in json.dumps(clipped)
    assert clipped["path"] == "a.txt"
