"""Tests for the structured HPC experiment execution framework (Phase 2).

Covers:
- Experiment, ExperimentResults, SlurmExperimentSpec model behavior
- ExperimentRunner: design, submit, monitor, collect, evaluate, CRUD, serialization
- ExperimentStatus and SweepStrategy enums
- Output parsers: RegexParser, JSONParser, CSVParser, CompositeParser
- ParameterSweep: grid, random, latin_hypercube generation
- SweepResults aggregation
- Integration with ResearchSession and OrchestratorStatus
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from r4.agent.experiment import (
    Experiment,
    ExperimentResults,
    ExperimentRunner,
    ExperimentStatus,
    SlurmExperimentSpec,
    SweepStrategy,
)
from r4.agent.hypothesis import HypothesisManager
from r4.agent.models import WorkflowDefinition
from r4.agent.output_parser import (
    CompositeParser,
    CSVParser,
    JSONParser,
    LLMParser,
    RegexParser,
)
from r4.agent.parameter_sweep import ParameterSweep
from r4.agent.research_orchestrator import (
    IterationRecord,
    OrchestratorStatus,
    ResearchSession,
)

# ── SlurmExperimentSpec ─────────────────────────────────────────────────────


def test_slurm_spec_defaults():
    spec = SlurmExperimentSpec(script_path="/run.sh")
    assert spec.partition == "gpu"
    assert spec.nodes == 1
    assert spec.gpus == 0
    assert spec.cpus_per_task == 4
    assert spec.time_limit == "02:00:00"
    assert spec.memory == "16G"


def test_slurm_spec_to_job_spec():
    spec = SlurmExperimentSpec(
        script_path="/run.sh",
        partition="cpu",
        gpus=2,
        extra_sbatch={"account": "proj_x"},
    )
    job_spec = spec.to_job_spec()
    assert job_spec["script_path"] == "/run.sh"
    assert job_spec["partition"] == "cpu"
    assert job_spec["gpus"] == 2
    assert job_spec["extra_sbatch"] == {"account": "proj_x"}
    assert "gpus" in job_spec


def test_slurm_spec_no_gpus_omitted():
    spec = SlurmExperimentSpec(script_path="/run.sh", gpus=0)
    job_spec = spec.to_job_spec()
    assert "gpus" not in job_spec


# ── ExperimentResults ────────────────────────────────────────────────────────


def test_results_defaults():
    r = ExperimentResults()
    assert r.exit_code == -1
    assert r.succeeded is False


def test_results_succeeded():
    r = ExperimentResults(exit_code=0)
    assert r.succeeded is True


def test_results_serialization_roundtrip():
    r = ExperimentResults(
        exit_code=0,
        stdout_summary="All good",
        parsed_outputs={"accuracy": 0.95},
        artifacts=["art_1"],
        resource_usage={"gpu_hours": 1.5},
        raw_status="completed",
        completed_at=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
    )
    d = r.to_dict()
    r2 = ExperimentResults.from_dict(d)
    assert r2.exit_code == 0
    assert r2.parsed_outputs["accuracy"] == 0.95
    assert r2.completed_at is not None


# ── Experiment ────────────────────────────────────────────────────────────────


def test_experiment_creation():
    e = Experiment(name="Test experiment")
    assert e.name == "Test experiment"
    assert e.status == ExperimentStatus.DESIGNED
    assert e.experiment_id  # auto-generated
    assert e.results is None


def test_experiment_with_spec():
    spec = SlurmExperimentSpec(script_path="/run.sh", gpus=1)
    e = Experiment(name="GPU experiment", slurm_spec=spec, hypothesis_id="h_001")
    assert e.slurm_spec is spec
    assert e.hypothesis_id == "h_001"


def test_experiment_serialization_roundtrip():
    spec = SlurmExperimentSpec(script_path="/run.sh", gpus=2)
    results = ExperimentResults(exit_code=0, parsed_outputs={"loss": 0.01})
    e = Experiment(
        name="Test",
        description="A test experiment",
        slurm_spec=spec,
        status=ExperimentStatus.COMPLETED,
        results=results,
        hypothesis_id="h_001",
    )
    d = e.to_dict()
    e2 = Experiment.from_dict(d)
    assert e2.name == "Test"
    assert e2.status == ExperimentStatus.COMPLETED
    assert e2.slurm_spec is not None
    assert e2.slurm_spec.gpus == 2
    assert e2.results is not None
    assert e2.results.parsed_outputs["loss"] == 0.01
    assert e2.hypothesis_id == "h_001"


# ── ExperimentStatus ──────────────────────────────────────────────────────────


def test_experiment_status_terminal():
    for status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED):
        assert status.is_terminal is True
        assert status.is_active is False


def test_experiment_status_active():
    for status in (ExperimentStatus.SUBMITTED, ExperimentStatus.RUNNING):
        assert status.is_active is True
        assert status.is_terminal is False


def test_experiment_status_designed():
    assert ExperimentStatus.DESIGNED.is_terminal is False
    assert ExperimentStatus.DESIGNED.is_active is False


# ── ExperimentRunner ──────────────────────────────────────────────────────────


def _mock_client():
    client = MagicMock()
    client.config.project_id = "test-project"
    return client


def test_runner_design():
    runner = ExperimentRunner(_mock_client())
    spec = SlurmExperimentSpec(script_path="/run.sh")
    exp = runner.design("Test experiment", spec, hypothesis_id="h_001")
    assert exp.status == ExperimentStatus.DESIGNED
    assert exp.hypothesis_id == "h_001"
    assert runner.get(exp.experiment_id) is exp


def test_runner_design_with_parameters():
    runner = ExperimentRunner(_mock_client())
    spec = SlurmExperimentSpec(script_path="/run.sh")
    exp = runner.design(
        "Param experiment",
        spec,
        parameters={"lr": 0.001, "epochs": 10},
    )
    assert exp.parameters["lr"] == 0.001


def test_runner_submit():
    client = _mock_client()
    job_mock = MagicMock()
    job_mock.job_id = "job_abc123"
    client.jobs.create.return_value = job_mock

    runner = ExperimentRunner(client)
    spec = SlurmExperimentSpec(script_path="/run.sh")
    exp = runner.design("Test", spec)
    exp = runner.submit(exp)

    assert exp.job_id == "job_abc123"
    assert exp.status == ExperimentStatus.SUBMITTED
    client.jobs.create.assert_called_once()


def test_runner_submit_not_designed_raises():
    client = _mock_client()
    runner = ExperimentRunner(client)
    exp = Experiment(name="Test", status=ExperimentStatus.RUNNING)
    with pytest.raises(ValueError, match="must be DESIGNED"):
        runner.submit(exp)


def test_runner_submit_no_spec_raises():
    client = _mock_client()
    runner = ExperimentRunner(client)
    exp = Experiment(name="Test")  # No slurm_spec
    with pytest.raises(ValueError, match="no Slurm spec"):
        runner.submit(exp)


def test_runner_check_status():
    client = _mock_client()
    job_mock = MagicMock()
    job_mock.status = "completed"
    job_mock.job_id = "job_123"
    job_mock.commit_attempt_id = "att_1"
    job_mock.attempts = []
    client.jobs.get.return_value = job_mock

    runner = ExperimentRunner(client)
    spec = SlurmExperimentSpec(script_path="/run.sh")
    exp = runner.design("Test", spec)
    exp.job_id = "job_123"
    exp.status = ExperimentStatus.RUNNING

    status = runner.check_status(exp)
    assert status == ExperimentStatus.COMPLETED


def test_runner_collect_results():
    client = _mock_client()
    job_mock = MagicMock()
    job_mock.status = "completed"
    job_mock.commit_attempt_id = None
    job_mock.attempts = []
    client.jobs.get.return_value = job_mock

    runner = ExperimentRunner(client)
    exp = Experiment(
        name="Test",
        status=ExperimentStatus.COMPLETED,
        job_id="job_123",
    )
    results = runner.collect_results(exp)
    assert results.exit_code == 0
    assert results.raw_status == "completed"


def test_runner_evaluate():
    client = _mock_client()
    mgr = HypothesisManager()
    h = mgr.create("Temperature increases rate")

    runner = ExperimentRunner(client, hypothesis_manager=mgr)
    results = ExperimentResults(exit_code=0, stdout_summary="Rate doubled")
    evidence = runner.evaluate(results, h.id, summary="Positive result")
    assert evidence.supports is True
    assert evidence.strength > 0


def test_runner_evaluate_no_manager_raises():
    client = _mock_client()
    runner = ExperimentRunner(client)
    results = ExperimentResults()
    with pytest.raises(ValueError, match="No HypothesisManager"):
        runner.evaluate(results, "h_001")


def test_runner_get_by_hypothesis():
    runner = ExperimentRunner(_mock_client())
    spec = SlurmExperimentSpec(script_path="/run.sh")
    e1 = runner.design("Exp A", spec, hypothesis_id="h_001")
    runner.design("Exp B", spec, hypothesis_id="h_002")
    e3 = runner.design("Exp C", spec, hypothesis_id="h_001")

    results = runner.get_by_hypothesis("h_001")
    assert len(results) == 2
    assert e1 in results
    assert e3 in results


def test_runner_get_active():
    runner = ExperimentRunner(_mock_client())
    spec = SlurmExperimentSpec(script_path="/run.sh")
    e1 = runner.design("Active", spec)
    e1.status = ExperimentStatus.RUNNING
    e2 = runner.design("Inactive", spec)
    e2.status = ExperimentStatus.COMPLETED

    active = runner.get_active()
    assert len(active) == 1
    assert e1 in active


def test_runner_serialization():
    runner = ExperimentRunner(_mock_client())
    spec = SlurmExperimentSpec(script_path="/run.sh")
    runner.design("Exp A", spec)
    runner.design("Exp B", spec, hypothesis_id="h_001")

    d = runner.to_dict()
    runner2 = ExperimentRunner.from_dict(d, _mock_client())
    assert len(runner2.get_all()) == 2


# ── Output Parsers ──────────────────────────────────────────────────────────


def test_regex_parser():
    parser = RegexParser(pattern=r"loss: (?P<loss>\d+\.\d+)")
    result = parser.parse(
        "Epoch 1: loss: 0.523\nEpoch 2: loss: 0.312\n",
        {"loss": float},
    )
    assert result["loss"] == 0.312  # Last match wins


def test_regex_parser_no_match():
    parser = RegexParser(pattern=r"accuracy: (?P<acc>\d+\.\d+)")
    result = parser.parse("no match here", {"acc": float})
    assert result == {}


def test_regex_parser_type_coercion():
    parser = RegexParser(pattern=r"count: (?P<count>\d+)")
    result = parser.parse("count: 42", {"count": int})
    assert result["count"] == 42
    assert isinstance(result["count"], int)


def test_json_parser_object():
    parser = JSONParser()
    result = parser.parse('{"loss": 0.5, "acc": 0.9}', {"loss": float, "acc": float})
    assert result["loss"] == 0.5
    assert result["acc"] == 0.9


def test_json_parser_with_key():
    parser = JSONParser(key="metrics")
    result = parser.parse(
        '{"metrics": {"gpu_util": 85}, "other": "data"}',
        {"gpu_util": int},
    )
    assert result["gpu_util"] == 85


def test_json_parser_jsonl():
    parser = JSONParser()
    result = parser.parse(
        '{"epoch": 1, "loss": 0.5}\n{"epoch": 2, "loss": 0.3}\n',
        {"epoch": int, "loss": float},
    )
    assert result["epoch"] == 2  # Last line wins
    assert result["loss"] == 0.3


def test_json_parser_empty():
    parser = JSONParser()
    result = parser.parse("", {})
    assert result == {}


def test_csv_parser():
    parser = CSVParser()
    output = "name,value\nalpha,0.9\nbeta,0.7\n"
    result = parser.parse(output, {"name": str, "value": float})
    assert result["name"] == "beta"  # Last row
    assert result["value"] == 0.7


def test_csv_parser_empty():
    parser = CSVParser()
    result = parser.parse("", {})
    assert result == {}


def test_composite_parser():
    regex_parser = RegexParser(pattern=r"GPU (?P<gpu_id>\d+): (?P<util>\d+)%")
    json_parser = JSONParser()

    composite = CompositeParser([regex_parser, json_parser])
    output = 'GPU 0: 85%\n{"loss": 0.3, "acc": 0.9}'
    result = composite.parse(output, {"gpu_id": int, "util": int, "loss": float, "acc": float})
    assert result["gpu_id"] == 0
    assert result["util"] == 85
    assert result["loss"] == 0.3


def test_llm_parser_no_client():
    parser = LLMParser()
    result = parser.parse("some output", {})
    assert result == {}


# ── ParameterSweep ──────────────────────────────────────────────────────────


def test_sweep_grid():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2], "y": ["a", "b"]},
        strategy=SweepStrategy.GRID,
    )
    experiments = sweep.generate_jobs()
    assert len(experiments) == 4  # 2 * 2
    params = [e.parameters for e in experiments]
    assert {"x": 1, "y": "a"} in params
    assert {"x": 2, "y": "b"} in params


def test_sweep_grid_max_experiments():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2, 3], "y": [4, 5, 6]},
        strategy=SweepStrategy.GRID,
        max_experiments=5,
    )
    experiments = sweep.generate_jobs()
    assert len(experiments) == 5


def test_sweep_random():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2, 3, 4, 5]},
        strategy=SweepStrategy.RANDOM,
        max_experiments=3,
        seed=42,
    )
    experiments = sweep.generate_jobs()
    assert len(experiments) == 3
    for exp in experiments:
        assert exp.parameters["x"] in [1, 2, 3, 4, 5]


def test_sweep_latin_hypercube():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2, 3, 4], "y": [10, 20, 30, 40]},
        strategy=SweepStrategy.LATIN_HYPERCUBE,
        max_experiments=4,
        seed=42,
    )
    experiments = sweep.generate_jobs()
    assert len(experiments) == 4
    for exp in experiments:
        assert exp.parameters["x"] in [1, 2, 3, 4]
        assert exp.parameters["y"] in [10, 20, 30, 40]


def test_sweep_bayesian_fallback():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2, 3]},
        strategy=SweepStrategy.BAYESIAN,
        max_experiments=2,
    )
    experiments = sweep.generate_jobs()
    assert len(experiments) == 2  # Falls back to random


def test_sweep_hypothesis_linkage():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2]},
        strategy=SweepStrategy.GRID,
        hypothesis_id="h_001",
    )
    experiments = sweep.generate_jobs()
    for exp in experiments:
        assert exp.hypothesis_id == "h_001"


def test_sweep_results_aggregation():
    sweep = ParameterSweep(
        base_name="test",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"x": [1, 2]},
        strategy=SweepStrategy.GRID,
        score_key="accuracy",
    )

    client = _mock_client()
    runner = ExperimentRunner(client)

    # Generate experiments and register them with the runner
    experiments = sweep.generate_jobs()
    for exp in experiments:
        runner._experiments[exp.experiment_id] = exp

    # Simulate completion
    experiments[0].status = ExperimentStatus.COMPLETED
    experiments[0].results = ExperimentResults(
        exit_code=0,
        parsed_outputs={"accuracy": 0.9},
    )
    experiments[1].status = ExperimentStatus.COMPLETED
    experiments[1].results = ExperimentResults(
        exit_code=0,
        parsed_outputs={"accuracy": 0.7},
    )

    results = sweep.aggregate_results(runner)
    assert results.completed == 2
    assert results.best_parameters == {"x": 1}  # 0.9 > 0.7
    assert results.best_score == 0.9


# ── Integration: ResearchSession with experiments ────────────────────────────


def test_session_experiments_field():
    session = ResearchSession(goal="Test research")
    assert session.experiments == []

    exp = Experiment(name="Test experiment")
    session.experiments.append(exp)
    assert len(session.experiments) == 1


def test_orchestrator_experimenting_status():
    assert OrchestratorStatus.EXPERIMENTING == "experimenting"
    assert OrchestratorStatus.EXPERIMENTING.value == "experimenting"


def test_iteration_record_fields():
    plan = WorkflowDefinition(name="test", description="")
    ir = IterationRecord(iteration=1, plan=plan)
    assert ir.hypotheses_tested == []
    assert ir.evidence_collected == []


# ── SweepStrategy Enum ──────────────────────────────────────────────────────


def test_sweep_strategy_values():
    assert SweepStrategy.GRID == "grid"
    assert SweepStrategy.RANDOM == "random"
    assert SweepStrategy.LATIN_HYPERCUBE == "latin_hypercube"
    assert SweepStrategy.BAYESIAN == "bayesian"
