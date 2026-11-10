"""S5: remote child parity uses the S3 inspector and lease-only rail."""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from r4.companion.cancellation import TurnCancelledError
from r4.companion.event_log import EventLog
from r4.companion.session_store import CompanionSessionStore
from r4.companion.subagent_sessions import SubagentSessionRecorder
from r4.companion.ui import rpc_schema
from r4.companion.ui.bridge_server import CompanionBridgeServer, _bridge_rpc_bindings
from r4.errors import APIError
from r4.venue_rail import VenueRailClient, VenueRailError

CHILD = "child-session"
ROOT = "root-session"
PARENT_AGENT = "agent-parent"
AGENT = "agent-child"


def _metadata() -> dict[str, Any]:
    return {
        "session_kind": "subagent",
        "parent_session_id": ROOT,
        "parent_agent_id": PARENT_AGENT,
        "agent_id": AGENT,
        "task": "actual remote child task",
        "system_prompt": "actual remote child prompt",
        "declared_tools": ["file_read"],
        "execution_kind": "async",
        "subagent_status": "running",
        "started_at": "2026-11-10T00:00:00+00:00",
        "ended_at": "",
        "terminal_error": "",
        "follow_up_count": 0,
        "venue": "frontend",
        "event_log_error": "",
    }


def _seed(store: CompanionSessionStore, *, child: str = CHILD) -> None:
    store.save_subagent(
        session_id=child,
        name="remote child",
        model="child-model",
        messages=[
            {"role": "user", "content": "actual remote child task"},
            {"role": "assistant", "content": "actual full child answer"},
        ],
        metadata=_metadata(),
    )


def _event(event_id: int, *, message: str = "") -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": CHILD,
        "event_type": "tool_result",
        "timestamp": f"2026-11-10T00:00:0{event_id}+00:00",
        "message": message or f"event-{event_id}",
        "data": {
            "child_session_id": CHILD,
            "parent_session_id": ROOT,
            "parent_agent_id": PARENT_AGENT,
            "agent_id": AGENT,
            "result": f"actual-result-{event_id}",
        },
        "tool_name": "file_read",
        "tool_status": "success",
        "tool_duration_ms": 2,
        "agent_id": AGENT,
        "parent_agent_id": PARENT_AGENT,
        "task_id": None,
        "progress_pct": None,
        "intent": None,
        "rationale": None,
    }


class _OwnerClient:
    def __init__(self, record: dict[str, Any] | None = None) -> None:
        self.record = record
        self.events = [_event(index) for index in (1, 2, 3)]
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.listing: list[dict[str, Any]] = []

    def get(self, url: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        if "/subagent?" in url:
            query = parse_qs(urlsplit(url).query)
            after = int(query.get("after_id", ["0"])[0])
            limit = int(query.get("limit", ["100"])[0])
            remaining = [row for row in self.events if row["id"] > after]
            page = remaining[:limit]
            return {
                "record": self.record,
                "events": page,
                "next_after_id": page[-1]["id"] if page else after,
                "has_more": len(remaining) > limit,
                "total_events": len(self.events),
                "limit": limit,
            }
        return {"snapshots": self.listing, "count": len(self.listing)}


def _reader(store: CompanionSessionStore, client: Any = None) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._store = store
    server._event_log = EventLog(str(store.db_path))
    server._session = None
    server._client = client
    return server


def _inspect(server: CompanionBridgeServer, **params: Any) -> dict[str, Any]:
    return _bridge_rpc_bindings(server)["inspect_subagent"]({"session_id": CHILD, **params})


def _assert_remote_history(
    first: dict[str, Any], second: dict[str, Any], record: dict[str, Any]
) -> None:
    assert first["session"]["messages"] == record["messages"]
    assert [row["id"] for row in first["events"]] == [1, 2]
    assert [row["id"] for row in second["events"]] == [3]
    assert second["has_more"] is False
    assert rpc_schema.validate_result("inspect_subagent", first) == []


def _assert_remote_read_only(server: Any, active: Any, first: dict[str, Any], owner: Any) -> None:
    assert first["source"] == "remote"
    assert first["remote_available"] is True
    assert first["read_only"] is True
    assert first["event_payload_cap"] == 8000
    assert server._session is active
    server._store.load.assert_not_called()
    server._store.save.assert_not_called()
    assert "/companion/sessions/child-session/subagent?" in owner.calls[0]


def test_owner_remote_read_reuses_s3_projection_pagination_and_generated_contract(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    record = store.get_session_record(CHILD)
    remote_store = CompanionSessionStore(db_path=tmp_path / "empty.db")
    owner = _OwnerClient(record)
    server = _reader(remote_store, owner)
    active = object()
    server._session = active
    load = MagicMock(side_effect=AssertionError("remote inspection constructed a writer"))
    save = MagicMock(side_effect=AssertionError("remote inspection saved a session"))
    remote_store.load = load
    remote_store.save = save

    first = _inspect(server, remote=True, limit=2)
    second = _inspect(server, remote=True, after_id=first["next_after_id"], limit=2)

    _assert_remote_history(first, second, record)
    _assert_remote_read_only(server, active, first, owner)


def test_remote_outage_and_unknown_never_fall_back_to_a_local_or_parent_row(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    owner = _OwnerClient()
    owner.error = RuntimeError("connection refused with private transport details")
    server = _reader(store, owner)

    with pytest.raises(ValueError, match="remote subagent inspection unavailable") as exc:
        _inspect(server, remote=True)
    assert "private transport details" not in str(exc.value)

    owner.error = APIError("subagent snapshot not found", status_code=404)
    with pytest.raises(ValueError, match="remote subagent session not found"):
        _inspect(server, remote=True)


def test_remote_inventory_retains_immediate_lineage_and_truthful_outage(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    owner = _OwnerClient()
    owner.listing = [
        {
            "session_id": CHILD,
            "name": "remote child",
            "updated_at": "2026-11-10T00:01:00+00:00",
            "venue": "frontend",
            "state": "ongoing",
            "archived_at": "",
            "session_kind": "subagent",
            "parent_session_id": ROOT,
            "parent_agent_id": PARENT_AGENT,
            "agent_id": AGENT,
            "subagent_status": "running",
        }
    ]
    server = _reader(store, owner)

    listed = server.list_sessions(
        {"session_kind": "subagent", "parent_session_id": ROOT, "agent_id": AGENT}
    )

    assert listed["remote_available"] is True
    (row,) = listed["sessions"]
    assert (row["sync"], row["parent_session_id"], row["agent_id"]) == (
        "remote-only",
        ROOT,
        AGENT,
    )
    assert row["subagent_status"] == "running"
    owner.error = RuntimeError("outage")
    degraded = server.list_sessions({"session_kind": "subagent"})
    assert degraded["remote_available"] is False
    assert degraded["remote_reason"]
    assert degraded["sessions"] == []


def test_local_child_inventory_and_inspection_make_no_remote_request(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    owner = _OwnerClient()
    owner.error = AssertionError("local-only behavior contacted the remote")
    server = _reader(store, owner)

    local = _inspect(server)
    listed = server.list_sessions({"session_kind": "subagent", "no_remote": True})

    assert local["source"] == "local"
    assert local["remote_available"] is False
    assert len(listed["sessions"]) == 1
    assert listed["remote_available"] is False
    assert listed["remote_reason"] == ""
    assert owner.calls == []


class _RecorderRemote:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def put_snapshot(self, record: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.records.append(json.loads(json.dumps(record)))

    def append_event(self, _child: str, event: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.events.append(json.loads(json.dumps(event)))


def _begin(recorder: SubagentSessionRecorder, *, agent_id: str = AGENT) -> Any:
    return recorder.begin(
        agent_id=agent_id,
        task="actual remote child task",
        system_prompt="actual remote child prompt",
        model="child-model",
        execution_kind="async",
        tools=[{"name": "file_read"}],
    )


def _assert_cancelled_remote_record(remote: _RecorderRemote) -> None:
    assert remote.records[0]["metadata"]["subagent_revision"] == 1
    assert remote.records[-1]["metadata"]["subagent_status"] == "cancelled"
    assert remote.records[-1]["metadata"]["parent_session_id"] == ROOT
    assert remote.records[-1]["metadata"]["agent_id"] == AGENT


def _assert_remote_event_history(remote: _RecorderRemote, child_id: str) -> None:
    assert [row["event_type"] for row in remote.events] == ["session_start", "tool_result", "error"]
    assert remote.events[-1]["data"]["status"] == "cancelled"
    assert all(row["session_id"] == child_id for row in remote.events)
    assert all(row["id"] > 0 for row in remote.events)


def test_recorder_mirrors_actual_child_snapshot_events_and_cancellation(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    remote = _RecorderRemote()
    recorder = SubagentSessionRecorder(store, ROOT, PARENT_AGENT, remote=remote, venue="frontend")

    execution = _begin(recorder)
    execution._record_event(
        type(
            "Event",
            (),
            {
                "event_type": "tool_result",
                "message": "actual result",
                "data": {"name": "file_read", "result": "actual output", "status": "success"},
            },
        )()
    )
    execution.fail(TurnCancelledError("cancelled by owner"))

    _assert_cancelled_remote_record(remote)
    _assert_remote_event_history(remote, execution.child_session_id)


def test_recorder_remote_begin_outage_is_named_and_marks_only_the_local_child_failed(
    tmp_path,
) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    remote = _RecorderRemote()
    remote.error = VenueRailError(0, "rail unreachable: ConnectError")
    recorder = SubagentSessionRecorder(store, ROOT, PARENT_AGENT, remote=remote, venue="frontend")
    parent_save = MagicMock(side_effect=AssertionError("child outage wrote the parent snapshot"))
    store.save = parent_save

    with pytest.raises(VenueRailError, match="rail unreachable"):
        _begin(recorder)

    (row,) = store.list_sessions(session_kind="subagent")
    assert row["subagent_status"] == "failed"
    record = store.get_session_record(row["id"])
    assert "rail unreachable" in record["metadata"]["terminal_error"]
    parent_save.assert_not_called()


def test_nested_remote_recording_keeps_immediate_parent_lineage(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    remote = _RecorderRemote()
    recorder = SubagentSessionRecorder(store, ROOT, PARENT_AGENT, remote=remote, venue="frontend")
    child = _begin(recorder)
    nested = _begin(recorder.nested(child), agent_id="agent-nested")

    assert nested.recorder.parent_session_id == child.child_session_id
    assert remote.records[-1]["metadata"]["parent_session_id"] == child.child_session_id
    assert remote.records[-1]["metadata"]["parent_agent_id"] == AGENT
    assert remote.records[-1]["metadata"]["agent_id"] == "agent-nested"


def test_lease_adapter_rechecks_child_lineage_receipts(tmp_path) -> None:
    module = importlib.import_module("r4.companion.subagent_remote")
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    record = store.get_session_record(CHILD)
    rail = MagicMock()
    rail.put_subagent_snapshot.return_value = {
        "session_id": "some-other-child",
        "parent_session_id": ROOT,
        "agent_id": AGENT,
    }
    adapter = module.VenueSubagentRemote(rail)

    with pytest.raises(VenueRailError, match="identity receipt"):
        adapter.put_snapshot(record)
    rail.put_subagent_snapshot.assert_called_once()
    assert "token" not in rail.put_subagent_snapshot.call_args.args


def _assert_child_paths(seen: list[httpx.Request]) -> None:
    assert seen[0].method == "PUT"
    assert seen[0].url.path == f"/umrs/companion/sessions/{ROOT}/rail/subagents/{CHILD}/snapshot"
    assert seen[1].method == "POST"
    assert seen[1].url.path == f"/umrs/companion/sessions/{ROOT}/rail/subagents/{CHILD}/events"


def _assert_lease_only_requests(seen: list[httpx.Request]) -> None:
    assert all(request.headers["Authorization"] == "Bearer lease-token" for request in seen)
    assert all("x-user-id" not in request.headers for request in seen)
    assert json.loads(seen[1].content)["source_event_id"] == 4


def test_rail_client_child_paths_are_parent_anchored_and_lease_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"session_id": CHILD, "applied": True})

    rail = VenueRailClient(
        "http://egress:8081", ROOT, "lease-token", transport=httpx.MockTransport(handler)
    )
    record = {"id": CHILD, "metadata": _metadata()}
    rail.put_subagent_snapshot(CHILD, payload=json.dumps(record))
    rail.append_subagent_event(CHILD, source_event_id=4, event=_event(4))

    _assert_child_paths(seen)
    _assert_lease_only_requests(seen)


def test_remote_event_strings_are_redacted_and_bounded_again_at_bridge_boundary(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    owner = _OwnerClient(store.get_session_record(CHILD))
    owner.events = [
        _event(
            1,
            message="m" * 9000,
        )
    ]
    owner.events[0]["data"]["result"] = "r" * 9000
    owner.events[0]["data"]["nested"] = ["Bearer abcdefghijklmnopqrstuvwxyz"]
    server = _reader(CompanionSessionStore(db_path=tmp_path / "empty.db"), owner)

    result = _inspect(server, remote=True)

    assert len(result["events"][0]["message"]) < 8100
    assert "[+1000 chars]" in result["events"][0]["message"]
    assert len(result["events"][0]["data"]["result"]) < 8100
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in str(result)
    assert result["event_payloads_may_be_abridged"] is True


def test_remote_replay_uses_the_local_cursor_validation_and_page_cap(tmp_path) -> None:
    store = CompanionSessionStore(db_path=tmp_path / "children.db")
    _seed(store)
    owner = _OwnerClient(store.get_session_record(CHILD))
    server = _reader(CompanionSessionStore(db_path=tmp_path / "empty.db"), owner)

    smallest = _inspect(server, remote=True, limit=0)
    largest = _inspect(server, remote=True, limit=10_000)
    with pytest.raises(ValueError, match="after_id"):
        _inspect(server, remote=True, after_id=-1)

    assert smallest["limit"] == 1
    assert largest["limit"] == 200
