"""Bridge verbs for the frontend venue + sealed sources (wave 2026-09-21)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from r4.companion import session_store as store_module
from r4.companion.session_store import CompanionSessionStore
from r4.companion.ui.bridge_server import (
    CompanionBridgeServer,
    _lease_remaining_sec,
    _snapshot_moved,
    _venue_error_text,
)
from r4.errors import APIError


class _Config:
    profile = "default"
    gateway_url = "http://gw.example"
    project_id = "demo"
    model = "config-model"
    endpoint_url = None
    endpoint_provider = None
    endpoint_api_key = None
    auto_retry = False


class _FakeClient:
    """Captures POST/GET; scripted answers by URL suffix."""

    def __init__(self):
        self.config = _Config()
        self._config = _Config()
        self._api_prefix = "/umrs"
        self.posts: list[tuple[str, Any]] = []
        self.gets: list[str] = []
        self.post_answers: dict[str, Any] = {}
        self.get_answers: dict[str, Any] = {}
        self.readiness = MagicMock(return_value={})
        self.list_endpoints = MagicMock(return_value={"endpoints": []})

    def _answer(self, table: dict[str, Any], path: str) -> Any:
        for suffix, answer in table.items():
            if path.endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {"status": "ok", "applied": True}

    def post(self, path: str, json: Any = None, **_kw: Any) -> Any:
        self.posts.append((path, json))
        return self._answer(self.post_answers, path)

    def get(self, path: str, **_kw: Any) -> Any:
        self.gets.append(path)
        return self._answer(self.get_answers, path)


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompanionSessionStore:
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "on")
    store_module.set_snapshot_enabled(None)
    return CompanionSessionStore(db_path=tmp_path / "sessions.db")


def _server(tmp_path, monkeypatch, *, venue="frontend"):
    client = _FakeClient()
    store = _store(tmp_path, monkeypatch)
    server = CompanionBridgeServer(client=client, store=store, model="gpt-x")
    session = server._open_session(
        session_id=None, explicit_model="gpt-x", skill=None, system_prompt=None, venue=venue
    )
    server._session = session
    server._configure_session(session)
    store.set_mirror_poster(client.post, client)
    return server, client, store, session


def _running(session_id: str, **over) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "venue": "frontend",
        "state": "running",
        "reason": "",
        "egress_ip": "172.20.8.2",
        "source_names": ["openai-main"],
        "default_source": "openai-main",
        "web_policy": "public",
        "runtime": {"phase": "idle", "turn_seq": 0, "partial": ""},
        "lease": {"expires_at": "2999-01-01T00:00:00+00:00", "is_valid": True},
    }
    payload.update(over)
    return payload


class TestStart:
    def test_start_mirrors_then_posts_and_records_runtime(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch, venue="local")
        client.post_answers["/venue/start"] = _running(session.session_id)
        result = server.session_venue_start(
            {"source_refs": ["openai-main"], "web_policy": "public"}
        )
        # mirror (snapshot POST) landed before the start POST
        paths = [p for p, _ in client.posts]
        mirror_at = paths.index(f"/umrs/companion/sessions/{session.session_id}/snapshot")
        start_at = paths.index(f"/umrs/companion/sessions/{session.session_id}/venue/start")
        assert mirror_at < start_at
        start_body = client.posts[start_at][1]
        assert start_body["source_refs"] == ["openai-main"]
        assert start_body["project"] == "demo"
        assert result["runtime"]["state"] == "running"
        assert session.metadata["venue"] == "frontend"
        assert session.metadata["venue_runtime"]["lease_expires_at"].startswith("2999")
        assert session.metadata["state"] == "ongoing"

    def test_source_not_pushed_surfaces_typed_prefix(self, tmp_path, monkeypatch):
        server, client, _, _ = _server(tmp_path, monkeypatch)
        client.post_answers["/venue/start"] = APIError(
            "API error: 409 — sources not pushed: openai-main (run `r4 source push openai-main`)",
            status_code=409,
            error_code="companionvenue.source_not_pushed",
        )
        with pytest.raises(ValueError) as exc:
            server.session_venue_start({"source_refs": ["openai-main"], "web_policy": "public"})
        text = str(exc.value)
        assert text.startswith("venue.source_not_pushed: sources not pushed: openai-main")

    def test_start_requires_sources(self, tmp_path, monkeypatch):
        server, _, _, _ = _server(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="venue.bad_request"):
            server.session_venue_start({"source_refs": []})

    def test_degraded_mirror_refuses_start(self, tmp_path, monkeypatch):
        server, client, store, _ = _server(tmp_path, monkeypatch)
        store._last_sync = {"mode": "degraded", "error": "journal 502"}
        client.post = MagicMock(side_effect=RuntimeError("journal 502"))  # mirror fails
        store.set_mirror_poster(client.post, client)  # the registered poster is what save() uses
        with pytest.raises(ValueError, match="venue.mirror_failed"):
            server.session_venue_start({"source_refs": ["openai-main"], "web_policy": "public"})


class TestSendRemote:
    def test_frontend_session_queues_instead_of_running(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch)
        session.metadata["venue_runtime"] = {"state": "running"}
        client.post_answers["/queue"] = {
            "applied": True,
            "queue": [{"id": "q-1", "content": "hi"}],
            "front": None,
        }
        result = server.send_message({"text": "hi", "client_id": "tui-a"})
        assert result["remote"] is True and result["queued"] is True
        queue_post = next(b for p, b in client.posts if p.endswith("/queue"))
        assert queue_post == {"action": "append", "content": "hi", "client_id": "tui-a"}

    def test_frontend_session_without_runtime_refuses(self, tmp_path, monkeypatch):
        server, _, _, session = _server(tmp_path, monkeypatch)
        session.metadata["venue_runtime"] = {"state": "stopped"}
        with pytest.raises(ValueError, match="venue.not_running"):
            server.send_message({"text": "hi"})

    def test_queue_not_applied_refuses(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch)
        session.metadata["venue_runtime"] = {"state": "running"}
        client.post_answers["/queue"] = {"applied": False, "queue": []}
        with pytest.raises(ValueError, match="venue.queue_failed"):
            server.send_message({"text": "hi"})


class TestStatusStopSync:
    def test_status_and_lease_remaining(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch)
        client.get_answers["/venue"] = _running(session.session_id)
        result = server.session_venue_status({})
        assert result["venue"]["state"] == "running"
        assert result["lease_remaining_sec"] > 0
        assert session.metadata["venue_runtime"]["state"] == "running"

    def test_stop_projects_stopped(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch)
        client.post_answers["/venue/stop"] = _running(session.session_id, state="stopped")
        result = server.session_venue_stop({})
        assert result["venue"]["state"] == "stopped"
        assert session.metadata["state"] == "stopped"

    def test_sync_adopts_server_row_when_it_moved(self, tmp_path, monkeypatch):
        server, client, store, session = _server(tmp_path, monkeypatch)
        sid = session.session_id
        remote_payload = {
            "id": sid,
            "name": "S",
            "created_at": session.created_at,
            "updated_at": "2026-09-21T09:00:00",
            "messages": json.dumps(
                [
                    {
                        "role": "user",
                        "content": "hi",
                        "timestamp": "t1",
                        "metadata": {"turn_id": "t1"},
                    },
                    {
                        "role": "assistant",
                        "content": "hello",
                        "timestamp": "t2",
                        "metadata": {"turn_id": "t1"},
                    },
                ]
            ),
            "active_skills": "[]",
            "memory": "{}",
            "metadata": json.dumps({"venue": "frontend", "turn_seq": 1}),
            "model": "gpt-x",
        }
        client.get_answers["/venue"] = _running(
            sid, runtime={"phase": "idle", "turn_seq": 1, "partial": ""}
        )
        client.get_answers[f"/companion/sessions/{sid}"] = {
            "session_id": sid,
            "name": "S",
            "payload": json.dumps(remote_payload),
            "updated_at": "2026-09-21T09:00:00",
            "venue": "frontend",
            "state": "ongoing",
            "archived_at": "",
        }
        result = server.session_venue_sync({})
        assert result["adopted"] is True
        assert result["turn_seq"] == 1
        assert [m["role"] for m in result["state"]["messages"]] == ["user", "assistant"]
        assert result["runtime"]["phase"] == "idle"
        assert server.session.messages[-1].content == "hello"
        # second sync with nothing new: no adoption
        again = server.session_venue_sync({})
        assert again["adopted"] is False

    def test_sync_error_is_typed(self, tmp_path, monkeypatch):
        server, client, _, _ = _server(tmp_path, monkeypatch)
        client.get_answers["/venue"] = APIError(
            "API error: 404 — no venue runtime",
            status_code=404,
            error_code="companionvenue.not_found",
        )
        with pytest.raises(ValueError, match="venue.not_found"):
            server.session_venue_sync({})


class TestApprovalsVerbs:
    def test_list_and_decide(self, tmp_path, monkeypatch):
        server, client, _, session = _server(tmp_path, monkeypatch)
        client.get_answers["/approvals"] = {
            "approvals": [{"approval_id": "appr-1", "status": "pending"}],
            "count": 1,
        }
        assert server.session_venue_approvals({})["count"] == 1
        client.post_answers["/decision"] = {
            "approval": {"approval_id": "appr-1", "status": "approved"}
        }
        result = server.session_venue_decide({"approval_id": "appr-1", "decision": "once"})
        assert result["approval"]["status"] == "approved"
        with pytest.raises(ValueError, match="venue.bad_request"):
            server.session_venue_decide({"approval_id": "appr-1", "decision": "maybe"})


class TestSources:
    def test_list_remote_rows(self, tmp_path, monkeypatch):
        from r4.source import Source, SourceStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        SourceStore().add(
            Source(name="openai-main", kind="public", url="https://api.example", api_key="sk-1")
        )
        SourceStore().add(Source(name="rccs", kind="platform", url="https://gw.example"))
        server, _, _, _ = _server(tmp_path, monkeypatch)
        from r4._cli.source import _key_fingerprint

        us = MagicMock()
        us.list_mine.return_value = [
            {"name": "openai-main", "key_fingerprint": _key_fingerprint("sk-1")}
        ]
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            result = server.source_list_remote({})
        rows = {r["name"]: r for r in result["rows"]}
        assert result["remote_available"] is True
        assert rows["openai-main"]["remote"] == "pushed" and rows["openai-main"]["pushed"] is True
        assert rows["openai-main"]["eligible"] is True
        assert rows["rccs"]["remote"] == "-" and rows["rccs"]["eligible"] is False
        assert "api_key" not in rows["openai-main"]

    def test_push_refusal_and_success(self, tmp_path, monkeypatch):
        from r4.source import Source, SourceStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        SourceStore().add(Source(name="nokey", kind="public", url="https://api.example"))
        SourceStore().add(
            Source(name="openai-main", kind="public", url="https://api.example", api_key="sk-1")
        )
        server, _, _, _ = _server(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="source.push_refused"):
            server.source_push({"name": "nokey"})
        us = MagicMock()
        us.push.return_value = {
            "allow_host": "https://api.example",
            "key_fingerprint": "sha256:abcd1234",
            "replaced": False,
        }
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            result = server.source_push({"name": "openai-main"})
        assert result == {
            "ok": True,
            "name": "openai-main",
            "allow_host": "https://api.example",
            "key_fingerprint": "sha256:abcd1234",
            "replaced": False,
        }
        assert us.push.call_args.kwargs["api_key"] == "sk-1"
        us.unpush.return_value = {"leases_revoked": 2}
        with patch("r4.user_sources.UserSourcesClient", return_value=us):
            assert server.source_unpush({"name": "openai-main"})["leases_revoked"] == 2


class TestHelpers:
    def test_queue_meta_accepts_server_dict_rows(self, tmp_path, monkeypatch):
        server, _, _, session = _server(tmp_path, monkeypatch)
        session.metadata["chat_queue"] = [
            "plain",
            {"id": "q-2", "content": "from server"},
            {"id": "q-3"},
            7,
        ]
        assert server._queue_meta() == ["plain", "from server"]

    def test_venue_error_text_prefers_header_code(self):
        exc = APIError(
            "API error: 503 — not configured",
            status_code=503,
            error_code="companionvenue.unavailable",
        )
        assert _venue_error_text(exc) == "venue.unavailable: not configured"
        plain = APIError("API error: 409 — busy", status_code=409)
        assert _venue_error_text(plain) == "venue.conflict: busy"

    def test_lease_remaining(self):
        assert _lease_remaining_sec({"lease": {"expires_at": "2999-01-01T00:00:00+00:00"}}) > 0
        assert _lease_remaining_sec({"lease": {"expires_at": "2000-01-01T00:00:00Z"}}) == 0
        assert _lease_remaining_sec({}) is None

    def test_snapshot_moved(self):
        session = MagicMock(session_id="s1", messages=[1, 2], updated_at="a")
        detail = {
            "session_id": "s1",
            "payload": json.dumps({"messages": [1, 2], "updated_at": "a"}),
        }
        assert _snapshot_moved(session, detail) is False
        detail = {
            "session_id": "s1",
            "payload": json.dumps({"messages": [1, 2, 3], "updated_at": "a"}),
        }
        assert _snapshot_moved(session, detail) is True
        assert _snapshot_moved(session, {"session_id": "other", "payload": "{}"}) is False


def test_save_mirrors_through_the_registered_poster_not_the_client(tmp_path, monkeypatch):
    """The venue worker registers the rail's put_snapshot as the mirror poster;
    save() must use it. Live 2026-09-25: save() used the sandbox client's own
    post(), the rail refused that gateway path (403) and the assistant reply
    never reached the journal although the turn completed."""
    _, client, store, session = _server(tmp_path, monkeypatch)
    rail_posts = []
    store.set_mirror_poster(lambda path, json=None, **kw: rail_posts.append(json) or {}, client)
    client.posts.clear()
    from r4.companion.session_models import SessionMessage

    session.messages.append(SessionMessage(role="assistant", content="REMOTE-OK"))
    store.save(session)
    assert rail_posts, "the registered (rail) poster received the snapshot"
    assert "REMOTE-OK" in rail_posts[-1]["payload"]
    assert not [p for p, _ in client.posts if p.endswith("/snapshot")]


# ── a copy older than the journal: adopt, never refuse (review 2026-09-25) ──


def _marker_instant(marker: str):
    from datetime import datetime

    return datetime.fromisoformat(marker.replace("Z", "+00:00")).astimezone()


def _lww_journal(client: _FakeClient, session) -> dict[str, Any]:
    """A journal that already holds a newer copy (the remote turn finished while
    nobody watched it here); snapshot PUTs follow the server's `<=` rule."""
    from datetime import timedelta

    sid = session.session_id
    newer = (_marker_instant(session.updated_at) + timedelta(minutes=5)).isoformat()
    rows = [
        {"role": "user", "content": "long task", "timestamp": "", "metadata": {}},
        {"role": "assistant", "content": "REMOTE-DONE", "timestamp": "", "metadata": {}},
    ]
    journal = {
        "marker": newer,
        "payload": json.dumps(
            {
                "id": sid,
                "name": session.name,
                "created_at": session.created_at,
                "updated_at": newer,
                "messages": json.dumps(rows),
                "active_skills": "[]",
                "memory": "{}",
                "metadata": json.dumps({"venue": "frontend", "turn_seq": 1}),
                "model": "gpt-x",
            }
        ),
    }
    plain_post, plain_get = client.post, client.get

    def post(path: str, json: Any = None, **kw: Any) -> Any:
        if not path.endswith("/snapshot"):
            return plain_post(path, json=json, **kw)
        client.posts.append((path, json))
        if _marker_instant(journal["marker"]) <= _marker_instant(json["updated_at"]):
            journal.update(marker=json["updated_at"], payload=json["payload"])
            return {"status": "ok", "applied": True}
        return {"status": "ok", "applied": False}

    def get(path: str, **kw: Any) -> Any:
        if not path.endswith(f"/companion/sessions/{sid}"):
            return plain_get(path, **kw)
        client.gets.append(path)
        return {
            "session_id": sid,
            "name": session.name,
            "payload": journal["payload"],
            "updated_at": journal["marker"],
            "venue": "frontend",
            "state": "ongoing",
        }

    client.post, client.get = post, get
    return journal


def _journal_assistant(journal: dict[str, Any]) -> str:
    rows = json.loads(json.loads(journal["payload"])["messages"])
    return next((r["content"] for r in reversed(rows) if r["role"] == "assistant"), "")


def test_start_adopts_a_newer_journal_copy_instead_of_refusing(tmp_path, monkeypatch):
    """Before: the stale local save came back applied=false → degraded →
    venue.mirror_failed on every retry, with no way to adopt from the TUI."""
    server, client, store, session = _server(tmp_path, monkeypatch)
    journal = _lww_journal(client, session)
    store.set_mirror_poster(client.post, client)
    client.post_answers["/venue/start"] = _running(session.session_id)
    result = server.session_venue_start({"source_refs": ["openai-main"], "web_policy": "public"})
    assert result["runtime"]["state"] == "running"
    assert result["adopted"] is True
    assert _last_content(result["state"]["messages"]) == "REMOTE-DONE"
    live = server.session
    assert live.messages[-1].content == "REMOTE-DONE"
    assert live.metadata["venue"] == "frontend"
    assert _journal_assistant(journal) == "REMOTE-DONE"
    assert store.sync_status()["mode"] == "mirrored"


def _last_content(rows: list[dict[str, Any]]) -> str:
    return str(rows[-1]["content"]) if rows else ""


def test_stop_adopts_the_newer_journal_copy_and_never_overwrites_it(tmp_path, monkeypatch):
    server, client, store, session = _server(tmp_path, monkeypatch)
    journal = _lww_journal(client, session)
    store.set_mirror_poster(client.post, client)
    client.post_answers["/venue/stop"] = _running(session.session_id, state="stopped")
    server.session_venue_stop({})
    assert [m.content for m in server.session.messages][-1] == "REMOTE-DONE"
    assert server.session.metadata["venue_runtime"]["state"] == "stopped"
    assert _journal_assistant(journal) == "REMOTE-DONE"


def test_stop_survives_an_unreachable_journal_read_after_the_refusal(tmp_path, monkeypatch):
    """The stop POST already succeeded: a timeout on the best-effort journal
    read must not turn it into an error (the TUI would keep polling)."""
    from r4.errors import R4TimeoutError

    server, client, store, session = _server(tmp_path, monkeypatch)
    _lww_journal(client, session)
    store.set_mirror_poster(client.post, client)
    sid = session.session_id

    def get(path: str, **_kw: Any) -> Any:
        if path.endswith(f"/companion/sessions/{sid}"):
            raise R4TimeoutError("Request timed out")
        return {}

    client.get = get
    client.post_answers["/venue/stop"] = _running(sid, state="stopped")
    result = server.session_venue_stop({})
    assert result["adopted"] is False
    assert server.session.metadata["venue_runtime"]["state"] == "stopped"
