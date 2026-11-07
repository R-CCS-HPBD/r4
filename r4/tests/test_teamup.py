"""Tests for ``r4.teamup`` module — roles and session contracts.

Covers:
- Role definitions and registry
- Session config validation
- Report generation
- Hardware profile lookup
- SOTA tracker basics
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest


def test_config_project_id_roundtrip_and_list_payload(tmp_path, monkeypatch):
    """2026-10-01: sessions carry project_id (work-pane scoping) — persisted,
    reloaded, and listed; legacy snapshots report None (honest absence)."""
    import json

    import r4.teamup.session as session_module

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)

    config = session_module.TeamupSessionConfig(name="scoped", project_id="proj-a")
    restored = session_module.TeamupSessionConfig.from_dict(config.to_dict())
    assert restored.project_id == "proj-a"

    legacy = session_module.TeamupSessionConfig.from_dict({"name": "legacy"})
    assert legacy.project_id is None

    client = SimpleNamespace()
    session = session_module.TeamupSession.create(config, client)
    rows = session_module.TeamupSession.list_sessions()
    row = next(r for r in rows if r["session_id"] == session.session_id)
    assert row["project_id"] == "proj-a"

    # Legacy snapshot on disk (no project_id key) lists None, never invented.
    (tmp_path / session.session_id / "session.json").write_text(
        json.dumps(
            {
                "session_id": session.session_id,
                "config": {"name": "legacy"},
                "status": "paused",
                "iteration": 1,
                "updated_at": "2026-10-01T00:00:00",
            }
        )
    )
    rows = session_module.TeamupSession.list_sessions()
    row = next(r for r in rows if r["session_id"] == session.session_id)
    assert row["project_id"] is None


# The (5, budget=0) case pinned the compute-budget stop rule, retired with the
# BudgetTracker in ADR-092 slice 4 (nothing charged it); the iteration cap is
# the one stop rule left.
@pytest.mark.parametrize("max_iterations", [1, 0])
def test_terminal_iteration_survives_reload(tmp_path, monkeypatch, max_iterations):
    import r4.teamup.session as session_module

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    client = SimpleNamespace(chat=lambda **_kwargs: SimpleNamespace(content="test reply"))
    config = session_module.TeamupSessionConfig(
        name="terminal-state", max_iterations=max_iterations
    )
    session = session_module.TeamupSession.create(config, client)

    assert asyncio.run(session.run_iteration()) is False
    restored = session_module.TeamupSession.load(session.session_id, client)
    assert restored.status is session_module.SessionStatus.COMPLETED
    assert restored.iteration == (1 if max_iterations else 0)
    assert asyncio.run(restored.run_iteration()) is False


def test_agent_activity_survives_reload(tmp_path, monkeypatch):
    import r4.teamup.session as session_module

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    client = SimpleNamespace()
    session = session_module.TeamupSession.create(
        session_module.TeamupSessionConfig(name="agent-state"), client
    )
    state = session.agent_states["pm"]
    state.status = session_module.AgentStatus.ERROR
    state.current_task = "review results"
    state.last_action_at = datetime(2026, 9, 28, 12, 0)
    state.errors = ["test failure"]
    state.messages_sent = 7
    state.messages_received = 8
    session._save_snapshot()

    restored = session_module.TeamupSession.load(session.session_id, client)
    assert restored.agent_states["pm"].to_dict() == state.to_dict()


# ── Role tests ─────────────────────────────────────────────────────────────


class TestAgentRoles:
    """A-802: Agent role definitions."""

    def test_role_enum_values(self):
        from r4.teamup.roles import AgentRole

        assert AgentRole.PM.value == "pm"
        assert AgentRole.SE.value == "se"
        assert AgentRole.PG.value == "pg"
        assert AgentRole.CD.value == "cd"

    def test_get_role_spec_by_enum(self):
        from r4.teamup.roles import AgentRole, get_role_spec

        spec = get_role_spec(AgentRole.PM)
        assert spec.role == AgentRole.PM
        assert spec.name == "Project Manager"
        assert not spec.can_execute
        assert not spec.can_submit_jobs

    def test_get_role_spec_by_string(self):
        from r4.teamup.roles import AgentRole, get_role_spec

        spec = get_role_spec("pg")
        assert spec.role == AgentRole.PG
        assert spec.can_execute
        assert spec.can_submit_jobs

    def test_programmer_can_execute_and_submit(self):
        from r4.teamup.roles import PG_SPEC

        assert PG_SPEC.can_execute is True
        assert PG_SPEC.can_submit_jobs is True

    def test_pm_and_se_cannot_execute(self):
        from r4.teamup.roles import PM_SPEC, SE_SPEC

        assert PM_SPEC.can_execute is False
        assert SE_SPEC.can_execute is False

    def test_default_roles(self):
        from r4.teamup.roles import AgentRole, get_default_roles

        defaults = get_default_roles()
        assert AgentRole.PM in defaults
        assert AgentRole.SE in defaults
        assert AgentRole.PG in defaults
        assert AgentRole.CD not in defaults  # CD is optional

    def test_role_spec_to_dict_roundtrip(self):
        from r4.teamup.roles import PM_SPEC

        d = PM_SPEC.to_dict()
        assert d["role"] == "pm"
        assert d["name"] == "Project Manager"
        assert isinstance(d["capabilities"], list)
        assert isinstance(d["tools"], list)

    def test_all_roles_have_system_prompts(self):
        from r4.teamup.roles import ROLE_SPECS

        for role, spec in ROLE_SPECS.items():
            assert spec.system_prompt, f"Role {role} has empty system prompt"
            assert len(spec.system_prompt) > 50, f"Role {role} has suspiciously short prompt"

    def test_invalid_role_string_raises(self):
        from r4.teamup.roles import get_role_spec

        with pytest.raises(ValueError):
            get_role_spec("invalid_role")


# ── Hardware profile tests ─────────────────────────────────────────────────


class TestHardwareProfiles:
    """Hardware profile lookup."""

    def test_get_fugaku_profile(self):
        from r4.teamup.hardware import get_hardware_profile

        profile = get_hardware_profile("fugaku")
        assert profile is not None
        assert "fugaku" in profile.name.lower() or "a64fx" in profile.name.lower()

    def test_get_nvidia_profile(self):
        from r4.teamup.hardware import get_hardware_profile

        profile = get_hardware_profile("nvidia_a100")
        assert profile is not None

    def test_get_generic_profile(self):
        from r4.teamup.hardware import get_hardware_profile

        profile = get_hardware_profile("generic")
        assert profile is not None

    def test_list_profiles(self):
        from r4.teamup.hardware import list_hardware_profiles

        profiles = list_hardware_profiles()
        assert len(profiles) >= 3

    def test_unknown_profile_returns_none(self):
        from r4.teamup.hardware import get_hardware_profile

        profile = get_hardware_profile("nonexistent_hardware_xyz")
        assert profile is None


# ── SOTA tracker tests ─────────────────────────────────────────────────────


class TestSOTATracker:
    """SOTA tracking basics."""

    def test_create_tracker(self, tmp_path):
        from r4.teamup.sota import SOTATracker

        tracker = SOTATracker(shared_dir=tmp_path / "sota")
        assert tracker is not None

    def test_record_and_retrieve(self, tmp_path):
        from r4.teamup.sota import SOTATracker

        tracker = SOTATracker(shared_dir=tmp_path / "sota")
        tracker.record(
            value=100.0,
            agent="pg",
            description="baseline",
        )
        best = tracker.get_local_best("pg")
        assert best is not None
        assert best.value == 100.0

    def test_new_record_improves(self, tmp_path):
        from r4.teamup.sota import SOTATracker

        tracker = SOTATracker(shared_dir=tmp_path / "sota")
        tracker.record(value=100.0, agent="pg", description="v1")
        tracker.record(value=200.0, agent="pg", description="v2")
        best = tracker.get_local_best("pg")
        assert best.value == 200.0

    def test_project_best_tracks_globally(self, tmp_path):
        from r4.teamup.sota import SOTATracker

        tracker = SOTATracker(shared_dir=tmp_path / "sota")
        tracker.record(value=50.0, agent="pg", description="pg run")
        tracker.record(value=80.0, agent="se", description="se run")
        project = tracker.get_project_best()
        assert project is not None
        assert project.value == 80.0


# ── Session config tests ──────────────────────────────────────────────────


class TestSessionConfig:
    """Session configuration validation."""

    def test_create_minimal_config(self):
        from r4.teamup.roles import AgentRole
        from r4.teamup.session import TeamupSessionConfig

        config = TeamupSessionConfig(
            name="test-session",
            roles=[AgentRole.PM, AgentRole.PG],
        )
        assert config.name == "test-session"
        assert len(config.roles) == 2

    def test_config_defaults(self):
        from r4.teamup.roles import AgentRole
        from r4.teamup.session import TeamupSessionConfig

        config = TeamupSessionConfig(
            name="test",
            roles=[AgentRole.PM],
        )
        # ADR-092 slice 4: the compute budget and SOTA target knobs are retired.
        assert not hasattr(config, "compute_budget")
        assert not hasattr(config, "target_performance")
        assert config.max_iterations > 0
