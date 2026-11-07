"""Pins for `r4 session fork` (ADR-057 D-4 leg 4)."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from r4._cli.sessions import _handle_fork
from r4.companion.session_store import CompanionSessionStore


def _args(**over):
    base = dict(
        session_id="src",
        new_id=None,
        name="",
        from_turn="",
        as_json=False,
    )
    base.update(over)
    return Namespace(**base)


def _seed(store: CompanionSessionStore, session_id="src") -> None:
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
    ]
    session.skills._active = set()
    session.context._memory = {}
    session.metadata = {"config": {}, "turn_seq": 2}
    session.agent.model = "m"
    session._sync_metadata_usage_state = lambda: None
    store.save(session)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    _seed(s)
    # _handle_fork imports the class inside the function via the module
    # namespace — patch the source module's attribute.
    import r4.companion.session_store as store_mod

    monkeypatch.setattr(store_mod, "CompanionSessionStore", lambda *a, **k: s)
    return s


class TestSessionForkCli:
    def test_full_fork_json(self, store, capsys):
        rc = _handle_fork(_args(new_id="fork-1", as_json=True))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["session_id"] == "fork-1"
        assert out["messages"] == 4
        assert out["forked_from"] == "src"

    def test_fork_from_turn_slices_messages(self, store, capsys):
        rc = _handle_fork(_args(new_id="fork-2", from_turn="t1"))
        assert rc == 0
        forked = store.load("fork-2", client=MagicMock())
        assert [m.content for m in forked.messages] == ["m1", "r1"]
        assert forked.metadata["forked_from_turn"] == "t1"

    def test_unknown_turn_exits_1_with_named_error(self, store, capsys):
        rc = _handle_fork(_args(new_id="fork-3", from_turn="t99"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "fork.turn_not_found: t99" in err

    def test_missing_session_exits_1(self, store, capsys):
        rc = _handle_fork(_args(session_id="nope"))
        assert rc == 1
        assert "session not found: nope" in capsys.readouterr().err

    def test_generated_id_when_unspecified(self, store, capsys):
        rc = _handle_fork(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Forked src" in out
        assert "sess-" in out
