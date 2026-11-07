#!/usr/bin/env python3
"""
E2E Smoke Test for RiVault Platform.

Tests the complete flow:
  r4 (simulated) → Gateway → UMRS → LiteLLM → vLLM

This test can be run against:
- Local Docker Compose deployment
- Kubernetes deployment
- Slurm deployment (with pre-started job)

Prerequisites:
- Gateway running
- UMRS running with endpoint registry API
- At least one Mini-RiVault instance registered

Usage:
    python test_e2e_smoke.py [--gateway-url URL] [--project-id ID]
"""

# This file is a standalone script (has main() and argparse), not a pytest
# test module. Prevent pytest from collecting the test_* functions which
# require a 'client' fixture that doesn't exist in the pytest context.
import pytest as _pytest

pytestmark = _pytest.mark.skip(
    reason="Standalone script — run with: python tests/test_e2e_smoke.py"
)

import argparse
import os
import sys
import time
from dataclasses import dataclass

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


@dataclass
class TestConfig:
    """Test configuration."""

    gateway_url: str
    project_id: str
    model: str
    jwt_secret: str
    timeout: int
    verbose: bool


class TestResult:
    """Test result accumulator."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str, detail: str = ""):
        """Record passed test."""
        self.passed += 1
        print(f"✅ {name}" + (f": {detail}" if detail else ""))

    def fail(self, name: str, detail: str):
        """Record failed test."""
        self.failed += 1
        self.errors.append((name, detail))
        print(f"❌ {name}: {detail}")

    def summary(self) -> bool:
        """Print summary and return True if all passed."""
        print()
        print("=" * 60)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        if self.errors:
            print()
            print("Failures:")
            for name, detail in self.errors:
                print(f"  - {name}: {detail}")
        print("=" * 60)
        return self.failed == 0


def create_test_jwt(project_id: str, secret: str) -> str:
    """Create a simple JWT for testing."""
    try:
        from jose import jwt as jose_jwt

        payload = {
            "project_id": project_id,
            "sub": "test-user",
            "exp": int(time.time()) + 3600,
        }
        return jose_jwt.encode(payload, secret, algorithm="HS256")
    except ImportError:
        # Fallback: use X-Project-ID header instead
        return None


def test_gateway_health(client: httpx.Client, config: TestConfig) -> bool:
    """Test 1: Gateway health check."""
    try:
        response = client.get(f"{config.gateway_url}/health")
        if response.status_code == 200:
            data = response.json()
            return data.get("status") == "ok"
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_gateway_ready(client: httpx.Client, config: TestConfig) -> bool:
    """Test 2: Gateway readiness (UMRS connectivity)."""
    try:
        response = client.get(f"{config.gateway_url}/ready")
        if response.status_code == 200:
            data = response.json()
            # Degraded is acceptable for smoke test
            return data.get("status") in ("ok", "degraded")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_chat_completion(
    client: httpx.Client,
    config: TestConfig,
    headers: dict,
) -> bool:
    """Test 3: Chat completion request."""
    try:
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "Say 'Hello RiVault' in one word."}],
            "max_tokens": 10,
            "temperature": 0.1,
        }

        response = client.post(
            f"{config.gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=config.timeout,
        )

        if config.verbose:
            print(f"  Response status: {response.status_code}")
            print(f"  Response body: {response.text[:500]}")

        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                content = choices[0]["message"].get("content", "")
                return len(content) > 0
        elif response.status_code == 503:
            print("  No active endpoint available (expected if no Mini-RiVault running)")
            return False

        return False
    except httpx.TimeoutException:
        print("  Request timed out")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_chat_streaming(
    client: httpx.Client,
    config: TestConfig,
    headers: dict,
) -> bool:
    """Test 4: Streaming chat completion."""
    try:
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "Count from 1 to 3."}],
            "max_tokens": 20,
            "stream": True,
        }

        # Need async client for streaming
        with httpx.stream(
            "POST",
            f"{config.gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=config.timeout,
        ) as response:
            if response.status_code != 200:
                return False

            chunks = 0
            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunks += 1
                    if config.verbose and chunks <= 3:
                        print(f"  Chunk {chunks}: {line[:100]}")

            return chunks > 0

    except httpx.TimeoutException:
        print("  Streaming request timed out")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_models_endpoint(
    client: httpx.Client,
    config: TestConfig,
    headers: dict,
) -> bool:
    """Test 5: List models endpoint."""
    try:
        response = client.get(
            f"{config.gateway_url}/gw/llm/v1/models",
            headers=headers,
            timeout=config.timeout,
        )

        if config.verbose:
            print(f"  Response: {response.text[:200]}")

        if response.status_code == 200:
            data = response.json()
            return "data" in data
        elif response.status_code == 503:
            print("  No active endpoint (expected if no Mini-RiVault)")
            return False

        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def test_unauthorized_request(
    client: httpx.Client,
    config: TestConfig,
) -> bool:
    """Test 6: Request without auth should be rejected."""
    try:
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": "test"}],
        }

        response = client.post(
            f"{config.gateway_url}/gw/llm/v1/chat/completions",
            json=payload,
            # No auth headers
        )

        # Should be 401 Unauthorized
        return response.status_code == 401

    except Exception as e:
        print(f"  Error: {e}")
        return False


def run_tests(config: TestConfig) -> bool:
    """Run all E2E tests."""
    print()
    print("=" * 60)
    print("RiVault Platform E2E Smoke Test")
    print("=" * 60)
    print(f"Gateway URL: {config.gateway_url}")
    print(f"Project ID:  {config.project_id}")
    print(f"Model:       {config.model}")
    print("=" * 60)
    print()

    results = TestResult()

    # Create auth headers
    headers = {}
    jwt_token = create_test_jwt(config.project_id, config.jwt_secret)
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    else:
        # Fallback to X-Project-ID header
        headers["X-Project-ID"] = config.project_id
        print("Note: Using X-Project-ID header (jose not installed)")
        print()

    client = httpx.Client(timeout=30)

    try:
        # Test 1: Gateway health
        print("Test 1: Gateway Health Check")
        if test_gateway_health(client, config):
            results.ok("Gateway health")
        else:
            results.fail("Gateway health", "Health endpoint not responding")

        # Test 2: Gateway readiness
        print("Test 2: Gateway Readiness Check")
        if test_gateway_ready(client, config):
            results.ok("Gateway readiness")
        else:
            results.fail("Gateway readiness", "UMRS not reachable")

        # Test 3: Unauthorized request rejected
        print("Test 3: Auth Enforcement")
        if test_unauthorized_request(client, config):
            results.ok("Auth enforcement", "Unauthorized request rejected")
        else:
            results.fail("Auth enforcement", "Unauthorized request was not rejected")

        # Test 4: Chat completion
        print("Test 4: Chat Completion")
        if test_chat_completion(client, config, headers):
            results.ok("Chat completion")
        else:
            results.fail("Chat completion", "Failed to get chat response")

        # Test 5: Streaming
        print("Test 5: Streaming Chat Completion")
        if test_chat_streaming(client, config, headers):
            results.ok("Streaming chat")
        else:
            results.fail("Streaming chat", "No streaming chunks received")

        # Test 6: Models endpoint
        print("Test 6: List Models")
        if test_models_endpoint(client, config, headers):
            results.ok("List models")
        else:
            results.fail("List models", "Failed to list models")

    finally:
        client.close()

    return results.summary()


def main():
    parser = argparse.ArgumentParser(
        description="RiVault Platform E2E Smoke Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--gateway-url",
        default=os.getenv("GATEWAY_URL", "http://localhost:8080"),
        help="Gateway URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("PROJECT_ID", "test-project"),
        help="Project ID for requests (default: test-project)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("TEST_MODEL", "rivault-gpt-oss-static"),
        help="Model name to test (default: rivault-gpt-oss-static)",
    )
    parser.add_argument(
        "--jwt-secret",
        default=os.getenv("JWT_SECRET", "dev-secret-change-in-production"),
        help="JWT signing secret",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Request timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    config = TestConfig(
        gateway_url=args.gateway_url.rstrip("/"),
        project_id=args.project_id,
        model=args.model,
        jwt_secret=args.jwt_secret,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    success = run_tests(config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
