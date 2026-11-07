"""Tests for Batch 6: MEM-C4 (Knowledge namespace isolation),
MEM-B8 (Workflow fork), MEM-C5 (Cross-experiment knowledge search).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest

UMRS_ROOT = Path(__file__).parent.parent / "umrs" / "src"

# Plain imports by ADR-053 (umrs resolves as a namespace package from the
# repo root; the old path-splice predates the jobs↔api cycle fix).
from umrs.jobs import knowledge_namespace, knowledge_search, workflow_fork  # noqa: E402

# ============================================================================
# MEM-C4: Knowledge Namespace Isolation Tests
# ============================================================================


class TestNamespaceCreate:
    """Test namespace creation."""

    def test_create_default_namespace(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(name="default", scope="default")
        assert ns.namespace_id.startswith("ns-")
        assert ns.name == "default"
        assert ns.scope == "default"
        assert ns.is_active is True

    def test_create_project_namespace(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(
            name="project:proj-1",
            scope="project",
            project_id="proj-1",
            owner_id="user-a",
            description="Project Alpha namespace",
        )
        assert ns.scope == "project"
        assert ns.project_id == "proj-1"
        assert ns.owner_id == "user-a"
        assert ns.description == "Project Alpha namespace"

    def test_create_experiment_namespace(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(
            name="experiment:exp-1",
            scope="experiment",
            project_id="proj-1",
            experiment_id="exp-1",
        )
        assert ns.scope == "experiment"
        assert ns.experiment_id == "exp-1"

    def test_invalid_scope_raises(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        with pytest.raises(ValueError, match="Invalid scope"):
            store.create_namespace(name="bad", scope="invalid_scope")

    def test_create_user_namespace(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(
            name="user:alice",
            scope="user",
            owner_id="alice",
        )
        assert ns.scope == "user"
        assert ns.owner_id == "alice"


class TestNamespaceQuery:
    """Test namespace queries."""

    def test_get_namespace(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(name="test-ns", scope="default")
        retrieved = store.get_namespace(ns.namespace_id)
        assert retrieved is not None
        assert retrieved.name == "test-ns"

    def test_get_nonexistent(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        assert store.get_namespace("ns-nonexistent") is None

    def test_list_all(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_namespace(name="ns1", scope="default")
        store.create_namespace(name="ns2", scope="project", project_id="p1")
        result = store.list_namespaces()
        assert len(result) == 2

    def test_list_by_project(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_namespace(name="ns1", scope="project", project_id="p1")
        store.create_namespace(name="ns2", scope="project", project_id="p2")
        result = store.list_namespaces(project_id="p1")
        assert len(result) == 1
        assert result[0].project_id == "p1"

    def test_list_by_scope(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_namespace(name="ns1", scope="default")
        store.create_namespace(name="ns2", scope="experiment", experiment_id="e1")
        result = store.list_namespaces(scope="experiment")
        assert len(result) == 1
        assert result[0].scope == "experiment"


class TestNamespaceDeactivate:
    """Test namespace deactivation."""

    def test_deactivate(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ns = store.create_namespace(name="to-delete", scope="default")
        ok = store.deactivate_namespace(ns.namespace_id)
        assert ok is True
        # After deactivation, active_only list should not include it
        result = store.list_namespaces(active_only=True)
        assert len(result) == 0

    def test_deactivate_nonexistent(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        ok = store.deactivate_namespace("ns-nonexistent")
        assert ok is False


class TestVisibilityPolicy:
    """Test namespace visibility policies."""

    def test_create_policy(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        policy = store.create_visibility_policy(
            job_id="job-123",
            visible_namespaces=["ns1", "ns2"],
            include_default=True,
            include_project=True,
        )
        assert policy.policy_id.startswith("pol-")
        assert policy.job_id == "job-123"
        assert policy.visible_namespaces == ["ns1", "ns2"]

    def test_get_policy_by_job(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_visibility_policy(job_id="job-456")
        policy = store.get_visibility_policy(job_id="job-456")
        assert policy is not None
        assert policy.job_id == "job-456"

    def test_get_policy_by_workflow(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_visibility_policy(workflow_name="wf-test")
        policy = store.get_visibility_policy(workflow_name="wf-test")
        assert policy is not None
        assert policy.workflow_name == "wf-test"

    def test_resolve_default(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        # No policy exists — should resolve with defaults
        namespaces = store.resolve_visible_namespaces(
            project_id="proj-1",
        )
        assert "default" in namespaces
        assert "project:proj-1" in namespaces

    def test_resolve_with_policy(self):
        store = knowledge_namespace.KnowledgeNamespaceStore()
        store.create_visibility_policy(
            job_id="job-789",
            visible_namespaces=["custom-ns"],
            include_default=True,
            include_project=False,
            include_experiment=True,
        )
        namespaces = store.resolve_visible_namespaces(
            job_id="job-789",
            project_id="proj-1",
            experiment_id="exp-1",
        )
        assert "custom-ns" in namespaces
        assert "default" in namespaces
        assert "experiment:exp-1" in namespaces
        assert "project:proj-1" not in namespaces

    def test_resolve_namespaces_method(self):
        policy = knowledge_namespace.NamespaceVisibility(
            policy_id="test",
            visible_namespaces=["extra"],
            include_default=True,
            include_project=True,
            include_experiment=False,
        )
        result = policy.resolve_namespaces("proj-A", "exp-B")
        assert "extra" in result
        assert "default" in result
        assert "project:proj-A" in result
        assert "experiment:exp-B" not in result


class TestNamespaceSerialization:
    """Test to_dict serialization."""

    def test_namespace_to_dict(self):
        ns = knowledge_namespace.KnowledgeNamespace(
            namespace_id="ns-abc",
            name="test",
            scope="project",
            project_id="p1",
        )
        d = ns.to_dict()
        assert d["namespace_id"] == "ns-abc"
        assert d["scope"] == "project"
        assert d["project_id"] == "p1"

    def test_visibility_to_dict(self):
        pol = knowledge_namespace.NamespaceVisibility(
            policy_id="pol-abc",
            job_id="job-1",
            visible_namespaces=["ns1"],
        )
        d = pol.to_dict()
        assert d["policy_id"] == "pol-abc"
        assert d["visible_namespaces"] == ["ns1"]


# ============================================================================
# MEM-B8: Workflow Fork Tests
# ============================================================================


class _MockJobStatus(Enum):
    COMPLETED = "completed"
    RUNNING = "running"
    FAILED = "failed"


class _MockJobType(Enum):
    WORKFLOW_RUN = "workflow_run"


@dataclass
class _MockJob:
    job_id: str
    project_id: str = "proj-1"
    user_id: str = "user-1"
    job_type: _MockJobType = _MockJobType.WORKFLOW_RUN
    spec: dict = field(default_factory=dict)
    status: _MockJobStatus = _MockJobStatus.COMPLETED


class _MockJobStore:
    """Minimal mock of JobStore for fork testing."""

    def __init__(self):
        self._jobs: dict[str, _MockJob] = {}
        self._counter = 0

    def add_job(self, job: _MockJob):
        self._jobs[job.job_id] = job

    def get_job(self, job_id: str):
        return self._jobs.get(job_id)

    def create_job(self, project_id, user_id, job_type, spec, **_kwargs):
        self._counter += 1
        job_id = f"job-fork-{self._counter}"
        self._jobs[job_id] = _MockJob(
            job_id=job_id,
            project_id=project_id,
            user_id=user_id,
            job_type=job_type,
            spec=spec,
        )
        return job_id


class TestWorkflowFork:
    """Test workflow fork operations."""

    def test_fork_completed_job(self):
        store = _MockJobStore()
        store.add_job(
            _MockJob(
                job_id="job-orig",
                spec={"model": "Qwen2.5", "batch_size": 4},
            )
        )
        forker = workflow_fork.WorkflowForker(store)
        result = forker.fork(
            workflow_fork.WorkflowForkRequest(
                source_job_id="job-orig",
                user_id="user-2",
                param_overrides={"batch_size": 8},
            )
        )
        assert result.new_job_id.startswith("job-fork-")
        assert result.source_job_id == "job-orig"
        assert result.param_overrides == {"batch_size": 8}
        # Verify forked spec
        assert result.forked_spec["batch_size"] == 8
        assert result.forked_spec["model"] == "Qwen2.5"
        assert result.forked_spec["forked_from_job_id"] == "job-orig"

    def test_fork_preserves_lineage(self):
        store = _MockJobStore()
        store.add_job(_MockJob(job_id="job-grandparent", spec={}))
        store.add_job(
            _MockJob(
                job_id="job-parent",
                spec={
                    "forked_from_job_id": "job-grandparent",
                    "original_job_id": "job-grandparent",
                },
            )
        )
        forker = workflow_fork.WorkflowForker(store)
        result = forker.fork(
            workflow_fork.WorkflowForkRequest(
                source_job_id="job-parent",
                user_id="user-1",
            )
        )
        # Lineage chain: new -> parent -> grandparent
        assert len(result.lineage_chain) == 3
        assert result.lineage_chain[0] == result.new_job_id
        assert result.lineage_chain[1] == "job-parent"
        assert result.lineage_chain[2] == "job-grandparent"
        # original_job_id should point to root
        assert result.forked_spec["original_job_id"] == "job-grandparent"

    def test_fork_running_job_fails(self):
        store = _MockJobStore()
        store.add_job(
            _MockJob(
                job_id="job-running",
                status=_MockJobStatus.RUNNING,
            )
        )
        forker = workflow_fork.WorkflowForker(store)
        with pytest.raises(workflow_fork.WorkflowForkError, match="status"):
            forker.fork(
                workflow_fork.WorkflowForkRequest(
                    source_job_id="job-running",
                    user_id="user-1",
                )
            )

    def test_fork_nonexistent_job_fails(self):
        store = _MockJobStore()
        forker = workflow_fork.WorkflowForker(store)
        with pytest.raises(workflow_fork.WorkflowForkError, match="not found"):
            forker.fork(
                workflow_fork.WorkflowForkRequest(
                    source_job_id="job-missing",
                    user_id="user-1",
                )
            )

    def test_fork_failed_job_allowed(self):
        store = _MockJobStore()
        store.add_job(
            _MockJob(
                job_id="job-failed",
                status=_MockJobStatus.FAILED,
                spec={"model": "llama"},
            )
        )
        forker = workflow_fork.WorkflowForker(store)
        result = forker.fork(
            workflow_fork.WorkflowForkRequest(
                source_job_id="job-failed",
                user_id="user-1",
                param_overrides={"model": "qwen"},
            )
        )
        assert result.forked_spec["model"] == "qwen"

    def test_fork_with_description(self):
        store = _MockJobStore()
        store.add_job(_MockJob(job_id="job-1", spec={"x": 1}))
        forker = workflow_fork.WorkflowForker(store)
        result = forker.fork(
            workflow_fork.WorkflowForkRequest(
                source_job_id="job-1",
                user_id="user-1",
                description="Testing with larger batch",
            )
        )
        assert result.forked_spec["fork_description"] == "Testing with larger batch"

    def test_get_fork_lineage(self):
        store = _MockJobStore()
        store.add_job(_MockJob(job_id="job-root", spec={}))
        store.add_job(
            _MockJob(
                job_id="job-child",
                spec={"forked_from_job_id": "job-root"},
            )
        )
        store.add_job(
            _MockJob(
                job_id="job-grandchild",
                spec={"forked_from_job_id": "job-child"},
            )
        )
        forker = workflow_fork.WorkflowForker(store)
        chain = forker.get_fork_lineage("job-grandchild")
        assert chain == ["job-grandchild", "job-child", "job-root"]


class TestWorkflowForkSerialization:
    """Test fork dataclass serialization."""

    def test_request_to_dict(self):
        req = workflow_fork.WorkflowForkRequest(
            source_job_id="job-1",
            user_id="user-1",
            param_overrides={"k": "v"},
        )
        d = req.to_dict()
        assert d["source_job_id"] == "job-1"
        assert d["param_overrides"] == {"k": "v"}

    def test_result_to_dict(self):
        result = workflow_fork.WorkflowForkResult(
            new_job_id="job-new",
            source_job_id="job-orig",
            forked_spec={"x": 1},
            param_overrides={"x": 1},
            lineage_chain=["job-new", "job-orig"],
        )
        d = result.to_dict()
        assert d["new_job_id"] == "job-new"
        assert d["lineage_chain"] == ["job-new", "job-orig"]


# ============================================================================
# MEM-C5: Cross-Experiment Knowledge Search Tests
# ============================================================================


class TestKnowledgeSearch:
    """Test cross-experiment knowledge search."""

    def _make_engine(self) -> knowledge_search.KnowledgeSearchEngine:
        engine = knowledge_search.KnowledgeSearchEngine()
        engine.add_item(
            {
                "memory_id": "m1",
                "content": "Seismic wave propagation simulation results",
                "namespace": "experiment:exp-1",
                "project_id": "proj-1",
                "source": "paper",
                "importance": 0.8,
            }
        )
        engine.add_item(
            {
                "memory_id": "m2",
                "content": "Molecular dynamics configuration parameters",
                "namespace": "experiment:exp-2",
                "project_id": "proj-1",
                "source": "code",
                "importance": 0.6,
            }
        )
        engine.add_item(
            {
                "memory_id": "m3",
                "content": "GPU performance benchmark results for seismic",
                "namespace": "project:proj-1",
                "project_id": "proj-1",
                "source": "benchmark",
                "importance": 0.9,
            }
        )
        engine.add_item(
            {
                "memory_id": "m4",
                "content": "Unrelated content in another project",
                "namespace": "default",
                "project_id": "proj-2",
                "source": "note",
                "importance": 0.5,
            }
        )
        return engine

    def test_search_project_scope(self):
        engine = self._make_engine()
        result = engine.search(
            query="seismic",
            scope="project",
            project_id="proj-1",
        )
        assert result.total_hits == 2
        assert result.scope == "project"
        assert result.project_id == "proj-1"
        # Ordered by importance desc
        assert result.hits[0].score >= result.hits[1].score

    def test_search_across_experiments(self):
        engine = self._make_engine()
        result = engine.search(
            query="results",
            scope="project",
            project_id="proj-1",
        )
        # Hits from multiple namespaces
        namespaces = {h.namespace for h in result.hits}
        assert len(namespaces) >= 2

    def test_search_specific_namespace(self):
        engine = self._make_engine()
        result = engine.search(
            query="seismic",
            scope="experiment",
            namespaces=["experiment:exp-1"],
        )
        assert result.total_hits == 1
        assert result.hits[0].memory_id == "m1"

    def test_search_no_results(self):
        engine = self._make_engine()
        result = engine.search(
            query="nonexistent-topic-xyz",
            scope="project",
            project_id="proj-1",
        )
        assert result.total_hits == 0
        assert result.hits == []

    def test_search_limit(self):
        engine = self._make_engine()
        result = engine.search(
            query="results",
            scope="project",
            project_id="proj-1",
            limit=1,
        )
        assert len(result.hits) <= 1

    def test_search_project_isolation(self):
        engine = self._make_engine()
        # Search proj-2 should not return proj-1 items
        result = engine.search(
            query="seismic",
            scope="project",
            project_id="proj-2",
        )
        assert result.total_hits == 0

    def test_hit_attribution(self):
        engine = self._make_engine()
        result = engine.search(query="seismic", scope="project", project_id="proj-1")
        hit = result.hits[0]
        assert hit.namespace != ""
        assert hit.source != ""
        assert hit.project_id == "proj-1"


class TestKnowledgeSearchSerialization:
    """Test search result serialization."""

    def test_hit_to_dict(self):
        hit = knowledge_search.KnowledgeSearchHit(
            memory_id="m1",
            content="test content",
            namespace="experiment:exp-1",
            project_id="proj-1",
            source="paper",
            score=0.85,
        )
        d = hit.to_dict()
        assert d["memory_id"] == "m1"
        assert d["score"] == 0.85
        assert d["namespace"] == "experiment:exp-1"

    def test_result_to_dict(self):
        result = knowledge_search.KnowledgeSearchResult(
            query="test",
            scope="project",
            project_id="proj-1",
            namespaces_searched=["ns1"],
            hits=[
                knowledge_search.KnowledgeSearchHit(
                    memory_id="m1",
                    content="c1",
                    namespace="ns1",
                    score=0.9,
                )
            ],
            total_hits=1,
        )
        d = result.to_dict()
        assert d["query"] == "test"
        assert d["total_hits"] == 1
        assert len(d["hits"]) == 1


# ============================================================================
# DTO Tests (r4-contracts)
# ============================================================================


class TestContractDTOs:
    """Test new provenance DTOs."""

    def test_knowledge_namespace_ref(self):
        from r4_contracts.provenance import KnowledgeNamespaceRef

        ref = KnowledgeNamespaceRef(
            namespace_id="ns-123",
            name="experiment:exp-1",
            scope="experiment",
            project_id="proj-1",
            experiment_id="exp-1",
        )
        d = ref.to_dict()
        assert d["namespace_id"] == "ns-123"
        assert d["scope"] == "experiment"

    def test_workflow_fork_ref(self):
        from r4_contracts.provenance import WorkflowForkRef

        ref = WorkflowForkRef(
            new_job_id="job-new",
            source_job_id="job-orig",
            param_overrides={"k": "v"},
            lineage_chain=["job-new", "job-orig"],
        )
        d = ref.to_dict()
        assert d["new_job_id"] == "job-new"
        assert d["lineage_chain"] == ["job-new", "job-orig"]

    def test_knowledge_search_hit_ref(self):
        from r4_contracts.provenance import KnowledgeSearchHitRef

        ref = KnowledgeSearchHitRef(
            memory_id="m1",
            content="test",
            namespace="ns1",
            score=0.75,
        )
        d = ref.to_dict()
        assert d["score"] == 0.75


# ============================================================================
# Integration Tests
# ============================================================================


class TestMigrationExists:
    """Verify migration file exists and has expected tables."""

    def test_0047_knowledge_namespaces(self):
        path = UMRS_ROOT / "umrs" / "migrations" / "0047_knowledge_namespaces.sql"
        assert path.exists()
        content = path.read_text()
        assert "knowledge_namespaces" in content
        assert "namespace_visibility_policies" in content
        assert "scope" in content


class TestAPIRouteExistence:
    """Retirement-invariant pins (ADR-053, 2026-09-15) — the
    /umrs/namespaces and /umrs/workflows/fork routers were deleted in the
    dormant-router retirement; their jobs modules remain the authority."""

    def test_namespaces_api_retired(self):
        assert not (UMRS_ROOT / "umrs" / "api" / "namespaces.py").exists()

    def test_workflow_fork_api_retired(self):
        assert not (UMRS_ROOT / "umrs" / "api" / "workflow_fork.py").exists()

    def test_jobs_modules_still_authority(self):
        pass


class TestMigrationFiles:
    """Verify migration files exist."""

    def test_0047_knowledge_namespaces(self):
        path = UMRS_ROOT / "umrs" / "migrations" / "0047_knowledge_namespaces.sql"
        assert path.exists()


class TestGatewayEdgeRoutes:
    """Edge exposure of the batch-6 surfaces.

    The Python gateway proxy modules (``gateway/gateway/routes/*.py``) were
    deleted with the whole Python gateway — ADR-014 Phases 3+4, commit
    0aac3ec2 (2026-09-11). NGINX + the auth sidecar are the only edge, so the
    edge assertion now reads ``gateway/nginx/conf.d/server.conf``.

    Only ``/gw/knowledge`` survived the removal with an NGINX analogue;
    ``/gw/namespaces`` and ``/gw/workflow-fork`` have no edge location at all
    (reported as a reachability gap, not asserted away here).
    """

    NGINX_CONF = Path(__file__).parent.parent / "gateway" / "nginx" / "conf.d" / "server.conf"

    def _knowledge_block(self) -> str:
        content = self.NGINX_CONF.read_text()
        start = content.index("location /gw/knowledge/ {")
        return content[start : content.index("\n    }", start)]

    def test_knowledge_route_proxied_at_edge(self):
        block = self._knowledge_block()
        assert "proxy_pass http://umrs" in block

    def test_knowledge_route_requires_auth(self):
        # CONSTITUTION §2: every /gw/* route goes through auth_request.
        block = self._knowledge_block()
        assert "auth_request /auth/validate;" in block

    def test_python_gateway_routes_are_gone(self):
        # ADR-014 Phase 4: the Python gateway package must stay deleted.
        legacy = Path(__file__).parent.parent / "gateway" / "gateway"
        assert not (legacy / "main.py").exists()
        assert not (legacy / "routes" / "__init__.py").exists()
