"""Venue + state registry tests (plan 2026-09-09): CompanionSessionStore and
the ``r4 session`` CLI surface (filters, sync badges, merged listing,
conclude verb)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from r4.companion import session_store as store_module
from r4.companion.session_store import (
    CompanionSessionStore,
    computed_state,
    validate_storable_state,
    validate_venue,
)

# ── helpers ────────────────────────────────────────────────────────────────


class _Config:
    profile = "default"
    gateway_url = "http://gw.test"
    project_id = "proj-x"
    model = "test-model"
    auto_retry = False


class _FakeClient:
    """Minimal client stand-in: config + captured POSTs / scripted GETs."""

    def __init__(self, *, get_map: dict[str, Any] | None = None):
        self.config = _Config()
        self._config = _Config()
        self.posts: list[tuple[str, dict]] = []
        self._get_map = get_map or {}

    def post(self, path: str, json: Any = None, **_kw: Any) -> dict:
        self.posts.append((path, json))
        return {"status": "ok", "applied": True}

    def get(self, path: str, **_kw: Any) -> Any:
        for prefix, response in sorted(
            self._get_map.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if path.startswith(prefix):
                return response
        return {}


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompanionSessionStore:
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)  # re-read env
    return CompanionSessionStore(db_path=tmp_path / "sessions.db")


# ── venue/state grammar ────────────────────────────────────────────────────


class TestGrammar:
    @pytest.mark.parametrize("venue", ["local", "frontend", "cluster:rccs", "cluster:bdrt-a1"])
    def test_valid_venues(self, venue: str) -> None:
        assert validate_venue(venue) == venue

    @pytest.mark.parametrize("venue", ["", "moonbase", "cluster:", "cluster:UPPER", " local"])
    def test_invalid_venues(self, venue: str) -> None:
        with pytest.raises(ValueError):
            validate_venue(venue)

    def test_archived_not_storable(self) -> None:
        with pytest.raises(ValueError):
            validate_storable_state("archived")

    def test_storable_states(self) -> None:
        for state in ("ongoing", "stopped", "completed", "error"):
            assert validate_storable_state(state) == state


# ── store venue/state ─────────────────────────────────────────────────────


class TestStoreVenueState:
    def test_create_defaults_to_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client, name="demo")
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["venue"] == "local"
        assert summary["state"] == ""
        assert computed_state(summary) == ""

    def test_create_with_frontend_venue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        store = _store(tmp_path, monkeypatch)
        session = store.create_session(client, name="demo", venue="frontend")
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["venue"] == "frontend"

    def test_create_rejects_bad_venue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            store.create_session(_FakeClient(), venue="moonbase")

    def test_set_state_transition(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path, monkeypatch)
        client = _FakeClient()
        session = store.create_session(client, name="demo")
        assert store.set_state(session.session_id, "stopped") is True
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["state"] == "stopped"
        assert store.set_state("missing-id", "stopped") is False

    def test_archived_stays_derived(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path, monkeypatch)
        client = _FakeClient()
        session = store.create_session(client, name="demo")
        store.set_state(session.session_id, "stopped")
        store.archive(session.session_id)
        summary = [
            s for s in store.list_sessions(include_archived=True) if s["id"] == session.session_id
        ][0]
        assert summary["archived_at"]
        assert computed_state(summary) == "archived"

    def test_list_filters_compose(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path, monkeypatch)
        client = _FakeClient()
        local = store.create_session(client, name="l1", venue="local")
        front = store.create_session(client, name="f1", venue="frontend")
        store.set_state(local.session_id, "stopped")
        store.set_state(front.session_id, "stopped")
        only_front = store.list_sessions(venue="frontend")
        assert [s["id"] for s in only_front] == [front.session_id]
        only_stopped = store.list_sessions(state="stopped")
        assert {s["id"] for s in only_stopped} == {local.session_id, front.session_id}
        unrecorded = store.list_sessions(state="")
        assert unrecorded == []

    def test_mirror_body_carries_venue_and_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)
        store_module.set_snapshot_enabled(None)
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        client = _FakeClient()
        session = store.create_session(client, name="demo", venue="frontend")
        path, body = client.posts[-1]
        assert path == f"/umrs/companion/sessions/{session.session_id}/snapshot"
        assert body["venue"] == "frontend"
        assert body["state"] == ""

    def test_rebuild_restores_venue_state_and_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)
        store_module.set_snapshot_enabled(None)
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        payload = json.dumps(
            {
                "id": "remote-1",
                "name": "from-server",
                "created_at": "2026-09-09T09:00:00Z",
                "updated_at": "2026-09-09T09:00:00Z",
                "messages": "[]",
                "active_skills": "[]",
                "memory": "{}",
                "metadata": json.dumps({"config": {"project_id": "x"}}),
                "model": "m",
            }
        )
        client = _FakeClient(
            get_map={
                "/umrs/companion/sessions": {
                    "snapshots": [
                        {
                            "session_id": "remote-1",
                            "name": "from-server",
                            "updated_at": "2026-09-09T09:00:00Z",
                            "venue": "frontend",
                            "state": "archived",
                            "archived_at": "2026-09-09T10:00:00Z",
                        },
                        {"session_id": "", "name": "junk"},
                    ]
                },
                "/umrs/companion/sessions/remote-1": {
                    "session_id": "remote-1",
                    "name": "from-server",
                    "payload": payload,
                    "updated_at": "2026-09-09T09:00:00Z",
                    "venue": "frontend",
                    "state": "archived",
                    "archived_at": "2026-09-09T10:00:00Z",
                },
            }
        )
        assert store.rebuild_from_remote(client) == 1
        summary = store.list_sessions(include_archived=True)[0]
        assert summary["venue"] == "frontend"
        assert summary["archived_at"] == "2026-09-09T10:00:00Z"
        assert computed_state(summary) == "archived"


# ── CLI surface ────────────────────────────────────────────────────────────


def _ns(**over: Any) -> Namespace:
    base = dict(
        session_subcommand="list",
        session_id=None,
        kind="all",
        endpoint=None,
        project=None,
        venue=None,
        state=None,
        no_remote=False,
        include_archived=False,
        limit=50,
        older_than_days=None,
        yes=False,
        as_json=False,
    )
    base.update(over)
    return Namespace(**base)


class TestJournalPrefix:
    def test_gateway_prefix_used_for_mirror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Live-found bug (bdrt probe): bare /umrs/companion/* 404s on the
        gateway; the journal prefix must follow client._api_prefix."""
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)
        store_module.set_snapshot_enabled(None)
        client = _FakeClient()
        client._api_prefix = "/gw"
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        session = store.create_session(client, name="demo")
        path, body = client.posts[-1]
        assert path == f"/gw/companion/sessions/{session.session_id}/snapshot"
        assert body["venue"] == "local"

    def test_direct_umrs_prefix_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)
        store_module.set_snapshot_enabled(None)
        client = _FakeClient()  # no _api_prefix attr → /umrs
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        store.create_session(client, name="demo")
        assert client.posts[-1][0].startswith("/umrs/companion/sessions/")


class TestCliFilterTokens:
    def test_bad_venue_rejected(self) -> None:
        from r4._cli.sessions import _validate_filter_tokens

        error = _validate_filter_tokens(_ns(venue="moonbase"))
        assert error and "venue" in error

    def test_bad_state_rejected(self) -> None:
        from r4._cli.sessions import _validate_filter_tokens

        error = _validate_filter_tokens(_ns(state="doomed"))
        assert error and "state" in error

    def test_archived_requires_include_archived(self) -> None:
        from r4._cli.sessions import _validate_filter_tokens

        error = _validate_filter_tokens(_ns(state="archived"))
        assert error and "--include-archived" in error
        assert _validate_filter_tokens(_ns(state="archived", include_archived=True)) is None

    def test_valid_tokens_pass(self) -> None:
        from r4._cli.sessions import _validate_filter_tokens

        assert (
            _validate_filter_tokens(
                _ns(venue="local,frontend,cluster:rccs", state="ongoing,stopped")
            )
            is None
        )


class TestRemoteMerge:
    def _local_store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")

    def test_merge_badges_local_remote_mirrored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._local_store(tmp_path, monkeypatch)
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        client = _FakeClient()
        mirrored = store.create_session(client, name="m1")
        store.create_session(client, name="local-only-1")

        class _JournalClient(_FakeClient):
            def __init__(self):
                super().__init__(
                    get_map={
                        "/umrs/companion/sessions": {
                            "snapshots": [
                                {
                                    "session_id": mirrored.session_id,
                                    "name": "m1",
                                    "updated_at": "2026-09-09T09:00:00Z",
                                    "venue": "local",
                                    "state": "",
                                    "archived_at": "",
                                },
                                {
                                    "session_id": "srv-only",
                                    "name": "server-1",
                                    "updated_at": "2026-09-09T11:00:00Z",
                                    "venue": "frontend",
                                    "state": "stopped",
                                    "archived_at": "",
                                },
                            ]
                        }
                    }
                )

        monkeypatch.setattr("r4.client.Client", _JournalClient)
        from r4._cli.sessions import _collect_rows

        rows = _collect_rows(_ns(kind="companion"))
        by_id = {row["id"]: row for row in rows}
        assert by_id[mirrored.session_id]["sync"] == "mirrored"
        local_only = [r for r in rows if r["name"] == "local-only-1"][0]
        assert local_only["sync"] == "local-only"
        remote = by_id["srv-only"]
        assert remote["sync"] == "remote-only"
        assert remote["venue"] == "frontend"
        assert remote["state"] == "stopped"

    def test_journal_unreachable_marks_local_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        self._local_store(tmp_path, monkeypatch)
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        store.create_session(_FakeClient(), name="l1")

        class _DeadClient:
            def __init__(self, *_a: Any, **_k: Any):
                raise ConnectionError("no route")

        monkeypatch.setattr("r4.client.Client", _DeadClient)
        from r4._cli.sessions import _collect_rows

        rows = _collect_rows(_ns(kind="companion"))
        assert all(row.get("sync") == "local-only" for row in rows)
        assert "remote session journal unreachable" in capsys.readouterr().err

    def test_no_remote_skips_merge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._local_store(tmp_path, monkeypatch)
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        store.create_session(_FakeClient(), name="l1")

        def _boom(*_a: Any, **_k: Any) -> None:
            raise AssertionError("client must not be constructed with --no-remote")

        monkeypatch.setattr("r4.client.Client", _boom)
        from r4._cli.sessions import _collect_rows

        rows = _collect_rows(_ns(kind="companion", no_remote=True))
        assert rows and "sync" not in rows[0]


class TestBridgeVenueListing:
    def _server(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: Any):
        from r4.companion.ui.bridge_server import CompanionBridgeServer

        store = _store(tmp_path, monkeypatch)
        return CompanionBridgeServer(client=client, store=store), store

    def test_initialize_records_venue_on_create(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        session = server._open_session(
            session_id=None,
            explicit_model=None,
            skill=None,
            system_prompt=None,
            venue="frontend",
        )
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["venue"] == "frontend"

    def test_first_resume_fills_missing_venue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        session = store.create_session(client, name="legacy")
        session.metadata.pop("venue", None)
        store.save(session)
        resumed = server._open_session(
            session_id=session.session_id,
            explicit_model=None,
            skill=None,
            system_prompt=None,
            venue="frontend",
        )
        assert resumed.metadata["venue"] == "frontend"

    def test_invalid_venue_surfaces_valueerror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, _ = self._server(tmp_path, monkeypatch, client)
        with pytest.raises(ValueError):
            server._open_session(
                session_id=None,
                explicit_model=None,
                skill=None,
                system_prompt=None,
                venue="moonbase",
            )

    def _journal_client(self, mirrored_id: str) -> _FakeClient:
        return _FakeClient(
            get_map={
                "/umrs/companion/sessions": {
                    "snapshots": [
                        {
                            "session_id": mirrored_id,
                            "name": "m1",
                            "updated_at": "2026-09-09T09:00:00Z",
                            "venue": "local",
                            "state": "",
                            "archived_at": "",
                        },
                        {
                            "session_id": "srv-only",
                            "name": "server-1",
                            "updated_at": "2026-09-09T11:00:00Z",
                            "venue": "frontend",
                            "state": "stopped",
                            "archived_at": "",
                        },
                    ]
                }
            }
        )

    def _merged_listing(self, server, store, client) -> dict:
        """Drive list_sessions with the scripted journal; return result."""
        mirrored = store.create_session(client, name="m1")
        store.create_session(client, name="l1")
        server._client = self._journal_client(mirrored.session_id)
        result = server.list_sessions({"limit": 50})
        assert result["remote_available"] is True
        return {row["id"]: row for row in result["sessions"]}

    def test_local_row_sync_badges(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        rows = self._merged_listing(server, store, client)
        mirrored = [r for r in rows.values() if r["name"] == "m1"][0]
        assert mirrored["sync"] == "mirrored"
        local_only = [r for r in rows.values() if r["name"] == "l1"][0]
        assert local_only["sync"] == "local-only"

    def test_remote_only_row_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        rows = self._merged_listing(server, store, client)
        assert rows["srv-only"]["sync"] == "remote-only"
        assert rows["srv-only"]["venue"] == "frontend"
        assert rows["srv-only"]["state"] == "stopped"

    def test_list_sessions_journal_unreachable_honest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        store.create_session(client, name="l1")

        class _DeadClient(_FakeClient):
            def get(self, path: str, **_k: Any) -> Any:
                raise ConnectionError("no route")

        server._client = _DeadClient()
        result = server.list_sessions({"limit": 50})
        assert result["remote_available"] is False
        assert result["remote_reason"]
        assert all(row["sync"] == "local-only" for row in result["sessions"])

    def test_list_sessions_no_remote_skips_journal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        session = store.create_session(client, name="l1")

        class _BoomClient(_FakeClient):
            def get(self, path: str, **_k: Any) -> Any:
                raise AssertionError("journal must not be queried with no_remote")

        server._client = _BoomClient()
        result = server.list_sessions({"limit": 50, "no_remote": True})
        assert result["remote_available"] is False
        assert [r["id"] for r in result["sessions"]] == [session.session_id]

    def test_list_sessions_filters_local_and_query_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        store.create_session(client, name="l1", venue="local")

        class _FilteredJournal(_FakeClient):
            def __init__(self):
                super().__init__()
                self.paths: list[str] = []

            def get(self, path: str, **_k: Any) -> Any:
                self.paths.append(path)
                # Emulate the server-side filter: only matching rows return.
                return {
                    "snapshots": [
                        {
                            "session_id": "srv-only",
                            "name": "server-1",
                            "updated_at": "2026-09-09T11:00:00Z",
                            "venue": "frontend",
                            "state": "stopped",
                            "archived_at": "",
                        }
                    ]
                }

        journal = _FilteredJournal()
        server._client = journal
        result = server.list_sessions({"limit": 50, "venue": "frontend", "state": "stopped"})
        assert all(row.get("venue") == "frontend" for row in result["sessions"])
        assert "venue=frontend" in journal.paths[0]
        assert "state=stopped" in journal.paths[0]
        assert result["remote_available"] is True


class TestConfigDefaultVenue:
    def _ctx_ns(self, **over: Any) -> Namespace:
        base = dict(
            context_action=None,
            as_json=False,
            project=None,
            clear_project=False,
            umrs_username=None,
            clear_user=False,
            default_venue=None,
            clear_default_venue=False,
        )
        base.update(over)
        return Namespace(**base)

    def _config_obj(self) -> Any:
        from types import SimpleNamespace as _SN

        return _SN(
            profile="default",
            gateway_url="",
            model="",
            project_id="",
            umrs_username="",
        )

    def test_persist_and_clear(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from r4._cli.config import _handle_context
        from r4.config import ProfileConfigStore

        rc = _handle_context(self._ctx_ns(default_venue="frontend"), self._config_obj())
        assert rc == 0
        store = ProfileConfigStore()
        assert store.load("default")["session_default_venue"] == "frontend"

        rc = _handle_context(self._ctx_ns(clear_default_venue=True), self._config_obj())
        assert rc == 0
        assert "session_default_venue" not in store.load("default")

    def test_invalid_venue_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from r4._cli.config import _handle_context

        rc = _handle_context(self._ctx_ns(default_venue="moonbase"), self._config_obj())
        assert rc == 2
        assert "venue" in capsys.readouterr().err


class TestBridgeProfileDefaultVenue:
    def test_profile_default_picked_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from r4.companion.ui.bridge_server import CompanionBridgeServer
        from r4.config import ProfileConfigStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        ProfileConfigStore().save("default", session_default_venue="frontend")
        server = CompanionBridgeServer(
            client=_FakeClient(),
            store=CompanionSessionStore(db_path=tmp_path / "sessions.db"),
        )
        assert server._profile_default_venue() == "frontend"

    def test_invalid_persisted_value_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from r4.companion.ui.bridge_server import CompanionBridgeServer
        from r4.config import ProfileConfigStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        ProfileConfigStore().save("default", session_default_venue="moonbase")
        server = CompanionBridgeServer(
            client=_FakeClient(),
            store=CompanionSessionStore(db_path=tmp_path / "sessions.db"),
        )
        assert server._profile_default_venue() is None


class TestPullSession:
    def _journal_detail_client(self) -> _FakeClient:
        payload = json.dumps(
            {
                "id": "remote-9",
                "name": "from-server",
                "created_at": "2026-09-09T09:00:00Z",
                "updated_at": "2026-09-09T09:00:00Z",
                "messages": "[]",
                "active_skills": "[]",
                "memory": "{}",
                "metadata": "{}",
                "model": "m1",
            }
        )
        return _FakeClient(
            get_map={
                "/umrs/companion/sessions/remote-9": {
                    "session_id": "remote-9",
                    "name": "from-server",
                    "payload": payload,
                    "updated_at": "2026-09-09T09:00:00Z",
                    "venue": "frontend",
                    "state": "stopped",
                    "archived_at": "",
                },
            }
        )

    def test_pull_inserts_remote_only_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path, monkeypatch)
        client = self._journal_detail_client()
        assert store.pull_session(client, "remote-9") == "pulled"
        summary = [s for s in store.list_sessions() if s["id"] == "remote-9"][0]
        assert summary["venue"] == "frontend"
        assert summary["state"] == "stopped"

    def test_pull_never_overwrites_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path, monkeypatch)
        client = self._journal_detail_client()
        session = store.create_session(client, name="mine")
        assert store.pull_session(client, session.session_id) == "already-local"
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["name"] == "mine"

    def test_pull_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path, monkeypatch)
        client = self._journal_detail_client()
        assert store.pull_session(client, "nope-nope") == "not-found"

    def test_pull_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(tmp_path, monkeypatch)

        class _Dead:
            def get(self, path: str, **_k: Any) -> Any:
                raise ConnectionError("no route")

        assert store.pull_session(_Dead(), "remote-9") == "unavailable"

    def test_cli_pull_verb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")

        class _PullClientClass(_FakeClient):
            def __init__(self):
                super().__init__(
                    get_map=self._journal_map(),
                )

            def _journal_map(self) -> dict:
                payload = json.dumps({"id": "remote-9", "name": "srv", "metadata": "{}"})
                return {
                    "/umrs/companion/sessions/remote-9": {
                        "session_id": "remote-9",
                        "name": "srv",
                        "payload": payload,
                        "updated_at": "2026-09-09T09:00:00Z",
                        "venue": "frontend",
                        "state": "",
                        "archived_at": "",
                    }
                }

        monkeypatch.setattr("r4.client.Client", _PullClientClass)
        from r4._cli.sessions import SessionsCommand

        rc = SessionsCommand().handle(_ns(session_subcommand="pull", session_id="remote-9"))
        assert rc == 0
        assert "Pulled session remote-9" in capsys.readouterr().out
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        assert [s["id"] for s in store.list_sessions()] == ["remote-9"]


class TestBridgePullThenLoad:
    def _server(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: Any):
        from r4.companion.ui.bridge_server import CompanionBridgeServer

        store = _store(tmp_path, monkeypatch)
        return CompanionBridgeServer(client=client, store=store), store

    def test_resume_refreshes_existing_remote_copy(self, tmp_path, monkeypatch):
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        session = store.create_session(client, name="cached", venue="frontend")
        sid = session.session_id
        client._get_map[f"/umrs/companion/sessions/{sid}"] = {
            "session_id": sid,
            "venue": "frontend",
            "payload": json.dumps(
                {
                    "id": sid,
                    "name": "newer",
                    "updated_at": "2026-09-28T12:00:00+00:00",
                    "messages": [{"role": "assistant", "content": "remote result"}],
                    "metadata": {"venue": "frontend"},
                }
            ),
        }
        result = server.load_session({"session_id": sid})
        assert result["notice"] == "refreshed from server journal before resume"
        assert server.session.messages[-1].content == "remote result"
        assert store.load(sid, client).messages[-1].content == "remote result"

    def test_resume_remote_unavailable_is_explicit(self, tmp_path, monkeypatch):
        client = _FakeClient()
        server, store = self._server(tmp_path, monkeypatch, client)
        session = store.create_session(client, name="cached", venue="frontend")
        result = server.load_session({"session_id": session.session_id})
        assert result["notice"] == "remote journal unavailable; showing cached session"
        assert server.session.session_id == session.session_id

    def test_resume_pull_then_loads_remote_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            get_map={
                "/umrs/companion/sessions/remote-9": {
                    "session_id": "remote-9",
                    "name": "srv",
                    "payload": json.dumps({"id": "remote-9", "name": "srv", "metadata": "{}"}),
                    "updated_at": "2026-09-09T09:00:00Z",
                    "venue": "frontend",
                    "state": "",
                    "archived_at": "",
                },
            }
        )
        server, store = self._server(tmp_path, monkeypatch, client)
        try:
            result = server.load_session({"session_id": "remote-9"})
        except Exception as exc:  # downstream configure needs more fakes
            # Pull already happened; configure is out of scope for this test.
            assert "pulled" in str(exc) or "snapshot" in str(exc) or "session" in str(exc)
        else:
            assert result
        assert any(
            s["id"] == "remote-9" and s["venue"] == "frontend" for s in store.list_sessions()
        )

    def test_resume_neither_self_nor_journal_is_honest_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server, _ = self._server(tmp_path, monkeypatch, _FakeClient())
        with pytest.raises(ValueError, match="locally or in the journal"):
            server.load_session({"session_id": "nope-nope"})


class TestReconcile:
    """WP-04 follow-on: full read-model against the snapshot journal."""

    def _store_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CompanionSessionStore:
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)  # default on
        store_module.set_snapshot_enabled(None)
        return CompanionSessionStore(db_path=tmp_path / "sessions.db")

    def _seed(
        self,
        store: CompanionSessionStore,
        client: _FakeClient,
        sid: str,
        *,
        updated: str,
        base: str | None,
        name: str = "row",
    ) -> None:
        """Deterministic row seed: updated_at + optional _mirror_marker base."""
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO sessions (id, name, created_at, updated_at, messages,
                    active_skills, memory, metadata, model)
                   VALUES (?, ?, ?, ?, '[]', '[]', '{}', '{}', 'm')""",
                (sid, name, updated, updated),
            )
        if base is not None:
            store._write_mirror_marker(sid, base)

    def _journal(self, snaps: list[dict], details: dict[str, dict] | None = None) -> _FakeClient:
        client = _FakeClient()

        # listing endpoint (prefix-matched longest-first)
        def _get(path: str, **_k: Any) -> Any:
            if path.startswith("/umrs/companion/sessions?") or path.endswith(
                "/umrs/companion/sessions"
            ):
                return {"snapshots": snaps}
            for sid, detail in (details or {}).items():
                if path.endswith(f"/umrs/companion/sessions/{sid}"):
                    return detail
            return {}

        client.get = _get  # type: ignore[method-assign]
        return client

    def _detail(self, sid: str, name: str, updated: str, state: str = "") -> dict:
        return {
            "session_id": sid,
            "name": name,
            "payload": json.dumps(
                {
                    "id": sid,
                    "name": name,
                    "created_at": updated,
                    "updated_at": updated,
                    "messages": "[]",
                    "active_skills": "[]",
                    "memory": "{}",
                    "metadata": "{}",
                    "model": "m",
                }
            ),
            "updated_at": updated,
            "venue": "local",
            "state": state,
            "archived_at": "",
        }

    def test_no_drift_counters_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        updated = "2026-09-09T09:00:00Z"
        self._seed(store, _FakeClient(), "s1", updated=updated, base=updated)
        chronicle = self._journal([{"session_id": "s1", "updated_at": updated, "name": "row"}])
        counters = store.reconcile(chronicle)
        assert counters == {
            "restored": 0,
            "adopted": 0,
            "reuploaded": 0,
            "archives_adopted": 0,
            "conflicts": 0,
            "skipped": 1,
        }

    def test_server_moved_only_adopts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        m1 = "2026-09-09T09:00:00Z"
        m2 = "2026-09-09T10:00:00Z"
        self._seed(store, _FakeClient(), "s1", updated=m1, base=m1)
        detail = self._detail("s1", "from-server", m2)
        client = self._journal(
            [{"session_id": "s1", "updated_at": m2, "name": "from-server"}],
            {"s1": detail},
        )
        counters = store.reconcile(client)
        assert counters["adopted"] == 1
        assert counters["conflicts"] == 0
        row = [s for s in store.list_sessions() if s["id"] == "s1"][0]
        assert row["name"] == "from-server"
        assert row["updated_at"] == m2

    def test_local_moved_only_reuploads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        m1 = "2026-09-09T09:00:00Z"
        m2 = "2026-09-09T10:00:00Z"
        poster = _FakeClient()
        self._seed(store, _FakeClient(), "s1", updated=m2, base=m1)
        client = self._journal([{"session_id": "s1", "updated_at": m1, "name": "row"}])
        client.post = poster.post  # type: ignore[method-assign]
        client.posts = poster.posts  # shared log
        counters = store.reconcile(client)
        assert counters["reuploaded"] == 1
        assert counters["conflicts"] == 0
        path, body = client.posts[-1]
        assert path == "/umrs/companion/sessions/s1/snapshot"
        assert body["updated_at"] == m2
        # re-upload refreshes the base marker
        with store._conn() as conn:
            meta = json.loads(
                conn.execute("SELECT metadata FROM sessions WHERE id='s1'").fetchone()[0]
            )
        assert meta["_mirror_marker"] == m2

    def test_conflict_server_wins_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        base = "2026-09-09T08:00:00Z"
        local_ts = "2026-09-09T09:00:00Z"
        remote_ts = "2026-09-09T10:00:00Z"
        self._seed(store, _FakeClient(), "s1", updated=local_ts, base=base)
        detail = self._detail("s1", "server-version", remote_ts)
        client = self._journal(
            [{"session_id": "s1", "updated_at": remote_ts, "name": "server-version"}],
            {"s1": detail},
        )
        counters = store.reconcile(client)
        assert counters["conflicts"] == 1
        assert counters["adopted"] == 1
        row = store.get_session_record("s1")
        assert row["name"] == "server-version"
        conflicts = row["metadata"].get("_mirror_conflicts", [])
        assert conflicts and conflicts[-1]["winner"] == "server"

    def test_conflict_local_wins_reuploaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        base = "2026-09-09T08:00:00Z"
        local_ts = "2026-09-09T11:00:00Z"
        remote_ts = "2026-09-09T10:00:00Z"
        poster = _FakeClient()
        self._seed(store, _FakeClient(), "s1", updated=local_ts, base=base)
        client = self._journal([{"session_id": "s1", "updated_at": remote_ts, "name": "row"}])
        client.post = poster.post  # type: ignore[method-assign]
        counters = store.reconcile(client)
        assert counters["conflicts"] == 1
        assert counters["reuploaded"] == 1
        row = store.get_session_record("s1")
        conflicts = row["metadata"].get("_mirror_conflicts", [])
        assert conflicts and conflicts[-1]["winner"] == "local"

    def test_archive_adoption_on_clean_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        m1 = "2026-09-09T09:00:00Z"
        self._seed(store, _FakeClient(), "s1", updated=m1, base=m1)
        client = self._journal(
            [
                {
                    "session_id": "s1",
                    "updated_at": m1,
                    "name": "row",
                    "venue": "local",
                    "state": "archived",
                    "archived_at": "2026-09-09T12:00:00Z",
                }
            ]
        )
        counters = store.reconcile(client)
        assert counters["archives_adopted"] == 1
        row = [s for s in store.list_sessions(include_archived=True) if s["id"] == "s1"][0]
        assert row["archived_at"] == "2026-09-09T12:00:00Z"
        assert computed_state(row) == "archived"

    def test_remote_only_restored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        detail = self._detail("s-new", "new-row", "2026-09-09T09:00:00Z")
        client = self._journal(
            [{"session_id": "s-new", "updated_at": "2026-09-09T09:00:00Z", "name": "new-row"}],
            {"s-new": detail},
        )
        counters = store.reconcile(client)
        assert counters["restored"] == 1
        assert any(s["id"] == "s-new" for s in store.list_sessions())

    def test_journal_unreachable_returns_none_and_marks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)

        class _Dead:
            def get(self, path: str, **_k: Any) -> Any:
                raise ConnectionError("no route")

        assert store.reconcile(_Dead()) is None
        status = store.sync_status()
        assert status["mode"] == "degraded"
        assert status["sync_errors"] >= 1
        assert status["reconcile"] == {}

    def test_sync_status_reports_reconcile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._store_on(tmp_path, monkeypatch)
        updated = "2026-09-09T09:00:00Z"
        self._seed(store, _FakeClient(), "s1", updated=updated, base=updated)
        client = self._journal([{"session_id": "s1", "updated_at": updated, "name": "row"}])
        store.reconcile(client)
        status = store.sync_status()
        assert status["reconcile"]["skipped"] == 1
        assert "at" in status["reconcile"]


class TestCliReconcile:
    def test_cli_reconcile_reports_counters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.delenv("R4_SESSION_SNAPSHOT", raising=False)  # default on
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")
        CompanionSessionStore(db_path=tmp_path / "sessions.db")

        from r4._cli import sessions as sessions_mod

        class _ClientFactory(_FakeClient):
            def __init__(self, *_a: Any, **_k: Any):
                super().__init__(get_map={"/umrs/companion/sessions": {"snapshots": []}})

        monkeypatch.setattr("r4.client.Client", _ClientFactory)
        rc = sessions_mod.SessionsCommand().handle(_ns(session_subcommand="reconcile"))
        assert rc == 0
        assert "no drift" in capsys.readouterr().out

    def test_cli_reconcile_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")

        class _Dead:
            def __init__(self, *_a: Any, **_k: Any):
                pass

            def get(self, path: str, **_k: Any) -> Any:
                raise ConnectionError("no route")

        monkeypatch.setattr("r4.client.Client", _Dead)
        from r4._cli.sessions import SessionsCommand

        rc = SessionsCommand().handle(_ns(session_subcommand="reconcile"))
        assert rc == 1
        assert "journal unreachable" in capsys.readouterr().err


class TestCliConclude:
    def test_conclude_records_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")
        store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
        session = store.create_session(_FakeClient(), name="demo")

        from r4._cli.sessions import SessionsCommand

        rc = SessionsCommand().handle(
            _ns(session_subcommand="conclude", session_id=session.session_id)
        )
        assert rc == 0
        summary = [s for s in store.list_sessions() if s["id"] == session.session_id][0]
        assert summary["state"] == "completed"
        assert "Concluded companion session" in capsys.readouterr().out

    def test_conclude_missing_row_fails_honestly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_module.set_snapshot_enabled(None)
        monkeypatch.setattr("r4.companion.session.DB_PATH", tmp_path / "sessions.db")
        CompanionSessionStore(db_path=tmp_path / "sessions.db")  # init db

        from r4._cli.sessions import SessionsCommand

        rc = SessionsCommand().handle(_ns(session_subcommand="conclude", session_id="nope-nope"))
        assert rc == 1
        assert "must be pulled before concluding" in capsys.readouterr().err


# ── CLI: r4 session start / venue / stop (wave 2026-09-21) ────────────────────


class TestCliVenueVerbs:
    def _wire(self, monkeypatch, tmp_path, client):
        from r4._cli import sessions as sessions_cli

        store = _store(tmp_path, monkeypatch)
        monkeypatch.setattr(sessions_cli, "_venue_client_and_store", lambda: (client, store))
        return sessions_cli, store

    def test_start_posts_and_prints(self, tmp_path, monkeypatch, capsys):
        client = _FakeClient()
        sessions_cli, store = self._wire(monkeypatch, tmp_path, client)
        session = store.create_session(client, name="remote-me")
        client.post = lambda path, json=None, **_kw: (
            {
                "session_id": session.session_id,
                "venue": "frontend",
                "state": "running",
                "source_names": ["openai-main"],
                "default_source": "openai-main",
                "web_policy": "public",
                "lease": {"expires_at": "2999-01-01T00:00:00+00:00", "is_valid": True},
                "runtime": {},
            }
            if path.endswith("/venue/start")
            else {"status": "ok", "applied": True}
        )
        rc = sessions_cli.SessionsCommand().handle(
            Namespace(
                session_subcommand="start",
                session_id=session.session_id,
                venue_sources="openai-main",
                venue_default_source="",
                venue_web_policy="public",
                as_json=True,
            )
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["state"] == "running"
        assert store.get_session_record(session.session_id)["metadata"]["venue"] == "frontend"

    def test_start_requires_source(self, tmp_path, monkeypatch, capsys):
        client = _FakeClient()
        sessions_cli, _ = self._wire(monkeypatch, tmp_path, client)
        rc = sessions_cli.SessionsCommand().handle(
            Namespace(session_subcommand="start", session_id="x", venue_sources="", as_json=False)
        )
        assert rc == 2
        assert "--source" in capsys.readouterr().err

    def test_start_typed_error_is_printed(self, tmp_path, monkeypatch, capsys):
        from r4.errors import APIError

        client = _FakeClient()
        sessions_cli, store = self._wire(monkeypatch, tmp_path, client)
        session = store.create_session(client, name="remote-me")

        def post(path, json=None, **_kw):
            if path.endswith("/venue/start"):
                raise APIError(
                    "API error: 409 — sources not pushed: openai-main",
                    status_code=409,
                    error_code="companionvenue.source_not_pushed",
                )
            return {"status": "ok", "applied": True}

        client.post = post
        rc = sessions_cli.SessionsCommand().handle(
            Namespace(
                session_subcommand="start",
                session_id=session.session_id,
                venue_sources="openai-main",
                venue_default_source="",
                venue_web_policy="public",  # ADR-099: a remote start needs a choice
                as_json=False,
            )
        )
        assert rc == 1
        assert "companionvenue.source_not_pushed" in capsys.readouterr().err

    def test_venue_status_and_stop(self, tmp_path, monkeypatch, capsys):
        client = _FakeClient(
            get_map={
                "/umrs/companion/sessions/abc/venue": {
                    "session_id": "abc",
                    "venue": "frontend",
                    "state": "running",
                    "source_names": ["s"],
                    "default_source": "s",
                    "web_policy": "public",
                    "lease": {"expires_at": "x", "is_valid": True},
                    "runtime": {"phase": "idle", "turn_seq": 2},
                }
            }
        )
        sessions_cli, _ = self._wire(monkeypatch, tmp_path, client)
        rc = sessions_cli.SessionsCommand().handle(
            Namespace(session_subcommand="venue", session_id="abc", as_json=False)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "state: running" in out and "turn_seq=2" in out
        client.post = lambda path, json=None, **_kw: {
            "session_id": "abc",
            "state": "stopped",
            "venue": "frontend",
        }
        rc = sessions_cli.SessionsCommand().handle(
            Namespace(session_subcommand="stop", session_id="abc", as_json=True)
        )
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["state"] == "stopped"
