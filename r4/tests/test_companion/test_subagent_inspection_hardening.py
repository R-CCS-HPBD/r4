"""D-70 S3 follow-up: non-resumable inspection and raw-child rail refusals."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bridge_rpc_harness import Harness, make_harness, run_verb

from r4.companion.session_store import CompanionSessionStore
from r4.companion.subagent_sessions import SubagentExecution, SubagentSessionRecorder


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def _child(h: Harness, *, agent_id: str = "agent-hardening-1") -> SubagentExecution:
    recorder = SubagentSessionRecorder(
        h.store,
        h.sid,
        "parent-agent-1",
        event_log=h.server._event_log,
    )
    return recorder.begin(
        agent_id=agent_id,
        task="inspect the experiment log",
        system_prompt="verbatim private child prompt",
        model="child-model",
        execution_kind="sync",
        tools=[{"name": "file_read"}],
    )


def _inspect(h: Harness, execution: SubagentExecution, **params: Any) -> dict[str, Any]:
    return run_verb(
        h,
        "inspect_subagent",
        lambda _h: {"session_id": execution.child_session_id, **params},
    )


def test_inspection_is_non_resumable_and_inventory_has_exact_lifecycle_filters(
    harness: Harness,
) -> None:
    running = _child(harness)
    completed = _child(harness, agent_id="agent-hardening-2")
    completed.complete("done")

    inspected = _inspect(harness, running)
    assert (inspected["read_only"], inspected["resumable"]) == (True, False)
    listed = harness.server.list_sessions(
        {"session_kind": "subagent", "subagent_status": "completed", "no_remote": True}
    )
    assert [row["id"] for row in listed["sessions"]] == [completed.child_session_id]
    row = listed["sessions"][0]
    assert {
        "execution_kind": row["execution_kind"],
        "started": bool(row["started_at"]),
        "ended": bool(row["ended_at"]),
    } == {"execution_kind": "sync", "started": True, "ended": True}
    assert harness.store.list_sessions(session_kind="subagent", subagent_status="failed") == []
    assert "task" not in row and "system_prompt" not in row


def test_record_event_interleaving_converges_on_the_next_cursor_read(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = _child(harness)
    real_page = harness.server._event_log.page

    def complete_before_page(*args: Any, **kwargs: Any) -> Any:
        execution.complete("finished between reads")
        return real_page(*args, **kwargs)

    monkeypatch.setattr(harness.server._event_log, "page", complete_before_page)
    running = _inspect(harness, execution)
    assert running["session"]["status"] == "running"
    assert [row["event_type"] for row in running["events"]] == ["session_start", "session_end"]

    monkeypatch.setattr(harness.server._event_log, "page", real_page)
    settled = _inspect(harness, execution, after_id=running["next_after_id"])
    assert settled["session"]["status"] == "completed"
    assert settled["session"]["messages"][-1]["content"] == "finished between reads"
    assert (settled["events"], settled["has_more"]) == ([], False)


def test_event_page_uses_session_id_cursor_index_without_sorting(harness: Harness) -> None:
    execution = _child(harness)
    with sqlite3.connect(harness.store.db_path) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM event_log "
            "WHERE session_id = ? AND id > 0 ORDER BY id ASC LIMIT 2",
            (execution.child_session_id,),
        ).fetchall()
    detail = " ".join(str(row[-1]) for row in plan)
    assert "idx_events_session_id" in detail
    assert "TEMP B-TREE" not in detail


def test_load_session_refuses_a_child_without_using_the_remote_pull_fallback(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = _child(harness)
    active = harness.server._session
    monkeypatch.setattr(
        harness.server,
        "_pull_then_load",
        lambda _session_id: pytest.fail("child load must not become a remote resume"),
    )

    with pytest.raises(ValueError, match="read-only"):
        harness.server.load_session({"session_id": execution.child_session_id})
    assert harness.server._session is active


@pytest.mark.parametrize("operation", ["pin", "rename", "archive", "fork", "delete", "state"])
def test_recorder_owned_child_rows_refuse_ordinary_raw_id_mutators(
    harness: Harness, operation: str
) -> None:
    execution = _child(harness)
    sid = execution.child_session_id
    before = harness.store.raw_row(sid)

    with pytest.raises(ValueError, match="read-only"):
        _mutate_child(harness.store, operation, sid)

    assert harness.store.raw_row(sid) == before
    assert harness.store.get_session_record("child-fork") is None


def _mutate_child(store: CompanionSessionStore, operation: str, sid: str) -> None:
    if operation == "pin":
        store.set_pinned(sid, True)
    elif operation == "rename":
        store.rename_record(sid, "renamed child")
    elif operation == "archive":
        store.archive(sid)
    elif operation == "fork":
        store.fork(sid, "child-fork")
    elif operation == "delete":
        store.delete(sid)
    elif operation == "state":
        store.set_state(sid, "completed")
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(operation)


@pytest.mark.parametrize(
    "operation",
    ["status", "stop", "approvals", "decide", "questions", "answer", "policy", "turns"],
)
def test_raw_child_remote_routes_refuse_before_transport(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    execution = _child(harness)
    monkeypatch.setattr(harness.client, "get", _remote_must_not_run)
    monkeypatch.setattr(harness.client, "post", _remote_must_not_run)
    monkeypatch.setattr(harness.client, "list_session_turns", _remote_must_not_run)

    with pytest.raises(ValueError, match="read-only"):
        _call_child_remote_route(harness, execution.child_session_id, operation)


def _remote_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("raw child route must be refused before transport")


def _call_child_remote_route(harness: Harness, sid: str, operation: str) -> Any:
    if operation == "status":
        return harness.server.session_venue_status({"session_id": sid})
    if operation == "stop":
        return harness.server.session_venue_stop({"session_id": sid})
    if operation == "approvals":
        return harness.server.session_venue_approvals({"session_id": sid})
    if operation == "decide":
        return harness.server.session_venue_decide(
            {"session_id": sid, "approval_id": "a1", "decision": "once"}
        )
    if operation == "questions":
        return harness.server.session_venue_questions({"session_id": sid})
    if operation == "answer":
        return harness.server.session_venue_answer(
            {"session_id": sid, "question_id": "q1", "answer": "answer"}
        )
    if operation == "policy":
        return harness.server.session_venue_policy({"session_id": sid})
    if operation == "turns":
        return harness.server.session_turns({"session_id": sid})
    raise AssertionError(operation)  # pragma: no cover - parametrization guard


@pytest.mark.parametrize("operation", ["status", "stop", "questions", "answer"])
def test_raw_child_cli_remote_routes_refuse_before_transport(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    from r4._cli import sessions as session_cli

    execution = _child(harness)
    monkeypatch.setattr(
        session_cli, "_venue_client_and_store", lambda: (harness.client, harness.store)
    )
    monkeypatch.setattr(harness.client, "get", _remote_must_not_run)
    monkeypatch.setattr(harness.client, "post", _remote_must_not_run)

    assert _call_child_cli_remote_route(execution.child_session_id, operation) == 1
    assert "read-only" in capsys.readouterr().err


def _call_child_cli_remote_route(sid: str, operation: str) -> int:
    from r4._cli import sessions as session_cli

    if operation == "status":
        return session_cli._handle_venue_status(SimpleNamespace(session_id=sid, as_json=True))
    if operation == "stop":
        return session_cli._handle_venue_stop(SimpleNamespace(session_id=sid, as_json=True))
    if operation == "questions":
        return session_cli._handle_venue_questions(SimpleNamespace(session_id=sid, as_json=True))
    if operation == "answer":
        args = SimpleNamespace(session_id=sid, answer_args=["q1", "answer"], as_json=True)
        return session_cli._handle_venue_answer(args)
    raise AssertionError(operation)  # pragma: no cover - parametrization guard
