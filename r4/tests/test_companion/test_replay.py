"""Tests for session replay from event log."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.companion.event_log import EventLog
from r4.companion.replay import SessionReplay


@pytest.fixture
def event_log():
    with tempfile.TemporaryDirectory() as tmp:
        yield EventLog(str(Path(tmp) / "test.db"))


@pytest.fixture
def replay(event_log):
    return SessionReplay(event_log)


def test_replay_empty_session(replay):
    events = replay.replay_session("nonexistent")
    assert events == []


def test_replay_returns_events_in_order(replay):
    for i in range(5):
        event_log_append(replay._event_log, f"event-{i}")
    events = replay.replay_session("s1")
    assert len(events) == 5
    # Should be oldest-to-newest
    assert events[0]["message"] == "event-0"
    assert events[-1]["message"] == "event-4"


def test_summarize_empty_session(replay):
    summary = replay.summarize("nonexistent")
    assert summary["total_events"] == 0
    assert summary["agents"] == []
    assert summary["tools_used"] == []


def test_summarize_returns_counts(replay):
    el = replay._event_log
    el.append(
        session_id="s1",
        event_type="tool_call",
        message="call1",
        agent_id="a1",
        tool_name="shell_exec",
    )
    el.append(
        session_id="s1",
        event_type="tool_result",
        message="result1",
        agent_id="a1",
        tool_name="shell_exec",
    )
    el.append(
        session_id="s1",
        event_type="tool_call",
        message="call2",
        agent_id="a2",
        tool_name="file_read",
    )
    summary = replay.summarize("s1")
    assert summary["total_events"] == 3
    assert summary["event_types"]["tool_call"] == 2
    assert summary["event_types"]["tool_result"] == 1
    assert sorted(summary["agents"]) == ["a1", "a2"]
    assert sorted(summary["tools_used"]) == ["file_read", "shell_exec"]


def test_format_timeline_empty(replay):
    result = replay.format_timeline("nonexistent")
    assert "no events" in result


def test_format_timeline_with_events(replay):
    el = replay._event_log
    el.append(
        session_id="s1",
        event_type="tool_call",
        message="Running ls",
        agent_id="a1",
        tool_name="shell_exec",
    )
    result = replay.format_timeline("s1")
    assert "tool_call" in result
    assert "shell_exec" in result


def test_replay_agent_filters_by_agent(replay):
    el = replay._event_log
    el.append(session_id="s1", event_type="tool_call", agent_id="a1", tool_name="t1")
    el.append(session_id="s1", event_type="tool_call", agent_id="a2", tool_name="t2")
    events = replay.replay_agent("s1", "a1")
    assert len(events) == 1
    assert events[0]["agent_id"] == "a1"


def event_log_append(event_log, message):
    event_log.append(
        session_id="s1",
        event_type="tool_call",
        message=message,
    )
