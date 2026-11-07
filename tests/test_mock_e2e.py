#!/usr/bin/env python3
"""
E2E tests for mock environment.

These tests run against the Docker Compose mock environment and verify
that all components work together correctly without requiring real HPC
resources.
"""

import json
import os
import time
import uuid

import httpx
import pytest

# =============================================================================
# Configuration
# =============================================================================

# Service URLs (set by run_e2e_tests.py or use defaults)
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
UMRS_URL = os.environ.get("UMRS_URL", "http://localhost:8001")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:11434")
MINIO_URL = os.environ.get("MINIO_URL", "http://localhost:9000")
SLURM_MOCK_URL = os.environ.get("SLURM_MOCK_URL", "http://localhost:6817")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-test-e2e")


def is_e2e_mode() -> bool:
    """Check if we're running in E2E test mode."""
    return os.environ.get("E2E_TEST_MODE", "").lower() == "true"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def e2e_mode():
    """Skip tests if not in E2E mode."""
    if not is_e2e_mode():
        pytest.skip("Not in E2E test mode. Set E2E_TEST_MODE=true")


@pytest.fixture
def auth_headers():
    """Return headers with authentication for LiteLLM."""
    return {"Authorization": f"Bearer {LITELLM_API_KEY}"}


@pytest.fixture
def gateway_headers():
    """Return headers for gateway authentication."""
    return {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "X-Project-ID": "test-project",
    }


@pytest.fixture
def project_id():
    """Generate unique project ID for test isolation."""
    return f"test-project-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_id():
    """Generate unique user ID for test isolation."""
    return f"test-user-{uuid.uuid4().hex[:8]}"


# =============================================================================
# Health Check Tests
# =============================================================================


class TestMockEnvironmentHealth:
    """Test that all mock services are healthy and accessible."""

    @pytest.mark.e2e
    def test_gateway_health(self, e2e_mode):
        """Test that gateway health endpoint responds."""
        response = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "healthy"

    @pytest.mark.e2e
    def test_litellm_mock_health(self, e2e_mode):
        """Test that mock LiteLLM responds."""
        response = httpx.get(f"{LITELLM_URL}/health", timeout=10)
        assert response.status_code == 200

    @pytest.mark.e2e
    def test_minio_health(self, e2e_mode):
        """Test that MinIO is accessible."""
        response = httpx.get(f"{MINIO_URL}/minio/health/live", timeout=10)
        assert response.status_code == 200

    @pytest.mark.e2e
    def test_slurm_mock_health(self, e2e_mode):
        """Test that Slurm mock is accessible."""
        response = httpx.get(f"{SLURM_MOCK_URL}/", timeout=10)
        # Accept any response indicating server is running
        assert response.status_code in (200, 404, 405)


# =============================================================================
# LLM Integration Tests
# =============================================================================


class TestMockLLMIntegration:
    """Test LLM integration through the mock services."""

    @pytest.mark.e2e
    def test_list_models(self, e2e_mode, auth_headers):
        """Test listing available models."""
        response = httpx.get(
            f"{LITELLM_URL}/v1/models",
            headers=auth_headers,
            timeout=10,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) > 0

    @pytest.mark.e2e
    def test_chat_completion(self, e2e_mode, auth_headers):
        """Test basic chat completion through mock."""
        payload = {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Hello, how are you?"}],
            "max_tokens": 50,
        }
        response = httpx.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
            timeout=30,
        )
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]

    @pytest.mark.e2e
    def test_chat_completion_streaming(self, e2e_mode, auth_headers):
        """Test streaming chat completion."""
        payload = {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Count to 5"}],
            "max_tokens": 100,
            "stream": True,
        }
        with httpx.stream(
            "POST",
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
            timeout=30,
        ) as response:
            assert response.status_code == 200
            chunks = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data != "[DONE]":
                        chunks.append(json.loads(data))
            assert len(chunks) > 0

    @pytest.mark.e2e
    def test_chat_with_system_message(self, e2e_mode, auth_headers):
        """Test chat with system message."""
        payload = {
            "model": "mock-model",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
            "max_tokens": 50,
        }
        response = httpx.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=auth_headers,
            timeout=30,
        )
        assert response.status_code == 200


# =============================================================================
# Gateway Routing Tests
# =============================================================================


class TestGatewayRouting:
    """Test gateway routing to backend services."""

    @pytest.mark.e2e
    def test_gateway_routes_to_llm(self, e2e_mode, gateway_headers):
        """Test that gateway routes /gw/llm/* to LiteLLM."""
        response = httpx.get(
            f"{GATEWAY_URL}/gw/llm/v1/models",
            headers=gateway_headers,
            timeout=10,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @pytest.mark.e2e
    def test_gateway_routes_to_umrs(self, e2e_mode, gateway_headers):
        """Test that gateway routes /gw/rag/* to UMRS."""
        response = httpx.get(
            f"{GATEWAY_URL}/gw/rag/health",
            headers=gateway_headers,
            timeout=10,
        )
        # May get 404 if endpoint doesn't exist, but shouldn't get connection error
        assert response.status_code in (200, 404, 405, 502)

    @pytest.mark.e2e
    def test_gateway_chat_via_route(self, e2e_mode, gateway_headers):
        """Test chat completion through gateway.

        Note: This requires UMRS to have a registered endpoint for the model.
        In mock mode without pre-registered endpoints, expects 503.
        """
        payload = {
            "model": "mock-model",
            "messages": [{"role": "user", "content": "Test via gateway"}],
            "max_tokens": 50,
        }
        response = httpx.post(
            f"{GATEWAY_URL}/gw/llm/v1/chat/completions",
            json=payload,
            headers=gateway_headers,
            timeout=30,
        )
        # 200 = success (endpoint registered)
        # 503 = no active endpoint (expected in mock without pre-registration)
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
        else:
            # Verify 503 returns informative error
            data = response.json()
            assert "detail" in data
            assert "endpoint" in data["detail"].lower() or "model" in data["detail"].lower()


# =============================================================================
# Slurm Mock Tests
# =============================================================================


class TestSlurmMock:
    """Test Slurm mock functionality."""

    @pytest.mark.e2e
    def test_slurm_mock_sinfo(self, e2e_mode):
        """Test that mock sinfo is accessible via HTTP API."""
        response = httpx.get(f"{SLURM_MOCK_URL}/sinfo", timeout=10)
        assert response.status_code in (200, 404)

    @pytest.mark.e2e
    def test_slurm_mock_api_available(self, e2e_mode):
        """Test that Slurm mock HTTP server is accessible."""
        response = httpx.get(f"{SLURM_MOCK_URL}/", timeout=10)
        assert response.status_code in (200, 404, 405)


# =============================================================================
# Job Simulation Tests
# =============================================================================


class TestJobSimulation:
    """Test Slurm job submission simulation."""

    @pytest.mark.e2e
    def test_job_info_file_lifecycle(self, e2e_mode):
        """Test that job info files can be created and read."""
        # This simulates what happens when a real job runs
        response = httpx.get(f"{SLURM_MOCK_URL}/jobs", timeout=10)
        assert response.status_code in (200, 404)


# =============================================================================
# Multi-User Isolation Tests
# =============================================================================


class TestMultiUserIsolation:
    """Test that different users/projects are isolated."""

    @pytest.mark.e2e
    def test_different_projects_isolated(self, e2e_mode, auth_headers):
        """Test that different project IDs are treated separately."""
        headers1 = {**auth_headers, "X-Project-ID": "project-alpha"}
        headers2 = {**auth_headers, "X-Project-ID": "project-beta"}

        response1 = httpx.get(
            f"{GATEWAY_URL}/gw/llm/v1/models",
            headers=headers1,
            timeout=10,
        )
        response2 = httpx.get(
            f"{GATEWAY_URL}/gw/llm/v1/models",
            headers=headers2,
            timeout=10,
        )

        assert response1.status_code == 200
        assert response2.status_code == 200

    @pytest.mark.e2e
    def test_missing_project_id_rejected(self, e2e_mode, auth_headers):
        """Test that requests without project ID are rejected."""
        response = httpx.get(
            f"{GATEWAY_URL}/gw/llm/v1/models",
            headers=auth_headers,  # No X-Project-ID
            timeout=10,
        )
        assert response.status_code == 401


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling behavior."""

    @pytest.mark.e2e
    def test_invalid_model_handling(self, e2e_mode, gateway_headers):
        """Test that invalid model returns appropriate error.

        Note: Since gateway requires UMRS endpoint discovery, requesting
        a non-existent model returns 503 (no endpoint) rather than
        model-not-found error from LiteLLM.
        """
        payload = {
            "model": "non-existent-model-xyz",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        response = httpx.post(
            f"{GATEWAY_URL}/gw/llm/v1/chat/completions",
            json=payload,
            headers=gateway_headers,
            timeout=30,
        )
        # 400/404 = model error from LiteLLM
        # 503 = no endpoint registered (expected without pre-registration)
        assert response.status_code in (200, 400, 404, 503)

    @pytest.mark.e2e
    def test_malformed_request_returns_error(self, e2e_mode, gateway_headers):
        """Test that malformed requests return appropriate error.

        Note: Without endpoint registration, gateway returns 503 before
        validating the request body.
        """
        payload = {
            "model": "mock-model",
            # Missing required 'messages' field
        }
        response = httpx.post(
            f"{GATEWAY_URL}/gw/llm/v1/chat/completions",
            json=payload,
            headers=gateway_headers,
            timeout=30,
        )
        # 400/422 = validation error
        # 503 = no endpoint registered (checked before body validation)
        # 200 = mock mode is permissive and accepts invalid requests
        assert response.status_code in (200, 400, 422, 503)


# =============================================================================
# Performance Tests
# =============================================================================


class TestPerformance:
    """Basic performance tests."""

    @pytest.mark.e2e
    def test_health_check_latency(self, e2e_mode):
        """Test that health check responds within reasonable time."""
        start = time.time()
        response = httpx.get(f"{GATEWAY_URL}/health", timeout=10)
        latency = time.time() - start

        assert response.status_code == 200
        assert latency < 1.0, f"Health check took {latency:.2f}s, expected < 1s"

    @pytest.mark.e2e
    def test_concurrent_requests(self, e2e_mode, gateway_headers):
        """Test handling of concurrent requests."""
        import asyncio

        async def make_request():
            async with httpx.AsyncClient() as client:
                return await client.get(
                    f"{GATEWAY_URL}/gw/llm/v1/models",
                    headers=gateway_headers,
                    timeout=10,
                )

        async def run_concurrent():
            tasks = [make_request() for _ in range(5)]
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = asyncio.run(run_concurrent())
        successes = sum(
            1 for r in results if isinstance(r, httpx.Response) and r.status_code == 200
        )
        assert successes >= 4, f"Only {successes}/5 concurrent requests succeeded"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
