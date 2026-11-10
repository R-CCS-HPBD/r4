"""S3: read-only durable subagent inspection through the existing bridge."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.event_log import EventLog
from r4.companion.session_store import CompanionSessionStore
from r4.companion.ui import rpc_schema
from r4.companion.ui.bridge_server import CompanionBridgeServer, _bridge_rpc_bindings
from r4.companion.ui.rpc_concurrency import RpcClass, rpc_class, serves_concurrently

CHILD_ID = "child-session"
PARENT_ID = "parent-session"
AGENT_ID = "agent-child"


def _metadata() -> dict[str, Any]:
    return {
        "session_kind": "subagent",
        "parent_session_id": PARENT_ID,
        "parent_agent_id": "agent-parent",
        "agent_id": AGENT_ID,
        "task": "inspect the actual task",
        "system_prompt": "the verbatim child system prompt",
        "declared_tools": ["file_read"],
        "execution_kind": "async",
        "subagent_status": "completed",
        "started_at": "2026-11-10T00:00:00+00:00",
        "ended_at": "2026-11-10T00:01:00+00:00",
        "terminal_error": "",
        "follow_up_count": 1,
        "venue": "local",
        "event_log_error": "",
    }


def _messages() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "inspect the actual task"},
        {"role": "user", "content": "a real follow-up", "metadata": {"subagent_turn": 1}},
        {"role": "assistant", "content": "full child reply " * 1000},
    ]


def _child(store: CompanionSessionStore) -> None:
    store.save_subagent(
        session_id=CHILD_ID,
        name="readable child",
        model="child-model",
        messages=_messages(),
        metadata=_metadata(),
    )


def _reader(store: CompanionSessionStore) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._store = store
    server._event_log = EventLog(str(store.db_path))
    server._session = None
    return server


def _inspect(server: CompanionBridgeServer, **params: Any) -> dict[str, Any]:
    request = {"session_id": CHILD_ID, **params}
    return _bridge_rpc_bindings(server)["inspect_subagent"](request)


def test_child_inventory_is_opt_in_and_preserves_ordinary_exclusions(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    server = _reader(store)

    assert store.list_sessions() == []
    listed = server.list_sessions({"session_kind": "subagent"})

    assert listed["remote_available"] is False
    assert len(listed["sessions"]) == 1
    row = listed["sessions"][0]
    lineage = {
        "id": CHILD_ID,
        "session_kind": "subagent",
        "parent_session_id": PARENT_ID,
        "parent_agent_id": "agent-parent",
        "agent_id": AGENT_ID,
        "subagent_status": "completed",
    }
    assert {key: row[key] for key in lineage} == lineage


def test_child_inventory_filters_by_immediate_lineage(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    server = _reader(store)
    params = {
        "session_kind": "subagent",
        "parent_session_id": PARENT_ID,
        "agent_id": AGENT_ID,
        "no_remote": True,
    }

    assert len(server.list_sessions(params)["sessions"]) == 1
    assert server.list_sessions({**params, "parent_session_id": "other"})["sessions"] == []
    assert server.list_sessions({**params, "agent_id": "other"})["sessions"] == []


def test_inspection_returns_record_and_paginated_actual_history(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    for index in range(3):
        store_event = EventLog(str(store.db_path)).append(
            session_id=CHILD_ID,
            event_type="tool_result" if index else "tool_call",
            message=f"event-{index}",
            data={"index": index, "parent_session_id": PARENT_ID},
            agent_id=AGENT_ID,
        )
        assert store_event > 0
    server = _reader(store)

    first = _inspect(server, limit=2)
    second = _inspect(server, after_id=first["next_after_id"], limit=2)

    expected_record = {
        "parent_session_id": PARENT_ID,
        "parent_agent_id": "agent-parent",
        "agent_id": AGENT_ID,
        "task": "inspect the actual task",
        "system_prompt": "the verbatim child system prompt",
        "model": "child-model",
        "declared_tools": ["file_read"],
        "execution_kind": "async",
        "status": "completed",
    }
    actual_record = {key: first["session"][key] for key in expected_record}
    assert (actual_record, first["read_only"], first["session"]["messages"]) == (
        expected_record,
        True,
        _messages(),
    )
    event_windows = (
        [row["message"] for row in first["events"]],
        first["has_more"],
        first["total_events"],
        [row["message"] for row in second["events"]],
        second["has_more"],
    )
    assert event_windows == (["event-0", "event-1"], True, 3, ["event-2"], False)


def test_inspection_never_loads_or_switches_the_active_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    server = _reader(store)
    active = object()
    server._session = active
    load = MagicMock(side_effect=AssertionError("inspection constructed a writer"))
    save = MagicMock(side_effect=AssertionError("inspection saved a session"))
    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(store, "save", save)

    before = store.get_session_record(CHILD_ID)
    result = _inspect(server)
    after = store.get_session_record(CHILD_ID)

    assert server._session is active
    assert after == before
    load.assert_not_called()
    save.assert_not_called()
    assert result["session"]["id"] == CHILD_ID


@pytest.mark.parametrize("session_id", ["missing", "ordinary"])
def test_inspection_refuses_unknown_and_nonchild_rows(tmp_path, session_id: str) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    if session_id == "ordinary":
        client = MagicMock()
        client._config.model = "parent-model"
        store.create_session(client, name="ordinary")
        session_id = next(iter(store.list_sessions()))["id"]
    server = _reader(store)

    with pytest.raises(ValueError, match="subagent session"):
        _inspect(server, session_id=session_id)


def test_event_payloads_are_bounded_and_redacted_at_the_inspector_boundary(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    EventLog(str(store.db_path)).append(
        session_id=CHILD_ID,
        event_type="tool_result",
        message="m" * 9000,
        data={
            "result": "r" * 9000,
            "api_key": "opaque-credential-value",
            "nested": ["Bearer abcdefghijklmnopqrstuvwxyz"],
        },
    )

    result = _inspect(server=_reader(store))

    assert result["event_payload_cap"] == 8000
    assert result["event_payloads_may_be_abridged"] is True
    event = result["events"][0]
    assert len(event["message"]) < 8100
    assert "[+1000 chars]" in event["message"]
    assert len(event["data"]["result"]) < 8100
    assert "[+1000 chars]" in event["data"]["result"]
    assert "opaque-credential-value" not in str(result)
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in str(result)


def test_inspection_is_a_read_rpc_with_a_valid_generated_contract(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)

    result = _inspect(_reader(store))

    assert rpc_class("inspect_subagent") is RpcClass.READ
    assert serves_concurrently("inspect_subagent") is True
    assert rpc_schema.validate_result("inspect_subagent", result) == []


def test_running_child_can_be_read_during_real_concurrent_writes(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    server = _reader(store)
    start = threading.Barrier(2)
    writer_errors: list[BaseException] = []

    def write_history() -> None:
        try:
            start.wait()
            for index in range(20):
                metadata = _metadata()
                metadata["subagent_status"] = "running"
                store.save_subagent(
                    session_id=CHILD_ID,
                    name="readable child",
                    model="child-model",
                    messages=_messages(),
                    metadata=metadata,
                )
                EventLog(str(store.db_path)).append(
                    session_id=CHILD_ID, event_type="tool_call", message=str(index)
                )
        except BaseException as exc:  # noqa: BLE001 — surfaced by the assertion below
            writer_errors.append(exc)

    writer = threading.Thread(target=write_history)
    writer.start()
    start.wait()
    snapshots = [_inspect(server, after_id=index, limit=5) for index in range(10)]
    writer.join(timeout=10)

    assert writer_errors == []
    assert not writer.is_alive()
    assert all(snapshot["session"]["id"] == CHILD_ID for snapshot in snapshots)
    assert len(_inspect(server, limit=200)["events"]) == 20


def test_event_page_rejects_bad_cursors_and_the_bridge_bounds_page_size(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _child(store)
    log = EventLog(str(store.db_path))
    log.append(session_id=CHILD_ID, event_type="tool_call")

    with pytest.raises(ValueError, match="after_id"):
        log.page(CHILD_ID, after_id=-1)
    assert _inspect(_reader(store), limit=0)["limit"] == 1
    assert _inspect(_reader(store), limit=10_000)["limit"] == 200
