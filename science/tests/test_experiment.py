"""Unit tests for Experiment.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.4
"""

from science.experiment import (
    Experiment,
    ExperimentResults,
    ExperimentStatus,
)


class TestExperimentCreation:
    """Tests for Experiment construction."""

    def test_default_creation(self):
        """Experiment can be created with minimal args."""
        exp = Experiment(hypothesis_id="h_001")
        assert exp.hypothesis_id == "h_001"
        assert exp.status == ExperimentStatus.PENDING
        assert exp.results is None
        assert exp.reward_score == 0.0
        assert exp.id.startswith("exp_")

    def test_with_protocol(self):
        """Experiment accepts a protocol dict."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"distance_threshold": 0.05},
            workflow_ref="example_analyze",
        )
        assert exp.protocol == {"distance_threshold": 0.05}
        assert exp.workflow_ref == "example_analyze"

    def test_unique_ids(self):
        """Each Experiment gets a unique id."""
        exp1 = Experiment(hypothesis_id="h_001")
        exp2 = Experiment(hypothesis_id="h_001")
        assert exp1.id != exp2.id


class TestExperimentExecute:
    """Tests for the ``execute()`` method."""

    def test_execute_without_runner(self):
        """Execute without a workflow runner returns empty results."""
        exp = Experiment(hypothesis_id="h_001")
        results = exp.execute()
        assert exp.status == ExperimentStatus.COMPLETED
        assert results.succeeded
        assert results.metrics == {}

    def test_execute_with_mock_runner(self):
        """Execute with a mock runner extracts metrics."""

        class MockRecord:
            results = {"recall": 0.82, "false_positive_rate": 0.15}
            artifacts = ["s3://bucket/output.json"]
            logs = "Processing complete"

        def mock_runner(workflow_json, **kwargs):
            return MockRecord()

        exp = Experiment(hypothesis_id="h_001", protocol={"threshold": 0.05})
        results = exp.execute(workflow_runner=mock_runner, workflow_json={})

        assert exp.status == ExperimentStatus.COMPLETED
        assert results.succeeded
        assert results.metrics["recall"] == 0.82
        assert results.metrics["false_positive_rate"] == 0.15
        assert len(results.artifacts) == 1

    def test_execute_failure(self):
        """Execute captures errors on failure."""

        def failing_runner(workflow_json, **kwargs):
            raise RuntimeError("Workflow crashed")

        exp = Experiment(hypothesis_id="h_001")
        results = exp.execute(workflow_runner=failing_runner, workflow_json={})

        assert exp.status == ExperimentStatus.FAILED
        assert not results.succeeded
        assert "Workflow crashed" in results.error


class TestExperimentResults:
    """Tests for ExperimentResults."""

    def test_succeeded_property(self):
        """``succeeded`` is True when no error."""
        results = ExperimentResults(metrics={"recall": 0.8})
        assert results.succeeded

    def test_failed_results(self):
        """``succeeded`` is False when error is present."""
        results = ExperimentResults(error="Something went wrong")
        assert not results.succeeded

    def test_to_dict(self):
        """``to_dict`` contains all fields."""
        results = ExperimentResults(
            metrics={"recall": 0.8},
            artifacts=["s3://bucket/output.json"],
            duration_seconds=60.0,
        )
        d = results.to_dict()
        assert "metrics" in d
        assert "artifacts" in d
        assert "duration_seconds" in d
        assert "succeeded" in d


class TestExperimentSerialisation:
    """Tests for Experiment serialisation."""

    def test_to_dict(self):
        """``to_dict`` contains all experiment fields."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"threshold": 0.05},
            workflow_ref="example_analyze",
        )
        d = exp.to_dict()
        assert d["hypothesis_id"] == "h_001"
        assert d["protocol"] == {"threshold": 0.05}
        assert d["workflow_ref"] == "example_analyze"
        assert d["status"] == "pending"
