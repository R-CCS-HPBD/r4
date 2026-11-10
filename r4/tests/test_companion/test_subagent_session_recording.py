"""S1/S2: durable child-session identity, lineage, history and lifecycle."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.cancellation import TurnCancelledError
from r4.companion.context import ContextManager
from r4.companion.event_log import EventLog
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.session_store import CompanionSessionStore
from r4.companion.skills import SkillRegistry


def _client() -> MagicMock:
    client = MagicMock()
    client._config.model = "parent-model"
    return client


def _companion(client: MagicMock) -> ResearchCompanion:
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def _parent(db_path) -> tuple[CompanionSessionStore, Any, list[Any]]:
    store = CompanionSessionStore(db_path=db_path)
    session = store.create_session(_client(), name="Parent", model="parent-model")
    events: list[Any] = []
    session.agent.on_event(events.append)
    return store, session, events


def _child(
    client: MagicMock,
    send: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> ResearchCompanion:
    child = _companion(client)
    monkeypatch.setattr(child, "send", send)
    return child


def _children_of(events: list[Any], kind: CompanionEventType) -> list[dict[str, Any]]:
    return [event.data for event in events if event.event_type is kind]


def _record(db_path, session_id: str) -> dict[str, Any]:
    found = CompanionSessionStore(db_path=db_path).get_session_record(session_id)
    assert found is not None
    return found


def _subagent_inventory(db_path) -> list[tuple[str, dict[str, Any]]]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, metadata FROM sessions").fetchall()
    decoded = [(sid, json.loads(raw)) for sid, raw in rows]
    return [(sid, meta) for sid, meta in decoded if meta.get("session_kind") == "subagent"]


def _immediate_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threading.Thread, "start", lambda thread: thread.run())


def _event_log_failure(original, blocked_kind: str):
    def append(**kwargs):
        if kwargs.get("event_type") == blocked_kind:
            raise RuntimeError(f"event log unavailable: {blocked_kind}")
        return original(**kwargs)

    return append


def _execution_append_failure(original, blocked_kind: str):
    def append(kind, message, data, **options):
        if kind == blocked_kind:
            raise RuntimeError(f"event log unavailable: {blocked_kind}")
        return original(kind, message, data, **options)

    return append


def _assert_identity(data: dict[str, Any], parent, agent_id: str) -> None:
    assert data["agent_id"] == agent_id
    assert data["parent_agent_id"] == parent.agent.agent_id
    assert data["parent_session_id"] == parent.session_id
    assert data["child_session_id"]


def _assert_running_before_send(saved: dict[str, Any], parent, task: str) -> None:
    meta = saved["metadata"]
    assert meta["subagent_status"] == "running"
    assert meta["parent_session_id"] == parent.session_id
    assert meta["declared_tools"] == ["file_read"]
    assert saved["messages"][0]["content"] == task


def _assert_saved_async_child(db_path, parent, spawn, results) -> None:
    result = next(row for row in results if row["agent_id"] == spawn["agent_id"])
    _assert_identity(result, parent, spawn["agent_id"])
    saved = _record(db_path, spawn["child_session_id"])
    assert saved["metadata"]["subagent_status"] == "completed"
    expected = spawn["task"]
    assert [m["content"] for m in saved["messages"]] == [expected, f"answer:{expected}"]


def _assert_fast_async_event_order(events: list[Any]) -> None:
    assert [event.event_type for event in events] == [
        CompanionEventType.AGENT_SPAWN,
        CompanionEventType.AGENT_RESULT,
        CompanionEventType.AGENT_SPAWN,
        CompanionEventType.AGENT_RESULT,
    ]


def _assert_nested_lineage(inventory, parent, outer) -> None:
    inner_meta = next(
        meta for _sid, meta in inventory if meta["parent_session_id"] != parent.session_id
    )
    assert inner_meta["parent_session_id"] == outer["child_session_id"]
    assert inner_meta["parent_agent_id"] == outer["agent_id"]


def _assert_sync_completion(saved: dict[str, Any], answer: str) -> None:
    assert saved["metadata"]["system_prompt"] == "verbatim prompt"
    assert saved["metadata"]["subagent_status"] == "completed"
    assert [message["content"] for message in saved["messages"]] == ["inspect this file", answer]


def _assert_sync_event_log(db_path, parent, spawn) -> None:
    logged = EventLog(str(db_path)).query(spawn["child_session_id"], agent_id=spawn["agent_id"])
    assert any(row["event_type"] == "tool_call" for row in logged)
    assert any(row["event_type"] == "tool_result" for row in logged)
    assert EventLog(str(db_path)).count(parent.session_id) == 0


def test_sync_row_precedes_send_and_full_history_survives(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "custom-children.db"
    store, parent, events = _parent(db_path)
    answer = "complete answer " * 200
    tools = [{"name": "file_read", "execute": lambda: "unused"}]
    checked: list[str] = []

    def send(task: str, **kwargs: Any) -> str:
        spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
        running = _record(db_path, spawn["child_session_id"])
        _assert_running_before_send(running, parent, task)
        checked.append("before-send")
        child._emit(
            CompanionEventType.TOOL_CALL,
            "reading",
            data={"name": "file_read", "arguments": {"path": "x"}, "tool_call_id": "call-1"},
        )
        child._emit(
            CompanionEventType.TOOL_RESULT,
            "read",
            data={"name": "file_read", "result": "contents", "tool_call_id": "call-1"},
        )
        return answer

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)

    returned = parent.agent.spawn_subagent(
        "inspect this file", system="verbatim prompt", tools=tools
    )

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    _assert_identity(spawn, parent, spawn["agent_id"])
    _assert_identity(result, parent, spawn["agent_id"])
    saved = _record(db_path, spawn["child_session_id"])
    assert returned == answer
    assert checked == ["before-send"]
    _assert_sync_completion(saved, answer)
    _assert_sync_event_log(db_path, parent, spawn)


def test_sync_exception_marks_failed_and_logs_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)

    def send(_task: str, **_kwargs: Any) -> str:
        raise RuntimeError("model down")

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    with pytest.raises(RuntimeError, match="model down"):
        parent.agent.spawn_subagent("doomed")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    _assert_identity(result, parent, spawn["agent_id"])
    saved = _record(db_path, spawn["child_session_id"])
    assert result["status"] == "error"
    assert saved["metadata"]["subagent_status"] == "failed"
    assert saved["metadata"]["terminal_error"] == "model down"
    assert saved["metadata"]["ended_at"]
    assert len(saved["messages"]) == 1
    logged = EventLog(str(db_path)).query(spawn["child_session_id"], event_type="error")
    assert "model down" in logged[-1]["message"]


def test_fast_async_results_follow_registration_and_rows_are_distinct(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    seen: list[str] = []

    def make_child(_model: str | None = None) -> ResearchCompanion:
        task_holder: list[str] = []

        def send(task: str, **_kwargs: Any) -> str:
            task_holder.append(task)
            spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[-1]
            saved = _record(db_path, spawn["child_session_id"])
            assert saved["metadata"]["subagent_status"] == "running"
            seen.append(f"work:{task}")
            return f"answer:{task}"

        return _child(parent.client, send, monkeypatch)

    monkeypatch.setattr(parent.agent, "_new_subagent", make_child)
    parent.agent.spawn_subagent_async("first")
    parent.agent.spawn_subagent_async("second")

    spawns = _children_of(events, CompanionEventType.AGENT_SPAWN)
    results = _children_of(events, CompanionEventType.AGENT_RESULT)
    assert len(spawns) == len(results) == 2
    assert len({row["agent_id"] for row in spawns}) == 2
    assert len({row["child_session_id"] for row in spawns}) == 2
    assert seen == ["work:first", "work:second"]
    _assert_fast_async_event_order(events)
    for spawn in spawns:
        _assert_saved_async_child(db_path, parent, spawn, results)


def test_over_cap_refusal_creates_no_row_or_spawn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    parent.agent._async_pool._max = 1
    entered = threading.Event()
    release = threading.Event()

    def send(task: str, **_kwargs: Any) -> str:
        entered.set()
        assert release.wait(3)
        return f"answer:{task}"

    monkeypatch.setattr(
        parent.agent, "_new_subagent", lambda _model=None: _child(parent.client, send, monkeypatch)
    )
    parent.agent.spawn_subagent_async("held")
    assert entered.wait(2)
    response = json.loads(parent.agent.spawn_subagent_async("rejected"))
    assert "cap 1" in response["error"]
    assert len(_subagent_inventory(db_path)) == 1
    assert len(_children_of(events, CompanionEventType.AGENT_SPAWN)) == 1

    release.set()
    assert parent.agent._async_pool.wait_idle()


def test_follow_up_appends_to_one_row_and_keeps_tools(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    tools = [{"name": "file_read"}]
    received_tools: list[Any] = []
    calls = 0

    def send(_task: str, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        received_tools.append(kwargs.get("tools"))
        child._emit(
            CompanionEventType.TOOL_RESULT,
            f"tool-{calls}",
            data={"name": "file_read", "result": f"result-{calls}"},
        )
        return f"answer-{calls}"

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("initial", tools=tools)
    agent_id = next(iter(parent.agent._async_pool._records))
    parent.agent.message_subagent(agent_id, "follow one")
    parent.agent.message_subagent(agent_id, "follow two")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert len(_subagent_inventory(db_path)) == 1
    assert [m["content"] for m in saved["messages"]] == [
        "initial",
        "answer-1",
        "follow one",
        "answer-2",
        "follow two",
        "answer-3",
    ]
    assert received_tools == [tools, tools, tools]
    assert saved["metadata"]["follow_up_count"] == 2
    assert saved["metadata"]["subagent_status"] == "completed"
    logged = EventLog(str(db_path)).query(spawn["child_session_id"], event_type="tool_result")
    assert [row["data"]["result"] for row in logged] == ["result-1", "result-2", "result-3"]


def test_resumed_parent_uses_the_same_custom_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    store, original, _events = _parent(db_path)
    parent = store.load(original.session_id, original.client)
    assert parent is not None
    events: list[Any] = []
    parent.agent.on_event(events.append)
    child = _child(parent.client, lambda _task, **_kw: "resumed answer", monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)

    parent.agent.spawn_subagent("after resume")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert parent.agent.agent_id == original.agent.agent_id
    assert saved["metadata"]["parent_session_id"] == original.session_id
    assert saved["messages"][-1]["content"] == "resumed answer"


def test_default_async_tool_catalog_stays_empty_on_follow_up(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    received: list[Any] = []

    def send(_task: str, **kwargs: Any) -> str:
        received.append(kwargs.get("tools"))
        return "ok"

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("initial")
    agent_id = next(iter(parent.agent._async_pool._records))
    parent.agent.message_subagent(agent_id, "follow")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert received == [None, None]
    assert saved["metadata"]["declared_tools"] is None


def test_cancelled_child_is_durable_cancelled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)

    def send(_task: str, **_kwargs: Any) -> str:
        raise TurnCancelledError()

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("cancel me")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert result["status"] == "cancelled"
    assert saved["metadata"]["subagent_status"] == "cancelled"
    record = next(iter(parent.agent._async_pool._records.values()))
    assert record.status == "cancelled"


def test_nested_lineage_uses_immediate_parent_and_same_db(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)

    def grandchild_send(_task: str, **_kwargs: Any) -> str:
        return "grandchild answer"

    grandchild = _child(parent.client, grandchild_send, monkeypatch)

    def child_send(_task: str, **_kwargs: Any) -> str:
        return child.spawn_subagent("nested", system="nested prompt")

    child = _child(parent.client, child_send, monkeypatch)
    child_events: list[Any] = []
    child.on_event(child_events.append)
    monkeypatch.setattr(child, "_new_subagent", lambda _model=None: grandchild)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    assert parent.agent.spawn_subagent("outer") == "grandchild answer"

    outer = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    inner = _children_of(child_events, CompanionEventType.AGENT_SPAWN)[0]
    assert inner["child_session_id"] != outer["child_session_id"]
    inventory = _subagent_inventory(db_path)
    assert len(inventory) == 2
    _assert_nested_lineage(inventory, parent, outer)


def test_event_log_redacts_and_marks_caps_and_child_rows_are_read_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    store, parent, events = _parent(db_path)
    # Synthetic redaction input; assemble it instead of publishing one key-shaped literal.
    secret = "sk-test-" + "1234567890abcdef" * 2
    capped = "x" * 2400

    def send(_task: str, **_kwargs: Any) -> str:
        child._emit(
            CompanionEventType.TOOL_RESULT,
            capped,
            data={
                "name": "shell_exec",
                "result": capped[:2000],
                "result_truncated": True,
                "result_chars": len(capped),
                "api_key": secret,
            },
        )
        return capped

    child = _child(parent.client, send, monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent("redact")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    logged = EventLog(str(db_path)).query(spawn["child_session_id"], event_type="tool_result")
    assert secret not in json.dumps(logged)
    assert logged[0]["data"]["result_truncated"] is True
    assert logged[0]["data"]["result_chars"] == 2400
    assert logged[0]["data"]["result_preview_cap"] == 2000
    saved = _record(db_path, spawn["child_session_id"])
    assert saved["messages"][-1]["content"] == capped
    assert store.load(spawn["child_session_id"], parent.client) is None
    assert all(row["id"] != spawn["child_session_id"] for row in store.list_sessions())


def test_sync_cancellation_matches_parent_and_durable_status(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)

    def send(_task: str, **_kwargs: Any) -> str:
        raise TurnCancelledError()

    monkeypatch.setattr(
        parent.agent, "_new_subagent", lambda _model=None: _child(parent.client, send, monkeypatch)
    )
    with pytest.raises(TurnCancelledError):
        parent.agent.spawn_subagent("cancel sync")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert result["status"] == "cancelled"
    assert saved["metadata"]["subagent_status"] == "cancelled"


def test_venue_is_resolved_at_begin_time(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    store = CompanionSessionStore(db_path=db_path)
    parent = store.create_session(_client(), name="Remote", venue="frontend")
    events: list[Any] = []
    parent.agent.on_event(events.append)
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(parent.client, lambda _task, **_kw: "ok", monkeypatch),
    )

    parent.agent.spawn_subagent("front")
    parent.metadata["venue"] = "cluster:rikyu"
    parent.agent.spawn_subagent("cluster")

    saved = [
        _record(db_path, row["child_session_id"])
        for row in _children_of(events, CompanionEventType.AGENT_SPAWN)
    ]
    assert [row["venue"] for row in saved] == ["frontend", "cluster:rikyu"]
    assert [row["metadata"]["venue"] for row in saved] == ["frontend", "cluster:rikyu"]


def test_begin_append_failure_never_leaves_running(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    recorder = parent.agent._subagent_sessions
    monkeypatch.setattr(
        recorder.event_log, "append", _event_log_failure(recorder.event_log.append, "session_start")
    )

    with pytest.raises(RuntimeError, match="session_start"):
        parent.agent.spawn_subagent("cannot register")

    inventory = _subagent_inventory(db_path)
    assert len(inventory) == 1
    assert inventory[0][1]["subagent_status"] == "failed"
    assert inventory[0][1]["event_log_error"]
    assert _children_of(events, CompanionEventType.AGENT_SPAWN) == []


def test_terminal_append_failure_preserves_completion_and_parent_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    recorder = parent.agent._subagent_sessions
    monkeypatch.setattr(
        recorder.event_log, "append", _event_log_failure(recorder.event_log.append, "session_end")
    )
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(parent.client, lambda _task, **_kw: "answer", monkeypatch),
    )

    assert parent.agent.spawn_subagent("still complete") == "answer"

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert result["status"] == "completed"
    assert saved["metadata"]["subagent_status"] == "completed"
    assert "session_end" in saved["metadata"]["event_log_error"]


def test_follow_up_append_failure_rolls_back_registration(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    child = _child(parent.client, lambda _task, **_kw: "answer", monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("initial")
    agent_id = next(iter(parent.agent._async_pool._records))
    record = parent.agent._async_pool.get(agent_id)
    execution = record.execution
    before = _record(db_path, execution.child_session_id)
    monkeypatch.setattr(
        execution, "_append", _execution_append_failure(execution._append, "session_resume")
    )

    with pytest.raises(RuntimeError, match="session_resume"):
        parent.agent.message_subagent(agent_id, "not accepted")

    after = _record(db_path, execution.child_session_id)
    assert after["metadata"] == before["metadata"]
    assert after["messages"] == before["messages"]
    assert record.status == "completed"
    assert len(_children_of(events, CompanionEventType.AGENT_SPAWN)) == 1


def test_thread_start_failure_finalizes_spawn(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(parent.client, lambda _task, **_kw: "unused", monkeypatch),
    )
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("start failed")),
    )

    with pytest.raises(RuntimeError, match="start failed"):
        parent.agent.spawn_subagent_async("cannot start")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    result = _children_of(events, CompanionEventType.AGENT_RESULT)[0]
    saved = _record(db_path, spawn["child_session_id"])
    assert result["status"] == "error"
    assert saved["metadata"]["subagent_status"] == "failed"
    assert parent.agent._async_pool.status() == []


def test_thread_start_failure_finalizes_resume(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    child = _child(parent.client, lambda _task, **_kw: "answer", monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("initial")
    agent_id = next(iter(parent.agent._async_pool._records))
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("resume start failed")),
    )

    with pytest.raises(RuntimeError, match="resume start failed"):
        parent.agent.message_subagent(agent_id, "accepted but unstarted")

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    saved = _record(db_path, spawn["child_session_id"])
    record = parent.agent._async_pool.get(agent_id)
    assert record.status == "failed"
    assert saved["metadata"]["subagent_status"] == "failed"
    assert saved["messages"][-1]["content"] == "accepted but unstarted"


def test_follow_up_preview_is_labelled_and_full_text_survives(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    _immediate_threads(monkeypatch)
    child = _child(parent.client, lambda _task, **_kw: "answer", monkeypatch)
    monkeypatch.setattr(parent.agent, "_new_subagent", lambda _model=None: child)
    parent.agent.spawn_subagent_async("initial")
    agent_id = next(iter(parent.agent._async_pool._records))
    message = "follow " * 80
    parent.agent.message_subagent(agent_id, message)

    spawn = _children_of(events, CompanionEventType.AGENT_SPAWN)[0]
    saved = _record(db_path, spawn["child_session_id"])
    resumed = EventLog(str(db_path)).query(spawn["child_session_id"], event_type="session_resume")[
        0
    ]
    assert saved["messages"][-2]["content"] == message
    assert resumed["data"]["message_truncated"] is True
    assert resumed["data"]["message_chars"] == len(message)
    assert resumed["data"]["message_preview_cap"] == 200


def test_real_async_children_keep_distinct_exactly_once_logs(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "children.db"
    _store, parent, events = _parent(db_path)
    barrier = threading.Barrier(2)

    def make_child(_model: str | None = None) -> ResearchCompanion:
        holder: dict[str, Any] = {}

        def send(task: str, **_kwargs: Any) -> str:
            child = holder["child"]
            child._emit(
                CompanionEventType.TOOL_RESULT, task, data={"name": "file_read", "result": task}
            )
            barrier.wait(timeout=3)
            return f"answer:{task}"

        child = _child(parent.client, send, monkeypatch)
        holder["child"] = child
        return child

    monkeypatch.setattr(parent.agent, "_new_subagent", make_child)
    parent.agent.spawn_subagent_async("first")
    parent.agent.spawn_subagent_async("second")
    assert parent.agent._async_pool.wait_idle()

    for spawn in _children_of(events, CompanionEventType.AGENT_SPAWN):
        task = spawn["task"]
        logged = EventLog(str(db_path)).query(spawn["child_session_id"], event_type="tool_result")
        assert [row["data"]["result"] for row in logged] == [task]


def test_child_save_never_invokes_parent_mirror(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "children.db"
    store, parent, _events = _parent(db_path)
    mirrored: list[Any] = []
    store._mirror_poster = lambda *args, **kwargs: mirrored.append((args, kwargs))
    monkeypatch.setattr(
        parent.agent,
        "_new_subagent",
        lambda _model=None: _child(parent.client, lambda _task, **_kw: "ok", monkeypatch),
    )

    parent.agent.spawn_subagent("local only")

    assert mirrored == []
    assert len(_subagent_inventory(db_path)) == 1
