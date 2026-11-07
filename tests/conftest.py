"""Shared pytest fixtures and configuration for R4 Platform tests.

This file provides common fixtures used across all test modules:
- Environment setup
- Mock services
- Test data generation
- Docker/K8s helpers
"""

import os
import socket
import subprocess
import time
from collections.abc import Generator
from unittest.mock import Mock, patch

import pytest

# =============================================================================
# Collection Configuration
# =============================================================================

# Exclude standalone smoke test script from pytest collection
collect_ignore = [
    "test_e2e_smoke.py",  # Standalone script, not pytest-compatible
]


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, no external deps)")
    config.addinivalue_line("markers", "integration: Integration tests (needs Docker)")
    config.addinivalue_line("markers", "e2e: End-to-end tests (full stack)")
    config.addinivalue_line("markers", "k8s: Kubernetes tests")
    config.addinivalue_line("markers", "hpc: HPC/Slurm tests")
    config.addinivalue_line("markers", "slow: Slow tests (performance)")
    config.addinivalue_line("markers", "gpu: Tests requiring GPU")


# Tier markers (D-5 / ADR-054): these keywords make a test opt-in only.
TIER_MARKERS = ("e2e", "k8s", "hpc", "integration")


def _tier_opted_in(item: pytest.Item) -> bool:
    """True when the run or the file explicitly opted into heavyweight tiers."""
    if os.environ.get("R4_RUN_E2E", "") in ("1", "true", "yes"):
        return True
    # Files carrying their own R4_E2E_* module guard opt in via that variable;
    # the module-level guard still enforces prerequisites (services, keys).
    return any(
        marker.name in TIER_MARKERS and os.environ.get(f"R4_E2E_{marker.name.upper()}")
        for marker in item.iter_markers()
    )


def _skip_tier_items(items) -> None:
    """D-5 tier rule: marker-carrying items skip unless the run opted in."""
    skip_tier = pytest.mark.skip(
        reason="heavyweight tier (e2e/k8s/hpc/integration): set R4_RUN_E2E=1 "
        "(or the file's own R4_E2E_<TIER> var) to include"
    )
    for item in items:
        if any(m in item.keywords for m in TIER_MARKERS) and not _tier_opted_in(item):
            item.add_marker(skip_tier)


def _apply_capability_skip(items, marker: str, available: bool, reason: str) -> None:
    """Tag items of one marker with a skip when the capability is missing."""
    if available:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if marker in item.keywords:
            item.add_marker(skip)


def _skip_capability_items(items) -> None:
    """Environment-capability skips: kubectl / slurm / cuda detection."""
    _apply_capability_skip(items, "k8s", is_kubectl_available(), "kubectl not available")
    _apply_capability_skip(items, "hpc", is_slurm_available(), "Slurm not available")
    _apply_capability_skip(items, "gpu", is_cuda_available(), "CUDA not available")


def pytest_collection_modifyitems(config, items):
    """Tier opt-in rule first (D-5), then environment-capability skips."""
    _skip_tier_items(items)
    _skip_capability_items(items)


# =============================================================================
# Environment Detection
# =============================================================================


def is_kubectl_available() -> bool:
    """Check if kubectl is available."""
    try:
        result = subprocess.run(["kubectl", "version", "--client"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def is_slurm_available() -> bool:
    """Check if Slurm is available."""
    try:
        result = subprocess.run(["sinfo", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def is_cuda_available() -> bool:
    """Check if CUDA is available."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def is_gateway_available() -> bool:
    """Check if Gateway is reachable (kept for shared gate users)."""
    gateway_url = os.environ.get("GATEWAY_URL", "http://localhost:8080")
    try:
        from urllib.parse import urlparse

        parsed = urlparse(gateway_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8080

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def is_docker_available() -> bool:
    """Check if Docker is available."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


# =============================================================================
# Environment Fixtures
# =============================================================================


@pytest.fixture
def test_env() -> Generator[dict[str, str], None, None]:
    """Set up test environment variables."""
    env_vars = {
        "UMRS_HOST": os.environ.get("UMRS_HOST", "localhost"),
        "UMRS_PORT": os.environ.get("UMRS_PORT", "5555"),
        "S3_ENDPOINT": os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        "S3_ACCESS_KEY": os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        "S3_SECRET_KEY": os.environ.get("S3_SECRET_KEY", "minioadmin"),
        "PGHOST": os.environ.get("PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGPORT", "5432"),
        "PGUSER": os.environ.get("PGUSER", "postgres"),
        "PGPASSWORD": os.environ.get("PGPASSWORD", "postgres"),
        "PGDATABASE": os.environ.get("PGDATABASE", "umrs"),
        "LITELLM_ENDPOINT": os.environ.get("LITELLM_ENDPOINT", "http://localhost:11434"),
        "LITELLM_API_KEY": os.environ.get("LITELLM_API_KEY", "sk-test"),
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def k8s_env() -> Generator[dict[str, str], None, None]:
    """Set up K8s test environment variables."""
    env_vars = {
        "K8S_NAMESPACE": os.environ.get("K8S_NAMESPACE", "umrs-system"),
        "REGISTRY_HOST": os.environ.get("REGISTRY_HOST", "localhost:5001"),
        "UMRS_HOST": "umrs-server",
        "UMRS_PORT": "5555",
        "UMRS_SINGLE_DATA_PORT": "true",
        "UMRS_DATA_PORT": "5556",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    with patch("boto3.client") as mock:
        client = Mock()
        # Set up common S3 operations
        client.list_objects_v2.return_value = {"Contents": []}
        client.put_object.return_value = {"ETag": '"abc123"'}
        client.get_object.return_value = {"Body": Mock(read=Mock(return_value=b"test content"))}
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pg_connection():
    """Mock PostgreSQL connection."""
    with patch("psycopg2.connect") as mock:
        conn = Mock()
        cursor = Mock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        conn.cursor.return_value = cursor
        mock.return_value = conn
        yield conn, cursor


@pytest.fixture
def mock_zmq_context():
    """Mock ZMQ context for MessageQueue testing."""
    with patch("zmq.Context") as mock:
        context = Mock()
        socket = Mock()
        socket.recv_json.return_value = {"status": "success"}
        context.socket.return_value = socket
        mock.return_value = context
        yield context, socket


@pytest.fixture
def mock_litellm_response():
    """Mock LiteLLM chat completion response."""
    return {
        "id": "mock-completion-001",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "rivault-gpt-oss-static",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "This is a mock response."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def sample_text_file(tmp_path):
    """Create a sample text file for testing."""
    file_path = tmp_path / "sample.txt"
    file_path.write_text(
        "This is sample text content for testing.\n"
        "It contains multiple lines.\n"
        "Used for embedding and RAG tests."
    )
    return file_path


@pytest.fixture
def sample_image_file(tmp_path):
    """Create a sample image file for testing."""
    try:
        from PIL import Image

        file_path = tmp_path / "sample.jpg"
        img = Image.new("RGB", (100, 100), color="red")
        img.save(file_path)
        return file_path
    except ImportError:
        pytest.skip("Pillow not installed")


@pytest.fixture
def sample_json_file(tmp_path):
    """Create a sample JSON file for testing."""
    import json

    file_path = tmp_path / "sample.json"
    data = {"name": "test", "values": [1, 2, 3], "nested": {"key": "value"}}
    file_path.write_text(json.dumps(data))
    return file_path


@pytest.fixture
def sample_embedding():
    """Generate a sample embedding vector."""
    import numpy as np

    return np.random.rand(768).tolist()


@pytest.fixture
def sample_messages():
    """Sample chat messages for LLM testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]


# =============================================================================
# Docker/K8s Helpers
# =============================================================================


@pytest.fixture(scope="session")
def docker_compose_up():
    """Start Docker Compose services for integration tests.

    Scope: session (runs once per test session)
    """
    if not is_docker_available():
        pytest.skip("Docker not available")

    compose_file = os.path.join(os.path.dirname(__file__), "..", "docker-compose.e2e.yaml")

    if not os.path.exists(compose_file):
        pytest.skip(f"Compose file not found: {compose_file}")

    # Start services
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d"], check=True, capture_output=True
    )

    # Wait for services to be ready
    time.sleep(10)

    yield

    # Cleanup
    # docker-compose (PyPI tool) died on modern CPython (PyYAML-5.4 build);
    # every supported host carries the CLI as the `compose` plugin (docker v5+).
    subprocess.run(["docker", "compose", "-f", compose_file, "down", "-v"], capture_output=True)


@pytest.fixture
def k8s_client():
    """Kubernetes client for K8s tests."""
    if not is_kubectl_available():
        pytest.skip("kubectl not available")

    try:
        from kubernetes import client, config

        config.load_kube_config()
        return client.CoreV1Api()
    except Exception as e:
        pytest.skip(f"Failed to create K8s client: {e}")


# =============================================================================
# HTTP Client Fixtures
# =============================================================================


@pytest.fixture
def http_client():
    """HTTP client for API testing."""
    import httpx

    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture
async def async_http_client():
    """Async HTTP client for API testing."""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# =============================================================================
# Utility Functions
# =============================================================================


def wait_for_service(url: str, timeout: int = 60, interval: int = 2) -> bool:
    """Wait for a service to become available.

    Args:
        url: URL to check (GET request)
        timeout: Maximum wait time in seconds
        interval: Check interval in seconds

    Returns:
        True if service is available, False otherwise
    """
    import httpx

    start = time.time()
    while time.time() - start < timeout:
        try:
            response = httpx.get(url, timeout=5)
            if response.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def run_command(cmd: list, timeout: int = 30) -> tuple:
    """Run a shell command and return (returncode, stdout, stderr).

    Args:
        cmd: Command as list of strings
        timeout: Command timeout in seconds

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr
