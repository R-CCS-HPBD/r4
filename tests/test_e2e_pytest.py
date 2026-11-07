"""
Pytest-compatible E2E tests for the RiVault Platform.

Run with: pytest tests/test_e2e_pytest.py -v
"""

import os

import httpx
import pytest

# Tier marker (D-5/ADR-054): opt-in only via R4_RUN_E2E=1.
pytestmark = pytest.mark.e2e

# Configuration from environment
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")
PROJECT_ID = os.getenv("PROJECT_ID", "test-project")
TEST_MODEL = os.getenv("TEST_MODEL", "rivault-gpt-oss-static")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")


@pytest.fixture
def gateway_url():
    """Gateway base URL."""
    return GATEWAY_URL.rstrip("/")


@pytest.fixture
def auth_headers():
    """Auth headers for requests."""
    # Use X-Project-ID for simplicity in tests
    return {"X-Project-ID": PROJECT_ID}


@pytest.fixture
def client():
    """HTTP client fixture."""
    with httpx.Client(timeout=30) as client:
        yield client


# =============================================================================
# Health & Readiness Tests
# =============================================================================


def test_gateway_health(client, gateway_url):
    _ = client  # fixture named for pytest
    """Gateway /health endpoint should return ok."""
    response = client.get(f"{gateway_url}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_gateway_readiness(client, gateway_url):
    """Gateway /ready endpoint should return ok or degraded."""
    response = client.get(f"{gateway_url}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")


# =============================================================================
# Authentication Tests
# =============================================================================


def test_unauthorized_request_rejected(client, gateway_url):
    """Requests without auth should be rejected with 401."""
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "test"}],
    }
    response = client.post(
        f"{gateway_url}/gw/llm/v1/chat/completions",
        json=payload,
        # No auth headers
    )
    assert response.status_code == 401


def test_invalid_token_rejected(client, gateway_url):
    """Requests with invalid JWT should be rejected."""
    payload = {
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "test"}],
    }
    response = client.post(
        f"{gateway_url}/gw/llm/v1/chat/completions",
        json=payload,
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert response.status_code == 401


# =============================================================================
# LLM Endpoint Tests (require running Mini-RiVault)
# =============================================================================


@pytest.mark.skipif(
    os.getenv("SKIP_LLM_TESTS", "true").lower() == "true",
    reason="LLM tests require running Mini-RiVault (set SKIP_LLM_TESTS=false)",
)
class TestLLMEndpoints:
    """Tests that require a running LLM backend."""

    def test_chat_completion(self, client, gateway_url, auth_headers):
        """Chat completion should return valid response."""
        payload = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 10,
            "temperature": 0.1,
        }
        response = client.post(
            f"{gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            headers=auth_headers,
            timeout=120,
        )
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "content" in data["choices"][0]["message"]

    def test_chat_completion_streaming(self, client, gateway_url, auth_headers):
        """Streaming chat completion should return SSE chunks."""
        _ = client  # fixture named for pytest
        payload = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Count to 3."}],
            "max_tokens": 20,
            "stream": True,
        }

        with httpx.stream(
            "POST",
            f"{gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            headers=auth_headers,
            timeout=120,
        ) as response:
            assert response.status_code == 200

            chunks = 0
            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunks += 1

            assert chunks > 0, "No streaming chunks received"

    def test_list_models(self, client, gateway_url, auth_headers):
        """List models should return valid response."""
        response = client.get(
            f"{gateway_url}/gw/llm/v1/models",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_missing_model_field(self, client, gateway_url, auth_headers):
        """Request without model field should return 400."""
        payload = {
            "messages": [{"role": "user", "content": "test"}],
        }
        response = client.post(
            f"{gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 400


# =============================================================================
# Contract Tests
# =============================================================================


def test_health_response_contract(client, gateway_url):
    """Health response should match expected contract."""
    response = client.get(f"{gateway_url}/health")
    data = response.json()

    # Required fields
    assert "status" in data
    assert "service" in data

    # Types
    assert isinstance(data["status"], str)
    assert isinstance(data["service"], str)


def test_ready_response_contract(client, gateway_url):
    """Readiness response should match expected contract."""
    response = client.get(f"{gateway_url}/ready")
    data = response.json()

    # Required fields
    assert "status" in data
    assert "checks" in data

    # Types
    assert isinstance(data["status"], str)
    assert isinstance(data["checks"], dict)


# =============================================================================
# Integration Notes
# =============================================================================

"""
To run LLM tests:

1. Start the platform (Docker Compose):
   cd internal_genAI && docker compose up -d

2. Wait for Mini-RiVault to be ready:
   curl http://localhost:11434/v1/models

3. Register endpoint in UMRS (or use auto-registration)

4. Run tests:
   SKIP_LLM_TESTS=false pytest tests/test_e2e_pytest.py -v

Environment variables:
- GATEWAY_URL: Gateway URL (default: http://localhost:8080)
- PROJECT_ID: Project ID for auth (default: test-project)
- TEST_MODEL: Model to test (default: rivault-gpt-oss-static)
- JWT_SECRET: JWT secret (default: dev-secret-change-in-production)
- SKIP_LLM_TESTS: Skip LLM tests (default: true)
"""
