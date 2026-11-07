"""Typed corrupt session rows (cycle 3, L3).

A row whose JSON does not decode failed ``initialize`` with the bare decoder
text ("Expecting value: line 1 …"). It is now ``SessionCorruptError`` naming
the session, the column and the recovery; the row is never rewritten.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from r4.companion.session_models import SessionMessage
from r4.companion.session_store import CompanionSessionStore, SessionCorruptError


@pytest.fixture(autouse=True)
def _local_only(monkeypatch):
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")


def _client() -> MagicMock:
    client = MagicMock()
    client.config = MagicMock(profile="default", gateway_url="", project_id="")
    return client


def _say(session, text: str) -> None:
    session.messages.append(SessionMessage(role="user", content=text))
    session.messages.append(SessionMessage(role="assistant", content=f"re: {text}"))


def _corrupt(tmp_path, column: str, value: str) -> tuple[CompanionSessionStore, str]:
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    session = store.create_session(_client())
    _say(session, "hello")
    store.save(session)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"UPDATE sessions SET {column} = ? WHERE id = ?", (value, session.session_id))
    return store, session.session_id


@pytest.mark.parametrize(
    ("column", "value", "detail"),
    [
        ("messages", '[{"role": "user", "content": "hel', "not JSON"),
        ("messages", '{"a": 1}', "expected a JSON array"),
        ("metadata", "{{{", "not JSON"),
        ("metadata", "[]", "expected a JSON object"),
        ("memory", "nope", "not JSON"),
        ("active_skills", "nope", "not JSON"),
    ],
)
def test_a_corrupt_column_is_a_typed_error_naming_session_and_column(
    tmp_path, column, value, detail
):
    store, sid = _corrupt(tmp_path, column, value)
    with pytest.raises(SessionCorruptError) as caught:
        store.load(sid, _client())
    err = caught.value
    assert sid in str(err) and column in str(err) and detail in str(err)
    assert "Expecting" not in str(err).split("(", 1)[0]  # not the bare decoder text
    assert "/sessions new" in err.hint and f"/sessions export {sid}" in err.hint
    assert err.data == {"code": "session_corrupt", "session_id": sid, "column": column}
    assert store.raw_row(sid)[column] == value  # never rewritten


def test_a_corrupt_message_item_is_a_typed_error(tmp_path):
    store, sid = _corrupt(tmp_path, "messages", '[1, "x", null]')
    with pytest.raises(SessionCorruptError, match=sid):
        store.load(sid, _client())
