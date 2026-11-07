"""Experiment — first-class scientific object.

An :class:`Experiment` wraps a UMRS workflow execution, adding
scientific context (hypothesis, protocol, results, reward).

The ``execute()`` method delegates to
``umrs.server.workflow_runner.run_workflow_record()``, so the
experiment layer is a thin wrapper over the existing execution
substrate.

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §2.2
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExperimentStatus(str, Enum):
    """Lifecycle status of an experiment."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ExperimentResults:
    """Structured output from a completed experiment.

    Attributes:
        metrics: Numeric results keyed by metric name
                 (e.g. ``{"recall": 0.82, "false_positive_rate": 0.15}``).
        artifacts: URIs of output artifacts.
        logs: Execution logs (stdout/stderr capture).
        duration_seconds: Wall-clock execution time.
        error: Error message if the experiment failed.
    """

    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    logs: str = ""
    duration_seconds: float = 0.0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """True if the experiment completed without error."""
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dictionary."""
        return {
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "logs": self.logs,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "succeeded": self.succeeded,
        }


@dataclass
class Experiment:
    """A first-class scientific experiment.

    Attributes:
        id: Unique experiment identifier (auto-generated if not provided).
        hypothesis_id: ID of the hypothesis this experiment tests.
        protocol: Experiment protocol/parameters
                  (e.g. ``{"distance_threshold": 0.05}``).
        workflow_ref: Reference to the UMRS workflow to execute.
        status: Current lifecycle status.
        results: Results from execution (populated after ``execute()``).
        reward_score: Scientific reward from execution
                      (populated after reward computation).
        input_files: List of input file paths for the experiment.
        preprocessing_steps: Auto-configured pre-processing steps
                             based on input file modalities.
        created_at: Creation timestamp.
        completed_at: Completion timestamp (if completed).
    """

    hypothesis_id: str
    protocol: dict[str, Any] = field(default_factory=dict)
    workflow_ref: str = ""
    id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:8]}")
    status: ExperimentStatus = ExperimentStatus.PENDING
    results: ExperimentResults | None = None
    reward_score: float = 0.0
    input_files: list[str] = field(default_factory=list)
    preprocessing_steps: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def auto_configure_preprocessing(self) -> None:
        """Auto-configure pre-processing steps based on input file modalities.

        Uses :func:`umrs.shared.modality.detect_modality` to detect
        the modality of each input file, then configures the
        appropriate pre-processing step:

        - ``movie`` → VideoStep (frame extraction)
        - ``audio`` → AudioStep (transcription)
        - ``notebook`` → NotebookStep (cell chunking)
        - ``code`` → CodeStep (AST chunking)
        - ``text_file`` → no pre-processing (direct ingestion)
        - ``image`` → no pre-processing (direct ingestion)

        This is called automatically by :meth:`execute` when
        ``input_files`` is non-empty and ``preprocessing_steps`` is
        empty.
        """
        if not self.input_files or self.preprocessing_steps:
            return

        try:
            from umrs.shared.modality import detect_modality
        except ImportError:
            return

        # Group files by modality
        modality_files: dict[str, list[str]] = {}
        for file_path in self.input_files:
            modality = detect_modality(file_path)
            modality_files.setdefault(modality, []).append(file_path)

        # Configure pre-processing steps based on modalities
        steps: list[dict[str, Any]] = []

        if "movie" in modality_files:
            steps.append(
                {
                    "name": "video_preprocess",
                    "entrypoint": "umrs.pipeline_sdk.templates.video_step:VideoStep",
                    "config": {
                        "fps": self.protocol.get("video_fps", 2.0),
                        "max_frames": 100,
                    },
                }
            )

        if "audio" in modality_files:
            steps.append(
                {
                    "name": "audio_preprocess",
                    "entrypoint": "umrs.pipeline_sdk.templates.audio_step:AudioStep",
                    "config": {
                        "language": self.protocol.get("audio_language", "en"),
                    },
                }
            )

        if "notebook" in modality_files:
            steps.append(
                {
                    "name": "notebook_preprocess",
                    "entrypoint": "umrs.pipeline_sdk.templates.notebook_step:NotebookStep",
                    "config": {
                        "mode": "cells",
                    },
                }
            )

        if "code" in modality_files:
            steps.append(
                {
                    "name": "code_preprocess",
                    "entrypoint": "umrs.pipeline_sdk.templates.code_step:CodeStep",
                    "config": {
                        "language": "python",
                        "include_imports": True,
                        "include_docstrings": True,
                    },
                }
            )

        self.preprocessing_steps = steps

    def execute(
        self,
        workflow_runner: Any | None = None,
        workflow_json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ExperimentResults:
        """Execute the experiment by running the referenced workflow.

        Delegates to ``umrs.server.workflow_runner.run_workflow_record()``
        when a ``workflow_runner`` callable is provided.  Otherwise,
        falls back to a simple in-process execution that can be
        overridden by domain plugins.

        Args:
            workflow_runner: Callable that runs the workflow
                             (typically ``run_workflow_record``).
            workflow_json: Workflow definition to execute.
            **kwargs: Additional arguments passed to the workflow runner.

        Returns:
            :class:`ExperimentResults` containing the execution output.
        """
        self.status = ExperimentStatus.RUNNING
        start_time = time.time()

        # Auto-configure pre-processing steps if input files are provided
        if self.input_files:
            self.auto_configure_preprocessing()

        try:
            if workflow_runner is not None and workflow_json is not None:
                import warnings

                warnings.warn(
                    "Experiment.execute(workflow_runner=...) is deprecated; "
                    "use umrs.knowledge.execution_port.WorkflowExecutionPort "
                    "(injected via ResearchProject / R4ClientWorkflowExecutor).",
                    DeprecationWarning,
                    stacklevel=2,
                )
                # Delegate to UMRS workflow runner
                record = workflow_runner(
                    workflow_json=workflow_json,
                    **kwargs,
                )
                metrics = _extract_metrics(record)
                artifacts = _extract_artifacts(record)
                logs = _extract_logs(record)
                self.results = ExperimentResults(
                    metrics=metrics,
                    artifacts=artifacts,
                    logs=logs,
                    duration_seconds=time.time() - start_time,
                )
            else:
                # No workflow runner: return empty results
                # Domain plugins can override this behaviour
                self.results = ExperimentResults(
                    duration_seconds=time.time() - start_time,
                )

            self.status = ExperimentStatus.COMPLETED
            self.completed_at = time.time()
            return self.results

        except Exception as exc:
            self.status = ExperimentStatus.FAILED
            self.completed_at = time.time()
            self.results = ExperimentResults(
                error=str(exc),
                duration_seconds=time.time() - start_time,
            )
            return self.results

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dictionary for A2L metadata."""
        return {
            "id": self.id,
            "hypothesis_id": self.hypothesis_id,
            "protocol": self.protocol,
            "workflow_ref": self.workflow_ref,
            "status": self.status.value,
            "results": self.results.to_dict() if self.results else None,
            "reward_score": self.reward_score,
            "input_files": self.input_files,
            "preprocessing_steps": self.preprocessing_steps,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


def _extract_metrics(record: Any) -> dict[str, float]:
    """Extract metrics from a workflow execution record."""
    metrics: dict[str, float] = {}
    results = getattr(record, "results", None) or {}
    if isinstance(results, dict):
        for key, value in results.items():
            if isinstance(value, (int, float)):
                metrics[key] = float(value)
    return metrics


def _extract_artifacts(record: Any) -> list[str]:
    """Extract artifact URIs from a workflow execution record."""
    artifacts = getattr(record, "artifacts", None) or []
    return list(artifacts) if artifacts else []


def _extract_logs(record: Any) -> str:
    """Extract logs from a workflow execution record."""
    logs = getattr(record, "logs", None) or ""
    return str(logs) if logs else ""
