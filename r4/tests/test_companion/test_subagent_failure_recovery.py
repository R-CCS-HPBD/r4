"""S5 reviewer: failed remote appends cannot lose lifecycle or transcript truth."""

from __future__ import annotations

import json
from typing import Any

from r4.companion.agent import CompanionEventType
from r4.venue_rail import VenueRailError
from test_companion.test_subagent_session_recording import _child, _parent


class RemoteProbe:
    def __init__(
        self,
        *,
        reject_completed_once: bool = False,
        fail_tool_once: bool = False,
        permanent: bool = False,
    ) -> None:
        self.reject_completed_once = reject_completed_once
        self.fail_tool_once = fail_tool_once
        self.permanent = permanent
        self.completed_rejections = 0
        self.tool_rejections = 0
        self.snapshots: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def put_snapshot(self, record: dict[str, Any]) -> None:
        status = str(record["metadata"].get("subagent_status") or "")
        rejected_once = self.reject_completed_once and not self.completed_rejections
        if status == "completed" and (self.permanent or rejected_once):
            self.completed_rejections += 1
            raise VenueRailError(503, "completed snapshot rejected")
        self.snapshots.append(json.loads(json.dumps(record)))

    def append_event(self, _child: str, event: dict[str, Any]) -> None:
        rejected_once = self.fail_tool_once and not self.tool_rejections
        if event["event_type"] == "tool_result" and (self.permanent or rejected_once):
            self.tool_rejections += 1
            raise VenueRailError(503, "tool result append rejected")
        self.events.append(json.loads(json.dumps(event)))


def _bind_remote(parent: Any, remote: RemoteProbe) -> None:
    parent.agent._subagent_sessions.remote = remote


def _snapshot(remote: RemoteProbe) -> dict[str, Any]:
    assert remote.snapshots
    return remote.snapshots[-1]


def _child_record(db_path: Any, child_session_id: str) -> dict[str, Any]:
    from r4.companion.session_store import CompanionSessionStore

    return CompanionSessionStore(db_path=db_path).get_session_record(child_session_id)


def _result_data(events: list[Any]) -> dict[str, Any]:
    results = [
        event.data for event in events if event.event_type is CompanionEventType.AGENT_RESULT
    ]
    assert results
    return results[-1]


def _respond_with_tool_result(child: Any, answer: str) -> str:
    for index in range(2):
        child._emit(
            CompanionEventType.TOOL_RESULT,
            f"tool result {index} must not disappear or overtake",
            data={
                "name": "file_read",
                "result": f"actual result {index}",
                "status": "success",
            },
        )
    return answer


def _assert_replayed_events(remote: RemoteProbe) -> None:
    assert remote.events[0]["event_type"] == "session_start"
    assert [event["event_type"] for event in remote.events[1:]] == [
        "tool_result",
        "tool_result",
        "session_end",
    ]
    event_ids = [event["id"] for event in remote.events]
    assert event_ids == sorted(event_ids)


def _assert_clean_completion(answer: str, record: dict[str, Any], events: list[Any]) -> None:
    assert answer == "answer"
    assert record["metadata"]["subagent_status"] == "completed"
    assert not record["metadata"].get("remote_degraded")
    assert _result_data(events)["status"] == "completed"


def _assert_degraded_record(record: dict[str, Any]) -> None:
    assert record["metadata"]["subagent_status"] == "completed"
    assert record["metadata"]["remote_degraded"] is True
    assert record["metadata"]["remote_sync_error"]


def _assert_degraded_result(result: dict[str, Any]) -> None:
    assert result["status"] == "completed"
    assert result["remote_degraded"] is True
    assert result["remote_sync_error"]


def test_tool_append_failure_is_retried_before_clean_completion(tmp_path, monkeypatch) -> None:
    remote = RemoteProbe(fail_tool_once=True)
    store, parent, events = _parent(tmp_path / "sessions.db")
    _bind_remote(parent, remote)
    child = _child(parent.client, lambda *_args, **_kwargs: "answer", monkeypatch)
    monkeypatch.setattr(
        child, "send", lambda *_args, **_kwargs: _respond_with_tool_result(child, "answer")
    )
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)

    answer = parent.agent.spawn_subagent("actual child task")
    record = _snapshot(remote)

    _assert_replayed_events(remote)
    _assert_clean_completion(answer, record, events)
    assert _child_record(store.db_path, record["id"])["messages"][-1]["content"] == "answer"


def test_sync_completed_snapshot_rejection_is_replayed_and_result_emitted(
    tmp_path, monkeypatch
) -> None:
    remote = RemoteProbe(reject_completed_once=True)
    _, parent, events = _parent(tmp_path / "sessions.db")
    _bind_remote(parent, remote)
    child = _child(parent.client, lambda *_args, **_kwargs: "answer", monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)

    answer = parent.agent.spawn_subagent("actual child task")
    record = _snapshot(remote)

    assert answer == "answer"
    assert remote.completed_rejections == 1
    assert record["metadata"]["subagent_status"] == "completed"
    assert _result_data(events)["status"] == "completed"


def test_async_completed_snapshot_rejection_preserves_pool_result_and_parent_notice(
    tmp_path, monkeypatch
) -> None:
    remote = RemoteProbe(reject_completed_once=True)
    _, parent, events = _parent(tmp_path / "sessions.db")
    _bind_remote(parent, remote)
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(
            parent.client, lambda *_args, **_kwargs: "async answer", monkeypatch
        ),
    )

    parent.agent.spawn_subagent_async("async child task")
    assert parent.agent._async_pool.wait_idle()
    record = next(iter(parent.agent._async_pool._records.values()))
    snapshot = _snapshot(remote)

    assert record.status == "completed"
    assert record.result == "async answer"
    assert remote.completed_rejections == 1
    assert snapshot["metadata"]["subagent_status"] == "completed"
    assert _result_data(events)["status"] == "completed"


def test_unrecoverable_appends_leave_durable_degraded_not_false_failure(
    tmp_path, monkeypatch
) -> None:
    remote = RemoteProbe(permanent=True)
    store, parent, events = _parent(tmp_path / "sessions.db")
    _bind_remote(parent, remote)
    child = _child(parent.client, lambda *_args, **_kwargs: "answer", monkeypatch)
    monkeypatch.setattr(
        child, "send", lambda *_args, **_kwargs: _respond_with_tool_result(child, "answer")
    )
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)

    answer = parent.agent.spawn_subagent("actual child task")
    snapshot = _snapshot(remote)
    child_record = _child_record(store.db_path, snapshot["id"])
    result = _result_data(events)

    assert answer == "answer"
    _assert_degraded_record(child_record)
    _assert_degraded_result(result)


def test_async_terminal_outage_does_not_turn_execution_success_into_pool_failure(
    tmp_path, monkeypatch
) -> None:
    remote = RemoteProbe(permanent=True)
    store, parent, events = _parent(tmp_path / "sessions.db")
    _bind_remote(parent, remote)
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(
            parent.client, lambda *_args, **_kwargs: "async answer", monkeypatch
        ),
    )

    parent.agent.spawn_subagent_async("async child task")
    assert parent.agent._async_pool.wait_idle()
    record = next(iter(parent.agent._async_pool._records.values()))
    snapshot = _snapshot(remote)
    result = _result_data(events)

    assert record.status == "completed"
    assert record.result == "async answer"
    assert snapshot["metadata"]["subagent_status"] == "running"
    child_record = _child_record(store.db_path, snapshot["id"])
    _assert_degraded_record(child_record)
    _assert_degraded_result(result)
