"""Tests for research_loop state model — serialization, phase transitions, budget.

Covers: ResearchLoopState (create/save/load), HypothesisRecord (evidence scoring),
ExperimentRecord (lifecycle), LoopBudget (exhaustion incl. wall clock, V-04),
LoopDecision. The test-only LoopOrchestrator was deleted 2026-09-22 (V-10/D-32).
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from r4.research_loop.state import (
    AgentAssignment,
    AgentRole,
    AgentStatus,
    EvidenceRecord,
    ExperimentRecord,
    ExperimentStatus,
    HypothesisRecord,
    HypothesisStatus,
    LoopBudget,
    LoopDecision,
    LoopPhase,
    ResearchLoopState,
)

# -- ResearchLoopState -----------------------------------------------------


class TestResearchLoopState:
    """Tests for ResearchLoopState creation, serialization, and record management."""

    def test_create_minimal(self) -> None:
        state = ResearchLoopState(
            loop_id="test-001",
            objective="Test research objective",
        )
        assert state.loop_id == "test-001"
        assert state.phase == LoopPhase.FORMULATING
        assert state.objective == "Test research objective"
        assert len(state.hypotheses) == 0
        assert isinstance(state.budget, LoopBudget)

    def test_add_hypothesis(self) -> None:
        state = ResearchLoopState(loop_id="test-001")
        hid = state.add_hypothesis("H₂O boils at 100°C at 1 atm")
        assert hid == "H-001"
        assert len(state.hypotheses) == 1
        assert state.current_hypothesis_id == "H-001"
        assert state.hypotheses["H-001"].statement == "H₂O boils at 100°C at 1 atm"
        assert state.hypotheses["H-001"].status == HypothesisStatus.PROPOSED
        assert state.hypotheses["H-001"].confidence == 0.5

    def test_add_multiple_hypotheses(self) -> None:
        state = ResearchLoopState(loop_id="test-001")
        state.add_hypothesis("First hypothesis")
        state.add_hypothesis("Second hypothesis")
        assert len(state.hypotheses) == 2
        assert "H-001" in state.hypotheses
        assert "H-002" in state.hypotheses
        assert state.current_hypothesis_id == "H-001"  # first is current

    def test_add_experiment(self) -> None:
        state = ResearchLoopState(loop_id="test-001")
        state.add_hypothesis("Test hypothesis")
        eid = state.add_experiment("H-001", name="Test experiment")
        assert eid == "E-001"
        assert state.experiments["E-001"].hypothesis_id == "H-001"
        assert state.experiments["E-001"].status == ExperimentStatus.PENDING

    def test_add_evidence(self) -> None:
        state = ResearchLoopState(loop_id="test-001")
        state.add_hypothesis("Test hypothesis")
        state.add_experiment("H-001")
        evid = state.add_evidence("E-001", "H-001", supports=True, strength=0.8)
        assert evid == "EV-001"
        assert "EV-001" in state.evidence_pool
        assert "EV-001" in state.hypotheses["H-001"].evidence_for
        assert len(state.hypotheses["H-001"].evidence_against) == 0

    def test_add_contradicting_evidence(self) -> None:
        state = ResearchLoopState(loop_id="test-001")
        state.add_hypothesis("Test hypothesis")
        state.add_experiment("H-001")
        _evid = state.add_evidence("E-001", "H-001", supports=False, strength=0.7)
        assert "EV-001" in state.hypotheses["H-001"].evidence_against
        assert len(state.hypotheses["H-001"].evidence_for) == 0

    def test_save_and_load_roundtrip(self) -> None:
        state = ResearchLoopState(
            loop_id="test-roundtrip",
            objective="Test roundtrip serialization",
            domain="chemistry",
        )
        state.add_hypothesis("Test hypothesis", confidence=0.7)
        state.add_experiment("H-001", name="My experiment")
        state.add_evidence("E-001", "H-001", supports=True, strength=0.9)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Override state path for test
            path = Path(tmpdir) / "state.json"

            # Monkey-patch save location
            state._state_dir = lambda: Path(tmpdir)
            state._state_path = lambda: Path(tmpdir) / "state.json"
            state.save()

            assert path.exists()
            with open(path) as f:
                raw = json.load(f)
            assert raw["loop_id"] == "test-roundtrip"
            assert raw["objective"] == "Test roundtrip serialization"
            assert "H-001" in raw["hypotheses"]
            assert "E-001" in raw["experiments"]
            assert "EV-001" in raw["evidence_pool"]

            # Load back
            loaded = ResearchLoopState.from_file(path)
            assert loaded.loop_id == "test-roundtrip"
            assert loaded.objective == "Test roundtrip serialization"
            assert len(loaded.hypotheses) == 1
            assert len(loaded.experiments) == 1
            assert len(loaded.evidence_pool) == 1
            assert loaded.hypotheses["H-001"].confidence == 0.7

    def test_list_loops(self) -> None:
        state = ResearchLoopState(
            loop_id="test-listable",
            objective="Listable",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Create the research_loops directory structure
            loop_dir = base / "research_loops" / "test-listable"
            loop_dir.mkdir(parents=True)
            state._state_dir = lambda: loop_dir
            state._state_path = lambda: loop_dir / "state.json"
            state.save()

            # Monkey-patch the base path
            assert (loop_dir / "state.json").exists()


# -- HypothesisRecord ------------------------------------------------------


class TestHypothesisRecord:
    """Tests for HypothesisRecord evidence scoring."""

    def test_net_evidence_score_empty(self) -> None:
        h = HypothesisRecord(id="H-001", statement="test")
        assert h.net_evidence_score({}) == 0.0

    def test_net_evidence_score_supporting_only(self) -> None:
        h = HypothesisRecord(id="H-001", statement="test", evidence_for=["EV-001", "EV-002"])
        evidence = {
            "EV-001": EvidenceRecord(
                id="EV-001",
                experiment_id="E-001",
                hypothesis_id="H-001",
                supports=True,
                strength=0.8,
            ),
            "EV-002": EvidenceRecord(
                id="EV-002",
                experiment_id="E-002",
                hypothesis_id="H-001",
                supports=True,
                strength=0.5,
            ),
        }
        assert h.net_evidence_score(evidence) == pytest.approx(1.3)

    def test_net_evidence_score_contradicting_only(self) -> None:
        h = HypothesisRecord(id="H-001", statement="test", evidence_against=["EV-001"])
        evidence = {
            "EV-001": EvidenceRecord(
                id="EV-001",
                experiment_id="E-001",
                hypothesis_id="H-001",
                supports=False,
                strength=0.9,
            ),
        }
        assert h.net_evidence_score(evidence) == pytest.approx(-0.9)

    def test_net_evidence_score_mixed(self) -> None:
        h = HypothesisRecord(
            id="H-001",
            statement="test",
            evidence_for=["EV-001"],
            evidence_against=["EV-002"],
        )
        evidence = {
            "EV-001": EvidenceRecord(
                id="EV-001",
                experiment_id="E-001",
                hypothesis_id="H-001",
                supports=True,
                strength=0.8,
            ),
            "EV-002": EvidenceRecord(
                id="EV-002",
                experiment_id="E-002",
                hypothesis_id="H-001",
                supports=False,
                strength=0.6,
            ),
        }
        assert h.net_evidence_score(evidence) == pytest.approx(0.2)

    def test_net_evidence_score_missing_ids(self) -> None:
        h = HypothesisRecord(id="H-001", statement="test", evidence_for=["EV-999"])
        assert h.net_evidence_score({}) == 0.0


# -- ExperimentRecord ------------------------------------------------------


class TestExperimentRecord:
    """Tests for ExperimentRecord lifecycle."""

    def test_default_status(self) -> None:
        e = ExperimentRecord(id="E-001", hypothesis_id="H-001")
        assert e.status == ExperimentStatus.PENDING

    def test_string_status_deserialization(self) -> None:
        e = ExperimentRecord(id="E-001", hypothesis_id="H-001", status="completed")
        assert e.status == ExperimentStatus.COMPLETED


# -- LoopBudget -------------------------------------------------------------


class TestLoopBudget:
    """Tests for LoopBudget exhaustion and consumption."""

    def test_not_exhausted_initially(self) -> None:
        b = LoopBudget()
        assert not b.is_exhausted

    def test_exhausted_by_iterations(self) -> None:
        b = LoopBudget(max_iterations=1)
        b.record_iteration()
        assert b.is_exhausted

    def test_exhausted_by_failures(self) -> None:
        b = LoopBudget(max_consecutive_failures=2)
        b.record_failure()
        b.record_failure()
        assert b.is_exhausted

    def test_exhausted_by_wall_clock(self) -> None:
        # V-04 pin: max_wall_clock_hours is enforced, not a dead field.
        past = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        b = LoopBudget(max_wall_clock_hours=48.0, started_at=past)
        assert b.is_exhausted

    def test_wall_clock_within_budget_not_exhausted(self) -> None:
        b = LoopBudget(max_wall_clock_hours=48.0)
        assert not b.is_exhausted

    def test_wall_clock_unparseable_stamp_fails_open(self) -> None:
        b = LoopBudget(started_at="not-a-timestamp")
        assert b._elapsed_hours == 0.0
        assert not b.is_exhausted

    def test_reset_failures(self) -> None:
        b = LoopBudget(max_consecutive_failures=3)
        b.record_failure()
        b.record_failure()
        b.reset_consecutive_failures()
        assert b.consecutive_failures == 0
        assert not b.is_exhausted

    def test_budget_remaining_pct(self) -> None:
        b = LoopBudget(max_iterations=10)
        assert b.budget_remaining_pct == pytest.approx(100.0)
        for _ in range(5):
            b.record_iteration()
        assert b.budget_remaining_pct == pytest.approx(50.0)


# -- Agent Roles -----------------------------------------------------------


class TestAgentAssignment:
    """Tests for AgentAssignment."""

    def test_string_role_deserialization(self) -> None:
        a = AgentAssignment(role="RL", agent_id="agent-1")
        assert a.role == AgentRole.RL

    def test_string_status_deserialization(self) -> None:
        a = AgentAssignment(role=AgentRole.DA, agent_id="agent-1", status="thinking")
        assert a.status == AgentStatus.THINKING


# -- LoopDecision ----------------------------------------------------------


class TestLoopDecision:
    """Tests for LoopDecision enum."""

    def test_all_decisions_exist(self) -> None:
        decisions = list(LoopDecision)
        assert len(decisions) == 5
        assert LoopDecision.CONTINUE_SAME.value == "continue_same"
        assert LoopDecision.PUBLISH.value == "publish"
        assert LoopDecision.STOP.value == "stop"
