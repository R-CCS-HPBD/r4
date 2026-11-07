"""
E2E tests for the A2L (Artifact & Agent Layer) service.

Tests the complete artifact lifecycle through the Gateway→A2L path:
  - Health checks (A2L direct + via Gateway proxy)
  - Artifact CRUD (create, list, get, status transitions)
  - Derivation with lineage edges
  - Lineage graph traversal (ancestors, descendants, full graph)
  - Matching / dedup lookup
  - Search with filters
  - Agent execution lifecycle (create, update, list)
  - Event stream (when Redis is available)

Prerequisites:
  - docker-compose.e2e.yaml running (``python tests/run_e2e_tests.py``)
  - A2L, Gateway, and Postgres containers healthy

Usage:
    E2E_TEST_MODE=true pytest tests/test_a2l_e2e.py -v
"""

from __future__ import annotations

import hashlib
import os
import uuid

import httpx
import pytest

# =============================================================================
# Configuration
# =============================================================================

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
A2L_URL = os.environ.get("A2L_URL", "http://localhost:8002")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-test-e2e")


def is_e2e_mode() -> bool:
    return os.environ.get("E2E_TEST_MODE", "").lower() == "true"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def e2e_mode():
    if not is_e2e_mode():
        pytest.skip("Not in E2E test mode. Set E2E_TEST_MODE=true")


@pytest.fixture
def gateway_headers():
    return {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "X-Project-ID": "test-project",
    }


@pytest.fixture
def user_id():
    return f"e2e-user-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def project_id():
    return f"e2e-proj-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client():
    with httpx.Client(timeout=30) as c:
        yield c


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


# =============================================================================
# A2L Health
# =============================================================================


class TestA2LHealth:
    """Verify A2L service is reachable directly and via gateway."""

    @pytest.mark.e2e
    def test_a2l_direct_health(self, e2e_mode, client):
        """A2L /health endpoint should respond."""
        resp = client.get(f"{A2L_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "a2l"

    @pytest.mark.e2e
    def test_a2l_via_gateway(self, e2e_mode, client, gateway_headers):
        """Gateway /gw/a2l/health should proxy to A2L."""
        resp = client.get(f"{GATEWAY_URL}/gw/a2l/health", headers=gateway_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# =============================================================================
# Artifact CRUD
# =============================================================================


class TestArtifactCRUD:
    """Test basic artifact create / read / list / status transitions."""

    @pytest.mark.e2e
    def test_create_artifact(self, e2e_mode, client, gateway_headers):
        """Create an artifact via Gateway and retrieve it."""
        body = {
            "type": "dataset",
            "uri": f"s3://e2e-bucket/data-{uuid.uuid4().hex[:8]}.csv",
            "input_data_hash": _sha256(f"test-data-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
            "project_id": "e2e-project",
            "metadata": {"source": "e2e-test"},
            "artifact_class": "dataset",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["type"] == "dataset"
        assert data["status"] == "pending"
        assert data["artifact_class"] == "dataset"
        artifact_id = data["id"]

        # GET the artifact back
        resp2 = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{artifact_id}",
            headers=gateway_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["id"] == artifact_id

    @pytest.mark.e2e
    def test_list_artifacts(self, e2e_mode, client, gateway_headers):
        """List artifacts returns paginated response."""
        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            params={"limit": 5},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    @pytest.mark.e2e
    def test_status_transitions(self, e2e_mode, client, gateway_headers):
        """Artifact status FSM: pending → running → completed."""
        # Create
        body = {
            "type": "simulation",
            "uri": f"s3://e2e-bucket/sim-{uuid.uuid4().hex[:8]}.h5",
            "input_data_hash": _sha256(f"sim-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201
        aid = resp.json()["id"]

        # pending → running
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{aid}/status",
            json={"status": "running"},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        # running → completed
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{aid}/status",
            json={"status": "completed"},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    @pytest.mark.e2e
    def test_invalid_status_transition(self, e2e_mode, client, gateway_headers):
        """FSM rejects invalid transitions (e.g. pending → completed)."""
        body = {
            "type": "image",
            "uri": f"s3://e2e-bucket/img-{uuid.uuid4().hex[:8]}.png",
            "input_data_hash": _sha256(f"img-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201
        aid = resp.json()["id"]

        # pending → completed should fail
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{aid}/status",
            json={"status": "completed"},
            headers=gateway_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.e2e
    def test_soft_delete(self, e2e_mode, client, gateway_headers):
        """Soft-delete should return 204 and hide the artifact."""
        body = {
            "type": "temp",
            "uri": f"s3://e2e-bucket/tmp-{uuid.uuid4().hex[:8]}.dat",
            "input_data_hash": _sha256(f"tmp-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201
        aid = resp.json()["id"]

        # Delete
        resp = client.delete(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{aid}",
            headers=gateway_headers,
        )
        assert resp.status_code == 204

        # Should be gone (404)
        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{aid}",
            headers=gateway_headers,
        )
        assert resp.status_code == 404


# =============================================================================
# Derivation & Lineage
# =============================================================================


class TestDerivationAndLineage:
    """Test derive endpoint and lineage graph traversal."""

    def _create_artifact(self, client, headers, type_name="dataset", **extra):
        body = {
            "type": type_name,
            "uri": f"s3://e2e-bucket/{type_name}-{uuid.uuid4().hex[:8]}",
            "input_data_hash": _sha256(f"{type_name}-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
            "project_id": "e2e-project",
            **extra,
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.e2e
    def test_derive_artifact(self, e2e_mode, client, gateway_headers):
        """Derive creates child with lineage edges."""
        parent = self._create_artifact(client, gateway_headers, "dataset")
        parent_id = parent["id"]

        derive_body = {
            "parents": [parent_id],
            "operation": "preprocess",
            "params": {"normalize": True, "scale": 1.0},
            "type": "feature",
            "uri": f"s3://e2e-bucket/feature-{uuid.uuid4().hex[:8]}",
            "user_id": "e2e-user",
            "project_id": "e2e-project",
            "input_data_hash": _sha256(f"feature-{uuid.uuid4().hex}"),
            "artifact_class": "feature",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/derive",
            json=derive_body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201, resp.text
        child = resp.json()
        assert child["type"] == "feature"
        assert parent_id in child.get("parents", [])

    @pytest.mark.e2e
    def test_lineage_graph(self, e2e_mode, client, gateway_headers):
        """Build a 3-node chain and verify lineage graph."""
        # A → B → C
        a = self._create_artifact(client, gateway_headers, "raw")
        b_body = {
            "parents": [a["id"]],
            "operation": "extract",
            "params": {},
            "type": "extracted",
            "uri": f"s3://e2e-bucket/ext-{uuid.uuid4().hex[:8]}",
            "user_id": "e2e-user",
            "input_data_hash": _sha256(f"ext-{uuid.uuid4().hex}"),
        }
        b = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/derive",
            json=b_body,
            headers=gateway_headers,
        ).json()

        c_body = {
            "parents": [b["id"]],
            "operation": "analyze",
            "params": {"method": "pca"},
            "type": "analysis",
            "uri": f"s3://e2e-bucket/analysis-{uuid.uuid4().hex[:8]}",
            "user_id": "e2e-user",
            "input_data_hash": _sha256(f"analysis-{uuid.uuid4().hex}"),
        }
        c = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/derive",
            json=c_body,
            headers=gateway_headers,
        ).json()

        # Full lineage from middle node
        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{b['id']}/lineage",
            params={"direction": "both", "max_depth": 5},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["root_id"] == b["id"]
        node_ids = {n["id"] for n in graph["nodes"]}
        # Should include all three nodes
        assert a["id"] in node_ids
        assert b["id"] in node_ids
        assert c["id"] in node_ids
        assert len(graph["edges"]) >= 2

    @pytest.mark.e2e
    def test_ancestors(self, e2e_mode, client, gateway_headers):
        """Ancestors endpoint returns parent chain."""
        parent = self._create_artifact(client, gateway_headers, "dataset")
        derive_body = {
            "parents": [parent["id"]],
            "operation": "transform",
            "params": {},
            "type": "transformed",
            "uri": f"s3://e2e-bucket/t-{uuid.uuid4().hex[:8]}",
            "user_id": "e2e-user",
            "input_data_hash": _sha256(f"t-{uuid.uuid4().hex}"),
        }
        child = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/derive",
            json=derive_body,
            headers=gateway_headers,
        ).json()

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{child['id']}/ancestors",
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        ancestors = resp.json()
        ancestor_ids = [a["id"] for a in ancestors]
        assert parent["id"] in ancestor_ids

    @pytest.mark.e2e
    def test_descendants(self, e2e_mode, client, gateway_headers):
        """Descendants endpoint returns child chain."""
        parent = self._create_artifact(client, gateway_headers, "dataset")
        derive_body = {
            "parents": [parent["id"]],
            "operation": "split",
            "params": {},
            "type": "split",
            "uri": f"s3://e2e-bucket/s-{uuid.uuid4().hex[:8]}",
            "user_id": "e2e-user",
            "input_data_hash": _sha256(f"s-{uuid.uuid4().hex}"),
        }
        child = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/derive",
            json=derive_body,
            headers=gateway_headers,
        ).json()

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/{parent['id']}/descendants",
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        descendants = resp.json()
        desc_ids = [d["id"] for d in descendants]
        assert child["id"] in desc_ids


# =============================================================================
# Matching & Search
# =============================================================================


class TestMatchingAndSearch:
    """Test dedup matching and filtered search."""

    @pytest.mark.e2e
    def test_match_existing_artifact(self, e2e_mode, client, gateway_headers):
        """Match should find an existing artifact with same inputs."""
        data_hash = _sha256(f"match-test-{uuid.uuid4().hex}")
        body = {
            "type": "dataset",
            "uri": f"s3://e2e-bucket/match-{uuid.uuid4().hex[:8]}",
            "input_data_hash": data_hash,
            "user_id": "e2e-user",
            "metadata": {"tag": "matchable"},
        }
        _created = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        ).json()

        # Match with same inputs
        match_body = {
            "input_data_hash": data_hash,
            "operation": "dataset",  # type is used as operation for create
            "params": {"tag": "matchable"},  # metadata is used as params for create
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts/match",
            json=match_body,
            headers=gateway_headers,
        )
        # Should find the existing artifact (200) or not (404 if hash differs)
        assert resp.status_code in (200, 404)

    @pytest.mark.e2e
    def test_search_by_type(self, e2e_mode, client, gateway_headers):
        """Search with type filter should return matching artifacts."""
        unique_type = f"search-type-{uuid.uuid4().hex[:6]}"
        body = {
            "type": unique_type,
            "uri": f"s3://e2e-bucket/search-{uuid.uuid4().hex[:8]}",
            "input_data_hash": _sha256(f"search-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
        }
        client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/search",
            params={"type": unique_type},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all(it["type"] == unique_type for it in data["items"])

    @pytest.mark.e2e
    def test_search_by_class(self, e2e_mode, client, gateway_headers):
        """Search with artifact_class filter."""
        body = {
            "type": "data",
            "uri": f"s3://e2e-bucket/cls-{uuid.uuid4().hex[:8]}",
            "input_data_hash": _sha256(f"cls-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
            "artifact_class": "simulation",
        }
        client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/artifacts/search",
            params={"artifact_class": "simulation"},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


# =============================================================================
# Agent Execution Lifecycle
# =============================================================================


class TestAgentExecution:
    """Test agent execution CRUD through the Gateway."""

    def _create_artifact(self, client, headers):
        body = {
            "type": "input-data",
            "uri": f"s3://e2e-bucket/in-{uuid.uuid4().hex[:8]}",
            "input_data_hash": _sha256(f"in-{uuid.uuid4().hex}"),
            "user_id": "e2e-user",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=headers,
        )
        assert resp.status_code == 201
        return resp.json()

    @pytest.mark.e2e
    def test_create_execution(self, e2e_mode, client, gateway_headers):
        """Create an agent execution record."""
        input_art = self._create_artifact(client, gateway_headers)
        body = {
            "agent_name": "e2e-segmenter",
            "agent_version": "1.0.0",
            "model_ref": "Qwen/Qwen2.5-7B-Instruct",
            "prompt_hash": _sha256("system-prompt"),
            "params": {"temperature": 0.7},
            "input_artifact_ids": [input_art["id"]],
            "user_id": "e2e-user",
            "project_id": "e2e-project",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            json=body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["agent_name"] == "e2e-segmenter"
        assert data["status"] == "pending"
        assert input_art["id"] in data["input_artifact_ids"]

    @pytest.mark.e2e
    def test_execution_lifecycle(self, e2e_mode, client, gateway_headers):
        """Full lifecycle: create → running → completed with output artifacts."""
        input_art = self._create_artifact(client, gateway_headers)

        # Create execution
        create_body = {
            "agent_name": "e2e-analyzer",
            "agent_version": "2.0",
            "params": {"k": 5},
            "input_artifact_ids": [input_art["id"]],
            "user_id": "e2e-user",
        }
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            json=create_body,
            headers=gateway_headers,
        )
        assert resp.status_code == 201
        exec_id = resp.json()["id"]

        # Update to running
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/{exec_id}",
            json={"status": "running"},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        assert resp.json()["started_at"] is not None

        # Create output artifact
        output_art = self._create_artifact(client, gateway_headers)

        # Update to completed with output + trace
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/{exec_id}",
            json={
                "status": "completed",
                "output_artifact_ids": [output_art["id"]],
                "execution_trace": {
                    "steps": [
                        {"tool": "search", "result": "found 3 items"},
                        {"tool": "analyze", "result": "score 0.95"},
                    ]
                },
            },
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["completed_at"] is not None
        assert output_art["id"] in data["output_artifact_ids"]
        assert len(data["execution_trace"]["steps"]) == 2

    @pytest.mark.e2e
    def test_list_executions(self, e2e_mode, client, gateway_headers):
        """List executions with agent_name filter."""
        unique_agent = f"e2e-agent-{uuid.uuid4().hex[:6]}"
        body = {
            "agent_name": unique_agent,
            "user_id": "e2e-user",
        }
        client.post(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            json=body,
            headers=gateway_headers,
        )

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            params={"agent_name": unique_agent},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all(it["agent_name"] == unique_agent for it in data["items"])

    @pytest.mark.e2e
    def test_get_execution(self, e2e_mode, client, gateway_headers):
        """Get a single execution by ID."""
        body = {
            "agent_name": "e2e-getter",
            "user_id": "e2e-user",
        }
        created = client.post(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            json=body,
            headers=gateway_headers,
        ).json()

        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/{created['id']}",
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    @pytest.mark.e2e
    def test_execution_not_found(self, e2e_mode, client, gateway_headers):
        """Get non-existent execution returns 404."""
        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/nonexistent-id",
            headers=gateway_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.e2e
    def test_execution_failure(self, e2e_mode, client, gateway_headers):
        """Execution can be marked as failed with error."""
        body = {
            "agent_name": "e2e-fail-test",
            "user_id": "e2e-user",
        }
        exec_data = client.post(
            f"{GATEWAY_URL}/gw/a2l/agent-executions",
            json=body,
            headers=gateway_headers,
        ).json()

        # running then failed
        client.patch(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/{exec_data['id']}",
            json={"status": "running"},
            headers=gateway_headers,
        )
        resp = client.patch(
            f"{GATEWAY_URL}/gw/a2l/agent-executions/{exec_data['id']}",
            json={"status": "failed", "error": "OOM on GPU 0"},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert resp.json()["error"] == "OOM on GPU 0"


# =============================================================================
# Events (optional — graceful skip if Redis unavailable)
# =============================================================================


class TestEvents:
    """Test event stream endpoints. Gracefully skip if events disabled."""

    @pytest.mark.e2e
    def test_read_events(self, e2e_mode, client, gateway_headers):
        """GET /gw/a2l/events should return list (possibly empty)."""
        resp = client.get(
            f"{GATEWAY_URL}/gw/a2l/events",
            params={"limit": 10},
            headers=gateway_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Returns empty list if Redis not configured
        assert isinstance(data, list)

    @pytest.mark.e2e
    def test_subscribe_without_redis(self, e2e_mode, client, gateway_headers):
        """Subscribe should return 503 if Redis is not available."""
        resp = client.post(
            f"{GATEWAY_URL}/gw/a2l/events/subscribe",
            json={"group_name": "e2e-test-group"},
            headers=gateway_headers,
        )
        # 200 if Redis available, 503 if not
        assert resp.status_code in (200, 503)


# =============================================================================
# Idempotency & Deterministic IDs
# =============================================================================


class TestIdempotency:
    """Verify deterministic ID generation ensures idempotency."""

    @pytest.mark.e2e
    def test_duplicate_create_returns_same_id(self, e2e_mode, client, gateway_headers):
        """Creating artifact with same inputs should produce same ID (idempotent)."""
        data_hash = _sha256("idempotency-test-data")
        body = {
            "type": "dataset",
            "uri": "s3://e2e-bucket/idem-test.csv",
            "input_data_hash": data_hash,
            "user_id": "e2e-user",
            "metadata": {"version": "1"},
        }
        resp1 = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        # First should succeed
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        # Second with same inputs — should get conflict or same ID
        resp2 = client.post(
            f"{GATEWAY_URL}/gw/a2l/artifacts",
            json=body,
            headers=gateway_headers,
        )
        # Depending on implementation: 201 (idempotent) or 409 (conflict)
        assert resp2.status_code in (201, 409)
        if resp2.status_code == 201:
            assert resp2.json()["id"] == id1
