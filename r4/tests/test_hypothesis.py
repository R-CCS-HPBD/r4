"""Tests for the hypothesis-driven research framework.

Covers:
- Hypothesis and Evidence model behavior
- HypothesisManager: create, evaluate, revise, lineage, serialization
- EvidenceStrength validation
- HypothesisStatus transitions
- Integration with ResearchSession and IterationRecord
"""

from __future__ import annotations

import pytest

from r4.agent.hypothesis import (
    Evidence,
    EvidenceStrength,
    Hypothesis,
    HypothesisManager,
    HypothesisStatus,
)
from r4.agent.models import WorkflowDefinition
from r4.agent.research_orchestrator import (
    IterationRecord,
    OrchestratorStatus,
    ResearchSession,
)

# ── Evidence Model ──────────────────────────────────────────────────────────


def test_evidence_creation():
    e = Evidence(source="exp_1", strength=0.8, summary="Rate doubled", supports=True)
    assert e.source == "exp_1"
    assert e.strength == 0.8
    assert e.summary == "Rate doubled"
    assert e.supports is True


def test_evidence_strength_validation():
    with pytest.raises(ValueError, match="0.0–1.0"):
        Evidence(source="x", strength=1.5, summary="bad", supports=True)
    with pytest.raises(ValueError, match="0.0–1.0"):
        Evidence(source="x", strength=-0.1, summary="bad", supports=True)


def test_evidence_strength_boundary():
    e0 = Evidence(source="x", strength=0.0, summary="min", supports=False)
    e1 = Evidence(source="x", strength=1.0, summary="max", supports=True)
    assert e0.strength == 0.0
    assert e1.strength == 1.0


def test_evidence_strength_enum():
    assert EvidenceStrength.WEAK == 0.2
    assert EvidenceStrength.MODERATE == 0.5
    assert EvidenceStrength.STRONG == 0.8
    assert EvidenceStrength.DECISIVE == 0.95


def test_evidence_serialization_roundtrip():
    e = Evidence(
        source="exp_1",
        strength=0.7,
        summary="Positive result",
        supports=True,
        metadata={"iteration": 1},
    )
    d = e.to_dict()
    e2 = Evidence.from_dict(d)
    assert e2.source == e.source
    assert e2.strength == e.strength
    assert e2.summary == e.summary
    assert e2.supports == e.supports
    assert e2.metadata == e.metadata


def test_evidence_from_dict_with_iso_date():
    d = {
        "source": "lit",
        "strength": 0.6,
        "summary": "Reference",
        "supports": True,
        "created_at": "2026-01-15T10:30:00Z",
    }
    e = Evidence.from_dict(d)
    assert e.source == "lit"
    assert e.created_at is not None


# ── Hypothesis Model ────────────────────────────────────────────────────────


def test_hypothesis_creation():
    h = Hypothesis(statement="Temperature increases reaction rate")
    assert h.statement == "Temperature increases reaction rate"
    assert h.confidence == 0.5
    assert h.status == HypothesisStatus.PROPOSED
    assert h.is_active is True
    assert h.is_terminal is False
    assert h.id  # auto-generated


def test_hypothesis_confidence_validation():
    with pytest.raises(ValueError, match="0.0–1.0"):
        Hypothesis(statement="test", confidence=1.5)
    with pytest.raises(ValueError, match="0.0–1.0"):
        Hypothesis(statement="test", confidence=-0.1)


def test_hypothesis_evidence_score_empty():
    h = Hypothesis(statement="test")
    assert h.evidence_score == 0.0


def test_hypothesis_evidence_score_positive():
    h = Hypothesis(statement="test")
    h.evidence_for.append(Evidence(source="s1", strength=0.8, summary="a", supports=True))
    h.evidence_for.append(Evidence(source="s2", strength=0.6, summary="b", supports=True))
    assert h.evidence_score == 1.4


def test_hypothesis_evidence_score_negative():
    h = Hypothesis(statement="test")
    h.evidence_against.append(
        Evidence(source="s1", strength=0.9, summary="contradicts", supports=False)
    )
    assert h.evidence_score == -0.9


def test_hypothesis_evidence_score_net():
    h = Hypothesis(statement="test")
    h.evidence_for.append(Evidence(source="s1", strength=0.8, summary="a", supports=True))
    h.evidence_against.append(Evidence(source="s2", strength=0.3, summary="b", supports=False))
    assert h.evidence_score == pytest.approx(0.5)


def test_hypothesis_status_terminal():
    h = Hypothesis(statement="test", status=HypothesisStatus.SUPPORTED)
    assert h.is_terminal is True
    assert h.is_active is False


def test_hypothesis_status_active():
    for status in (
        HypothesisStatus.PROPOSED,
        HypothesisStatus.TESTING,
        HypothesisStatus.INCONCLUSIVE,
    ):
        h = Hypothesis(statement="test", status=status)
        assert h.is_active is True
        assert h.is_terminal is False


def test_hypothesis_serialization_roundtrip():
    h = Hypothesis(
        statement="Temp increases rate",
        confidence=0.7,
        status=HypothesisStatus.SUPPORTED,
        evidence_for=[Evidence(source="exp_1", strength=0.8, summary="Doubled", supports=True)],
        evidence_against=[],
        metadata={"a2l_artifact_id": "art_123"},
    )
    d = h.to_dict()
    h2 = Hypothesis.from_dict(d)
    assert h2.statement == h.statement
    assert h2.confidence == h.confidence
    assert h2.status == h.status
    assert len(h2.evidence_for) == 1
    assert h2.evidence_for[0].source == "exp_1"
    assert h2.metadata == h.metadata


# ── HypothesisManager ───────────────────────────────────────────────────────


def test_manager_create():
    mgr = HypothesisManager()
    h = mgr.create("Temperature increases reaction rate")
    assert h.statement == "Temperature increases reaction rate"
    assert h.status == HypothesisStatus.PROPOSED
    assert mgr.get(h.id) is h


def test_manager_create_with_parent():
    mgr = HypothesisManager()
    h1 = mgr.create("Original hypothesis")
    h2 = mgr.create("Revised hypothesis", parent_id=h1.id, revision_reason="New data")
    assert h2.parent_hypothesis_id == h1.id
    assert h1.status == HypothesisStatus.RETIRED  # Parent auto-retired
    assert h2.is_active is True


def test_manager_get_active():
    mgr = HypothesisManager()
    h1 = mgr.create("Active hypothesis")
    h2 = mgr.create("Another active")
    active = mgr.get_active()
    assert len(active) == 2
    assert h1 in active
    assert h2 in active


def test_manager_get_active_excludes_terminal():
    mgr = HypothesisManager()
    h = mgr.create("Will be refuted")
    h.status = HypothesisStatus.REFUTED
    assert len(mgr.get_active()) == 0


def test_manager_lineage():
    mgr = HypothesisManager()
    h1 = mgr.create("Root hypothesis")
    h2 = mgr.create("Revision 1", parent_id=h1.id, revision_reason="New evidence")
    h3 = mgr.create("Revision 2", parent_id=h2.id, revision_reason="Further data")

    lineage = mgr.get_lineage(h3.id)
    assert len(lineage) == 3
    assert lineage[0].id == h1.id
    assert lineage[1].id == h2.id
    assert lineage[2].id == h3.id


def test_manager_lineage_single():
    mgr = HypothesisManager()
    h = mgr.create("Root")
    lineage = mgr.get_lineage(h.id)
    assert len(lineage) == 1
    assert lineage[0].id == h.id


def test_manager_lineage_unknown():
    mgr = HypothesisManager()
    assert mgr.get_lineage("nonexistent") == []


# ── HypothesisManager.evaluate ──────────────────────────────────────────────


def test_evaluate_supports_hypothesis():
    mgr = HypothesisManager()
    h = mgr.create("Temp increases rate")
    evidence = [
        Evidence(source="exp_1", strength=0.8, summary="Rate doubled at +10K", supports=True),
        Evidence(source="lit_1", strength=0.6, summary="Arrhenius predicts this", supports=True),
    ]
    status = mgr.evaluate(h.id, evidence)
    assert status == HypothesisStatus.SUPPORTED
    assert h.confidence > 0.5
    assert len(h.evidence_for) == 2


def test_evaluate_refutes_hypothesis():
    mgr = HypothesisManager()
    h = mgr.create("Temp increases rate")
    evidence = [
        Evidence(source="exp_1", strength=0.9, summary="Rate decreased at +10K", supports=False),
        Evidence(source="exp_2", strength=0.7, summary="No change observed", supports=False),
    ]
    status = mgr.evaluate(h.id, evidence)
    assert status == HypothesisStatus.REFUTED
    assert h.confidence < 0.5
    assert len(h.evidence_against) == 2


def test_evaluate_inconclusive():
    mgr = HypothesisManager()
    h = mgr.create("Temp increases rate")
    evidence = [
        Evidence(source="exp_1", strength=0.5, summary="Rate increased slightly", supports=True),
        Evidence(source="exp_2", strength=0.4, summary="Rate decreased slightly", supports=False),
    ]
    status = mgr.evaluate(h.id, evidence)
    assert status == HypothesisStatus.INCONCLUSIVE


def test_evaluate_no_evidence_stays_proposed():
    mgr = HypothesisManager()
    h = mgr.create("Test hypothesis")
    status = mgr.evaluate(h.id, [])
    assert status == HypothesisStatus.PROPOSED


def test_evaluate_unknown_hypothesis():
    mgr = HypothesisManager()
    status = mgr.evaluate("nonexistent", [])
    assert status == HypothesisStatus.PROPOSED


# ── HypothesisManager.revise ────────────────────────────────────────────────


def test_revise_creates_child():
    mgr = HypothesisManager()
    h1 = mgr.create("Original")
    h2 = mgr.revise(h1.id, "Revised statement", "New evidence contradicts")
    assert h2.parent_hypothesis_id == h1.id
    assert h2.statement == "Revised statement"
    assert h1.status == HypothesisStatus.RETIRED
    assert h2.confidence < h1.confidence  # Inherited but lowered


def test_revise_unknown_raises():
    mgr = HypothesisManager()
    with pytest.raises(ValueError, match="unknown hypothesis"):
        mgr.revise("nonexistent", "new", "reason")


# ── HypothesisManager serialization ─────────────────────────────────────────


def test_serialization_roundtrip():
    mgr = HypothesisManager()
    h1 = mgr.create("Hypothesis A")
    mgr.evaluate(h1.id, [Evidence(source="s1", strength=0.8, summary="support", supports=True)])
    _h2 = mgr.create("Hypothesis B")

    d = mgr.to_dict()
    mgr2 = HypothesisManager.from_dict(d)

    assert len(mgr2.get_all()) == 2
    h1_restored = mgr2.get(h1.id)
    assert h1_restored is not None
    assert h1_restored.statement == "Hypothesis A"
    assert h1_restored.status == HypothesisStatus.SUPPORTED
    assert len(h1_restored.evidence_for) == 1


def test_serialization_empty():
    mgr = HypothesisManager()
    d = mgr.to_dict()
    mgr2 = HypothesisManager.from_dict(d)
    assert len(mgr2.get_all()) == 0


# ── HypothesisManager.summary ───────────────────────────────────────────────


def test_summary_empty():
    mgr = HypothesisManager()
    assert "No hypotheses" in mgr.summary()


def test_summary_with_hypotheses():
    mgr = HypothesisManager()
    mgr.create("Test hypothesis A")
    mgr.create("Test hypothesis B")
    summary = mgr.summary()
    assert "Test hypothesis A" in summary
    assert "Test hypothesis B" in summary
    assert "proposed" in summary


# ── Integration: ResearchSession with hypotheses ────────────────────────────


def test_session_hypotheses_field():
    session = ResearchSession(goal="Test research")
    assert session.hypotheses == []

    h = Hypothesis(statement="Test hypothesis")
    session.hypotheses.append(h)
    assert len(session.hypotheses) == 1
    assert session.hypotheses[0].statement == "Test hypothesis"


def test_iteration_record_hypothesis_fields():
    plan = WorkflowDefinition(name="test", description="")
    ir = IterationRecord(iteration=1, plan=plan)
    assert ir.hypotheses_tested == []
    assert ir.evidence_collected == []

    e = Evidence(source="exp_1", strength=0.7, summary="result", supports=True)
    ir.hypotheses_tested.append("h_001")
    ir.evidence_collected.append(e)
    assert len(ir.hypotheses_tested) == 1
    assert len(ir.evidence_collected) == 1


def test_orchestrator_status_new_states():
    assert OrchestratorStatus.HYPOTHESIZING == "hypothesizing"
    assert OrchestratorStatus.EVALUATING == "evaluating"
    assert OrchestratorStatus.HYPOTHESIZING.value == "hypothesizing"
    assert OrchestratorStatus.EVALUATING.value == "evaluating"
