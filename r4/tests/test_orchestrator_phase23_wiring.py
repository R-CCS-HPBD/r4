"""Integration tests for Phase 2 (EIG selection).

Tests that the ResearchOrchestrator:
- Uses EIG to select the next hypothesis to test
- Updates beliefs after observing evidence
- Maintains a WorkingResearchState for fast queries

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §5 and §6

(The A2L provenance-chain tests left with their method 2026-10-19 —
cleanup C-2(iv); `derive` requires `params`, which the calls omitted.)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.agent.belief_state import BeliefState
from r4.agent.hypothesis import Hypothesis, HypothesisManager
from r4.agent.research_orchestrator import ResearchOrchestrator
from r4.agent.working_research_state import WorkingResearchState


def _build_orchestrator(hypotheses: list[Hypothesis] | None = None):
    """Build a ResearchOrchestrator with a mock client and seeded hypotheses."""
    mock_client = MagicMock()
    mock_client.config.umrs_username = "tester"
    mock_client.config.project_id = "proj_001"

    hmgr = HypothesisManager()
    for h in hypotheses or []:
        hmgr._hypotheses[h.id] = h  # direct insert for test

    orch = ResearchOrchestrator(
        client=mock_client,
        hypothesis_manager=hmgr,
    )
    return orch


class TestEIGSelectionWiring:
    """Test that the orchestrator uses EIG for hypothesis selection."""

    def test_belief_state_accessible(self):
        """belief_state property returns a BeliefState."""
        orch = _build_orchestrator()
        assert isinstance(orch.belief_state, BeliefState)

    def test_working_state_accessible(self):
        """working_state property returns a WorkingResearchState."""
        orch = _build_orchestrator()
        assert isinstance(orch.working_state, WorkingResearchState)

    def test_select_next_hypothesis_returns_highest_eig(self):
        """select_next_hypothesis returns the hypothesis with highest EIG."""
        h_certain = Hypothesis(id="h_certain", statement="Certain", confidence=0.9)
        h_uncertain = Hypothesis(id="h_uncertain", statement="Uncertain", confidence=0.5)

        orch = _build_orchestrator(hypotheses=[h_certain, h_uncertain])
        selected = orch.select_next_hypothesis()

        # The uncertain hypothesis should have higher EIG
        assert selected == "h_uncertain"

    def test_select_next_hypothesis_no_hypotheses(self):
        """select_next_hypothesis returns None when no hypotheses exist."""
        orch = _build_orchestrator()
        assert orch.select_next_hypothesis() is None

    def test_update_beliefs_from_evidence(self):
        """update_beliefs_from_evidence updates the belief state."""
        orch = _build_orchestrator()

        # Add a hypothesis to belief state
        orch.belief_state.add_hypothesis("h_001", confidence=0.5)

        # Update with supporting evidence
        orch.update_beliefs_from_evidence("h_001", supports=True, strength=0.8)

        # Confidence should have increased
        assert orch.belief_state.hypotheses["h_001"].confidence > 0.5

    def test_update_beliefs_auto_adds_hypothesis(self):
        """update_beliefs_from_evidence auto-adds unknown hypotheses."""
        orch = _build_orchestrator()

        orch.update_beliefs_from_evidence("h_new", supports=True, strength=0.7)

        assert "h_new" in orch.belief_state.hypotheses
        assert orch.belief_state.hypotheses["h_new"].confidence > 0.5


class TestWorkingResearchStateIntegration:
    """Test that the orchestrator maintains a WorkingResearchState."""

    def test_working_state_starts_empty(self):
        """WorkingResearchState starts with no questions or hypotheses."""
        orch = _build_orchestrator()
        assert orch.working_state.beliefs.num_hypotheses == 0
        assert len(orch.working_state.portfolio.active_question_ids) == 0

    def test_belief_state_and_working_state_are_independent(self):
        """belief_state and working_state are separate objects."""
        orch = _build_orchestrator()
        assert orch.belief_state is not orch.working_state
        assert orch.belief_state is not orch.working_state.beliefs

    def test_working_state_can_track_experiments(self):
        """WorkingResearchState can record experiment results."""
        orch = _build_orchestrator()
        orch.working_state.record_experiment("e_001", reward=0.7)
        orch.working_state.record_experiment("e_002", reward=0.9)

        assert orch.working_state.portfolio.total_experiments == 2
        assert orch.working_state.adr == pytest.approx(0.8)
