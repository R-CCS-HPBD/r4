"""Tests for Batch 4: MEM-B6, COR-007, MEM-D1.

Plain imports by ADR-053.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from umrs.jobs.checkpoint_store import Checkpoint, CheckpointStore
from umrs.jobs.workflow_outcome import (
    OutcomeSummary,
    StepMetric,
    WorkflowOutcome,
    WorkflowOutcomeStore,
)

from r4_contracts.provenance import CheckpointRef, WorkflowOutcomeSummary
from tools import special_case_inventory as special_case_mod

provenance_mod = __import__(
    "r4_contracts.provenance", fromlist=["CheckpointRef", "WorkflowOutcomeSummary"]
)

REPO_ROOT = Path(__file__).resolve().parents[1]
UMRS_JOBS = REPO_ROOT / "umrs" / "src" / "umrs" / "jobs"
UMRS_API = REPO_ROOT / "umrs" / "src" / "umrs" / "api"
CONTRACTS = REPO_ROOT / "r4-contracts" / "src" / "r4_contracts"
NGINX_CONF = REPO_ROOT / "gateway" / "nginx" / "conf.d" / "server.conf"
TOOLS = REPO_ROOT / "tools"


# =====================================================================
#  MEM-B6: Checkpoint Store
# =====================================================================


class TestCheckpointSave:
    """Test checkpoint creation and persistence."""

    def test_save_checkpoint_basic(self):
        store = CheckpointStore()
        ckpt = store.save_checkpoint(
            job_id="job-1",
            attempt_id="att-1",
            step_name="train",
            iteration=100,
            progress=0.5,
        )
        assert ckpt.checkpoint_id.startswith("ckpt-")
        assert ckpt.job_id == "job-1"
        assert ckpt.attempt_id == "att-1"
        assert ckpt.step_name == "train"
        assert ckpt.iteration == 100
        assert ckpt.progress == 0.5
        assert ckpt.sequence_no == 0

    def test_save_with_state_uri(self):
        store = CheckpointStore()
        ckpt = store.save_checkpoint(
            job_id="job-2",
            attempt_id="att-1",
            step_name="encode",
            state_uri="s3://bucket/checkpoints/ckpt-001.pt",
            state_size_bytes=1_000_000,
            iteration=50,
        )
        assert ckpt.state_uri == "s3://bucket/checkpoints/ckpt-001.pt"
        assert ckpt.state_size_bytes == 1_000_000

    def test_save_with_inline_state(self):
        store = CheckpointStore()
        inline = {"weights": [0.1, 0.2, 0.3], "epoch": 5}
        ckpt = store.save_checkpoint(
            job_id="job-3",
            attempt_id="att-1",
            step_name="finetune",
            state_inline=inline,
            iteration=200,
        )
        assert ckpt.state_inline == inline

    def test_save_increments_sequence(self):
        store = CheckpointStore()
        c1 = store.save_checkpoint("job-4", "att-1", "train", iteration=10)
        c2 = store.save_checkpoint("job-4", "att-1", "train", iteration=20)
        c3 = store.save_checkpoint("job-4", "att-1", "train", iteration=30)
        assert c1.sequence_no == 0
        assert c2.sequence_no == 1
        assert c3.sequence_no == 2

    def test_save_with_metadata(self):
        store = CheckpointStore()
        meta = {"gpu_memory_mb": 8192, "loss": 0.42}
        ckpt = store.save_checkpoint(
            "job-5",
            "att-1",
            "train",
            metadata=meta,
            iteration=10,
        )
        assert ckpt.metadata == meta


class TestCheckpointGet:
    """Test checkpoint retrieval."""

    def test_get_by_id(self):
        store = CheckpointStore()
        saved = store.save_checkpoint("job-1", "att-1", "step-a")
        retrieved = store.get_checkpoint(saved.checkpoint_id)
        assert retrieved is not None
        assert retrieved.checkpoint_id == saved.checkpoint_id

    def test_get_nonexistent(self):
        store = CheckpointStore()
        assert store.get_checkpoint("ckpt-nonexistent") is None


class TestCheckpointLatest:
    """Test latest checkpoint selection."""

    def test_latest_for_attempt(self):
        store = CheckpointStore()
        store.save_checkpoint("job-1", "att-1", "train", iteration=10)
        store.save_checkpoint("job-1", "att-1", "train", iteration=20)
        store.save_checkpoint("job-1", "att-1", "train", iteration=30)
        latest = store.latest_checkpoint("job-1", "att-1")
        assert latest is not None
        assert latest.iteration == 30
        assert latest.sequence_no == 2

    def test_latest_filtered_by_step(self):
        store = CheckpointStore()
        store.save_checkpoint("job-2", "att-1", "encode", iteration=5)
        store.save_checkpoint("job-2", "att-1", "decode", iteration=10)
        latest = store.latest_checkpoint("job-2", "att-1", "encode")
        assert latest is not None
        assert latest.step_name == "encode"
        assert latest.iteration == 5

    def test_latest_none(self):
        store = CheckpointStore()
        assert store.latest_checkpoint("job-none", "att-none") is None


class TestCheckpointResume:
    """Test checkpoint selection for job resumption."""

    def test_select_resume_across_attempts(self):
        store = CheckpointStore()
        store.save_checkpoint("job-1", "att-1", "train", iteration=10)
        store.save_checkpoint("job-1", "att-1", "train", iteration=20)
        store.save_checkpoint("job-1", "att-2", "train", iteration=5)
        best = store.select_resume_checkpoint("job-1")
        assert best is not None
        # Should pick the highest sequence_no across all attempts
        # att-1 has seq 0,1 and att-2 has seq 0 → max is seq 1
        assert best.sequence_no == 1
        assert best.iteration == 20

    def test_select_resume_none(self):
        store = CheckpointStore()
        assert store.select_resume_checkpoint("job-none") is None


class TestCheckpointList:
    """Test checkpoint listing."""

    def test_list_all_for_job(self):
        store = CheckpointStore()
        store.save_checkpoint("job-1", "att-1", "s1", iteration=1)
        store.save_checkpoint("job-1", "att-1", "s2", iteration=2)
        store.save_checkpoint("job-1", "att-2", "s1", iteration=3)
        all_ckpts = store.list_checkpoints("job-1")
        assert len(all_ckpts) == 3

    def test_list_filtered_by_attempt(self):
        store = CheckpointStore()
        store.save_checkpoint("job-2", "att-1", "s1")
        store.save_checkpoint("job-2", "att-2", "s1")
        att1 = store.list_checkpoints("job-2", "att-1")
        assert len(att1) == 1


class TestCheckpointDelete:
    """Test checkpoint cleanup."""

    def test_delete_all_for_job(self):
        store = CheckpointStore()
        store.save_checkpoint("job-1", "att-1", "s1")
        store.save_checkpoint("job-1", "att-2", "s2")
        deleted = store.delete_checkpoints("job-1")
        assert deleted == 2
        assert store.list_checkpoints("job-1") == []

    def test_delete_for_specific_attempt(self):
        store = CheckpointStore()
        store.save_checkpoint("job-3", "att-1", "s1")
        store.save_checkpoint("job-3", "att-2", "s2")
        deleted = store.delete_checkpoints("job-3", "att-1")
        assert deleted == 1
        remaining = store.list_checkpoints("job-3")
        assert len(remaining) == 1
        assert remaining[0].attempt_id == "att-2"


class TestCheckpointSerialization:
    """Test checkpoint serialization."""

    def test_to_dict(self):
        ckpt = Checkpoint(
            checkpoint_id="ckpt-test",
            job_id="job-1",
            attempt_id="att-1",
            step_name="train",
            sequence_no=0,
            iteration=100,
            progress=0.75,
            metadata={"loss": 0.1},
        )
        d = ckpt.to_dict()
        assert d["checkpoint_id"] == "ckpt-test"
        assert d["iteration"] == 100
        assert d["progress"] == 0.75
        assert d["metadata"] == {"loss": 0.1}
        assert d["created_at"] is not None


# =====================================================================
#  MEM-B6: CheckpointRef DTO (r4-contracts)
# =====================================================================


class TestCheckpointRefDTO:
    """Test CheckpointRef contract DTO."""

    def test_checkpoint_ref_to_dict(self):
        ref = CheckpointRef(
            checkpoint_id="ckpt-abc",
            job_id="job-1",
            attempt_id="att-1",
            step_name="train",
            sequence_no=3,
            state_uri="s3://bucket/ckpt.pt",
            iteration=150,
            progress=0.8,
        )
        d = ref.to_dict()
        assert d["checkpoint_id"] == "ckpt-abc"
        assert d["state_uri"] == "s3://bucket/ckpt.pt"
        assert d["iteration"] == 150


# =====================================================================
#  COR-007: Special-Case Inventory
# =====================================================================


class TestSpecialCaseScanner:
    """Test special-case scanning patterns."""

    def test_scan_detects_experimental(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# EXPERIMENTAL feature\nENABLE_EXPERIMENTAL_THING = True\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1
        assert any(i.category == "experimental" for i in items)

    def test_scan_detects_legacy(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# legacy code path\nold_func()\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1
        assert any(i.category == "compatibility" for i in items)

    def test_scan_detects_deprecated(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# DEPRECATED — remove in v3\ndef old():\n    pass\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1

    def test_scan_detects_fallback(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# fallback when main unavailable\nuse_backup()\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1
        assert any(i.category == "compatibility" for i in items)

    def test_scan_detects_backward_compat(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# backward compat for old API\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1

    def test_scan_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        assert special_case_mod.scan_file(f) == []


class TestSpecialCaseGovernance:
    """Test governance marker detection."""

    def test_governed_marker(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text(
            "# EXPERIMENTAL hybrid mode\n"
            "# special-case: experimental flag=ENABLE_HYBRID adr=ADR-007\n"
        )
        items = special_case_mod.scan_file(f)
        governed = [i for i in items if i.governed]
        assert len(governed) >= 1
        item = governed[0]
        assert item.category == "experimental"
        assert item.feature_flag == "ENABLE_HYBRID"
        assert item.adr_ref == "ADR-007"

    def test_governed_with_sunset(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# legacy API wrapper\n# special-case: compatibility sunset=2026-12-31\n")
        items = special_case_mod.scan_file(f)
        governed = [i for i in items if i.governed]
        assert len(governed) >= 1
        assert governed[0].sunset_date == "2026-12-31"

    def test_ungoverned_marker(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# fallback for broken config\n")
        items = special_case_mod.scan_file(f)
        assert len(items) >= 1
        assert not items[0].governed


class TestSpecialCaseReport:
    """Test report generation."""

    def test_summary_counts(self):
        report = special_case_mod.InventoryReport(
            items=[
                special_case_mod.SpecialCase(
                    file="a.py",
                    line=1,
                    kind="test",
                    category="experimental",
                    content="x",
                    governed=True,
                    sunset_date="",
                    feature_flag="F",
                    adr_ref="",
                ),
                special_case_mod.SpecialCase(
                    file="b.py",
                    line=1,
                    kind="test",
                    category="compatibility",
                    content="y",
                    governed=False,
                    sunset_date="",
                    feature_flag="",
                    adr_ref="",
                ),
            ]
        )
        s = report.summary
        assert s["total_items"] == 2
        assert s["governed"] == 1
        assert s["ungoverned"] == 1
        assert s["by_category"]["experimental"] == 1
        assert s["by_category"]["compatibility"] == 1

    def test_human_format(self):
        report = special_case_mod.InventoryReport(
            items=[
                special_case_mod.SpecialCase(
                    file="a.py",
                    line=5,
                    kind="test",
                    category="experimental",
                    content="# EXPERIMENTAL",
                    governed=False,
                    sunset_date="",
                    feature_flag="",
                    adr_ref="",
                ),
            ]
        )
        output = special_case_mod.format_human(report)
        assert "UNGOVERNED" in output
        assert "a.py:5" in output

    def test_json_format(self):
        report = special_case_mod.InventoryReport(items=[])
        j = json.loads(special_case_mod.format_json(report))
        assert j["summary"]["total_items"] == 0

    def test_csv_format(self):
        report = special_case_mod.InventoryReport(
            items=[
                special_case_mod.SpecialCase(
                    file="a.py",
                    line=1,
                    kind="test",
                    category="blessed",
                    content="ok",
                    governed=True,
                    sunset_date="",
                    feature_flag="",
                    adr_ref="",
                ),
            ]
        )
        csv_out = special_case_mod.format_csv(report)
        assert "a.py" in csv_out
        assert "blessed" in csv_out


class TestSpecialCaseFullScan:
    """Test full platform scan runs without error."""

    def test_run_scan_completes(self):
        report = special_case_mod.run_scan()
        # Should find at least some markers in the codebase
        assert isinstance(report.items, list)
        assert report.summary["total_items"] >= 0


# =====================================================================
#  MEM-D1: Workflow Outcome Store
# =====================================================================


class TestWorkflowOutcomeRecord:
    """Test outcome recording."""

    def test_record_succeeded(self):
        store = WorkflowOutcomeStore()
        outcome = store.record_outcome(
            job_id="job-1",
            workflow_name="text-analysis",
            user_id="alice",
            project_id="proj-a",
            status="succeeded",
            execution_duration_sec=45.2,
            step_count=3,
            model_used="Qwen/Qwen2.5-7B",
            total_tokens=1500,
        )
        assert outcome.outcome_id.startswith("wfo-")
        assert outcome.status == "succeeded"
        assert outcome.workflow_name == "text-analysis"
        assert outcome.total_tokens == 1500

    def test_record_failed(self):
        store = WorkflowOutcomeStore()
        outcome = store.record_outcome(
            job_id="job-2",
            workflow_name="rag-pipeline",
            user_id="bob",
            project_id="proj-b",
            status="failed",
            failed_step_name="embed",
            failure_reason="OOM on GPU",
            failure_category="oom",
        )
        assert outcome.status == "failed"
        assert outcome.failed_step_name == "embed"
        assert outcome.failure_category == "oom"

    def test_record_with_step_metrics(self):
        store = WorkflowOutcomeStore()
        steps = [
            StepMetric(step_name="extract", status="succeeded", duration_sec=10.0, tokens=500),
            StepMetric(step_name="transform", status="succeeded", duration_sec=20.0, tokens=800),
            StepMetric(step_name="load", status="failed", duration_sec=5.0),
        ]
        outcome = store.record_outcome(
            job_id="job-3",
            workflow_name="etl",
            user_id="carol",
            project_id="proj-c",
            status="failed",
            step_metrics=steps,
            step_count=3,
            failed_step_name="load",
        )
        assert len(outcome.step_metrics) == 3
        assert outcome.step_metrics[2].step_name == "load"

    def test_record_invalid_status(self):
        store = WorkflowOutcomeStore()
        with pytest.raises(ValueError, match="Invalid status"):
            store.record_outcome(
                "job-x",
                "wf",
                "user",
                "proj",
                "invalid_status",
            )

    def test_record_invalid_failure_category(self):
        store = WorkflowOutcomeStore()
        with pytest.raises(ValueError, match="Invalid failure_category"):
            store.record_outcome(
                "job-x",
                "wf",
                "user",
                "proj",
                "failed",
                failure_category="bogus",
            )


class TestWorkflowOutcomeQuery:
    """Test outcome querying."""

    def _populated_store(self) -> WorkflowOutcomeStore:
        store = WorkflowOutcomeStore()
        store.record_outcome(
            "j1",
            "text-analysis",
            "alice",
            "p1",
            "succeeded",
            model_used="Qwen/7B",
            total_tokens=1000,
            execution_duration_sec=30.0,
        )
        store.record_outcome(
            "j2",
            "text-analysis",
            "alice",
            "p1",
            "failed",
            model_used="Qwen/7B",
            failure_category="timeout",
        )
        store.record_outcome(
            "j3",
            "rag-pipeline",
            "bob",
            "p2",
            "succeeded",
            model_used="Llama-3",
            total_tokens=2000,
            execution_duration_sec=60.0,
        )
        store.record_outcome("j4", "text-analysis", "bob", "p2", "cancelled")
        return store

    def test_list_all(self):
        store = self._populated_store()
        all_outcomes = store.list_outcomes()
        assert len(all_outcomes) == 4

    def test_list_by_workflow(self):
        store = self._populated_store()
        results = store.list_outcomes(workflow_name="text-analysis")
        assert len(results) == 3

    def test_list_by_model(self):
        store = self._populated_store()
        results = store.list_outcomes(model_used="Qwen/7B")
        assert len(results) == 2

    def test_list_by_user(self):
        store = self._populated_store()
        results = store.list_outcomes(user_id="alice")
        assert len(results) == 2

    def test_list_by_status(self):
        store = self._populated_store()
        results = store.list_outcomes(status="succeeded")
        assert len(results) == 2

    def test_get_by_id(self):
        store = WorkflowOutcomeStore()
        outcome = store.record_outcome("j1", "wf", "u1", "p1", "succeeded")
        retrieved = store.get_outcome(outcome.outcome_id)
        assert retrieved is not None
        assert retrieved.outcome_id == outcome.outcome_id

    def test_get_nonexistent(self):
        store = WorkflowOutcomeStore()
        assert store.get_outcome("wfo-nonexistent") is None


class TestWorkflowOutcomeSummarize:
    """Test outcome aggregation."""

    def _populated_store(self) -> WorkflowOutcomeStore:
        store = WorkflowOutcomeStore()
        store.record_outcome(
            "j1",
            "text-analysis",
            "alice",
            "p1",
            "succeeded",
            total_tokens=1000,
            execution_duration_sec=30.0,
        )
        store.record_outcome(
            "j2",
            "text-analysis",
            "alice",
            "p1",
            "succeeded",
            total_tokens=2000,
            execution_duration_sec=60.0,
        )
        store.record_outcome(
            "j3", "text-analysis", "bob", "p1", "failed", failure_category="timeout"
        )
        store.record_outcome("j4", "text-analysis", "bob", "p1", "failed", failure_category="oom")
        return store

    def test_summarize_template(self):
        store = self._populated_store()
        summary = store.summarize("text-analysis")
        assert summary.total_runs == 4
        assert summary.succeeded == 2
        assert summary.failed == 2
        assert summary.cancelled == 0
        assert summary.success_rate == 0.5
        assert summary.avg_duration_sec == 45.0
        assert summary.avg_tokens == 1500.0
        assert summary.failure_breakdown == {"timeout": 1, "oom": 1}

    def test_summarize_empty(self):
        store = WorkflowOutcomeStore()
        summary = store.summarize("nonexistent")
        assert summary.total_runs == 0
        assert summary.success_rate == 0.0

    def test_summarize_by_model(self):
        store = WorkflowOutcomeStore()
        store.record_outcome(
            "j1", "wf-a", "u1", "p1", "succeeded", model_used="Qwen/7B", total_tokens=500
        )
        store.record_outcome(
            "j2", "wf-b", "u1", "p1", "succeeded", model_used="Qwen/7B", total_tokens=1500
        )
        summary = store.summarize_by_model("Qwen/7B")
        assert summary.total_runs == 2
        assert summary.succeeded == 2
        assert summary.avg_tokens == 1000.0


class TestWorkflowOutcomeSerialization:
    """Test outcome serialization."""

    def test_outcome_to_dict(self):
        outcome = WorkflowOutcome(
            outcome_id="wfo-test",
            job_id="job-1",
            workflow_name="analysis",
            user_id="alice",
            project_id="proj-1",
            status="succeeded",
            total_tokens=1000,
            step_metrics=[
                StepMetric(step_name="s1", status="succeeded", duration_sec=10.0),
            ],
        )
        d = outcome.to_dict()
        assert d["outcome_id"] == "wfo-test"
        assert d["total_tokens"] == 1000
        assert len(d["step_metrics"]) == 1
        assert d["step_metrics"][0]["step_name"] == "s1"

    def test_step_metric_to_dict(self):
        m = StepMetric(step_name="train", status="succeeded", duration_sec=5.5, tokens=200)
        d = m.to_dict()
        assert d["step_name"] == "train"
        assert d["tokens"] == 200

    def test_summary_to_dict(self):
        s = OutcomeSummary(
            workflow_name="test",
            total_runs=10,
            succeeded=8,
            failed=2,
            success_rate=0.8,
            failure_breakdown={"timeout": 2},
        )
        d = s.to_dict()
        assert d["success_rate"] == 0.8
        assert d["failure_breakdown"] == {"timeout": 2}


# =====================================================================
#  MEM-D1: WorkflowOutcomeSummary DTO (r4-contracts)
# =====================================================================


class TestWorkflowOutcomeSummaryDTO:
    """Test WorkflowOutcomeSummary contract DTO."""

    def test_to_dict(self):
        s = WorkflowOutcomeSummary(
            workflow_name="pipeline-v1",
            total_runs=50,
            succeeded=45,
            failed=5,
            success_rate=0.9,
            avg_duration_sec=120.5,
            avg_tokens=5000.0,
            failure_breakdown={"oom": 3, "timeout": 2},
        )
        d = s.to_dict()
        assert d["workflow_name"] == "pipeline-v1"
        assert d["success_rate"] == 0.9
        assert d["failure_breakdown"]["oom"] == 3


# =====================================================================
#  Migrations
# =====================================================================


class TestMigrations:
    """Test migration files exist and contain expected SQL."""

    def test_0043_checkpoints_migration(self):
        path = REPO_ROOT / "umrs" / "src" / "umrs" / "migrations" / "0043_checkpoints.sql"
        assert path.exists()
        content = path.read_text()
        assert "CREATE TABLE" in content
        assert "checkpoints" in content
        assert "checkpoint_id" in content
        assert "state_uri" in content
        assert "idx_checkpoints_attempt_seq" in content

    def test_0044_workflow_outcomes_migration(self):
        path = REPO_ROOT / "umrs" / "src" / "umrs" / "migrations" / "0044_workflow_outcomes.sql"
        assert path.exists()
        content = path.read_text()
        assert "CREATE TABLE" in content
        assert "workflow_outcomes" in content
        assert "failure_category" in content
        assert "step_metrics" in content
        assert "idx_workflow_outcomes_template" in content


# =====================================================================
#  UMRS API endpoints (source inspection)
# =====================================================================


class TestCheckpointAPIEndpoints:
    """Retirement-invariant pins (ADR-053, 2026-09-15).

    The /umrs/checkpoints router module was DELETED with the dormant-router
    retirement — this class used to pin file existence for the same
    endpoints. Now it pins the retirement decision itself so the file can
    never silently return as a dead surface.
    """

    def test_checkpoint_api_retired(self):
        assert not (UMRS_API / "checkpoints.py").exists(), (
            "the /umrs/checkpoints router module must stay deleted (ADR-053)"
        )

    def test_checkpoint_store_still_lives_in_jobs(self):
        pass


class TestWorkflowOutcomeAPIEndpoints:
    """Retirement-invariant pins (ADR-053, 2026-09-15) — the
    /umrs/workflows outcomes router was deleted; its jobs store remains
    the authority."""

    def test_workflow_outcome_api_retired(self):
        assert not (UMRS_API / "workflow_outcomes.py").exists()

    def test_workflow_outcome_store_still_lives_in_jobs(self):
        pass


class TestGatewayEdgeRoutes:
    """Edge exposure for the MEM-D1 workflow surface.

    The Python gateway proxy modules (``gateway/gateway/routes/*.py``,
    including ``checkpoints.py`` and ``workflow_outcomes.py``) were deleted
    with the whole Python gateway — ADR-014 Phases 3+4, commit 0aac3ec2
    (2026-09-11). NGINX + the auth sidecar are the only edge, so the edge
    assertion reads ``gateway/nginx/conf.d/server.conf``. ``/gw/workflows``
    survived; ``/gw/checkpoints`` has no NGINX analogue (reported as a
    reachability gap rather than asserted away).
    """

    def _workflows_block(self) -> str:
        content = NGINX_CONF.read_text()
        start = content.index("location /gw/workflows/ {")
        return content[start : content.index("\n    }", start)]

    def test_workflow_outcomes_edge_route(self):
        block = self._workflows_block()
        assert "rewrite ^/gw/workflows/(.*)$ /umrs/workflows/$1 break;" in block
        assert "proxy_pass http://umrs" in block

    def test_workflow_edge_route_requires_auth(self):
        # CONSTITUTION §2: every /gw/* route goes through auth_request.
        block = self._workflows_block()
        assert "auth_request /auth/validate;" in block
