"""Pins: full goal always reaches agents + word-boundary slug + kind conflict text (2026-10-01)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import r4.teamup.session as session_module
from r4.teamup.bootstrap import _goal_slug
from r4.teamup.roles import AgentRole
from r4.teamup.session import TeamupSession, TeamupSessionConfig


def test_slug_truncates_at_word_boundary_with_marker() -> None:
    long_goal = "please chech the current status of project and plan for the next steps."
    slug = _goal_slug(long_goal, "research", cap=44)
    assert slug == "research:please chech the current status of project…"
    assert "project a" not in slug  # never mints phantom tails


def test_slug_short_goal_unchanged() -> None:
    assert _goal_slug("short goal", "code") == "code:short goal"
    assert _goal_slug("", "research") == "research team"


def test_full_goal_reaches_role_context(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    goal = "check the workspace project status and plan the next steps"
    team = TeamupSession.create(
        TeamupSessionConfig(name="research:…", optimization_goal=goal),
        SimpleNamespace(),
    )
    role = next(iter(team.agent_states.keys()))
    ctx = team._build_agent_context_sync(AgentRole(role))
    assert f"**Goal**: {goal}" in ctx


def test_absent_goal_falls_back_to_name_honestly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    team = TeamupSession.create(TeamupSessionConfig(name="research:…"), SimpleNamespace())
    role = next(iter(team.agent_states.keys()))
    ctx = team._build_agent_context_sync(AgentRole(role))
    assert "**Goal**: research:…" in ctx  # honest absence, never invented words


def test_conflict_text_names_watch_point() -> None:
    from r4.teamup.session import TeamupSession

    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM])
    team = TeamupSession(cfg, SimpleNamespace(), session_dir=Path("/tmp/x-fake-nonexistent"))
    key = team.session_dir.resolve()
    with TeamupSession._active_runs_lock:
        TeamupSession._active_runs[key] = team
    try:
        with pytest.raises(ValueError, match="Work pane"):
            team.run_in_background(lambda _s: None)
    finally:
        with TeamupSession._active_runs_lock:
            TeamupSession._active_runs.pop(key, None)
