"""Snapshot LWW markers carry their UTC offset (live remote-session receipt 2026-09-25).

The journal orders snapshots by ``update_marker::timestamptz``, which reads a
naive value as UTC. A JST workstation's naive ``18:41`` then outranked the UTC
venue worker's ``09:41:50`` written ten seconds later, so every worker save
came back ``applied=False`` and the journal never showed the turn.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

from r4.companion.session import CompanionSession
from r4.companion.session_store import CompanionSessionStore


def _session() -> CompanionSession:
    return CompanionSession(
        session_id="marker-offset",
        name="Marker",
        client=MagicMock(),
        max_llm_calls_per_session=0,
    )


def test_created_marker_carries_an_offset():
    session = _session()
    assert datetime.fromisoformat(session.created_at).tzinfo is not None
    assert datetime.fromisoformat(session.updated_at).tzinfo is not None


def test_turn_start_bumps_the_marker_with_an_offset():
    session = _session()
    session.updated_at = "2000-01-01T00:00:00"
    session._start_turn("hello")
    bumped = datetime.fromisoformat(session.updated_at)
    assert bumped.tzinfo is not None
    assert bumped > datetime(2000, 1, 2, tzinfo=timezone.utc)


def test_a_session_keeps_the_offset_it_carries():
    """A venue worker runs in UTC but stamps the owner's wall clock, so every
    marker of one session shares an offset (and a stored naive marker from
    before 2026-09-25 is read in that offset by the journal)."""
    session = _session()
    session.updated_at = "2026-01-01T00:00:00-05:00"
    session._start_turn("hello")
    bumped = datetime.fromisoformat(session.updated_at)
    assert bumped.utcoffset() == timedelta(hours=-5)


def test_save_attaches_the_local_offset_to_a_naive_marker(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    session = _session()
    naive = "2026-09-25T18:41:40.123456"
    session.updated_at = naive
    store.save(session)
    upgraded = datetime.fromisoformat(session.updated_at)
    assert upgraded.tzinfo is not None
    assert upgraded == datetime.fromisoformat(naive).astimezone()


def test_offset_markers_order_by_instant_across_zones():
    jst = timezone(timedelta(hours=9))
    workstation = datetime(2026, 9, 25, 18, 41, 40, tzinfo=jst).isoformat()
    worker = datetime(2026, 9, 25, 9, 41, 50, tzinfo=timezone.utc).isoformat()
    assert CompanionSessionStore._ts_newer(worker, workstation)
    assert not CompanionSessionStore._ts_newer(workstation, worker)


def test_mixed_naive_and_offset_markers_compare_instead_of_raising():
    legacy = datetime(2026, 9, 25, 12, 0, 0).isoformat()
    later = (datetime(2026, 9, 25, 12, 0, 0).astimezone() + timedelta(seconds=5)).isoformat()
    assert CompanionSessionStore._ts_newer(later, legacy)
    assert not CompanionSessionStore._ts_newer(legacy, later)


# ── a refused PUT is not a mirror ───────────────────────────────────────────


def _store_replying(tmp_path, monkeypatch, reply):
    import r4.companion.session_store as session_store

    monkeypatch.setattr(session_store, "_SNAPSHOT_ENABLED", True)
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    client = MagicMock()
    posts: list = []

    def poster(_path, json=None, **_kw):
        posts.append(json)
        return reply

    store.set_mirror_poster(poster, client)
    client.post.side_effect = poster
    session = store.create_session(client, name="LWW")
    return store, client, session, posts


def _row_marker(store, sid: str) -> str:
    with store._conn() as conn:
        row = conn.execute("SELECT metadata FROM sessions WHERE id = ?", (sid,)).fetchone()
    return str(store._decode_json_object(row[0]).get("_mirror_marker") or "")


def test_applied_reply_records_the_sync_base(tmp_path, monkeypatch):
    store, _client, session, posts = _store_replying(
        tmp_path, monkeypatch, {"status": "ok", "applied": True}
    )
    store.save(session)
    assert posts
    assert store.sync_status()["mode"] == "mirrored"
    assert _row_marker(store, session.session_id) == session.updated_at


def test_refused_reply_is_degraded_and_keeps_no_sync_base(tmp_path, monkeypatch):
    """A journal that kept a newer row answers applied=false. Recording this
    save's marker as the base would let reconcile() adopt the older server
    copy over the local turn."""
    store, _client, session, _posts = _store_replying(
        tmp_path, monkeypatch, {"status": "ok", "applied": False}
    )
    store.save(session)
    status = store.sync_status()
    assert status["mode"] == "degraded"
    assert status["sync_errors"] >= 1
    assert _row_marker(store, session.session_id) == ""


def test_refused_reconcile_upload_counts_nothing(tmp_path, monkeypatch):
    store, client, session, _posts = _store_replying(
        tmp_path, monkeypatch, {"status": "ok", "applied": False}
    )
    with store._conn() as conn:
        assert store._mirror_row_up(conn, client, session.session_id) == 0
    assert _row_marker(store, session.session_id) == ""


# ── adoption, turn-finish markers, offset-keeping store stamps (review 2026-09-25) ──

JST_MARKER = "2026-09-25T18:00:00.000001+09:00"
# An offset no CI or maintainer host uses, so "keeps the session's offset"
# can never pass by coinciding with the host's own.
NPT_MARKER = "2026-09-24T10:00:00.000001+05:45"


def _journal_detail(sid: str, *, metadata: Any, messages: Any, marker: str = JST_MARKER) -> dict:
    import json

    return {
        "session_id": sid,
        "name": "Remote",
        "updated_at": marker,
        "venue": "frontend",
        "state": "ongoing",
        "payload": json.dumps(
            {
                "id": sid,
                "name": "Remote",
                "created_at": marker,
                "updated_at": marker,
                "messages": messages,
                "active_skills": "[]",
                "memory": "{}",
                "metadata": metadata,
                "model": "m",
            }
        ),
    }


def test_adopt_tolerates_object_metadata_from_the_server_queue(tmp_path):
    """The server's queue writes left payload.metadata as an object: adopting
    it raised TypeError, so the watcher, reconcile and pull all failed."""
    import json

    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    rows = json.dumps([{"role": "assistant", "content": "R", "timestamp": "", "metadata": {}}])
    detail = _journal_detail("obj-meta", metadata={"chat_queue": [], "turn_seq": 1}, messages=rows)
    assert store.adopt_remote_detail("obj-meta", detail)
    session = store.load("obj-meta", MagicMock())
    assert session is not None
    assert [m.content for m in session.messages] == ["R"]
    assert session.metadata["venue"] == "frontend"


def test_adopt_tolerates_object_shaped_rows_from_old_state_change_mirrors(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    rows = [{"role": "user", "content": "U", "timestamp": "", "metadata": {}}]
    detail = _journal_detail("obj-rows", metadata={"venue": "frontend"}, messages=rows)
    assert store.adopt_remote_detail("obj-rows", detail)
    assert [m.content for m in store.load("obj-rows", MagicMock()).messages] == ["U"]


def test_adoption_records_the_journal_marker_as_the_sync_base(tmp_path):
    """No base after an adoption read local == remote as a both-moved tie:
    reconcile re-uploaded the local copy (and a stale one applied on the tie)."""
    import json

    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    detail = _journal_detail("base", metadata=json.dumps({}), messages="[]")
    assert store.adopt_remote_detail("base", detail)
    assert _row_marker(store, "base") == JST_MARKER
    counters = {k: 0 for k in ("restored", "adopted", "reuploaded", "conflicts", "skipped")}
    counters["archives_adopted"] = 0
    snap = {"session_id": "base", "updated_at": JST_MARKER, "state": "ongoing"}
    with store._conn() as conn:
        store._reconcile_row(conn, MagicMock(), snap, store._local_row_markers(conn), counters)
    assert counters["skipped"] == 1
    assert counters["conflicts"] == counters["reuploaded"] == 0


def test_finished_turn_outranks_its_turn_start_copy():
    session = _session()
    session.updated_at = NPT_MARKER
    session._start_turn("q")
    started = session.updated_at
    session._finish_turn("q", "a")
    assert datetime.fromisoformat(session.updated_at) > datetime.fromisoformat(started)
    assert session.updated_at.endswith("+05:45")


def test_worker_post_turn_bump_outranks_the_turn_start_copy():
    from types import SimpleNamespace

    from r4.venue_worker import VenueWorker

    holder = SimpleNamespace(session=SimpleNamespace(updated_at=NPT_MARKER))
    VenueWorker._bump_marker(holder)
    assert datetime.fromisoformat(holder.session.updated_at) > datetime.fromisoformat(NPT_MARKER)
    assert holder.session.updated_at.endswith("+05:45")


def _jst_row(tmp_path, monkeypatch):
    store, client, session, posts = _store_replying(
        tmp_path, monkeypatch, {"status": "ok", "applied": True}
    )
    session.updated_at = NPT_MARKER
    store.save(session)
    return store, client, session, posts


def _stored_updated_at(store, sid: str) -> str:
    with store._conn() as conn:
        return conn.execute("SELECT updated_at FROM sessions WHERE id = ?", (sid,)).fetchone()[0]


def test_store_stamps_keep_the_sessions_offset(tmp_path, monkeypatch):
    """rename/set_state/archive/fork stamped UTC; _now_marker(like) then kept
    +00:00 for that session forever (wrong order and wall time on JST)."""
    store, _client, session, _posts = _jst_row(tmp_path, monkeypatch)
    sid = session.session_id
    assert store.rename_record(sid, "Renamed")
    assert _stored_updated_at(store, sid).endswith("+05:45")
    assert store.set_state(sid, "completed")
    assert _stored_updated_at(store, sid).endswith("+05:45")
    assert store.archive(sid)
    assert _stored_updated_at(store, sid).endswith("+05:45")
    fork = store.fork(sid, "fork-id")
    assert fork is not None
    assert _stored_updated_at(store, "fork-id").endswith("+05:45")


def test_state_change_mirror_posts_the_rows_own_marker_and_string_fields(tmp_path, monkeypatch):
    import json

    store, _client, session, posts = _jst_row(tmp_path, monkeypatch)
    sid = session.session_id
    posts.clear()
    assert store.set_state(sid, "completed")
    body = posts[-1]
    row_marker = _stored_updated_at(store, sid)
    assert body["updated_at"] == row_marker
    blob = json.loads(body["payload"])
    assert blob["updated_at"] == row_marker
    for key in ("messages", "active_skills", "memory", "metadata"):
        assert isinstance(blob[key], str), key
    assert _row_marker(store, sid) == row_marker


def test_cli_start_path_adopts_the_newer_journal_copy(tmp_path, monkeypatch):
    """`r4 session start` refused on every retry once the local copy trailed
    the journal; adopt_newer_journal hands it the journal's copy instead."""
    import json

    store, client, session, _posts = _store_replying(
        tmp_path, monkeypatch, {"status": "ok", "applied": False}
    )
    sid = session.session_id
    rows = json.dumps([{"role": "assistant", "content": "DONE", "timestamp": "", "metadata": {}}])
    client.get.return_value = _journal_detail(sid, metadata=json.dumps({}), messages=rows)
    store.save(session)
    assert store.sync_status()["journal_newer"] is True
    adopted = store.adopt_newer_journal(session, client)
    assert adopted is not None
    assert [m.content for m in adopted.messages] == ["DONE"]


def test_adopted_row_carries_the_journal_marker_so_its_resave_applies(tmp_path):
    """Review 2026-09-25: legacy state-change rows hold a journal marker newer
    than their payload's own updated_at. Adopting kept the payload's value, so
    every re-save of the adopted copy lost to the marker it was adopted at."""
    import json

    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    detail = _journal_detail("legacy", metadata=json.dumps({}), messages="[]")
    blob = json.loads(detail["payload"])
    blob["updated_at"] = "2026-09-25T17:00:00+09:00"  # older than the journal marker
    detail["payload"] = json.dumps(blob)
    assert store.adopt_remote_detail("legacy", detail)
    assert _stored_updated_at(store, "legacy") == JST_MARKER
    assert store.load("legacy", MagicMock()).updated_at == JST_MARKER


def test_cli_session_start_adopts_a_newer_journal_copy(tmp_path, monkeypatch):
    """`r4 session start` refused on every retry once the local copy trailed
    the journal (applied=false → degraded); it now adopts the journal copy."""
    import json
    from argparse import Namespace

    import r4._cli.sessions as cli
    import r4.companion.session_store as session_store

    monkeypatch.setattr(session_store, "_SNAPSHOT_ENABLED", True)
    store = CompanionSessionStore(db_path=tmp_path / "s.db")
    local = store.create_session(MagicMock(), name="Local")
    sid = local.session_id
    newer = "2999-01-01T00:00:00+05:45"
    rows = json.dumps([{"role": "assistant", "content": "DONE", "timestamp": "", "metadata": {}}])
    journal = {"detail": _journal_detail(sid, metadata=json.dumps({}), messages=rows, marker=newer)}

    def post(path, json=None, **_kw):
        if path.endswith("/snapshot"):
            stored = journal["detail"]["updated_at"]
            if datetime.fromisoformat(stored) <= datetime.fromisoformat(json["updated_at"]):
                journal["detail"] = {
                    **journal["detail"],
                    "updated_at": json["updated_at"],
                    "payload": json["payload"],
                }
                return {"status": "ok", "applied": True}
            return {"status": "ok", "applied": False}
        return {"state": "provisioning", "session_id": sid}

    client = MagicMock()
    client.post.side_effect = post
    client.get.side_effect = lambda path, **_kw: journal["detail"]
    store.set_mirror_poster(client.post, client)
    monkeypatch.setattr(cli, "_venue_client_and_store", lambda: (client, store))
    monkeypatch.setattr(
        cli, "_venue_url", lambda _c, s, tail: f"/umrs/companion/sessions/{s}/venue{tail}"
    )
    args = Namespace(
        session_id=sid,
        venue_sources="rikyu",
        venue_default_source=None,
        venue_web_policy="public",  # ADR-099: a remote start needs a choice
        as_json=True,
    )
    assert cli._handle_venue_start(args) == 0
    started = [c for c in client.post.call_args_list if c.args[0].endswith("/venue/start")]
    assert started, "the start POST went out after the adoption"
    adopted = store.load(sid, client)
    assert [m.content for m in adopted.messages] == ["DONE"]
    assert adopted.metadata["venue"] == "frontend"


def test_a_bump_outranks_a_marker_stamped_ahead_of_this_clock():
    """A skewed clock's marker ahead of now: the bump must still be newer."""
    from r4.companion.session import _now_marker

    ahead = "2999-01-01T00:00:00+05:45"
    bumped = _now_marker(ahead)
    assert datetime.fromisoformat(bumped) > datetime.fromisoformat(ahead)
    assert bumped.endswith("+05:45")
    naive_ahead = "2999-01-01T00:00:00"
    assert (
        datetime.fromisoformat(_now_marker(naive_ahead))
        > datetime.fromisoformat(naive_ahead).astimezone()
    )
