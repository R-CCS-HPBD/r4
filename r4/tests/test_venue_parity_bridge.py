"""ADR-099 D-8 (M-6) on the local bridge and the CLI.

- the bridge computes its agent settings with the shared rule, sends them
  before a remote start and on every change while the session is remote;
- ``ask_user_mode=auto`` auto-answers locally too, with the remote's text;
- remote questions are listed and answered through two bridge verbs and
  ``r4 session questions|answer``; an "always" decided remotely reaches the
  settings so a restarted worker keeps it.
"""

from __future__ import annotations

import json
import threading
from argparse import Namespace
from typing import Any

import pytest
from test_bridge_venue_verbs import _FakeClient, _running, _store

from r4.companion.agent_settings import (
    ASK_USER_AUTO_ANSWER,
    apply_agent_settings,
    effective_agent_settings,
)
from r4.companion.permissions import PermissionMode
from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.errors import APIError


class _PutClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self.puts: list[tuple[str, Any]] = []
        self.put_error: Exception | None = None

    def put(self, path: str, json: Any = None, **_kw: Any) -> Any:
        if self.put_error is not None:
            raise self.put_error
        self.puts.append((path, json))
        return {"session_id": "x", "rev": len(self.puts)}


def _server(tmp_path, monkeypatch, *, venue="frontend", **server_kw):
    client = _PutClient()
    store = _store(tmp_path, monkeypatch)
    server = CompanionBridgeServer(client=client, store=store, model="gpt-x", **server_kw)
    session = server._open_session(
        session_id=None, explicit_model="gpt-x", skill=None, system_prompt=None, venue=venue
    )
    server._session = session
    server._configure_session(session)
    store.set_mirror_poster(client.post, client)
    return server, client, session


def _settings_puts(client: _PutClient, sid: str) -> list[dict]:
    url = f"/umrs/companion/sessions/{sid}/venue/settings"
    return [body for path, body in client.puts if path == url]


class TestSharedRule:
    def test_bridge_default_is_auto_safe_and_settings_say_so(self, tmp_path, monkeypatch):
        server, _, session = _server(tmp_path, monkeypatch, venue="local")
        assert session.permissions.mode == PermissionMode.AUTO_SAFE
        doc = server._agent_settings(session)
        assert doc["permission_mode"] == "auto_safe"
        assert doc["interaction_mode"] == "chat" and doc["ask_user_mode"] == "ask"

    def test_launch_autopilot_trusts(self, tmp_path, monkeypatch):
        _, _, session = _server(tmp_path, monkeypatch, venue="local", interaction_mode="autopilot")
        assert session.permissions.mode == PermissionMode.TRUST

    def test_applying_the_sessions_own_settings_leaves_metadata_alone(self, tmp_path, monkeypatch):
        _, _, session = _server(tmp_path, monkeypatch, venue="local")
        before = dict(session.metadata)
        apply_agent_settings(session, effective_agent_settings(session))
        assert session.metadata == before

    def test_leaving_autopilot_returns_to_the_bridge_default(self, tmp_path, monkeypatch):
        server, _, session = _server(tmp_path, monkeypatch, venue="local")
        server.set_interaction_mode({"mode": "autopilot"})
        assert session.permissions.mode == PermissionMode.TRUST
        server.set_interaction_mode({"mode": "chat"})
        assert session.permissions.mode == PermissionMode.AUTO_SAFE


class TestInteractionModeSwitch:
    """A9 (2026-10-23, kept and pinned): a mid-session switch to chat or plan
    now leaves the session in the mode a resume of it gives (the bridge's
    session-open rule raises ``ask_always`` to ``auto_safe``). Before lane A
    the switch applied the old Textual default ``ask_always``, which the next
    resume of the same session silently undid; under M-6 the remote worker
    applies the one session-open rule, so the local switch follows it too.
    ADR-104: there is no separate permission override any more."""

    @pytest.mark.parametrize("mode", ["plan", "chat"])
    def test_switch_lands_where_a_resume_lands(self, tmp_path, monkeypatch, mode):
        server, _, session = _server(tmp_path, monkeypatch, venue="local")
        server.set_interaction_mode({"mode": "autopilot"})
        server.set_interaction_mode({"mode": mode})
        switched = session.permissions.mode
        server._configure_session(session)  # what a resume applies
        assert switched == session.permissions.mode == PermissionMode.AUTO_SAFE

    def test_a_legacy_override_is_folded_into_the_mode(self, tmp_path, monkeypatch):
        """ADR-104 D-1: a saved ``permission_override`` is read once (trust →
        autopilot, anything else dropped) and never pins a later switch."""
        server, _, session = _server(tmp_path, monkeypatch, venue="local")
        session.metadata["permission_override"] = "ask_always"
        server.set_interaction_mode({"mode": "plan"})
        assert session.permissions.mode == PermissionMode.AUTO_SAFE
        assert "permission_override" not in session.metadata
        assert server._agent_settings(session)["permission_mode"] == "auto_safe"


class TestPush:
    def test_start_sends_the_settings_before_the_start(self, tmp_path, monkeypatch):
        server, client, session = _server(tmp_path, monkeypatch, venue="local")
        session.metadata["workflow_armed"] = True
        client.post_answers["/venue/start"] = _running(session.session_id)
        result = server.session_venue_start(
            {"source_refs": ["openai-main"], "web_policy": "public"}
        )
        sent = _settings_puts(client, session.session_id)
        assert len(sent) == 1
        assert sent[0]["permission_mode"] == "auto_safe"
        assert sent[0]["workflow_armed"] is True
        assert result["settings_error"] == ""

    def test_a_failed_push_is_named_and_does_not_block_the_start(self, tmp_path, monkeypatch):
        server, client, session = _server(tmp_path, monkeypatch, venue="local")
        client.put_error = APIError("API error: 404", status_code=404)
        client.post_answers["/venue/start"] = _running(session.session_id)
        result = server.session_venue_start(
            {"source_refs": ["openai-main"], "web_policy": "public"}
        )
        assert "404" in result["settings_error"]
        assert result["runtime"]["state"] == "running"

    def test_changes_reach_a_remote_session(self, tmp_path, monkeypatch):
        server, client, session = _server(tmp_path, monkeypatch, venue="frontend")
        server.set_interaction_mode({"mode": "autopilot"})
        server.set_interaction_mode({"mode": "plan"})
        server.set_write_requires_plan({"enabled": True})
        server.set_workflow_armed({"armed": True})
        sent = _settings_puts(client, session.session_id)
        assert [doc["permission_mode"] for doc in sent] == ["trust"] + ["auto_safe"] * 3
        assert sent[1]["interaction_mode"] == "plan"
        assert sent[2]["write_requires_plan"] is True
        assert sent[3]["workflow_armed"] is True

    def test_local_session_changes_send_nothing(self, tmp_path, monkeypatch):
        server, client, _ = _server(tmp_path, monkeypatch, venue="local")
        server.set_interaction_mode({"mode": "autopilot"})
        assert client.puts == []

    def test_remote_always_reaches_the_settings(self, tmp_path, monkeypatch):
        server, client, session = _server(tmp_path, monkeypatch, venue="frontend")
        client.post_answers["/decision"] = {
            "approval": {"approval_id": "a1", "action": {"tool": "shell_exec"}}
        }
        server.session_venue_decide({"approval_id": "a1", "decision": "always"})
        assert "shell_exec" in session.permissions.session_approved_names
        assert _settings_puts(client, session.session_id)[-1]["session_approved_tools"] == [
            "shell_exec"
        ]

    def test_remote_always_on_a_fenced_card_grants_nothing(self, tmp_path, monkeypatch):
        # Fix 2026-10-30: an "always" on a fenced remote card approves that
        # one call; no local session grant, no settings push to the worker.
        server, client, session = _server(tmp_path, monkeypatch, venue="frontend")
        before = len(_settings_puts(client, session.session_id))
        client.post_answers["/decision"] = {
            "approval": {
                "approval_id": "a1",
                "action": {"tool": "shell_exec", "fenced": True, "fence_reason": "taint"},
            }
        }
        server.session_venue_decide({"approval_id": "a1", "decision": "always"})
        assert not session.permissions.session_approved_names
        assert len(_settings_puts(client, session.session_id)) == before


class TestAskUserLocal:
    def test_auto_mode_answers_without_asking(self, tmp_path, monkeypatch):
        server, client, _ = _server(tmp_path, monkeypatch, venue="local")
        client.config.ask_user_mode = "auto"
        emitted: list[dict] = []
        server._emit_line = emitted.append
        assert server._on_ask_user("which?", ["a"]) == ASK_USER_AUTO_ANSWER
        assert emitted == []

    def test_env_auto_mode(self, tmp_path, monkeypatch):
        server, _, _ = _server(tmp_path, monkeypatch, venue="local")
        monkeypatch.setenv("R4_ASK_USER_MODE", "auto")
        assert server._on_ask_user("which?", []) == ASK_USER_AUTO_ANSWER

    def test_autopilot_auto_answers_even_when_the_setting_is_ask(self, tmp_path, monkeypatch):
        server, _, session = _server(tmp_path, monkeypatch, venue="local")
        server.set_interaction_mode({"mode": "autopilot"})
        emitted: list[dict] = []
        server._emit_line = emitted.append
        server._read_ask_user = lambda _rid: pytest.fail("autopilot asked the user")
        assert session.permissions.mode is PermissionMode.TRUST
        assert server._on_ask_user("which?", ["A", "B"]) == ASK_USER_AUTO_ANSWER
        assert emitted == []

    def test_ask_mode_asks(self, tmp_path, monkeypatch):
        server, _, _ = _server(tmp_path, monkeypatch, venue="local")
        emitted: list[dict] = []
        server._emit_line = emitted.append
        server._read_ask_user = lambda _rid: "B"
        assert server._on_ask_user("which?", ["A", "B"]) == "B"
        assert emitted[0]["ask_user_request"]["question"] == "which?"


class TestQuestionVerbs:
    def test_list_and_answer(self, tmp_path, monkeypatch):
        server, client, session = _server(tmp_path, monkeypatch)
        sid = session.session_id
        client.get_answers["/questions"] = {
            "questions": [{"question_id": "q-1", "question": "Which?", "options": ["a"]}]
        }
        listed = server.session_venue_questions({})
        assert listed["questions"][0]["question_id"] == "q-1"
        assert client.gets[-1] == f"/umrs/companion/sessions/{sid}/questions"
        client.post_answers["/answer"] = {"question": {"question_id": "q-1", "status": "answered"}}
        out = server.session_venue_answer({"question_id": "q-1", "answer": "a"})
        assert out["question"]["status"] == "answered"
        assert client.posts[-1] == (
            f"/umrs/companion/sessions/{sid}/questions/q-1/answer",
            {"answer": "a"},
        )

    def test_closed_question_is_typed(self, tmp_path, monkeypatch):
        server, client, _ = _server(tmp_path, monkeypatch)
        client.post_answers["/answer"] = APIError(
            "API error: 409 — question q-1 is answered",
            status_code=409,
            error_code="companionvenue.question_closed",
        )
        with pytest.raises(ValueError, match="^venue.question_closed"):
            server.session_venue_answer({"question_id": "q-1", "answer": "a"})

    def test_answer_needs_text(self, tmp_path, monkeypatch):
        server, _, _ = _server(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="venue.bad_request"):
            server.session_venue_answer({"question_id": "q-1", "answer": "  "})


class TestCli:
    def _args(self, sub: str, sid: str = "sess-1", rest=(), as_json=False) -> Namespace:
        return Namespace(
            session_subcommand=sub, session_id=sid, answer_args=list(rest), as_json=as_json
        )

    def test_answer_posts_the_joined_text(self, monkeypatch, capsys):
        from r4._cli import sessions

        client = _PutClient()
        client.post_answers["/answer"] = {"question": {"question_id": "q-1"}}
        monkeypatch.setattr(sessions, "_venue_client_and_store", lambda: (client, None))
        assert sessions._handle_venue_answer(self._args("answer", rest=["q-1", "use", "B"])) == 0
        assert client.posts == [
            ("/umrs/companion/sessions/sess-1/questions/q-1/answer", {"answer": "use B"})
        ]
        assert "answered q-1" in capsys.readouterr().out

    def test_answer_usage(self, capsys):
        from r4._cli import sessions

        assert sessions._handle_venue_answer(self._args("answer", rest=["q-1"])) == 2
        assert "usage" in capsys.readouterr().err

    def test_questions_lists(self, monkeypatch, capsys):
        from r4._cli import sessions

        client = _PutClient()
        client.get_answers["/questions"] = {
            "questions": [{"question_id": "q-1", "question": "Which?", "options": ["a", "b"]}]
        }
        monkeypatch.setattr(sessions, "_venue_client_and_store", lambda: (client, None))
        assert sessions._handle_venue_questions(self._args("questions")) == 0
        out = capsys.readouterr().out
        assert "q-1" in out and "Which?" in out and "a | b" in out

    def test_start_pushes_settings(self, tmp_path, monkeypatch, capsys):
        from r4._cli import sessions

        client = _PutClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client, model="gpt-x")
        session.metadata["interaction_mode"] = "autopilot"
        store.save(session)
        client.post_answers["/venue/start"] = _running(session.session_id)
        monkeypatch.setattr(sessions, "_venue_client_and_store", lambda: (client, store))
        args = Namespace(
            session_subcommand="start",
            session_id=session.session_id,
            venue_sources="openai-main",
            venue_default_source=None,
            venue_web_policy="none",
            as_json=True,
        )
        assert sessions._handle_venue_start(args) == 0
        sent = _settings_puts(client, session.session_id)
        assert sent and sent[0]["permission_mode"] == "trust"
        paths = [p for p, _ in client.posts]
        assert paths[-1].endswith("/venue/start")


class TestAttachPush:
    """RS-1 round-2 finding 3 (M-6): attaching to or resuming a remote
    session sends this client's settings; before, only a start or a change
    did, so a pre-RS-1 start stayed at rev 0 ("your r4 client is too old")
    and a relaunch with --yolo was ignored until some toggle."""

    def _saved(self, tmp_path, monkeypatch, venue: str) -> str:
        _, _, session = _server(tmp_path, monkeypatch, venue=venue)
        _store(tmp_path, monkeypatch).save(session)
        return session.session_id

    def _relaunched(self, tmp_path, monkeypatch, sid: str = "", journal=None, **server_kw):
        """A new bridge; the journal answers for ``sid`` with ``journal`` as
        its payload (default: the cached copy, nothing to adopt)."""
        client = _PutClient()
        store = _store(tmp_path, monkeypatch)
        server = CompanionBridgeServer(client=client, store=store, model="gpt-x", **server_kw)
        if sid:
            client.get_answers[f"/companion/sessions/{sid}"] = {
                "session_id": sid,
                "venue": "frontend",
                "payload": json.dumps(journal or {"id": sid, "messages": []}),
            }
        return server, client

    def test_resume_sends_this_clients_settings(self, tmp_path, monkeypatch):
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid, interaction_mode="autopilot")
        server.load_session({"session_id": sid})
        sent = _settings_puts(client, sid)
        assert len(sent) == 1
        assert sent[0]["permission_mode"] == "trust"
        assert sent[0]["interaction_mode"] == "autopilot"

    def test_initialize_on_a_remote_session_sends_the_settings(self, tmp_path, monkeypatch):
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid)
        server.initialize({"session_id": sid})
        sent = _settings_puts(client, sid)
        assert len(sent) == 1 and sent[0]["permission_mode"] == "auto_safe"

    def test_initialize_rejoin_sends_the_journals_settings(self, tmp_path, monkeypatch):
        """Round 4, R1: a restarted tab bridge rejoins with
        ``initialize {session_id}`` and no load_session follows. Another
        device ran ``/approve ask``; the rejoin pushed the stale cached
        ``auto_safe`` before, loosening the worker until the next change."""
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        newer = {
            "id": sid,
            "updated_at": "2099-10-23T12:00:00+00:00",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"venue": "frontend", "interaction_mode": "autopilot"},
        }
        server, client = self._relaunched(tmp_path, monkeypatch, sid, newer)
        snap = server.initialize({"session_id": sid})
        assert snap.get("notice") == "refreshed from server journal before resume"
        assert server.session.permissions.mode == PermissionMode.TRUST
        sent = _settings_puts(client, sid)
        assert len(sent) == 1 and sent[0]["permission_mode"] == "trust"
        for thread in threading.enumerate():
            if thread.name == "bridge-init-save":
                thread.join(timeout=5)
        # The boot save writes the adopted session, never the stale one.
        row = _store(tmp_path, monkeypatch).load(sid, client)
        assert row is not None and row.metadata.get("interaction_mode") == "autopilot"

    def test_initialize_rejoin_without_the_journal_sends_nothing(self, tmp_path, monkeypatch):
        """Round 4, R1: the same rule as load_session (round 3, N4)."""
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid)
        client.get_answers[f"/companion/sessions/{sid}"] = TimeoutError("read timed out")
        snap = server.initialize({"session_id": sid})
        assert snap["notice"] == "remote journal unavailable; showing cached session"
        assert _settings_puts(client, sid) == []

    def test_a_failed_push_never_blocks_the_resume(self, tmp_path, monkeypatch):
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid)
        client.put_error = APIError("API error: 503", status_code=503)
        server.load_session({"session_id": sid})
        assert server.session.session_id == sid

    def test_resume_sends_the_adopted_sessions_settings(self, tmp_path, monkeypatch):
        """Round 3, N1: another client ran ``/approve ask`` and turned the
        write-plan gate and the workflow arm on; resume adopts that journal
        copy, and the push carries it (it sent the stale cached copy's
        looser ``auto_safe`` before)."""
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        newer = {
            "id": sid,
            "updated_at": "2099-10-23T12:00:00+00:00",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {
                "venue": "frontend",
                "permission_override": "trust",
                "write_requires_plan": True,
                "workflow_armed": True,
            },
        }
        server, client = self._relaunched(tmp_path, monkeypatch, sid, newer)
        result = server.load_session({"session_id": sid})
        assert result.get("notice") == "refreshed from server journal before resume"
        # ADR-104 D-1: a legacy trust override resumes as autopilot.
        assert server.session.permissions.mode == PermissionMode.TRUST
        assert server.session.metadata["interaction_mode"] == "autopilot"
        sent = _settings_puts(client, sid)
        assert len(sent) == 1
        assert sent[0] == server._agent_settings(server.session)
        assert sent[0]["permission_mode"] == "trust"
        assert sent[0]["write_requires_plan"] is True and sent[0]["workflow_armed"] is True

    def test_resume_without_the_journal_sends_nothing(self, tmp_path, monkeypatch):
        """Round 3, N4: a journal that did not answer leaves the worker on
        the last settings sent: the cached copy may be stale (N1), and a
        second slow call ran load_session past the TUI's 30 s timeout."""
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid)
        client.get_answers[f"/companion/sessions/{sid}"] = TimeoutError("read timed out")
        result = server.load_session({"session_id": sid})
        assert result["notice"] == "remote journal unavailable; showing cached session"
        assert _settings_puts(client, sid) == []

    def test_attach_pushes_wait_less_than_a_change_push(self, tmp_path, monkeypatch):
        """Round 3, N4: 15 s journal read + attach push stays under 30 s."""
        sid = self._saved(tmp_path, monkeypatch, "frontend")
        server, client = self._relaunched(tmp_path, monkeypatch, sid)
        timeouts: list[Any] = []
        plain_put = client.put

        def put(path: str, json: Any = None, **kw: Any) -> Any:
            timeouts.append(kw.get("timeout"))
            return plain_put(path, json=json, **kw)

        client.put = put  # type: ignore[method-assign]
        server.load_session({"session_id": sid})
        server.initialize({"session_id": sid})
        assert len(timeouts) == 2
        assert all(0 < float(t) <= 5 for t in timeouts)

    def test_a_local_resume_sends_nothing(self, tmp_path, monkeypatch):
        sid = self._saved(tmp_path, monkeypatch, "local")
        server, client = self._relaunched(tmp_path, monkeypatch)
        server.load_session({"session_id": sid})
        server.initialize({"session_id": sid})
        assert client.puts == []
