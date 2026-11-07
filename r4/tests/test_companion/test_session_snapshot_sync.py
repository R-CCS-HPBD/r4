"""Session snapshot mirror + rebuild tests (ADR-036 F2 first leg, WP-04)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from r4.companion import session_store as store_mod
from r4.companion.session_store import CompanionSessionStore


class _FakeClient:
    def __init__(self) -> None:
        self.posted: list[tuple[str, dict]] = []
        self.fail_next_post: Exception | None = None

    def post(self, path, json=None, **kwargs):  # noqa: ARG002
        if self.fail_next_post is not None:
            err = self.fail_next_post
            self.fail_next_post = None
            raise err
        self.posted.append((path, json))
        return {"status": "ok", "applied": True}


def _fake_session(client) -> SimpleNamespace:
    session = SimpleNamespace(
        session_id="sess-9",
        name="Syncdemo",
        created_at="2026-09-06T10:00:00Z",
        updated_at="2026-09-06T10:00:01Z",
        messages=[],
        metadata={},
        client=client,
        skills=SimpleNamespace(_active=set()),
        context=SimpleNamespace(_memory={}),
        agent=SimpleNamespace(model="m1"),
    )
    session._sync_metadata_usage_state = lambda: None
    return session


@pytest.fixture()
def tmp_store(tmp_path: Path) -> CompanionSessionStore:
    store_mod.set_snapshot_enabled(None)  # env-driven (on unless 'off')
    return CompanionSessionStore(db_path=tmp_path / "sessions.db")


class TestMirror:
    def test_save_mirrors_to_server(self, tmp_store: CompanionSessionStore) -> None:
        client = _FakeClient()
        tmp_store.save(_fake_session(client))
        assert client.posted == [
            (
                "/umrs/companion/sessions/sess-9/snapshot",
                {
                    "name": "Syncdemo",
                    "updated_at": "2026-09-06T10:00:01Z",
                    "payload": client.posted[0][1]["payload"],
                    # Venue registry (plan 2026-09-09): mirror body advertises
                    # venue ('local' default — client-created) + recorded state.
                    "venue": "local",
                    "state": "",
                },
            )
        ]
        assert tmp_store.sync_status()["mode"] == "mirrored"
        payload = json.loads(client.posted[0][1]["payload"])
        assert payload["id"] == "sess-9"
        assert payload["model"] == "m1"

    def test_mirror_failure_never_breaks_save(self, tmp_store: CompanionSessionStore) -> None:
        client = _FakeClient()
        client.fail_next_post = RuntimeError("no route")
        tmp_store.save(_fake_session(client))
        assert tmp_store.sync_status()["sync_errors"] == 1
        assert tmp_store.sync_status()["mode"] == "degraded"
        assert tmp_store.load("sess-9", client) is not None  # local row intact

    def test_snapshot_disabled_stays_local(
        self, tmp_store: CompanionSessionStore, monkeypatch
    ) -> None:
        monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
        store_mod.set_snapshot_enabled(None)  # force env re-read
        client = _FakeClient()
        tmp_store.save(_fake_session(client))
        assert client.posted == []
        assert tmp_store.sync_status()["mode"] == "local"


class TestRebuild:
    def test_rebuild_restores_absent_rows(self, tmp_store: CompanionSessionStore) -> None:
        client = _FakeClient()
        payload = json.dumps(
            {
                "id": "sess-9",
                "name": "Syncdemo",
                "created_at": "2026-09-06T10:00:00Z",
                "updated_at": "2026-09-06T10:00:01Z",
                "messages": "[]",
                "active_skills": "[]",
                "memory": "{}",
                "metadata": "{}",
                "model": "m1",
            }
        )

        def synced_get(path, **kwargs):  # noqa: ARG001
            if path == "/umrs/companion/sessions":
                return {"snapshots": [{"session_id": "sess-9", "name": "Syncdemo"}]}
            return {
                "session_id": "sess-9",
                "name": "Syncdemo",
                "payload": payload,
                "updated_at": "2026-09-06T10:00:01Z",
            }

        client.get = synced_get  # type: ignore[method-assign]
        rebuilt = tmp_store.rebuild_from_remote(client)
        assert rebuilt == 1
        assert tmp_store.load("sess-9", client) is not None

    def test_rebuild_never_overwrites_local_row(self, tmp_store: CompanionSessionStore) -> None:
        client = _FakeClient()
        tmp_store.save(_fake_session(client))
        payload = json.dumps({"id": "sess-9", "name": "remote-preferred", "metadata": "{}"})

        def synced_get(path, **kwargs):  # noqa: ARG001
            if path == "/umrs/companion/sessions":
                return {"snapshots": [{"session_id": "sess-9", "name": "x"}]}
            return {"session_id": "sess-9", "name": "remote-preferred", "payload": payload}

        client.get = synced_get  # type: ignore[method-assign]
        rebuilt = tmp_store.rebuild_from_remote(client)
        assert rebuilt == 0

    def test_rebuild_is_noop_when_enabled_off(self, tmp_store: CompanionSessionStore) -> None:
        store_mod.set_snapshot_enabled(False)
        client = _FakeClient()
        assert tmp_store.rebuild_from_remote(client) == 0


class TestDeleteTombstones:
    """WP-04 G-P6-1 round 2: mirrored deletes never resurrect across reconnect."""

    def _client_with_journal(self) -> _FakeClient:
        client = _FakeClient()
        client.journal: dict[str, dict] = {}

        def _get(path, **kwargs):  # noqa: ARG001
            if path == "/umrs/companion/sessions":
                return {
                    "snapshots": [
                        {
                            "session_id": sid,
                            "name": row["name"],
                            "updated_at": row.get("updated_at", ""),
                        }
                        for sid, row in client.journal.items()
                    ]
                }
            sid = path.rsplit("/", 1)[-1]
            row = client.journal.get(sid)
            if row is None:
                raise RuntimeError("404 snapshot not found")
            return row

        def _delete(path, **kwargs):  # noqa: ARG001
            sid = path.rsplit("/", 1)[-1]
            if sid not in client.journal:
                raise RuntimeError("404 snapshot not found")
            del client.journal[sid]
            return {"status": "ok", "deleted": True}

        client.get = _get  # type: ignore[method-assign]
        client.delete = _delete  # type: ignore[method-assign]
        return client

    def test_mirrored_delete_uses_journal_delete_no_tombstone(self, tmp_store):
        client = self._client_with_journal()
        tmp_store.save(_fake_session(client))
        # Journal row present (mirror POSTs materialized it here manually).
        client.journal["sess-9"] = {
            "session_id": "sess-9",
            "name": "Syncdemo",
            "payload": json.dumps({"id": "sess-9", "metadata": "{}"}),
        }
        outcome = tmp_store.delete("sess-9", client=client)
        assert outcome == "deleted"
        assert "sess-9" not in client.journal
        assert tmp_store._tombstoned_ids(tmp_store._conn()) == set()

    def test_offline_mirrored_delete_tombstones_and_reconcile_never_restores(self, tmp_store):
        client = self._client_with_journal()
        tmp_store.save(_fake_session(client))
        client.journal["sess-9"] = {
            "session_id": "sess-9",
            "name": "Syncdemo",
            "updated_at": "2026-09-06T10:00:01Z",
            "payload": json.dumps({"id": "sess-9", "metadata": "{}"}),
        }
        failing = _FakeClient()  # no delete verb wired here → mirror delete fails
        outcome = tmp_store.delete("sess-9", client=failing)
        assert outcome == "mirror-pending"
        assert "sess-9" in tmp_store._tombstoned_ids(tmp_store._conn())

        counters = tmp_store.reconcile(client)
        # restored counter NOT incremented; the journal delete retry fired and won.
        assert counters.get("restored", 0) == 0
        assert counters.get("tombstones_resolved", 0) == 1
        assert "sess-9" not in client.journal
        assert tmp_store._tombstoned_ids(tmp_store._conn()) == set()
