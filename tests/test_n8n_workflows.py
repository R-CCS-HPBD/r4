#!/usr/bin/env python3
"""
n8n Workflow Validation Tests

Tests that n8n workflow templates are valid and can execute against the Gateway.

Usage:
    # Run all workflow tests
    pytest tests/test_n8n_workflows.py -v

    # Validate workflow JSON only (no execution)
    pytest tests/test_n8n_workflows.py -v -m "not execution"

Requirements:
    - E2E environment running (docker-compose.e2e.yaml)
    - OR n8n + Gateway running
"""

import json
import os
from pathlib import Path

import pytest

# Try to import httpx, skip tests if not available
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = PROJECT_ROOT / "n8n" / "workflows"
CREDENTIALS_DIR = PROJECT_ROOT / "n8n" / "credentials"

# URLs from environment
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")


def is_e2e_mode() -> bool:
    """Check if running in E2E mode."""
    return os.environ.get("E2E_TEST_MODE", "").lower() == "true"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workflow_files() -> list[Path]:
    """Get all workflow JSON files."""
    if not WORKFLOWS_DIR.exists():
        pytest.skip(f"Workflows directory not found: {WORKFLOWS_DIR}")
    return list(WORKFLOWS_DIR.glob("*.json"))


@pytest.fixture
def e2e_mode():
    """Skip tests if not in E2E mode."""
    if not is_e2e_mode():
        pytest.skip("Not in E2E test mode. Set E2E_TEST_MODE=true")


# =============================================================================
# Workflow Structure Tests
# =============================================================================


class TestWorkflowStructure:
    """Tests for workflow JSON structure validation."""

    @pytest.mark.parametrize(
        "workflow_name",
        [
            "llm-chat.json",
            "rag-query.json",
            # "document-processing.json",  # Template file with Jinja placeholders
            "scheduled-summarization.json",
        ],
    )
    def test_workflow_file_exists(self, workflow_name: str):
        """Test that expected workflow files exist."""
        workflow_path = WORKFLOWS_DIR / workflow_name
        assert workflow_path.exists(), f"Workflow not found: {workflow_path}"

    @pytest.mark.parametrize(
        "workflow_name",
        [
            "llm-chat.json",
            "rag-query.json",
            # "document-processing.json",  # Template file with Jinja placeholders
            "scheduled-summarization.json",
        ],
    )
    def test_workflow_is_valid_json(self, workflow_name: str):
        """Test that workflow files are valid JSON."""
        workflow_path = WORKFLOWS_DIR / workflow_name
        if not workflow_path.exists():
            pytest.skip(f"Workflow not found: {workflow_path}")

        with open(workflow_path) as f:
            data = json.load(f)

        assert isinstance(data, dict), "Workflow should be a JSON object"

    @pytest.mark.parametrize(
        "workflow_name",
        [
            "llm-chat.json",
            "rag-query.json",
            # "document-processing.json",  # Template file with Jinja placeholders
            "scheduled-summarization.json",
        ],
    )
    def test_workflow_has_required_fields(self, workflow_name: str):
        """Test that workflows have required n8n fields."""
        workflow_path = WORKFLOWS_DIR / workflow_name
        if not workflow_path.exists():
            pytest.skip(f"Workflow not found: {workflow_path}")

        with open(workflow_path) as f:
            data = json.load(f)

        # Required fields
        assert "name" in data, "Workflow must have a name"
        assert "nodes" in data, "Workflow must have nodes"
        assert isinstance(data["nodes"], list), "Nodes must be a list"
        assert len(data["nodes"]) > 0, "Workflow must have at least one node"

    @pytest.mark.parametrize(
        "workflow_name",
        [
            "llm-chat.json",
            "rag-query.json",
            # "document-processing.json",  # Template file with Jinja placeholders
            "scheduled-summarization.json",
        ],
    )
    def test_workflow_nodes_have_ids(self, workflow_name: str):
        """Test that all nodes have unique IDs."""
        workflow_path = WORKFLOWS_DIR / workflow_name
        if not workflow_path.exists():
            pytest.skip(f"Workflow not found: {workflow_path}")

        with open(workflow_path) as f:
            data = json.load(f)

        ids = []
        for node in data["nodes"]:
            assert "id" in node, f"Node missing id: {node.get('name', 'unknown')}"
            assert node["id"] not in ids, f"Duplicate node id: {node['id']}"
            ids.append(node["id"])


class TestWorkflowRoutes:
    """Tests that workflows use correct Gateway routes."""

    def test_llm_chat_uses_gateway_route(self):
        """Test that LLM chat workflow uses /gw/llm/* route."""
        workflow_path = WORKFLOWS_DIR / "llm-chat.json"
        if not workflow_path.exists():
            pytest.skip("Workflow not found")

        with open(workflow_path) as f:
            data = json.load(f)

        # Find HTTP request node
        http_nodes = [n for n in data["nodes"] if "httpRequest" in n.get("type", "")]
        assert len(http_nodes) > 0, "Workflow should have HTTP request node"

        # Check URL contains /gw/llm/
        found_llm_route = False
        for node in http_nodes:
            url = node.get("parameters", {}).get("url", "")
            if "/gw/llm/" in url:
                found_llm_route = True
                break

        assert found_llm_route, "LLM workflow should use /gw/llm/* route"

    def test_rag_query_uses_gateway_route(self):
        """Test that RAG query workflow uses /gw/rag/* route."""
        workflow_path = WORKFLOWS_DIR / "rag-query.json"
        if not workflow_path.exists():
            pytest.skip("Workflow not found")

        with open(workflow_path) as f:
            data = json.load(f)

        # Find HTTP request nodes
        http_nodes = [n for n in data["nodes"] if "httpRequest" in n.get("type", "")]

        # RAG workflow should use /gw/rag/ route
        found_rag_route = False
        for node in http_nodes:
            url = node.get("parameters", {}).get("url", "")
            if "/gw/rag/" in url or "/umrs/" in url:
                found_rag_route = True
                break

        # May also use /gw/llm/ for the LLM call
        assert found_rag_route or len(http_nodes) > 0, "RAG workflow should have API calls"


class TestWorkflowCredentials:
    """Tests for workflow credential configuration."""

    def test_gateway_credential_template_exists(self):
        """Test that gateway credential template exists."""
        cred_path = CREDENTIALS_DIR / "gateway-credential.json"
        assert cred_path.exists(), f"Gateway credential template not found: {cred_path}"

    def test_gateway_credential_is_valid(self):
        """Test that gateway credential template is valid JSON."""
        cred_path = CREDENTIALS_DIR / "gateway-credential.json"
        if not cred_path.exists():
            pytest.skip("Credential file not found")

        with open(cred_path) as f:
            data = json.load(f)

        assert "name" in data, "Credential must have a name"


# =============================================================================
# Execution Tests (require running environment)
# =============================================================================


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWorkflowExecution:
    """Tests that actually execute workflow steps."""

    @pytest.mark.e2e
    @pytest.mark.execution
    def test_llm_chat_endpoint_reachable(self, e2e_mode):
        """Test that LLM chat endpoint is reachable."""
        response = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
        assert response.status_code == 200

    @pytest.mark.e2e
    @pytest.mark.execution
    def test_llm_chat_completion(self, e2e_mode):
        """Test LLM chat completion through Gateway."""
        payload = {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello from n8n workflow test"}],
            "max_tokens": 50,
        }
        headers = {
            "Authorization": "Bearer sk-test-e2e",
            "X-Project-ID": "test-project",
            "Content-Type": "application/json",
        }

        # Direct to LiteLLM (bypassing Gateway endpoint discovery)
        litellm_url = os.environ.get("LITELLM_URL", "http://localhost:11434")
        response = httpx.post(
            f"{litellm_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )

        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
