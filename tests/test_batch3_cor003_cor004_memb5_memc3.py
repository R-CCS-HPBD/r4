"""Tests for Batch 3: COR-003 completion, COR-004, MEM-B5, MEM-C3.

Tests cover:
  - Model manifest locks (deploy/model_manifest.py)
  - Extensibility governance (r4-contracts extensibility, sandbox enforcer)
  - Replay inputs (execution trace store snapshots)
  - Context packing API (enhanced pack_context with RAG + token breakdown)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from umrs.api import context as _ctx_mod
from umrs.jobs import execution_trace_store as _trace_store_mod

# Pre-load model_manifest (ADR-053 plain imports from deployed trees)
from deploy import model_manifest as _model_manifest_mod
from r4_contracts import extensibility as _ext_mod
from r4_contracts import provenance as _prov_mod
from r4_mcp.runners import sandbox_enforcer as _sandbox_mod

ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# COR-003: Model Manifest Locks
# ============================================================================


class TestModelManifestProfiles:
    """Test model manifest profile inventory."""

    def test_all_profiles_exist(self):
        profiles = _model_manifest_mod._PROFILE_MODELS
        assert "hpc-full" in profiles
        assert "hpc-minimal" in profiles
        assert "k8s-cluster" in profiles
        assert "dev-compose" in profiles

    def test_hpc_full_has_llm_and_embedding(self):
        models = _model_manifest_mod._PROFILE_MODELS["hpc-full"]
        names = [m.name for m in models]
        assert "Qwen2.5-7B-Instruct" in names
        assert "all-MiniLM-L6-v2" in names

    def test_hpc_minimal_has_llm_only(self):
        models = _model_manifest_mod._PROFILE_MODELS["hpc-minimal"]
        names = [m.name for m in models]
        assert "Qwen2.5-7B-Instruct" in names
        assert len(models) == 1

    def test_model_ref_display(self):
        ref = _model_manifest_mod.ModelManifestRef(
            "test-model",
            "/models/test",
            version="1.0",
            source="filesystem_path",
            purpose="test",
        )
        assert "test-model" in ref.display
        assert "v1.0" in ref.display


class TestModelManifestResolve:
    """Test path resolution and hashing."""

    def test_resolve_env_var(self):
        with patch.dict(os.environ, {"VLLM_MODEL_PATH": "/data/models/qwen"}):
            result = _model_manifest_mod._resolve_path("${VLLM_MODEL_PATH}")
            assert result == "/data/models/qwen"

    def test_resolve_missing_env_var(self):
        env = dict(os.environ)
        env.pop("NONEXISTENT_VAR", None)
        with patch.dict(os.environ, env, clear=True):
            result = _model_manifest_mod._resolve_path("${NONEXISTENT_VAR}")
            assert result == ""

    def test_compute_dir_hash_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"test model data")
            f.flush()
            h = _model_manifest_mod._compute_dir_hash(Path(f.name))
            assert len(h) == 64  # sha256 hex
        os.unlink(f.name)

    def test_compute_dir_hash_dir(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "file1.txt").write_text("a")
            (Path(d) / "file2.txt").write_text("b")
            h = _model_manifest_mod._compute_dir_hash(Path(d))
            assert len(h) == 64

    def test_compute_dir_hash_nonexistent(self):
        h = _model_manifest_mod._compute_dir_hash(Path("/nonexistent/path"))
        assert h == ""


class TestModelManifestLock:
    """Test lock file generation and verification."""

    def test_manifest_to_json(self):
        manifest = _model_manifest_mod.ModelManifest(
            profile="test",
            models=[{"name": "m1", "path": "/p"}],
        )
        j = manifest.to_json()
        data = json.loads(j)
        assert data["profile"] == "test"
        assert len(data["models"]) == 1

    def test_cmd_list_unknown_profile(self):
        assert _model_manifest_mod.cmd_list("nonexistent") == 1


# ============================================================================
# COR-004: Extensibility Governance
# ============================================================================


class TestSandboxSchemaVersion:
    """Test schema versioning."""

    def test_current_version_is_v1(self):
        assert _ext_mod.CURRENT_SCHEMA_VERSION.value == "1"

    def test_v1_in_supported(self):
        assert _ext_mod.SandboxSchemaVersion.V1 in _ext_mod.SUPPORTED_SCHEMA_VERSIONS

    def test_extension_manifest_has_schema_version(self):
        m = _ext_mod.ExtensionManifest(
            name="test",
            version="1.0",
            entry_point="python server.py",
            tools=["tool1"],
        )
        assert m.schema_version == "1"


class TestExtensionManifestValidation:
    """Test manifest validation with schema version."""

    def test_valid_manifest(self):
        m = _ext_mod.ExtensionManifest(
            name="test",
            version="1.0",
            entry_point="python server.py",
            tools=["tool1"],
            schema_version="1",
        )
        errors = m.validate()
        assert errors == []

    def test_unknown_schema_version_rejected(self):
        m = _ext_mod.ExtensionManifest(
            name="test",
            version="1.0",
            entry_point="python server.py",
            tools=["tool1"],
            schema_version="99",
        )
        errors = m.validate()
        assert any("schema_version" in e for e in errors)

    def test_serialization_includes_schema_version(self):
        m = _ext_mod.ExtensionManifest(
            name="test",
            version="1.0",
            entry_point="python server.py",
            tools=["tool1"],
        )
        d = m.to_dict()
        assert "schema_version" in d
        assert d["schema_version"] == "1"

    def test_from_dict_preserves_schema_version(self):
        data = {
            "name": "test",
            "version": "1.0",
            "entry_point": "python server.py",
            "capabilities": {"tools": ["t1"]},
            "schema_version": "1",
        }
        m = _ext_mod.ExtensionManifest.from_dict(data)
        assert m.schema_version == "1"


class TestCapabilityGrant:
    """Test capability grant record."""

    def test_grant_to_dict(self):
        g = _ext_mod.CapabilityGrant(
            extension_name="my-ext",
            capability="WRITE_REMOTE",
            granted_by="admin",
            granted_at="2026-01-01T00:00:00Z",
        )
        d = g.to_dict()
        assert d["extension_name"] == "my-ext"
        assert d["capability"] == "WRITE_REMOTE"
        assert d["scope"] == "user"


class TestPluginJobContract:
    """Test plugin job contract validation."""

    def test_valid_contract(self):
        c = _ext_mod.PluginJobContract(
            plugin_name="test",
            job_type="inference",
            execution_authorities=["compose"],
        )
        assert c.validate() == []

    def test_invalid_authority(self):
        c = _ext_mod.PluginJobContract(
            plugin_name="test",
            job_type="inference",
            execution_authorities=["lambda"],
        )
        errors = c.validate()
        assert any("lambda" in e for e in errors)


class TestSandboxEnforcer:
    """Test sandbox resource limit enforcement."""

    def test_limits_validate_positive(self):
        limits = _sandbox_mod.SandboxLimits(max_memory_mb=512, max_runtime_sec=30)
        limits.validate()  # should not raise

    def test_limits_reject_zero(self):
        with pytest.raises(ValueError, match="max_memory_mb"):
            _sandbox_mod.SandboxLimits(max_memory_mb=0).validate()

    def test_build_preexec_fn_returns_callable(self):
        limits = _sandbox_mod.SandboxLimits()
        fn = _sandbox_mod.build_preexec_fn(limits)
        # On macOS/Linux, should return a callable
        if fn is not None:
            assert callable(fn)


# ============================================================================
# MEM-B5: Replay Inputs
# ============================================================================


class TestInputSnapshot:
    """Test InputSnapshot DTO."""

    def test_snapshot_to_dict(self):
        s = _prov_mod.InputSnapshot(
            trace_id="trc-abc",
            step_name="step1",
            inputs_hash="sha256:abc",
            inputs_data={"prompt": "hello"},
        )
        d = s.to_dict()
        assert d["trace_id"] == "trc-abc"
        assert d["inputs_data"]["prompt"] == "hello"


class TestReplayInputStore:
    """Test execution trace store replay features."""

    def setup_method(self):
        self.store = _trace_store_mod.ExecutionTraceStore(db_url=None)

    def test_create_trace_stores_input_snapshot(self):
        record = self.store.create_trace(
            job_id="j1",
            step_name="step1",
            inputs={"prompt": "hello world"},
        )
        assert record.inputs_hash is not None
        assert record.inputs_snapshot == {"prompt": "hello world"}

    def test_create_trace_no_snapshot_for_none_inputs(self):
        record = self.store.create_trace(
            job_id="j1",
            step_name="step1",
            inputs=None,
        )
        assert record.inputs_snapshot is None
        assert record.inputs_hash is None

    def test_complete_trace_stores_output_snapshot(self):
        record = self.store.create_trace(
            job_id="j1",
            step_name="step1",
        )
        completed = self.store.complete_trace(
            record.trace_id,
            outputs={"result": "ok"},
        )
        assert completed.outputs_snapshot == {"result": "ok"}
        assert completed.outputs_hash is not None

    def test_get_input_snapshot(self):
        record = self.store.create_trace(
            job_id="j1",
            step_name="step1",
            inputs={"key": "value"},
        )
        snapshot = self.store.get_input_snapshot(record.trace_id)
        assert snapshot == {"key": "value"}

    def test_get_output_snapshot(self):
        record = self.store.create_trace(
            job_id="j1",
            step_name="step1",
        )
        self.store.complete_trace(record.trace_id, outputs={"out": 42})
        snapshot = self.store.get_output_snapshot(record.trace_id)
        assert snapshot == {"out": 42}

    def test_find_traces_by_step(self):
        self.store.create_trace(job_id="j1", step_name="preprocess")
        self.store.create_trace(job_id="j2", step_name="preprocess")
        self.store.create_trace(job_id="j3", step_name="inference")
        results = self.store.find_traces_by_step("preprocess")
        assert len(results) == 2

    def test_find_traces_by_step_with_status(self):
        r1 = self.store.create_trace(job_id="j1", step_name="step1")
        self.store.create_trace(job_id="j2", step_name="step1")
        self.store.start_trace(r1.trace_id)
        results = self.store.find_traces_by_step("step1", status="running")
        assert len(results) == 1

    def test_find_traces_by_inputs_hash(self):
        r1 = self.store.create_trace(
            job_id="j1",
            step_name="s1",
            inputs={"x": 1},
        )
        self.store.create_trace(
            job_id="j2",
            step_name="s1",
            inputs={"x": 1},
        )
        self.store.create_trace(
            job_id="j3",
            step_name="s1",
            inputs={"x": 2},
        )
        results = self.store.find_traces_by_inputs_hash(r1.inputs_hash)
        assert len(results) == 2


class TestReplayAPIEndpoints:
    """Test replay API endpoint registration via source inspection."""

    def _read_traces_source(self) -> str:
        p = ROOT / "umrs" / "src" / "umrs" / "api" / "traces.py"
        return p.read_text()

    def test_umrs_traces_has_inputs_endpoint(self):
        src = self._read_traces_source()
        assert "/{trace_id}/inputs" in src
        assert "InputSnapshotResponse" in src

    def test_umrs_traces_has_outputs_endpoint(self):
        src = self._read_traces_source()
        assert "/{trace_id}/outputs" in src
        assert "OutputSnapshotResponse" in src

    def test_umrs_traces_has_step_query(self):
        src = self._read_traces_source()
        assert "/step/{step_name}" in src
        assert "find_traces_by_step" in src


# ============================================================================
# MEM-C3: Context Packing API
# ============================================================================


class TestContextPackRequest:
    """Test enhanced context pack request model."""

    def test_request_has_rag_fields(self):
        ctx_mod = _ctx_mod
        req = ctx_mod.ContextPackRequest(
            conversation_id="conv-1",
            include_rag=True,
            rag_query="search term",
            rag_namespace_id="ns-1",
            rag_limit=10,
        )
        assert req.include_rag is True
        assert req.rag_query == "search term"
        assert req.rag_limit == 10


class TestContextPackResponse:
    """Test enhanced context pack response model."""

    def test_response_has_retrieval_context(self):
        ctx_mod = _ctx_mod
        resp = ctx_mod.ContextPackResponse(
            conversation_id="conv-1",
            messages=[],
            retrieval_context=[{"content": "test", "source": "rag"}],
            token_breakdown={"messages": 0, "summary": 0, "memory": 0, "retrieval": 10},
        )
        assert len(resp.retrieval_context) == 1
        assert resp.token_breakdown["retrieval"] == 10


class TestContextPackStore:
    """Test context store pack_context with RAG and token breakdown."""

    def setup_method(self):
        self.ctx_mod = _ctx_mod
        # Create a fresh store
        self.store = self.ctx_mod._ContextStore()

    def test_pack_nonexistent_conversation_raises(self):
        from fastapi import HTTPException

        req = self.ctx_mod.ContextPackRequest(conversation_id="nonexistent")
        with pytest.raises(HTTPException) as excinfo:
            self.store.pack_context(req)
        assert excinfo.value.status_code == 404

    def test_pack_with_conversation(self):
        # Create a conversation
        conv = self.store.create_conversation(
            self.ctx_mod.ConversationCreate(user_id="u1", title="test")
        )
        # Add messages
        self.store.append_messages(
            conv.conversation_id,
            [
                self.ctx_mod.MessageCreate(role="user", content="hello", token_count=5),
                self.ctx_mod.MessageCreate(role="assistant", content="hi", token_count=3),
            ],
        )
        # Pack
        req = self.ctx_mod.ContextPackRequest(conversation_id=conv.conversation_id)
        resp = self.store.pack_context(req)
        assert len(resp.messages) == 2
        assert resp.total_tokens == 8
        assert resp.token_breakdown["messages"] == 8

    def test_pack_with_memory(self):
        conv = self.store.create_conversation(
            self.ctx_mod.ConversationCreate(user_id="u1", title="test")
        )
        # Store memory
        self.store.store_memory(
            self.ctx_mod.MemoryItemCreate(
                user_id="u1",
                content="important fact about X",
                importance=0.9,
            )
        )
        # Pack with memory query
        req = self.ctx_mod.ContextPackRequest(
            conversation_id=conv.conversation_id,
            include_memory=True,
            memory_query="important",
        )
        resp = self.store.pack_context(req)
        assert len(resp.memory_items) >= 1
        assert resp.token_breakdown["memory"] > 0

    def test_pack_with_rag(self):
        conv = self.store.create_conversation(
            self.ctx_mod.ConversationCreate(user_id="u1", title="test")
        )
        # Store some "RAG-like" memory items
        self.store.store_memory(
            self.ctx_mod.MemoryItemCreate(
                user_id="u1",
                content="RAG document chunk about physics",
                source="document",
                importance=0.8,
            )
        )
        # Pack with RAG
        req = self.ctx_mod.ContextPackRequest(
            conversation_id=conv.conversation_id,
            include_rag=True,
            rag_query="physics",
        )
        resp = self.store.pack_context(req)
        assert len(resp.retrieval_context) >= 1
        assert resp.token_breakdown["retrieval"] > 0

    def test_token_breakdown_sums_to_total(self):
        conv = self.store.create_conversation(
            self.ctx_mod.ConversationCreate(user_id="u1", title="test")
        )
        self.store.append_messages(
            conv.conversation_id,
            [
                self.ctx_mod.MessageCreate(role="user", content="hello", token_count=5),
            ],
        )
        req = self.ctx_mod.ContextPackRequest(conversation_id=conv.conversation_id)
        resp = self.store.pack_context(req)
        breakdown_sum = sum(resp.token_breakdown.values())
        assert resp.total_tokens == breakdown_sum


class TestContextPackRoute:
    """MEM-C3 context-pack surface lives on the UMRS router.

    The former gateway proxy module ``gateway/gateway/routes/context.py`` was
    deleted with the whole Python gateway (ADR-014 Phases 3+4, commit
    0aac3ec2, 2026-09-11); UMRS owns the route itself now.
    """

    def test_context_router_prefix(self):
        assert _ctx_mod.router.prefix == "/umrs/context"

    def test_context_router_has_pack_route(self):
        routes = [r.path for r in _ctx_mod.router.routes]
        assert any(r.endswith("/pack") for r in routes)


# ============================================================================
# Migration file existence
# ============================================================================


class TestMigrationFiles:
    """Verify migration files exist."""

    def test_replay_inputs_migration(self):
        p = ROOT / "umrs" / "src" / "umrs" / "migrations" / "0042_replay_inputs.sql"
        assert p.exists()
        content = p.read_text()
        assert "inputs_snapshot" in content
        assert "outputs_snapshot" in content
        assert "idx_execution_traces_step_status" in content
        assert "idx_execution_traces_inputs_hash" in content
