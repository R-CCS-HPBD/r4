"""Tests for Batch 5: MEM-A3 + MEM-A4 + MEM-C2.

- MEM-A3: Session-scoped tool output cache
- MEM-A4: Session crash recovery from execution traces
- MEM-C2: Vector versioning for embedding model upgrades

Plain imports by ADR-053 (umrs resolves as a namespace package from the
repo root; the old spec-load machinery predates the jobs↔api cycle fix).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from umrs.jobs.execution_trace_store import ExecutionTraceStore
from umrs.jobs.session_recovery import (
    RecoveredSession,
    RecoveredStep,
    SessionRecoverer,
    SessionRecoveryResult,
)
from umrs.jobs.tool_output_cache import (
    ToolOutputCache,
    _compute_inputs_hash,
)
from umrs.jobs.vector_version_store import (
    ReembedJob,
    VectorVersion,
    VectorVersionStore,
    VersionSummary,
)

ROOT = Path(__file__).resolve().parent.parent
UMRS_SRC = ROOT / "umrs" / "src"

# Contracts DTOs — import the installed package. Loading provenance.py by
# path through ``_load_module`` used to fabricate an empty ``r4_contracts``
# parent (``__path__ = []``) whenever this file was collected before any real
# ``import r4_contracts``; every later ``import r4_contracts.<x>`` in the
# session then failed with ModuleNotFoundError (R-12, 2026-09-13).
from r4_contracts.provenance import RecoveredSessionRef, VectorVersionRef  # noqa: E402

# ===================================================================
# MEM-A3: Tool Output Cache Tests
# ===================================================================


class TestToolOutputCachePut:
    """Test putting entries into the cache."""

    def test_basic_put(self):
        cache = ToolOutputCache()
        entry = cache.put("sess-1", "search", {"query": "hello"}, {"results": [1, 2, 3]})
        assert entry.entry_id.startswith("toc-")
        assert entry.session_id == "sess-1"
        assert entry.tool_name == "search"
        assert entry.output == {"results": [1, 2, 3]}
        assert entry.expires_at is not None

    def test_put_replaces_same_inputs(self):
        cache = ToolOutputCache()
        _e1 = cache.put("sess-1", "search", {"q": "x"}, {"v": 1})
        _e2 = cache.put("sess-1", "search", {"q": "x"}, {"v": 2})
        # Should replace — only 1 entry for this session
        entries = cache.list_session("sess-1")
        assert len(entries) == 1
        assert entries[0].output == {"v": 2}

    def test_put_different_tools(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "x"}, {"v": 1})
        cache.put("sess-1", "fetch", {"url": "http://x"}, {"v": 2})
        entries = cache.list_session("sess-1")
        assert len(entries) == 2

    def test_put_different_sessions(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "x"}, {"v": 1})
        cache.put("sess-2", "search", {"q": "x"}, {"v": 2})
        assert len(cache.list_session("sess-1")) == 1
        assert len(cache.list_session("sess-2")) == 1

    def test_put_with_output_ref(self):
        cache = ToolOutputCache()
        entry = cache.put("sess-1", "big_tool", {}, None, output_ref="s3://bucket/key")
        assert entry.output is None
        assert entry.output_ref == "s3://bucket/key"


class TestToolOutputCacheGet:
    """Test cache lookups."""

    def test_cache_hit(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "hello"}, {"results": [1]})
        result = cache.get("sess-1", "search", {"q": "hello"})
        assert result is not None
        assert result.output == {"results": [1]}
        assert result.hit_count == 1

    def test_cache_miss(self):
        cache = ToolOutputCache()
        result = cache.get("sess-1", "search", {"q": "hello"})
        assert result is None

    def test_cache_miss_wrong_session(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "hello"}, {"results": [1]})
        result = cache.get("sess-2", "search", {"q": "hello"})
        assert result is None

    def test_cache_miss_different_inputs(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "hello"}, {"results": [1]})
        result = cache.get("sess-1", "search", {"q": "world"})
        assert result is None

    def test_hit_increments_count(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "x"}, {"v": 1})
        cache.get("sess-1", "search", {"q": "x"})
        cache.get("sess-1", "search", {"q": "x"})
        result = cache.get("sess-1", "search", {"q": "x"})
        assert result.hit_count == 3


class TestToolOutputCacheExpiry:
    """Test TTL and expiry logic."""

    def test_entry_expires(self):
        cache = ToolOutputCache()
        _entry = cache.put("sess-1", "search", {"q": "x"}, {"v": 1}, ttl_seconds=0)
        # Immediately expired
        import time

        time.sleep(0.01)
        result = cache.get("sess-1", "search", {"q": "x"})
        assert result is None

    def test_cleanup_expired(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "a"}, {"v": 1}, ttl_seconds=0)
        cache.put("sess-1", "fetch", {"q": "b"}, {"v": 2}, ttl_seconds=3600)
        time.sleep(0.01)
        removed = cache.cleanup_expired()
        assert removed == 1
        assert len(cache.list_session("sess-1")) == 1


class TestToolOutputCacheInvalidate:
    """Test cache invalidation."""

    def test_invalidate_all(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "a"}, {"v": 1})
        cache.put("sess-1", "fetch", {"q": "b"}, {"v": 2})
        count = cache.invalidate("sess-1")
        assert count == 2
        assert len(cache.list_session("sess-1")) == 0

    def test_invalidate_by_tool(self):
        cache = ToolOutputCache()
        cache.put("sess-1", "search", {"q": "a"}, {"v": 1})
        cache.put("sess-1", "fetch", {"q": "b"}, {"v": 2})
        count = cache.invalidate("sess-1", tool_name="search")
        assert count == 1
        entries = cache.list_session("sess-1")
        assert len(entries) == 1
        assert entries[0].tool_name == "fetch"

    def test_invalidate_nonexistent(self):
        cache = ToolOutputCache()
        count = cache.invalidate("no-such-session")
        assert count == 0


class TestToolOutputCacheSerialization:
    """Test serialization."""

    def test_to_dict_roundtrip(self):
        cache = ToolOutputCache()
        entry = cache.put("sess-1", "search", {"q": "x"}, {"results": [1, 2]})
        d = entry.to_dict()
        assert d["session_id"] == "sess-1"
        assert d["tool_name"] == "search"
        assert d["output"] == {"results": [1, 2]}
        assert "entry_id" in d
        assert "expires_at" in d


class TestInputsHash:
    """Test deterministic hashing."""

    def test_same_inputs_same_hash(self):
        h1 = _compute_inputs_hash("tool", {"a": 1, "b": 2})
        h2 = _compute_inputs_hash("tool", {"b": 2, "a": 1})
        assert h1 == h2

    def test_different_tool_different_hash(self):
        h1 = _compute_inputs_hash("tool_a", {"a": 1})
        h2 = _compute_inputs_hash("tool_b", {"a": 1})
        assert h1 != h2


# ===================================================================
# MEM-A4: Session Crash Recovery Tests
# ===================================================================


class TestRecoveredStep:
    """Test RecoveredStep dataclass."""

    def test_to_dict(self):
        step = RecoveredStep(
            trace_id="trc-abc",
            step_name="embed",
            step_type="embedding",
            status="succeeded",
            progress=1.0,
            has_inputs=True,
            has_outputs=True,
        )
        d = step.to_dict()
        assert d["trace_id"] == "trc-abc"
        assert d["step_name"] == "embed"
        assert d["status"] == "succeeded"
        assert d["has_inputs"] is True


class TestRecoveredSession:
    """Test RecoveredSession dataclass."""

    def test_to_dict(self):
        session = RecoveredSession(
            job_id="job-1",
            execution_id="exec-1",
            job_status="RUNNING",
            can_retry=True,
            total_steps=3,
            completed_steps=1,
            failed_steps=1,
            running_steps=1,
            resume_from_step="step_2",
        )
        d = session.to_dict()
        assert d["job_id"] == "job-1"
        assert d["can_retry"] is True
        assert d["resume_from_step"] == "step_2"
        assert d["total_steps"] == 3


class TestSessionRecoveryResult:
    """Test SessionRecoveryResult."""

    def test_empty_result(self):
        result = SessionRecoveryResult()
        assert result.total_recovered == 0
        d = result.to_dict()
        assert d["total_recovered"] == 0
        assert d["recovered_sessions"] == []

    def test_with_sessions(self):
        result = SessionRecoveryResult()
        session = RecoveredSession(
            job_id="j1",
            execution_id=None,
            job_status="LOST",
            can_retry=True,
            total_steps=2,
            completed_steps=1,
            failed_steps=0,
            running_steps=1,
        )
        result.recovered_sessions.append(session)
        result.resumable_jobs.append("j1")
        assert result.total_recovered == 1


class TestSessionRecoverer:
    """Test SessionRecoverer with in-memory stores."""

    def _make_trace_store_with_traces(self, job_id: str, steps: list[dict]) -> ExecutionTraceStore:
        store = ExecutionTraceStore()
        for step in steps:
            trace = store.create_trace(
                job_id=job_id,
                step_name=step["name"],
                step_type=step.get("type", "generic"),
                execution_id=step.get("execution_id"),
            )
            if step.get("status") == "succeeded":
                store.complete_trace(trace.trace_id, outputs={"ok": True})
            elif step.get("status") == "failed":
                store.fail_trace(trace.trace_id, error="test error")
            elif step.get("status") == "running":
                store.start_trace(trace.trace_id)
        return store

    def test_recover_empty(self):
        trace_store = ExecutionTraceStore()

        # Minimal mock job_store
        class MockJobStore:
            db_enabled = False
            umrs_instance_id = "test"
            _jobs = {}

            def get_job(self, _job_id):
                return None

            def _get_conn(self):
                raise Exception("no db")

        recoverer = SessionRecoverer(MockJobStore(), trace_store)
        result = recoverer.recover_sessions(lost_job_ids=["nonexistent"])
        assert result.total_recovered == 0

    def test_recover_with_traces(self):
        trace_store = self._make_trace_store_with_traces(
            "job-1",
            [
                {"name": "step_1", "status": "succeeded", "execution_id": "exec-1"},
                {"name": "step_2", "status": "running", "execution_id": "exec-1"},
                {"name": "step_3", "status": "pending", "execution_id": "exec-1"},
            ],
        )

        class MockJobStore:
            db_enabled = False
            umrs_instance_id = "test"
            _jobs = {}

            def get_job(self, _job_id):
                return None

            def _get_conn(self):
                raise Exception("no db")

        recoverer = SessionRecoverer(MockJobStore(), trace_store)
        result = recoverer.recover_sessions(lost_job_ids=["job-1"])
        assert result.total_recovered == 1
        session = result.recovered_sessions[0]
        assert session.job_id == "job-1"
        assert session.total_steps == 3
        assert session.completed_steps == 1
        assert session.running_steps == 2  # running + pending
        assert session.resume_from_step == "step_2"

    def test_recover_completed_job(self):
        trace_store = self._make_trace_store_with_traces(
            "job-2",
            [
                {"name": "step_1", "status": "succeeded"},
                {"name": "step_2", "status": "succeeded"},
            ],
        )

        class MockJobStore:
            db_enabled = False
            umrs_instance_id = "test"
            _jobs = {}

            def get_job(self, _job_id):
                return None

            def _get_conn(self):
                raise Exception("no db")

        recoverer = SessionRecoverer(MockJobStore(), trace_store)
        result = recoverer.recover_sessions(lost_job_ids=["job-2"])
        session = result.recovered_sessions[0]
        assert session.completed_steps == 2
        assert session.resume_from_step is None

    def test_recover_failed_job(self):
        trace_store = self._make_trace_store_with_traces(
            "job-3",
            [
                {"name": "step_1", "status": "succeeded"},
                {"name": "step_2", "status": "failed"},
            ],
        )

        class MockJobStore:
            db_enabled = False
            umrs_instance_id = "test"
            _jobs = {}

            def get_job(self, _job_id):
                return None

            def _get_conn(self):
                raise Exception("no db")

        recoverer = SessionRecoverer(MockJobStore(), trace_store)
        result = recoverer.recover_sessions(lost_job_ids=["job-3"])
        session = result.recovered_sessions[0]
        assert session.failed_steps == 1
        assert session.first_failed_step == "step_2"
        assert session.resume_from_step == "step_2"


# ===================================================================
# MEM-C2: Vector Versioning Tests
# ===================================================================


def _two_registered(case) -> list:
    """Register two versions on one model (v2 active) and return the list."""
    store = VectorVersionStore()
    store.register_version("bge-small", "1.5")
    store.register_version("bge-small", "2.0")
    return store.list_versions("bge-small")


class TestVectorVersionRegister:
    """Test registering embedding model versions."""

    def test_register_version(self):
        store = VectorVersionStore()
        v = store.register_version("bge-small", "1.5", embedding_dimension=384)
        assert v.version_id.startswith("emv-")
        assert v.model_name == "bge-small"
        assert v.model_version == "1.5"
        assert v.is_active is True

    def test_register_deactivates_previous_inactive_member(self):
        inactive = [v for v in _two_registered(self) if not v.is_active]
        assert len(inactive) == 1
        assert inactive[0].model_version == "1.5"

    def test_register_deactivates_previous_active_member(self):
        versions = _two_registered(self)
        assert len(versions) == 2
        active = [v for v in versions if v.is_active]
        assert len(active) == 1
        assert active[0].model_version == "2.0"

    def test_register_without_set_active(self):
        store = VectorVersionStore()
        _v1 = store.register_version("bge-small", "1.5")
        _v2 = store.register_version("bge-small", "2.0", set_active=False)
        # v1 should still be active
        active = store.get_active_version("bge-small")
        assert active.model_version == "1.5"

    def test_register_different_models(self):
        store = VectorVersionStore()
        store.register_version("bge-small", "1.5")
        store.register_version("e5-large", "1.0")
        assert len(store.list_versions()) == 2
        assert store.get_active_version("bge-small").model_version == "1.5"
        assert store.get_active_version("e5-large").model_version == "1.0"


class TestVectorVersionQuery:
    """Test querying versions."""

    def test_get_active_version(self):
        store = VectorVersionStore()
        store.register_version("bge-small", "1.5")
        active = store.get_active_version("bge-small")
        assert active is not None
        assert active.model_version == "1.5"

    def test_get_active_none(self):
        store = VectorVersionStore()
        assert store.get_active_version("nonexistent") is None

    def test_list_versions_filter(self):
        store = VectorVersionStore()
        store.register_version("bge-small", "1.5")
        store.register_version("bge-small", "2.0")
        store.register_version("e5-large", "1.0")
        assert len(store.list_versions("bge-small")) == 2
        assert len(store.list_versions("e5-large")) == 1
        assert len(store.list_versions()) == 3

    def test_version_summary(self):
        store = VectorVersionStore()
        store.register_version("bge-small", "2.0", embedding_dimension=384)
        summary = store.get_version_summary()
        assert summary.total_vectors == 0
        assert summary.active_version == "bge-small:2.0"
        assert len(summary.model_versions) == 1


class TestReembedJob:
    """Test re-embedding job lifecycle."""

    def test_create_reembed(self):
        store = VectorVersionStore()
        store.register_version("bge-small", "1.5")
        job = store.create_reembed_job(
            source_model="bge-small",
            source_version="1.5",
            target_model="bge-small",
            target_version="2.0",
            target_dimension=384,
        )
        assert job.reembed_id.startswith("reb-")
        assert job.status == "pending"
        assert job.source_version == "1.5"
        assert job.target_version == "2.0"

    def test_update_reembed_progress(self):
        store = VectorVersionStore()
        job = store.create_reembed_job("m", "1.0", "m", "2.0")
        updated = store.update_reembed_progress(
            job.reembed_id, processed=50, failed=2, status="running"
        )
        assert updated.processed_vectors == 50
        assert updated.failed_vectors == 2
        assert updated.status == "running"
        assert updated.started_at is not None

    def test_complete_reembed(self):
        store = VectorVersionStore()
        job = store.create_reembed_job("m", "1.0", "m", "2.0")
        store.update_reembed_progress(job.reembed_id, 100, status="running")
        updated = store.update_reembed_progress(job.reembed_id, 100, status="succeeded")
        assert updated.status == "succeeded"
        assert updated.completed_at is not None

    def test_fail_reembed(self):
        store = VectorVersionStore()
        job = store.create_reembed_job("m", "1.0", "m", "2.0")
        updated = store.update_reembed_progress(
            job.reembed_id, 50, failed=10, status="failed", error="OOM"
        )
        assert updated.status == "failed"
        assert updated.error == "OOM"

    def test_invalid_status_raises(self):
        store = VectorVersionStore()
        job = store.create_reembed_job("m", "1.0", "m", "2.0")
        with pytest.raises(ValueError):
            store.update_reembed_progress(job.reembed_id, 0, status="bogus")

    def test_list_reembed_jobs(self):
        store = VectorVersionStore()
        store.create_reembed_job("m", "1.0", "m", "2.0")
        store.create_reembed_job("n", "1.0", "n", "2.0")
        assert len(store.list_reembed_jobs()) == 2
        assert len(store.list_reembed_jobs(status="pending")) == 2

    def test_get_reembed_job(self):
        store = VectorVersionStore()
        job = store.create_reembed_job("m", "1.0", "m", "2.0")
        fetched = store.get_reembed_job(job.reembed_id)
        assert fetched is not None
        assert fetched.reembed_id == job.reembed_id

    def test_progress_property(self):
        job = ReembedJob(
            reembed_id="r1",
            source_model="m",
            source_version="1.0",
            target_model="m",
            target_version="2.0",
            total_vectors=100,
            processed_vectors=75,
        )
        assert job.progress == 0.75


class TestVectorVersionSerialization:
    """Test serialization."""

    def test_version_to_dict(self):
        v = VectorVersion(
            version_id="emv-1",
            model_name="bge",
            model_version="1.5",
            embedding_dimension=384,
            is_active=True,
        )
        d = v.to_dict()
        assert d["version_id"] == "emv-1"
        assert d["model_name"] == "bge"
        assert d["embedding_dimension"] == 384

    def test_reembed_to_dict(self):
        job = ReembedJob(
            reembed_id="reb-1",
            source_model="m",
            source_version="1.0",
            target_model="m",
            target_version="2.0",
            total_vectors=100,
            processed_vectors=50,
        )
        d = job.to_dict()
        assert d["progress"] == 0.5
        assert d["source_version"] == "1.0"

    def test_summary_to_dict(self):
        summary = VersionSummary(
            total_vectors=1000,
            model_versions=[{"model_name": "bge", "model_version": "1.5"}],
            active_version="bge:1.5",
            pending_reembeds=1,
        )
        d = summary.to_dict()
        assert d["total_vectors"] == 1000
        assert d["active_version"] == "bge:1.5"


# ===================================================================
# DTO Tests
# ===================================================================


class TestRecoveredSessionRefDTO:
    """Test RecoveredSessionRef contract DTO."""

    def test_to_dict(self):
        ref = RecoveredSessionRef(
            job_id="job-1",
            execution_id="exec-1",
            job_status="LOST",
            can_retry=True,
            total_steps=3,
            completed_steps=1,
            failed_steps=1,
            running_steps=1,
            resume_from_step="step_2",
        )
        d = ref.to_dict()
        assert d["job_id"] == "job-1"
        assert d["can_retry"] is True
        assert d["resume_from_step"] == "step_2"


class TestVectorVersionRefDTO:
    """Test VectorVersionRef contract DTO."""

    def test_to_dict(self):
        ref = VectorVersionRef(
            version_id="emv-1",
            model_name="bge-small",
            model_version="2.0",
            embedding_dimension=384,
            is_active=True,
        )
        d = ref.to_dict()
        assert d["version_id"] == "emv-1"
        assert d["model_name"] == "bge-small"
        assert d["embedding_dimension"] == 384


# ===================================================================
# Migration and API route existence tests
# ===================================================================


class TestMigrations:
    """Test migration files exist."""

    def test_tool_output_cache_migration(self):
        path = ROOT / "umrs" / "src" / "umrs" / "migrations" / "0045_tool_output_cache.sql"
        assert path.exists()
        content = path.read_text()
        assert "tool_output_cache" in content

    def test_vector_versioning_migration(self):
        path = ROOT / "umrs" / "src" / "umrs" / "migrations" / "0046_vector_versioning.sql"
        assert path.exists()
        content = path.read_text()
        assert "embedding_model_versions" in content
        assert "reembed_jobs" in content


class TestAPIRouteExistence:
    """Retirement-invariant pins (ADR-053, 2026-09-15) — the
    /umrs/tool-cache, /umrs/recovery and /umrs/vectors routers were all
    deleted in the dormant-router retirement; their jobs-side stores
    remain the authority."""

    def test_tool_cache_api_retired(self):
        assert not (ROOT / "umrs" / "src" / "umrs" / "api" / "tool_cache.py").exists()

    def test_recovery_api_retired(self):
        assert not (ROOT / "umrs" / "src" / "umrs" / "api" / "recovery.py").exists()

    def test_vectors_api_retired(self):
        assert not (ROOT / "umrs" / "src" / "umrs" / "api" / "vectors.py").exists()

    def test_jobs_stores_still_authority(self):
        pass
