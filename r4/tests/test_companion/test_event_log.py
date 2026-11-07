"""Tests for the persistent event log."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.companion.event_log import EventLog


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "test.db")


def test_append_returns_event_id(tmp_db):
    log = EventLog(tmp_db)
    eid = log.append(
        session_id="s1",
        event_type="tool_call",
        message="Calling tool: shell_exec",
        data={"tool": "shell_exec", "command": "ls"},
        tool_name="shell_exec",
        tool_status="running",
        agent_id="agent-1",
    )
    assert eid > 0


def test_query_returns_events(tmp_db):
    log = EventLog(tmp_db)
    log.append(
        session_id="s1",
        event_type="tool_call",
        message="Calling tool: shell_exec",
        data={"tool": "shell_exec"},
        tool_name="shell_exec",
    )
    log.append(
        session_id="s1",
        event_type="tool_result",
        message="Tool 'shell_exec' completed",
        data={"tool": "shell_exec", "result": "file1.py"},
        tool_name="shell_exec",
        tool_status="success",
    )
    events = log.query("s1")
    assert len(events) == 2
    assert events[0]["event_type"] == "tool_call"
    assert events[1]["event_type"] == "tool_result"
    assert events[0]["tool_name"] == "shell_exec"
    assert events[1]["tool_status"] == "success"


def test_query_by_agent_id(tmp_db):
    log = EventLog(tmp_db)
    log.append(session_id="s1", event_type="tool_call", agent_id="a1")
    log.append(session_id="s1", event_type="tool_call", agent_id="a2")
    events = log.query("s1", agent_id="a1")
    assert len(events) == 1
    assert events[0]["agent_id"] == "a1"


def test_query_by_event_type(tmp_db):
    log = EventLog(tmp_db)
    log.append(session_id="s1", event_type="tool_call")
    log.append(session_id="s1", event_type="tool_result")
    log.append(session_id="s1", event_type="tool_call")
    events = log.query("s1", event_type="tool_call")
    assert len(events) == 2


def test_tail_returns_most_recent(tmp_db):
    log = EventLog(tmp_db)
    for i in range(10):
        log.append(
            session_id="s1",
            event_type="tool_call",
            message=f"Event {i}",
        )
    events = log.tail("s1", limit=3)
    assert len(events) == 3
    # Most recent should be last
    assert events[-1]["message"] == "Event 9"
    assert events[0]["message"] == "Event 7"


def test_count_returns_total(tmp_db):
    log = EventLog(tmp_db)
    assert log.count("s1") == 0
    log.append(session_id="s1", event_type="tool_call")
    log.append(session_id="s1", event_type="tool_result")
    log.append(session_id="s2", event_type="tool_call")
    assert log.count("s1") == 2
    assert log.count("s2") == 1


def test_data_is_parsed_as_json(tmp_db):
    log = EventLog(tmp_db)
    log.append(
        session_id="s1",
        event_type="tool_call",
        data={"tool": "shell_exec", "command": "ls -la"},
    )
    events = log.query("s1")
    assert len(events) == 1
    assert isinstance(events[0]["data"], dict)
    assert events[0]["data"]["tool"] == "shell_exec"
    assert events[0]["data"]["command"] == "ls -la"


def test_query_with_since_filter(tmp_db):
    log = EventLog(tmp_db)
    log.append(session_id="s1", event_type="tool_call", message="first")
    # Capture timestamp of first event
    first_events = log.query("s1")
    first_ts = first_events[0]["timestamp"]
    log.append(session_id="s1", event_type="tool_result", message="second")
    # Query events since the first timestamp
    events_since = log.query("s1", since=first_ts)
    assert len(events_since) == 1
    assert events_since[0]["message"] == "second"


def test_multiple_sessions_are_isolated(tmp_db):
    log = EventLog(tmp_db)
    log.append(session_id="s1", event_type="tool_call", message="s1-event")
    log.append(session_id="s2", event_type="tool_call", message="s2-event")
    s1_events = log.query("s1")
    s2_events = log.query("s2")
    assert len(s1_events) == 1
    assert len(s2_events) == 1
    assert s1_events[0]["message"] == "s1-event"
    assert s2_events[0]["message"] == "s2-event"


def test_empty_data_defaults_to_empty_dict(tmp_db):
    log = EventLog(tmp_db)
    log.append(session_id="s1", event_type="tool_call")
    events = log.query("s1")
    assert len(events) == 1
    assert events[0]["data"] == {}
