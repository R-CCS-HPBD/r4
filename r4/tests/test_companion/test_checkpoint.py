"""Tests for the checkpoint manager."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.companion.checkpoint import CheckpointManager


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "test.db")


@pytest.fixture
def cm(tmp_db):
    return CheckpointManager(tmp_db)


def test_save_checkpoint_returns_id(cm):
    cp_id = cm.save_checkpoint(
        session_id="s1",
        agent_id="agent-1",
        state={"messages": [], "progress": 0},
    )
    assert cp_id
    assert len(cp_id) == 36  # UUID


def test_load_latest_checkpoint(cm):
    cm.save_checkpoint(
        session_id="s1",
        state={"progress": 10},
    )
    cm.save_checkpoint(
        session_id="s1",
        state={"progress": 50},
    )
    state = cm.load_latest_checkpoint("s1")
    assert state["progress"] == 50


def test_load_latest_checkpoint_none(cm):
    state = cm.load_latest_checkpoint("nonexistent")
    assert state is None


def test_list_checkpoints(cm):
    cm.save_checkpoint(session_id="s1", state={"v": 1})
    cm.save_checkpoint(session_id="s1", state={"v": 2})
    cm.save_checkpoint(session_id="s2", state={"v": 3})
    checkpoints = cm.list_checkpoints("s1")
    assert len(checkpoints) == 2


def test_delete_checkpoint(cm):
    cp_id = cm.save_checkpoint(session_id="s1", state={"v": 1})
    assert cm.delete_checkpoint(cp_id) is True
    # Verify deleted
    assert cm.load_latest_checkpoint("s1") is None


def test_delete_nonexistent_checkpoint(cm):
    assert cm.delete_checkpoint("nonexistent-id") is False


def test_count_checkpoints(cm):
    assert cm.count_checkpoints("s1") == 0
    cm.save_checkpoint(session_id="s1", state={})
    cm.save_checkpoint(session_id="s1", state={})
    assert cm.count_checkpoints("s1") == 2


def test_load_latest_checkpoint_by_agent(cm):
    cm.save_checkpoint(session_id="s1", agent_id="agent-1", state={"v": 1})
    cm.save_checkpoint(session_id="s1", agent_id="agent-2", state={"v": 2})
    state = cm.load_latest_checkpoint("s1", agent_id="agent-2")
    assert state["v"] == 2


def test_checkpoint_state_is_json(cm):
    state = {"messages": ["hello", "world"], "nested": {"a": 1}}
    cm.save_checkpoint(session_id="s1", state=state)
    loaded = cm.load_latest_checkpoint("s1")
    assert loaded["messages"] == ["hello", "world"]
    assert loaded["nested"]["a"] == 1
