"""Tests for RichREPL fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import CompanionEvent, CompanionEventType


class TestRichREPL:
    def test_initialization(self):
        from r4.companion.ui.repl import RichREPL

        session = MagicMock()
        session.agent = MagicMock()
        repl = RichREPL(session)
        assert repl.session == session

    def test_on_event_tool_call(self):
        from r4.companion.ui.repl import RichREPL

        session = MagicMock()
        session.agent = MagicMock()
        repl = RichREPL(session)

        event = CompanionEvent(
            event_type=CompanionEventType.TOOL_CALL,
            message="Called list_dir",
        )
        repl._on_event(event)

    def test_on_event_error(self):
        from r4.companion.ui.repl import RichREPL

        session = MagicMock()
        session.agent = MagicMock()
        repl = RichREPL(session)

        event = CompanionEvent(
            event_type=CompanionEventType.ERROR,
            message="Something went wrong",
        )
        repl._on_event(event)

    def test_save_session(self):
        from r4.companion.ui.repl import RichREPL

        session = MagicMock()
        session.agent = MagicMock()
        session.messages = []
        repl = RichREPL(session)

        # Best-effort save should not raise
        repl._save_session()


# -- the fallback actually starts (companion sweep 2026-10-21, cli lane R1) ----
# repl.py imported ``.session`` / ``.session_store`` from the ``ui`` package,
# so ``r4`` without the TUI binary and ``r4 companion --legacy-tui`` died with
# "No module named 'r4.companion.ui.session'"; the session save at exit
# failed the same way, swallowed by its best-effort except.


def test_launch_repl_builds_a_session_and_runs(monkeypatch):
    from unittest.mock import patch

    from r4.companion.ui import repl as repl_module

    ran = []
    monkeypatch.setattr(repl_module.RichREPL, "run", lambda self: ran.append(self.session))
    monkeypatch.setattr("r4.companion.model_resolution.resolve_initial_model", lambda _c, **_k: "m")
    with patch("r4.companion.session.CompanionSession") as session_cls:
        session = MagicMock()
        session.metadata = {}
        session.skills.activate.return_value = True
        session_cls.return_value = session
        repl_module.launch_repl(MagicMock(), model="m", skill="coding", system="be brief")
    assert ran == [session]
    session.skills.activate.assert_called_once_with("coding")
    assert session.metadata["system_prompt_override"] == "be brief"


def test_launch_repl_refuses_an_unknown_skill(monkeypatch):
    from unittest.mock import patch

    import pytest

    from r4.companion.ui import repl as repl_module

    monkeypatch.setattr(repl_module.RichREPL, "run", lambda self: None)
    monkeypatch.setattr(
        "r4.companion.model_resolution.resolve_initial_model", lambda _c, **_k: None
    )
    with patch("r4.companion.session.CompanionSession") as session_cls:
        session_cls.return_value.skills.activate.return_value = False
        with pytest.raises(ValueError, match="unknown skill: nope"):
            repl_module.launch_repl(MagicMock(), skill="nope")


def test_save_session_reaches_the_store():
    from unittest.mock import patch

    from r4.companion.ui.repl import RichREPL

    session = MagicMock()
    with patch("r4.companion.session_store.CompanionSessionStore") as store_cls:
        RichREPL(session)._save_session()
    store_cls.return_value.save.assert_called_once_with(session)


# -- terminal entries resume and pick a model like the console (R2) ----------


def _real_session(tmp_path, monkeypatch):
    """A stored session with one exchange, in a scratch store."""
    from r4.companion import session_store as store_module
    from r4.companion.session import CompanionSession
    from r4.companion.session_models import SessionMessage
    from r4.companion.session_store import CompanionSessionStore

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(store_module, "DB_PATH", db)
    monkeypatch.setattr("r4.companion.session.DB_PATH", db, raising=False)
    client = MagicMock()
    client.config.model = "builtin-default"
    session = CompanionSession(session_id="keep-me", name="kept", client=client)
    session.messages.append(SessionMessage(role="user", content="earlier question"))
    session.messages.append(SessionMessage(role="assistant", content="earlier answer"))
    CompanionSessionStore(db).save(session)
    return client


def test_resume_loads_the_stored_conversation(tmp_path, monkeypatch):
    from r4.companion.ui.repl import open_cli_session

    client = _real_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "r4.companion.model_resolution.resolve_initial_model", lambda _c, **_k: "fake-a@pub"
    )
    session = open_cli_session(client, name="REPL Session", session_id="keep-me")
    assert [m.content for m in session.messages][-2:] == ["earlier question", "earlier answer"]


def test_unknown_resume_id_is_an_error_not_a_new_session(tmp_path, monkeypatch):
    import pytest

    from r4.companion.ui.repl import open_cli_session

    client = _real_session(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="session not found: nope"):
        open_cli_session(client, name="REPL Session", session_id="nope")


def test_new_session_takes_the_model_the_console_would_pick(tmp_path, monkeypatch):
    from r4.companion.ui.repl import open_cli_session

    client = _real_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "r4.companion.model_resolution.resolve_initial_model",
        lambda _c, *, explicit, saved: explicit or "fake-a@pub",
    )
    assert open_cli_session(client, name="Headless").agent.model == "fake-a@pub"
    assert open_cli_session(client, name="Headless", model="x@y").agent.model == "x@y"
