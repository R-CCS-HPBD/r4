"""Unit tests for ResearchProgram.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §5.4
"""

import pytest

from r4.agent.research_program import (
    Metric,
    ProgramBudget,
    ProgramStatus,
    ResearchProgram,
)


class TestMetric:
    def test_metric_defaults(self):
        metric = Metric(name="recall")
        assert metric.name == "recall"
        assert metric.direction == "maximize"
        assert metric.weight == 1.0

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            Metric(name="recall", direction="sideways")


class TestProgramBudget:
    def test_remaining_budget(self):
        budget = ProgramBudget(
            total_gpu_hours=100.0,
            total_iterations=40,
            total_cost=500.0,
            gpu_hours_used=25.0,
            iterations_used=10,
            cost_used=100.0,
        )
        assert budget.remaining_gpu_hours == 75.0
        assert budget.remaining_iterations == 30
        assert budget.remaining_cost == 400.0
        assert budget.fraction_remaining == pytest.approx(0.75)

    def test_consume(self):
        budget = ProgramBudget()
        budget.consume(gpu_hours=2.5, iterations=3, cost=10.0)
        assert budget.gpu_hours_used == pytest.approx(2.5)
        assert budget.iterations_used == 3
        assert budget.cost_used == pytest.approx(10.0)

    def test_is_exhausted(self):
        budget = ProgramBudget(total_iterations=2, iterations_used=2)
        assert budget.is_exhausted is True


class TestResearchProgram:
    def test_initialization(self):
        program = ResearchProgram(
            id="rp_001",
            name="Autonomous Detection Study",
            goals=["Maximize object detection accuracy"],
            metrics=[
                Metric(name="recall"),
                Metric(name="false_positive_rate", direction="minimize"),
            ],
        )
        assert program.id == "rp_001"
        assert program.status == ProgramStatus.ACTIVE
        assert len(program.metrics) == 2
        assert program.can_run_more() is True

    def test_question_management(self):
        program = ResearchProgram(id="rp_001", name="Test")
        program.add_question("q_001")
        program.add_question("q_001")
        program.add_question("q_002")
        assert program.questions == ["q_001", "q_002"]

        program.remove_question("q_001")
        assert program.questions == ["q_002"]

    def test_status_transitions(self):
        program = ResearchProgram(id="rp_001", name="Test")
        program.pause()
        assert program.status == ProgramStatus.PAUSED
        program.resume()
        assert program.status == ProgramStatus.ACTIVE
        program.complete()
        assert program.status == ProgramStatus.COMPLETED

    def test_invalid_pause_transition_raises(self):
        program = ResearchProgram(id="rp_001", name="Test", status=ProgramStatus.PAUSED)
        with pytest.raises(ValueError):
            program.pause()

    def test_budget_exhaustion_blocks_program(self):
        program = ResearchProgram(id="rp_001", name="Test")
        program.budget.consume(
            gpu_hours=program.budget.total_gpu_hours,
            iterations=0,
            cost=0.0,
        )
        assert program.budget_exhausted is True
        assert program.can_run_more() is False

    def test_to_dict_round_trip(self):
        program = ResearchProgram(
            id="rp_001",
            name="Autonomous Detection Study",
            goals=["Goal A"],
            questions=["q_001"],
            metrics=[Metric(name="recall")],
            description="Long-running autonomous study",
        )
        program.budget.consume(gpu_hours=3.0, iterations=2, cost=5.0)

        data = program.to_dict()
        restored = ResearchProgram.from_dict(data)

        assert restored.id == program.id
        assert restored.name == program.name
        assert restored.questions == ["q_001"]
        assert restored.budget.gpu_hours_used == pytest.approx(3.0)
        assert restored.metrics[0].name == "recall"
        assert restored.description == "Long-running autonomous study"
