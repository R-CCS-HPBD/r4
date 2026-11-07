"""Auto-titling tests: sessions created with the "Session YYYY-MM-DD HH:MM"
placeholder get a content-derived title from the first user message — on
save() for live sessions, and as a read-only display fallback in listings
for legacy rows. User-chosen names are never rewritten."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from r4.companion import session_store as store_module
from r4.companion.session_models import SessionMessage
from r4.companion.session_store import (
    AUTO_SESSION_NAME_RE,
    CompanionSessionStore,
    derive_session_title,
)

# ── helpers ────────────────────────────────────────────────────────────────


class _Config:
    profile = "default"
    gateway_url = "http://gw.test"
    project_id = "proj-x"
    model = "test-model"
    auto_retry = False


class _FakeClient:
    def __init__(self):
        self.config = _Config()
        self._config = _Config()
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, json: Any = None, **_kw: Any) -> dict:
        self.posts.append((path, json))
        return {"status": "ok", "applied": True}


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompanionSessionStore:
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)  # re-read env
    return CompanionSessionStore(db_path=tmp_path / "sessions.db")


def _summary(store: CompanionSessionStore, session_id: str) -> dict[str, Any]:
    return [s for s in store.list_sessions() if s["id"] == session_id][0]


# ── derive_session_title (pure) ────────────────────────────────────────────


class TestDeriveSessionTitle:
    def test_first_user_message_wins(self) -> None:
        messages = [
            SessionMessage(role="assistant", content="hello!"),
            SessionMessage(role="user", content="fix the login redirect bug"),
            SessionMessage(role="user", content="and the tests"),
        ]
        assert derive_session_title(messages) == "fix the login redirect bug"

    def test_dict_rows_accepted(self) -> None:
        messages = [{"role": "user", "content": "dict row title"}]
        assert derive_session_title(messages) == "dict row title"

    def test_slash_commands_skipped(self) -> None:
        messages = [
            SessionMessage(role="user", content="/help"),
            SessionMessage(role="user", content="  /sessions  "),
            SessionMessage(role="user", content="real question follows"),
        ]
        assert derive_session_title(messages) == "real question follows"

    def test_whitespace_collapsed(self) -> None:
        messages = [SessionMessage(role="user", content="line one\n\n  line   two\tend")]
        assert derive_session_title(messages) == "line one line two end"

    def test_long_message_truncates_at_word_boundary(self) -> None:
        content = "word " * 30 + "tail"
        title = derive_session_title([SessionMessage(role="user", content=content)])
        assert title.endswith("…")
        assert len(title) <= 61  # 60 + ellipsis
        assert "tail" not in title

    def test_single_long_token_hard_cut(self) -> None:
        title = derive_session_title([SessionMessage(role="user", content="x" * 200)])
        assert title == "x" * 60 + "…"

    def test_empty_when_no_qualifying_message(self) -> None:
        assert derive_session_title([]) == ""
        assert derive_session_title(None) == ""
        assert derive_session_title([SessionMessage(role="user", content="   ")]) == ""
        assert derive_session_title([SessionMessage(role="assistant", content="hi")]) == ""

    def test_placeholder_pattern_matches_create_default(self) -> None:
        assert AUTO_SESSION_NAME_RE.match("Session 2026-09-19 14:32")
        assert not AUTO_SESSION_NAME_RE.match("demo")
        assert not AUTO_SESSION_NAME_RE.match("fix the login redirect bug")
        assert not AUTO_SESSION_NAME_RE.match("Session 2026-09-19 14:32 (fork)")


# ── save() integration ─────────────────────────────────────────────────────


class TestSaveAutoTitle:
    def test_placeholder_retitled_on_save(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)  # placeholder name, no messages
        assert AUTO_SESSION_NAME_RE.match(session.name)

        session.messages.append(SessionMessage(role="user", content="fix the login redirect bug"))
        store.save(session)

        assert session.name == "fix the login redirect bug"
        assert _summary(store, session.session_id)["name"] == "fix the login redirect bug"

    def test_title_stable_across_later_saves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)
        session.messages.append(SessionMessage(role="user", content="original topic"))
        store.save(session)

        session.messages.append(SessionMessage(role="user", content="unrelated follow-up"))
        store.save(session)

        assert _summary(store, session.session_id)["name"] == "original topic"

    def test_user_chosen_name_never_rewritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client, name="demo")
        session.messages.append(SessionMessage(role="user", content="something else entirely"))
        store.save(session)

        assert _summary(store, session.session_id)["name"] == "demo"

    def test_placeholder_kept_when_only_slash_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)
        session.messages.append(SessionMessage(role="user", content="/help"))
        store.save(session)

        assert AUTO_SESSION_NAME_RE.match(_summary(store, session.session_id)["name"])

    def test_rename_then_save_keeps_renamed_title(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rename_record on a non-live row + later load/save: the loaded name
        is the renamed one (not the placeholder), so auto-title stays out."""
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)
        session.messages.append(SessionMessage(role="user", content="first topic"))
        store.save(session)  # auto-titles to "first topic"
        store.rename_record(session.session_id, "my custom name")

        loaded = store.load(session.session_id, client)
        assert loaded is not None
        loaded.messages.append(SessionMessage(role="user", content="second topic"))
        store.save(loaded)

        assert _summary(store, session.session_id)["name"] == "my custom name"


# ── list-time display fallback (legacy rows) ───────────────────────────────


class TestListDisplayFallback:
    def test_legacy_placeholder_row_shows_derived_title(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rows saved before auto-titling (placeholder name + messages, never
        re-saved) show a derived title in listings — without a DB write."""
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)
        placeholder = session.name

        # Simulate a pre-auto-title row: messages present, name still placeholder.
        row = {"role": "user", "content": "legacy question about vllm"}
        messages = json.dumps([row])
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                "UPDATE sessions SET messages = ? WHERE id = ?",
                (messages, session.session_id),
            )

        assert _summary(store, session.session_id)["name"] == "legacy question about vllm"

        # Read-only: the stored name is untouched.
        with sqlite3.connect(str(store.db_path)) as conn:
            stored = conn.execute(
                "SELECT name FROM sessions WHERE id = ?", (session.session_id,)
            ).fetchone()[0]
        assert stored == placeholder

    def test_legacy_placeholder_without_messages_keeps_placeholder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client)

        assert AUTO_SESSION_NAME_RE.match(_summary(store, session.session_id)["name"])
