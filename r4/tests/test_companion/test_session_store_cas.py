"""Compare-and-swap session saves (cycle 3, T1).

T1: two bridges on one session (two consoles, or a console and ``r4 chat``)
each held a copy and saved it last-writer-wins: the second save erased the
first one's turns. A save is now compare-and-swap on the row's revision.
The losing writer gets ``SessionConflictError``; what it had not saved yet
is kept as its own session, never dropped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from unittest.mock import MagicMock

import pytest

from r4.companion.session_models import SessionMessage
from r4.companion.session_store import CompanionSessionStore, SessionConflictError


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


def _users(store: CompanionSessionStore, sid: str) -> list[str]:
    with sqlite3.connect(store.db_path) as conn:
        (raw,) = conn.execute("SELECT messages FROM sessions WHERE id = ?", (sid,)).fetchone()
    return [m["content"] for m in json.loads(raw) if m["role"] == "user"]


def _two_consoles(tmp_path):
    """One db, two stores (two bridge processes), both holding the session."""
    db = tmp_path / "sessions.db"
    first = CompanionSessionStore(db_path=db)
    session = first.create_session(_client())
    _say(session, "A1")
    first.save(session)
    second = CompanionSessionStore(db_path=db)
    return first, session, second, second.load(session.session_id, _client())


def test_the_second_writer_is_refused_and_the_first_turns_survive(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    _say(b, "B1")
    second.save(b)
    _say(a, "A2")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    assert _users(first, a.session_id) == ["A1", "B1"]  # B1 was erased before
    err = caught.value
    assert err.session_id == a.session_id
    assert "changed in another console" in str(err)
    assert f"/sessions resume {a.session_id}" in err.hint
    assert err.data["code"] == "session_conflict"


def test_the_refused_turn_is_kept_as_its_own_session(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    _say(b, "B1")
    second.save(b)
    _say(a, "A2")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    copy_id = caught.value.copy_id
    assert copy_id not in ("", a.session_id)
    assert copy_id in str(caught.value)
    kept = first.load(copy_id, _client())
    assert _users(first, copy_id) == ["A1", "A2"]
    assert kept.metadata["conflict_copy_of"] == a.session_id
    assert kept.name.endswith("(unsaved copy)")


def test_a_stale_copy_keeps_one_copy_across_failing_saves(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    _say(b, "B1")
    second.save(b)
    _say(a, "A2")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    copy_id = caught.value.copy_id
    _say(a, "A3")
    with pytest.raises(SessionConflictError) as again:
        first.save(a)
    assert again.value.copy_id == copy_id
    assert _users(first, copy_id) == ["A1", "A2", "A3"]


def test_a_stale_copy_with_nothing_new_makes_no_copy(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    _say(b, "B1")
    second.save(b)
    a.metadata["workflow_armed"] = True  # a metadata-only change
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    assert caught.value.copy_id == ""
    assert len(first.list_sessions(limit=10)) == 1


def test_opening_in_a_second_console_does_not_make_the_first_stale(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    second.save(b)  # the second bridge's init save: metadata only
    _say(a, "A2")
    first.save(a)
    assert _users(first, a.session_id) == ["A1", "A2"]


def test_ensure_current_refuses_before_a_turn(tmp_path):
    first, a, second, b = _two_consoles(tmp_path)
    first.ensure_current(a)  # nothing moved yet
    _say(b, "B1")
    second.save(b)
    with pytest.raises(SessionConflictError):
        first.ensure_current(a)
    reloaded = first.load(a.session_id, _client())
    first.ensure_current(reloaded)  # a reload is current again
    _say(reloaded, "A2")
    first.save(reloaded)
    assert _users(first, a.session_id) == ["A1", "B1", "A2"]


def test_saves_of_one_session_from_many_threads_never_conflict(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    session = store.create_session(_client())
    errors: list[BaseException] = []

    def _save(n: int) -> None:
        try:
            for i in range(5):
                session.metadata[f"t{n}"] = i
                store.save(session)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_save, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []


def test_a_server_adoption_makes_a_live_copy_stale(tmp_path):
    first, a, _second, _b = _two_consoles(tmp_path)
    payload = {
        "id": a.session_id,
        "name": a.name,
        "messages": json.dumps([{"role": "user", "content": "remote"}]),
        "metadata": "{}",
    }
    detail = {"session_id": a.session_id, "payload": json.dumps(payload), "updated_at": "z"}
    assert first.adopt_remote_detail(a.session_id, detail)
    _say(a, "A2")
    with pytest.raises(SessionConflictError):
        first.save(a)
    assert _users(first, a.session_id) == ["remote"]


def test_a_pre_revision_database_migrates_and_saves(tmp_path):
    db = tmp_path / "sessions.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL,
               created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
               messages TEXT NOT NULL DEFAULT '[]', active_skills TEXT NOT NULL DEFAULT '[]',
               memory TEXT NOT NULL DEFAULT '{}', metadata TEXT NOT NULL DEFAULT '{}',
               model TEXT NOT NULL DEFAULT '')"""
        )
        conn.execute(
            "INSERT INTO sessions (id, name, created_at, updated_at, messages)"
            " VALUES ('old', 'legacy', 't', 't', ?)",
            (json.dumps([{"role": "user", "content": "from before"}]),),
        )
    store = CompanionSessionStore(db_path=db)
    session = store.load("old", _client())
    assert session._store_revision == 0
    _say(session, "now")
    store.save(session)
    assert session._store_revision == 1
    assert _users(store, "old") == ["from before", "now"]


def _conflicted(tmp_path):
    """Tab 1's end-of-turn save lost to another console: (store, a, copy_id)."""
    first, a, second, b = _two_consoles(tmp_path)
    _say(b, "B1")
    second.save(b)
    _say(a, "A2")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    return first, a, caught.value.copy_id


def _continue_in_copy(first: CompanionSessionStore, copy_id: str, text: str) -> None:
    """The user follows the hint: resumes the copy in another tab, goes on there."""
    tab3 = CompanionSessionStore(db_path=first.db_path)
    copy = tab3.load(copy_id, _client())
    _say(copy, text)
    tab3.save(copy)


def test_a_later_stale_save_leaves_a_continued_copy_alone(tmp_path):
    # Cycle 3 review data-integrity#0: the stale tab's next save (a toggle,
    # the auto-title, the exit save) DELETEd the copy and re-inserted its
    # own stale messages, erasing C1.
    first, a, copy_id = _conflicted(tmp_path)
    _continue_in_copy(first, copy_id, "C1")
    first.rename_record(copy_id, "my branch")
    a.metadata["workflow_armed"] = True
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    assert caught.value.copy_id == copy_id
    assert _users(first, copy_id) == ["A1", "A2", "C1"]
    assert first.load(copy_id, _client()).name == "my branch"


def test_new_messages_on_a_stale_tab_never_overwrite_a_continued_copy(tmp_path):
    first, a, copy_id = _conflicted(tmp_path)
    _continue_in_copy(first, copy_id, "C1")
    _say(a, "A3")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    fresh = caught.value.copy_id
    assert fresh not in ("", copy_id, a.session_id)
    assert _users(first, copy_id) == ["A1", "A2", "C1"]
    assert _users(first, fresh) == ["A1", "A2", "A3"]


def test_an_untouched_copy_is_updated_in_place_and_keeps_its_name(tmp_path):
    first, a, copy_id = _conflicted(tmp_path)
    first.rename_record(copy_id, "my branch")  # a rename is not a conversation change
    _say(a, "A3")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    assert caught.value.copy_id == copy_id
    assert _users(first, copy_id) == ["A1", "A2", "A3"]
    assert first.load(copy_id, _client()).name == "my branch"


def test_an_untouched_copy_updated_in_place_stays_archived(tmp_path):
    # Cycle 3 verifier round 2 residual (3): an archive does not move the
    # revision, so the in-place update passed its compare-and-swap and wrote
    # this tab's metadata over the copy's, dropping the archive stamp.
    first, a, copy_id = _conflicted(tmp_path)
    assert first.archive(copy_id)
    _say(a, "A3")
    with pytest.raises(SessionConflictError) as caught:
        first.save(a)
    assert caught.value.copy_id == copy_id
    assert _users(first, copy_id) == ["A1", "A2", "A3"]
    assert first.get_session_record(copy_id)["metadata"].get("archived_at")


def test_the_conflict_hint_holds_on_every_console(tmp_path):
    # Cycle 3 review tui-runtime#1: "/sessions resume <id> reloads this tab"
    # is true only on the c3 TUI; an older one answers "already open in a
    # tab" and reloads nothing, so the hint names that way out too.
    first, a, copy_id = _conflicted(tmp_path)
    with pytest.raises(SessionConflictError) as caught:
        first.ensure_current(a)
    hint = caught.value.hint
    assert f"/sessions resume {a.session_id}" in hint
    assert "already open in a tab" in hint and "/tab close" in hint and "/tab new" in hint
    err = SessionConflictError(a.session_id, copy_id=copy_id, stage="after_turn")
    assert err.hint.index(f"/sessions resume {copy_id}") < err.hint.index("/tab close")
    assert err.data["stage"] == "after_turn" and "do not send it again" in str(err)
