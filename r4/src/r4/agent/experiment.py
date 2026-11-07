"""Experiment models and runner for structured HPC experiment execution.

Bridges the hypothesis-driven research framework (Phase 1) with UMRS durable
job tracking for real HPC experiment execution on Slurm/K8s/Compose backends.

Key abstractions:
- ``Experiment``: a first-class experiment entity linked to hypotheses and UMRS jobs
- ``SlurmExperimentSpec``: Slurm-specific resource requirements
- ``ExperimentResults``: structured output from a completed experiment
- ``ExperimentRunner``: lifecycle manager (design → submit → monitor → collect → evaluate)

Usage::

    from r4 import Client
    from r4.agent.experiment import ExperimentRunner, SlurmExperimentSpec

    client = Client()
    runner = ExperimentRunner(client)

    spec = SlurmExperimentSpec(
        script_path="/home/user/experiments/run_sim.sh",
        partition="gpu",
        gpus=1,
        time_limit="01:00:00",
    )
    exp = runner.design("Test temperature effect", spec, hypothesis_id="h_001")
    exp = runner.submit(exp)
    results = runner.collect_results(exp.job_id)
    evidence = runner.evaluate(results, hypothesis)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .hypothesis import Evidence, HypothesisManager

if TYPE_CHECKING:
    from ..client import Client

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class ExperimentStatus(str, Enum):
    """Lifecycle states for an experiment."""

    DESIGNED = "designed"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }

    @property
    def is_active(self) -> bool:
        return self in {ExperimentStatus.SUBMITTED, ExperimentStatus.RUNNING}


class SweepStrategy(str, Enum):
    """Parameter sweep strategies."""

    GRID = "grid"
    RANDOM = "random"
    LATIN_HYPERCUBE = "latin_hypercube"
    BAYESIAN = "bayesian"


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class SlurmExperimentSpec:
    """Slurm-specific resource requirements for an experiment.

    Maps to sbatch parameters when submitted via UMRS durable jobs.
    """

    script_path: str
    partition: str = "gpu"
    nodes: int = 1
    gpus: int = 0
    cpus_per_task: int = 4
    time_limit: str = "02:00:00"
    memory: str = "16G"
    extra_sbatch: Dict[str, str] = field(default_factory=dict)
    input_files: List[str] = field(default_factory=list)
    output_patterns: List[str] = field(default_factory=list)
    environment_vars: Dict[str, str] = field(default_factory=dict)

    def to_job_spec(self) -> Dict[str, Any]:
        """Convert to UMRS job spec dict for ``client.jobs.create()``."""
        spec: Dict[str, Any] = {
            "script_path": self.script_path,
            "partition": self.partition,
            "nodes": self.nodes,
            "cpus_per_task": self.cpus_per_task,
            "time_limit": self.time_limit,
            "memory": self.memory,
        }
        if self.gpus > 0:
            spec["gpus"] = self.gpus
        if self.extra_sbatch:
            spec["extra_sbatch"] = self.extra_sbatch
        if self.input_files:
            spec["input_files"] = self.input_files
        if self.output_patterns:
            spec["output_patterns"] = self.output_patterns
        if self.environment_vars:
            spec["environment_vars"] = self.environment_vars
        return spec


@dataclass
class ExperimentResults:
    """Structured output from a completed experiment."""

    exit_code: int = -1
    stdout_summary: str = ""
    parsed_outputs: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    log_uri: Optional[str] = None
    raw_status: str = ""
    completed_at: Optional[datetime] = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout_summary": self.stdout_summary,
            "parsed_outputs": self.parsed_outputs,
            "artifacts": self.artifacts,
            "resource_usage": self.resource_usage,
            "log_uri": self.log_uri,
            "raw_status": self.raw_status,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentResults":
        completed_at = data.get("completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)
        return cls(
            exit_code=data.get("exit_code", -1),
            stdout_summary=data.get("stdout_summary", ""),
            parsed_outputs=data.get("parsed_outputs", {}),
            artifacts=data.get("artifacts", []),
            resource_usage=data.get("resource_usage", {}),
            log_uri=data.get("log_uri"),
            raw_status=data.get("raw_status", ""),
            completed_at=completed_at,
        )


@dataclass
class Experiment:
    """A first-class experiment entity.

    Links a research hypothesis to an HPC job execution with structured
    result collection. Experiments are submitted through UMRS durable jobs
    and tracked via A2L artifact provenance.
    """

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    slurm_spec: Optional[SlurmExperimentSpec] = None
    expected_output_schema: Dict[str, Any] = field(default_factory=dict)
    status: ExperimentStatus = ExperimentStatus.DESIGNED
    results: Optional[ExperimentResults] = None
    hypothesis_id: Optional[str] = None
    job_id: Optional[str] = None
    project_id: Optional[str] = None
    experiment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    reward_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "slurm_spec": self.slurm_spec.to_job_spec() if self.slurm_spec else None,
            "expected_output_schema": self.expected_output_schema,
            "status": self.status.value,
            "results": self.results.to_dict() if self.results else None,
            "hypothesis_id": self.hypothesis_id,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
            "reward_score": self.reward_score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Experiment":
        slurm_data = data.get("slurm_spec")
        slurm_spec = SlurmExperimentSpec(**slurm_data) if slurm_data else None
        results_data = data.get("results")
        results = ExperimentResults.from_dict(results_data) if results_data else None
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        completed_at = data.get("completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)
        return cls(
            experiment_id=data.get("experiment_id", uuid.uuid4().hex[:12]),
            name=data["name"],
            description=data.get("description", ""),
            parameters=data.get("parameters", {}),
            slurm_spec=slurm_spec,
            expected_output_schema=data.get("expected_output_schema", {}),
            status=ExperimentStatus(data.get("status", "designed")),
            results=results,
            hypothesis_id=data.get("hypothesis_id"),
            job_id=data.get("job_id"),
            project_id=data.get("project_id"),
            created_at=created_at or datetime.now(timezone.utc),
            completed_at=completed_at,
            metadata=data.get("metadata", {}),
            reward_score=float(data.get("reward_score", 0.0)),
        )


# ── Experiment Runner ────────────────────────────────────────────────────────

# Default polling configuration
_POLL_INTERVAL = 5.0
_POLL_TIMEOUT = 7200.0  # 2 hours default
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ExperimentRunner:
    """Lifecycle manager for HPC experiments.

    Bridges the research hypothesis framework with UMRS durable job
    submission, monitoring, and result collection.

    All job submissions go through UMRS → Gateway → Slurm (never direct).
    """

    def __init__(
        self,
        client: Client,
        hypothesis_manager: Optional[HypothesisManager] = None,
    ) -> None:
        self._client = client
        self._hypothesis_manager = hypothesis_manager
        self._experiments: Dict[str, Experiment] = {}

    # ── Design ───────────────────────────────────────────────────────────

    def design(
        self,
        name: str,
        slurm_spec: SlurmExperimentSpec,
        *,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        hypothesis_id: Optional[str] = None,
        expected_output_schema: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Experiment:
        """Design a new experiment from a spec.

        Creates an Experiment in DESIGNED status, ready for submission.
        If ``hypothesis_id`` is provided, links the experiment to that
        hypothesis in the HypothesisManager.
        """
        exp = Experiment(
            name=name,
            description=description,
            parameters=parameters or {},
            slurm_spec=slurm_spec,
            hypothesis_id=hypothesis_id,
            expected_output_schema=expected_output_schema or {},
            project_id=project_id,
            metadata=metadata or {},
        )
        self._experiments[exp.experiment_id] = exp
        logger.info("Designed experiment %s: %s", exp.experiment_id, name)
        return exp

    # ── Submit ───────────────────────────────────────────────────────────

    def submit(self, experiment: Experiment) -> Experiment:
        """Submit an experiment via UMRS durable jobs.

        Converts the SlurmExperimentSpec to a UMRS job spec and submits
        through ``client.jobs.create()`` with ``job_type="pipeline_run"``.
        """
        if experiment.status != ExperimentStatus.DESIGNED:
            raise ValueError(
                f"Cannot submit experiment in {experiment.status.value} status; must be DESIGNED"
            )

        if not experiment.slurm_spec:
            raise ValueError("Experiment has no Slurm spec — cannot submit")

        job_spec = experiment.slurm_spec.to_job_spec()
        job_spec["experiment_id"] = experiment.experiment_id
        if experiment.hypothesis_id:
            job_spec["hypothesis_id"] = experiment.hypothesis_id
        job_spec["parameters"] = experiment.parameters

        project_id = experiment.project_id or self._client.config.project_id

        job = self._client.jobs.create(
            job_type="pipeline_run",
            spec=job_spec,
            project_id=project_id,
        )

        experiment.job_id = job.job_id
        experiment.status = ExperimentStatus.SUBMITTED
        logger.info(
            "Submitted experiment %s as UMRS job %s",
            experiment.experiment_id,
            job.job_id,
        )
        return experiment

    # ── Monitor ──────────────────────────────────────────────────────────

    def monitor(
        self,
        experiment: Experiment,
        *,
        poll_interval: float = _POLL_INTERVAL,
        poll_timeout: float = _POLL_TIMEOUT,
    ) -> Experiment:
        """Poll UMRS job until terminal state.

        Blocks until the experiment completes, fails, is cancelled, or
        the poll timeout is reached.
        """
        if not experiment.job_id:
            raise ValueError("Experiment has no UMRS job ID — cannot monitor")

        if experiment.status.is_terminal:
            return experiment

        experiment.status = ExperimentStatus.RUNNING
        elapsed = 0.0

        while elapsed < poll_timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                job = self._client.jobs.get(experiment.job_id)
            except Exception:
                logger.debug("Transient error polling job %s, retrying", experiment.job_id)
                continue

            if job.status in _TERMINAL_STATUSES:
                return self._update_from_job(experiment, job)

        logger.warning(
            "Experiment %s timed out after %.0fs", experiment.experiment_id, poll_timeout
        )
        return experiment

    def check_status(self, experiment: Experiment) -> ExperimentStatus:
        """Non-blocking status check for an experiment."""
        if not experiment.job_id:
            return experiment.status
        if experiment.status.is_terminal:
            return experiment.status

        try:
            job = self._client.jobs.get(experiment.job_id)
        except Exception:
            return experiment.status

        if job.status in _TERMINAL_STATUSES:
            self._update_from_job(experiment, job)
        elif job.status == "running":
            experiment.status = ExperimentStatus.RUNNING

        return experiment.status

    # ── Collect Results ──────────────────────────────────────────────────

    def collect_results(self, experiment: Experiment) -> ExperimentResults:
        """Collect and structure results from a completed experiment.

        Parses the UMRS job output into an ``ExperimentResults`` object.
        Must be called after the experiment reaches a terminal state.
        """
        if not experiment.job_id:
            raise ValueError("Experiment has no UMRS job ID — cannot collect results")

        if not experiment.status.is_terminal:
            logger.warning(
                "Collecting results from non-terminal experiment %s (status: %s)",
                experiment.experiment_id,
                experiment.status.value,
            )

        try:
            job = self._client.jobs.get(experiment.job_id)
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch job {experiment.job_id}: {exc}") from exc

        results = self._parse_job_results(job)
        experiment.results = results
        if experiment.status.is_terminal and experiment.completed_at is None:
            experiment.completed_at = datetime.now(timezone.utc)

        logger.info(
            "Collected results for experiment %s (exit_code=%d)",
            experiment.experiment_id,
            results.exit_code,
        )
        return results

    # ── Evaluate ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        results: ExperimentResults,
        hypothesis_id: str,
        *,
        summary: str = "",
    ) -> Evidence:
        """Evaluate experiment results against a hypothesis.

        Creates an ``Evidence`` object from the experiment results and
        evaluates the hypothesis in the HypothesisManager. Returns the
        evidence for further use.

        Requires a ``HypothesisManager`` to be configured.
        """
        if not self._hypothesis_manager:
            raise ValueError("No HypothesisManager configured — cannot evaluate")

        hypothesis = self._hypothesis_manager.get(hypothesis_id)
        if not hypothesis:
            raise ValueError(f"Unknown hypothesis: {hypothesis_id}")

        # Determine evidence strength from result quality
        strength = self._infer_evidence_strength(results)
        supports = results.succeeded

        evidence = Evidence(
            source=f"experiment:{results.raw_status}",
            strength=strength,
            summary=summary or results.stdout_summary[:200] if results.stdout_summary else "",
            supports=supports,
            metadata={
                "exit_code": results.exit_code,
                "resource_usage": results.resource_usage,
            },
        )

        self._hypothesis_manager.evaluate(hypothesis_id, [evidence])
        logger.info(
            "Evaluated hypothesis %s: strength=%.2f supports=%s",
            hypothesis_id,
            strength,
            supports,
        )
        return evidence

    # ── CRUD ─────────────────────────────────────────────────────────────

    def get(self, experiment_id: str) -> Optional[Experiment]:
        return self._experiments.get(experiment_id)

    def get_all(self) -> List[Experiment]:
        return list(self._experiments.values())

    def get_by_hypothesis(self, hypothesis_id: str) -> List[Experiment]:
        return [e for e in self._experiments.values() if e.hypothesis_id == hypothesis_id]

    def get_active(self) -> List[Experiment]:
        return [e for e in self._experiments.values() if e.status.is_active]

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiments": {eid: e.to_dict() for eid, e in self._experiments.items()},
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        client: Client,
        hypothesis_manager: Optional[HypothesisManager] = None,
    ) -> "ExperimentRunner":
        runner = cls(client, hypothesis_manager=hypothesis_manager)
        for eid, edata in data.get("experiments", {}).items():
            runner._experiments[eid] = Experiment.from_dict(edata)
        return runner

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _update_from_job(self, experiment: Experiment, job: Any) -> Experiment:
        """Update experiment status from UMRS job state."""
        status_map = {
            "completed": ExperimentStatus.COMPLETED,
            "failed": ExperimentStatus.FAILED,
            "cancelled": ExperimentStatus.CANCELLED,
        }
        experiment.status = status_map.get(job.status, ExperimentStatus.FAILED)
        if experiment.status.is_terminal:
            experiment.completed_at = datetime.now(timezone.utc)
        logger.info(
            "Experiment %s updated to %s from job %s",
            experiment.experiment_id,
            experiment.status.value,
            job.job_id,
        )
        return experiment

    def _parse_job_results(self, job: Any) -> ExperimentResults:
        """Parse UMRS job output into ExperimentResults."""
        results = ExperimentResults(
            raw_status=job.status,
            exit_code=0 if job.status == "completed" else 1,
        )

        # Extract commit attempt details
        if job.commit_attempt_id and hasattr(job, "attempts"):
            commit = next(
                (a for a in job.attempts if a.attempt_id == job.commit_attempt_id),
                None,
            )
            if commit:
                results.log_uri = commit.log_uri
                results.resource_usage = commit.metrics if commit.metrics else {}
                if commit.exit_reason:
                    results.stdout_summary = commit.exit_reason

        return results

    @staticmethod
    def _infer_evidence_strength(results: ExperimentResults) -> float:
        """Infer evidence strength from experiment result quality."""
        if not results.succeeded:
            return 0.3  # Failed experiment = weak evidence against

        # Stronger evidence if we have structured outputs
        strength = 0.5
        if results.parsed_outputs:
            strength = 0.7
        if results.resource_usage:
            strength = min(strength + 0.1, 0.9)
        if results.artifacts:
            strength = min(strength + 0.05, 0.95)

        return strength
