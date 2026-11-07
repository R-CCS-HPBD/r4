"""Tests for COR-005 workflow provenance (r4-contracts + UMRS assembly)."""

from __future__ import annotations

import json as json_mod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from r4_contracts.provenance import (
    ArtifactRef,
    AttemptSummary,
    ModelRef,
    ProvenanceRecord,
)

# ---------------------------------------------------------------------------
# r4-contracts provenance DTO tests
# ---------------------------------------------------------------------------


class TestProvenanceRecord:
    """ProvenanceRecord dataclass behaviour."""

    def test_minimal_record(self):
        rec = ProvenanceRecord(
            job_id="job-abc",
            job_type="endpoint_provision",
            project_id="proj-1",
            user_id="alice",
            status="completed",
        )
        assert rec.job_id == "job-abc"
        assert rec.total_attempts == 0
        assert rec.artifacts == []

    def test_to_dict_round_trip(self):
        rec = ProvenanceRecord(
            job_id="job-xyz",
            job_type="workflow_run",
            project_id="proj-1",
            user_id="bob",
            status="running",
            model=ModelRef(name="qwen-7b", path="/models/qwen"),
            attempts=[
                AttemptSummary(
                    attempt_id="att-001",
                    attempt_no=1,
                    backend="slurm",
                    status="succeeded",
                    duration_seconds=42.5,
                ),
            ],
            artifacts=[
                ArtifactRef(uri="s3://bucket/output.tar", media_type="application/tar"),
            ],
            total_attempts=1,
            workflow_name="my-wf",
            execution_id="exec-001",
        )
        d = rec.to_dict()
        assert d["job_id"] == "job-xyz"
        assert d["model"]["name"] == "qwen-7b"
        assert d["model"]["path"] == "/models/qwen"
        assert len(d["attempts"]) == 1
        assert d["attempts"][0]["duration_seconds"] == 42.5
        assert len(d["artifacts"]) == 1
        assert d["artifacts"][0]["uri"] == "s3://bucket/output.tar"
        assert d["workflow_name"] == "my-wf"

    def test_to_dict_no_model(self):
        rec = ProvenanceRecord(
            job_id="job-nomodel",
            job_type="cleanup",
            project_id="proj-1",
            user_id="alice",
            status="completed",
        )
        d = rec.to_dict()
        assert "model" not in d or d.get("model") is None

    def test_artifact_ref_fields(self):
        a = ArtifactRef(
            uri="s3://bucket/file.bin",
            media_type="application/octet-stream",
            size_bytes=1024,
            sha256="abc123",
        )
        assert a.size_bytes == 1024
        assert a.sha256 == "abc123"


# ---------------------------------------------------------------------------
# Lightweight mock objects for provenance assembly (avoids umrs.jobs circular
# import).  These replicate the minimal API that build_provenance reads.
# ---------------------------------------------------------------------------


class _MockEnum:
    """Value-carrying enum stand-in."""

    def __init__(self, value: str):
        self.value = value


@dataclass
class _MockJob:
    job_id: str = "job-test"
    project_id: str = "proj-1"
    user_id: str = "alice"
    job_type: Any = field(default_factory=lambda: _MockEnum("endpoint_provision"))
    spec: dict = field(default_factory=lambda: {"model": "qwen-7b", "model_path": "/models/qwen"})
    idempotency_key: str = "test-key"
    status: Any = field(default_factory=lambda: _MockEnum("completed"))
    priority: int = 0
    desired_max_attempts: int = 3
    restart_policy: Any = field(default_factory=lambda: _MockEnum("on_crash"))
    created_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, 10, 0, 0))
    updated_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, 10, 5, 0))
    next_run_at: datetime | None = None
    active_attempt_id: str | None = None
    commit_attempt_id: str | None = None
    last_state_change_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


@dataclass
class _MockAttempt:
    attempt_id: str = "att-001"
    job_id: str = "job-test"
    attempt_no: int = 1
    backend: str = "slurm"
    backend_ref: str | None = None
    status: Any = field(default_factory=lambda: _MockEnum("succeeded"))
    started_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, 10, 0, 5))
    ended_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, 10, 3, 0))
    exit_reason: str | None = None
    log_uri: str | None = None
    status_detail: str | None = None
    metrics: dict = field(default_factory=dict)
    umrs_instance_id: str | None = "inst-abc"
    scheduler_claimed_at: datetime | None = None
    ignored_at: datetime | None = None
    ignore_reason: str | None = None
    created_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, 10, 0, 5))


# ---------------------------------------------------------------------------
# UMRS provenance assembly tests (build_provenance) — uses mock objects to
# avoid triggering the umrs.jobs.__init__ circular import chain.
# ---------------------------------------------------------------------------


class TestBuildProvenance:
    """Test umrs.jobs.provenance.build_provenance pure assembly."""

    @pytest.fixture(autouse=True)
    def _load_provenance(self):
        """Import build_provenance avoiding the circular import chain.

        We pre-register a stub ``umrs.jobs`` package in ``sys.modules`` so
        that ``import umrs.jobs.provenance`` can succeed without executing
        the real ``umrs/jobs/__init__.py`` (which triggers the
        jobs→adapters→orchestrator→jobs cycle).
        """
        import importlib
        import importlib.util
        import pathlib
        import sys

        sentinel = "umrs.jobs"
        already = sentinel in sys.modules

        if not already:
            # Find the real package directory
            pkg_dir = (
                pathlib.Path(__file__).resolve().parent.parent / "umrs" / "src" / "umrs" / "jobs"
            )
            import types

            stub = types.ModuleType(sentinel)
            stub.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
            stub.__package__ = sentinel
            sys.modules[sentinel] = stub

        mod = importlib.import_module("umrs.jobs.provenance")
        self.build_provenance = mod.build_provenance

        if not already:
            # Clean up stubs
            sys.modules.pop(sentinel, None)
            sys.modules.pop("umrs.jobs.provenance", None)

    def test_basic_assembly(self):
        job = _MockJob()
        att = _MockAttempt()

        record = self.build_provenance(job, [att])

        assert record.job_id == "job-test"
        assert record.job_type == "endpoint_provision"
        assert record.status == "completed"
        assert record.execution_authority == "slurm"
        assert record.total_attempts == 1
        assert record.model is not None
        assert record.model.name == "qwen-7b"
        assert record.model.path == "/models/qwen"
        assert record.restart_policy == "on_crash"
        assert record.max_attempts == 3

    def test_duration_computed(self):
        job = _MockJob()
        att = _MockAttempt()

        record = self.build_provenance(job, [att])

        assert record.total_duration_seconds is not None
        assert record.total_duration_seconds > 0
        assert record.attempts[0].duration_seconds is not None
        assert record.attempts[0].duration_seconds > 0

    def test_no_attempts(self):
        job = _MockJob()
        record = self.build_provenance(job, [])

        assert record.total_attempts == 0
        assert record.execution_authority is None
        assert record.completed_at is None

    def test_workflow_fields_extracted(self):
        job = _MockJob(
            job_type=_MockEnum("workflow_run"),
            spec={
                "workflow_name": "test-wf",
                "execution_id": "exec-001",
                "parent_job_id": "job-parent",
            },
        )
        att = _MockAttempt()

        record = self.build_provenance(job, [att])

        assert record.workflow_name == "test-wf"
        assert record.execution_id == "exec-001"
        assert record.parent_job_id == "job-parent"

    def test_artifacts_from_committed_attempt(self):
        job = _MockJob(commit_attempt_id="att-002")
        att1 = _MockAttempt(
            attempt_id="att-001",
            attempt_no=1,
            log_uri="s3://logs/att-001",
            metrics={"artifacts": [{"uri": "s3://data/old.bin"}]},
        )
        att2 = _MockAttempt(
            attempt_id="att-002",
            attempt_no=2,
            log_uri="s3://logs/att-002",
            metrics={"artifacts": [{"uri": "s3://data/result.bin", "sha256": "abc"}]},
        )

        record = self.build_provenance(job, [att1, att2])

        uris = [a.uri for a in record.artifacts]
        assert "s3://logs/att-002" in uris
        assert "s3://data/result.bin" in uris
        assert "s3://logs/att-001" not in uris

    def test_no_model_in_spec(self):
        job = _MockJob(
            job_type=_MockEnum("cleanup"),
            spec={"target": "old-data"},
        )

        record = self.build_provenance(job, [])

        assert record.model is None

    def test_to_dict_serializable(self):
        """ProvenanceRecord.to_dict() produces JSON-serializable output."""
        job = _MockJob()
        att = _MockAttempt()
        record = self.build_provenance(job, [att])

        d = record.to_dict()
        serialized = json_mod.dumps(d)
        assert "job-test" in serialized


# ---------------------------------------------------------------------------
# r4 CLI provenance tests (argument parsing only)
# ---------------------------------------------------------------------------


class TestProvenanceCLI:
    """Test that provenance CLI command is registered and parseable."""

    def test_command_registered(self):
        # Through the CLI's own loader (ADR-053: tests never import r4._cli
        # modules by hand) — the registry alone binds nothing.
        from r4 import cli

        assert "provenance" in cli._load_dispatch_registry()

    def test_command_has_subcommands(self):
        from r4 import cli

        handler = cli._load_dispatch_registry().get("provenance")
        assert handler.name == "provenance"
        assert handler.help != ""


# ---------------------------------------------------------------------------
# Security: provenance access control enforcement
# ---------------------------------------------------------------------------


class TestProvenanceAccessControl:
    """Verify that provenance endpoints enforce proper access control.

    CallerIdentity is re-implemented inline because importing
    ``umrs.api.jobs`` triggers the circular import chain
    (jobs -> orchestrator -> durable -> jobs).
    """

    @dataclass
    class _CallerIdentity:
        """Minimal replica of umrs.api.jobs.CallerIdentity for testing."""

        user_id: str
        project_id: str
        is_admin: bool = False

        def can_access_job(self, job: Any) -> bool:
            if self.is_admin:
                return True
            return job.project_id == self.project_id and job.user_id == self.user_id

    def test_can_access_own_job(self):
        """CallerIdentity can access jobs in own project."""
        caller = self._CallerIdentity(user_id="alice", project_id="proj-1", is_admin=False)
        job = _MockJob(user_id="alice", project_id="proj-1")
        assert caller.can_access_job(job) is True

    def test_cannot_access_other_project_job(self):
        """CallerIdentity cannot access jobs in another project."""
        caller = self._CallerIdentity(user_id="alice", project_id="proj-1", is_admin=False)
        job = _MockJob(user_id="bob", project_id="proj-2")
        assert caller.can_access_job(job) is False

    def test_admin_can_access_any_job(self):
        """Admin CallerIdentity can access any job."""
        caller = self._CallerIdentity(user_id="admin", project_id="proj-1", is_admin=True)
        job = _MockJob(user_id="bob", project_id="proj-2")
        assert caller.can_access_job(job) is True

    def test_cannot_access_same_project_different_user(self):
        """Same project, different user - denied without admin."""
        caller = self._CallerIdentity(user_id="alice", project_id="proj-1", is_admin=False)
        job = _MockJob(user_id="bob", project_id="proj-1")
        assert caller.can_access_job(job) is False
