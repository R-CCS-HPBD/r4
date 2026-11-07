# Testing Guide for R4

This document provides comprehensive testing guidelines for R4, covering component-level tests, integration tests, and end-to-end (E2E) tests. It is designed to enable test-driven development (TDD) for both human developers and AI agents/copilots.

> **Updated 2026-09-22 (design reset).** Procedures below are the current test
> entry points; the framing of components follows the pre-design narrative
> ("Platform Gateway" = the NGINX + auth-sidecar edge, UMRS's edge process by
> ADR-059; 2026-10-18: said "gap D-02", Implemented by design amendment). Test tiers
> by marker and the `R4_RUN_E2E=1` opt-in are ADR-054; the gate for every tree is
> `bash scripts/ci-lint-local.sh` (ADR-052) — [developer.md §3](docs/onboarding/developer.md#3-first-green-gate). Design: [docs/R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md);
> gaps: [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

---

## Table of Contents

1. [Testing Philosophy](#testing-philosophy)
2. [Test Directory Structure](#test-directory-structure)
3. [Component Testing](#component-testing)
   - [UMRS (Unified Multimodal Retrieval System)](#umrs-unified-multimodal-retrieval-system)
   - [Mini-RiVault (vLLM + LiteLLM)](#mini-rivault-vllm--litellm)
   - [Embedding Worker](#embedding-worker)
   - [Platform Gateway](#platform-gateway)
4. [Integration Testing](#integration-testing)
5. [End-to-End (E2E) Testing](#end-to-end-e2e-testing)
6. [Kubernetes Testing](#kubernetes-testing)
7. [Mock Testing](#mock-testing)
8. [Test-Driven Development Guidelines](#test-driven-development-guidelines)
9. [Running Tests](#running-tests)
10. [Writing New Tests](#writing-new-tests)
11. [CI/CD Integration](#cicd-integration)

---

## Testing Philosophy

### Principles

1. **HPC-First**: Tests must validate behavior in HPC environments (Slurm + Podman; Singularity is the legacy path, gap D-12)
2. **Isolation**: Each component should be testable independently
3. **Reproducibility**: Tests must produce consistent results
4. **Fast Feedback**: Unit tests should run in seconds, integration tests in minutes
5. **Documentation**: Tests serve as living documentation of expected behavior

### Test Pyramid

```
                    ┌───────────┐
                    │   E2E     │  ← Few, slow, high confidence
                    │   Tests   │
                   ┌┴───────────┴┐
                   │ Integration │  ← Some, medium speed
                   │    Tests    │
                  ┌┴─────────────┴┐
                  │  Unit Tests   │  ← Many, fast, focused
                  └───────────────┘
```

---

## Test Directory Structure

```
r4/
├── tests/                          # Central test directory
│   ├── docker/                     # Docker files for test infrastructure
│   │   ├── Dockerfile.test-runner  # Test runner container
│   │   ├── Dockerfile.slurm-mock   # Mock Slurm environment
│   │   ├── Dockerfile.compute-mock # Mock compute node
│   │   ├── mock_litellm.py         # LiteLLM mock server
│   │   └── mock_slurm/             # Slurm mock scripts
│   ├── setup_kind_cluster.sh       # Kind cluster setup
│   ├── run_k8s_tests.sh            # K8s test runner
│   ├── run_e2e_tests.py            # E2E test orchestrator
│   ├── run_hpc_tests.sh            # HPC environment tests
│   ├── test_e2e_pytest.py          # Pytest-based E2E tests
│   ├── test_e2e_smoke.py           # Quick smoke tests
│   ├── test_extensibility_security.py  # Plugin/MCP security tests
│   ├── test_k8s_e2e.py             # Kubernetes E2E tests
│   ├── test_mock_e2e.py            # Mock-based E2E tests
│   ├── test_mock_env.py            # Environment mocking tests
│   ├── test_model_control_e2e.py   # Model control flow tests
│   ├── test_n8n_workflows.py       # n8n workflow tests
│   ├── test_rag_e2e.py             # RAG functionality tests
│   ├── test_slurm_e2e.py           # Slurm integration tests
│   ├── test_tool_calling.py        # LLM tool calling tests
│   ├── E2E_TESTING.md              # E2E testing documentation
│   └── requirements.txt            # Test dependencies
├── r4/
│   └── tests/                      # r4 SDK unit tests
│       ├── test_client.py          # Client, ChatMessage, ChatResponse tests
│       ├── test_config.py          # Config creation and validation tests
│       ├── test_errors.py          # Custom exception tests
│       ├── test_jupyter.py         # Jupyter integration tests
│       └── test_rag.py             # RAGQuery, RAGResponse tests
├── gateway/
│   ├── nginx/                      # NGINX reverse proxy
│   │   └── conf.d/server.conf      # Route definitions
│   ├── sidecar/                    # Auth sidecar service
│   │   └── main.py                 # JWT validation, audit logging
│   └── tests/                      # Platform Gateway unit tests
│       ├── test_contracts.py       # Gateway-UMRS/LiteLLM contract tests
│       ├── test_files.py           # Presigned URL and MIME type tests
│       ├── test_ratelimit.py       # Rate limiting logic tests
│       └── test_security_contracts.py  # Security invariant tests
├── mcp-servers/
│   └── tests/                      # MCP server tests
│       └── test_base.py            # Server registry and base tests
├── umrs/
│   ├── tests/                      # UMRS-specific tests
│   │   ├── test_embeddings/        # Embedding tests
│   └── docker/
│       ├── test_umrs_api.sh        # UMRS API test script
│       └── test_rag_end_to_end.sh  # RAG E2E test script
```

## Current Test Entry Points

Use the repository Makefile as the source of truth for the default test tiers:

| Command | Purpose | Notes |
|---------|---------|-------|
| `make test-unit` | Fast local validation without external services | Includes some tests with `e2e` in the filename if they run entirely against mocks or local fixtures; excludes service-backed suites in the Makefile ignore list |
| `make e2e-test` | Docker Compose end-to-end validation | Runs `tests/run_e2e_tests.py` |
| `make e2e-quick` | Smaller smoke-oriented Compose suite | Uses the smoke subset of `tests/run_e2e_tests.py` |
| `make test-slurm-check` | Slurm prerequisite check | Validates HPC prerequisites without running the full Slurm suite |
| `make test-slurm` | Slurm/HPC integration validation | Runs `tests/test_slurm_e2e.py` |
| `make test-k8s-check` | Kubernetes prerequisite check | Validates Kind/Kubernetes prerequisites only |
| `make test-k8s` | Kubernetes integration validation | Runs `tests/test_k8s_e2e.py` |

When this document and the test list diverge, prefer the commands in `Makefile` and
the runners under `tests/`.

---

## Component Testing

### UMRS (Unified Multimodal Retrieval System)

UMRS is the core metadata and RAG service. It handles:
- S3 object management
- Embedding generation and storage
- PostgreSQL/pgvector operations
- ZMQ-based client-server communication

#### Unit Tests

**Location**: `umrs/src/umrs/` (alongside source files or in `umrs/tests/`)

**Key modules to test**:

| Module | Test Focus | Example Tests |
|--------|------------|---------------|
| `MessageQueue.py` | ZMQ communication | Connection handling, message serialization, UMRS_SINGLE_DATA_PORT mode |
| `embedding_worker.py` | Embedding generation | Model loading, batch processing, error handling |
| `server_umrs.py` | Server operations | Request routing, thread management, cleanup |
| `client_umrs.py` | Client operations | Command sending, response parsing, timeout handling |

**Example Unit Test** (`umrs/tests/test_message_queue.py`):

```python
"""Unit tests for MessageQueue module."""
import pytest
import os
from unittest.mock import Mock, patch

# Test UMRS_SINGLE_DATA_PORT mode for K8s
class TestMessageQueueK8sMode:
    """Tests for K8s-compatible single data port mode."""

    def test_single_data_port_enabled(self):
        """When UMRS_SINGLE_DATA_PORT=true, use fixed port with hostname."""
        with patch.dict(os.environ, {
            'UMRS_SINGLE_DATA_PORT': 'true',
            'UMRS_DATA_PORT': '5556'
        }):
            from umrs.MessageQueue import MessageQueue
            mq = MessageQueue()
            # Verify fixed port is used
            assert mq.data_port == 5556

    def test_tunnel_mode_uses_localhost(self):
        """When UMRS_TUNNEL_MODE=true, return localhost for data transfer."""
        with patch.dict(os.environ, {
            'UMRS_TUNNEL_MODE': 'true',
            'UMRS_DATA_PORT': '5556'
        }):
            from umrs.MessageQueue import MessageQueue
            mq = MessageQueue()
            # Tunnel mode should return localhost
            assert 'localhost' in mq.get_data_endpoint()

    def test_default_dynamic_port(self):
        """Default behavior uses dynamic port allocation."""
        with patch.dict(os.environ, {}, clear=True):
            from umrs.MessageQueue import MessageQueue
            mq = MessageQueue()
            # Dynamic port should be allocated
            assert mq.data_port != 5556
```

**Running UMRS unit tests**:
```bash
cd umrs
python -m pytest tests/ -v
```

#### Integration Tests

**Location**: `umrs/tests/` and `umrs/docker/test_umrs_api.sh`

**Test scenarios**:

1. **S3 + PostgreSQL Integration**
   ```bash
   # Run from umrs/docker
   ./test_umrs_api.sh
   ```

2. **Embedding Worker Integration**
   ```python
   # tests/test_embedding_integration.py
   def test_embedding_worker_processes_image():
       """Verify embedding worker can process an image file."""
       worker = EmbeddingWorker(model="clip-ViT-B-32")
       embedding = worker.process_file("test_image.jpg", modality="image")
       assert len(embedding) == 512  # CLIP embedding size
   ```

3. **ZMQ Communication Integration**
   ```python
   def test_client_server_communication():
       """Verify client can send commands to server."""
       # Start server in background
       # Send reload command
       # Verify response
   ```

### Mini-RiVault (vLLM + LiteLLM)

Mini-RiVault provides on-demand LLM inference via vLLM with a LiteLLM proxy.

#### Unit Tests

**Location**: `internal_genAI/` and `tests/`

**Key components to test**:

| Component | Test Focus |
|-----------|------------|
| vLLM startup | Model loading, GPU allocation, port binding |
| LiteLLM proxy | API compatibility, request routing |
| Cluster readiness | Multi-GPU coordination |

**Example Test** (`tests/test_vllm_config.py`):

```python
"""Tests for vLLM configuration."""
import pytest

class TestVLLMConfig:
    """Tests for vLLM configuration validation."""

    def test_max_model_len_respected(self):
        """VLLM_MAX_MODEL_LEN should limit context window."""
        config = VLLMConfig(max_model_len=4096)
        assert config.max_model_len == 4096

    def test_tensor_parallel_requires_multiple_gpus(self):
        """Tensor parallelism requires sufficient GPUs."""
        with pytest.raises(ValueError):
            VLLMConfig(tensor_parallel_size=2, available_gpus=1)

    def test_tool_calling_enabled(self):
        """Tool calling should be configurable."""
        config = VLLMConfig(enable_tool_calling=True)
        assert config.enable_tool_calling is True
```

#### Integration Tests

**Cluster Readiness Check** (`internal_genAI/vllm_fs/ClusterReadinessCheck.py`):

```python
"""Test multi-GPU cluster readiness."""
def test_cluster_readiness():
    """All workers must be ready before accepting requests."""
    checker = ClusterReadinessCheck(expected_workers=4)
    
    # Simulate workers coming online
    checker.register_worker("worker-0")
    checker.register_worker("worker-1")
    
    assert not checker.is_ready()  # Not all workers
    
    checker.register_worker("worker-2")
    checker.register_worker("worker-3")
    
    assert checker.is_ready()  # All workers registered
```

### Embedding Worker

The embedding worker generates vector embeddings for different modalities.

#### Unit Tests

```python
"""Tests for embedding worker."""
import pytest
from unittest.mock import Mock

class TestEmbeddingWorker:
    """Unit tests for embedding generation."""

    def test_text_embedding_dimensions(self):
        """Text embeddings should have correct dimensions."""
        worker = EmbeddingWorker(model="intfloat/e5-base-v2")
        embedding = worker.embed_text("Hello world")
        assert len(embedding) == 768

    def test_image_embedding_with_clip(self):
        """CLIP model should produce 512-dim embeddings."""
        worker = EmbeddingWorker(model="clip-ViT-B-32")
        embedding = worker.embed_image("test.jpg")
        assert len(embedding) == 512

    def test_batch_processing(self):
        """Batch processing should be more efficient."""
        worker = EmbeddingWorker(model="intfloat/e5-base-v2")
        texts = ["text1", "text2", "text3"]
        embeddings = worker.embed_batch(texts)
        assert len(embeddings) == 3

    def test_device_selection(self):
        """Worker should use specified device."""
        worker = EmbeddingWorker(device="cpu")
        assert worker.device == "cpu"
```

### Platform Gateway

The gateway (NGINX + Auth Sidecar) routes requests to appropriate backend services.

**Architecture:**
- **NGINX** (:8080): High-performance reverse proxy with rate limiting and SSE streaming
- **Auth Sidecar** (:8081): JWT/API key validation, audit logging, presigned URL generation

#### Unit Tests

```python
"""Tests for auth sidecar."""
import pytest
from gateway.sidecar.main import validate_api_key, generate_presigned_url

class TestAuthSidecar:
    """Tests for auth sidecar validation."""

    def test_valid_api_key(self):
        """Valid API keys should pass validation."""
        result = validate_api_key("sk-demo-key")
        assert result is not None
        assert result.user_id is not None

    def test_invalid_api_key_prefix(self):
        """Keys without sk- prefix should fail."""
        with pytest.raises(HTTPException) as exc:
            validate_api_key("invalid-key")
        assert exc.value.status_code == 401

    def test_presigned_url_generation(self):
        """Presigned URLs should be valid for specified TTL."""
        url = generate_presigned_url("my-bucket", "path/to/file.pdf", ttl=3600)
        assert "X-Amz-Signature" in url
        assert "X-Amz-Expires=3600" in url

class TestNginxRoutes:
    """Tests for NGINX route configuration (validated via integration tests)."""

    def test_llm_route_streams(self):
        """LLM requests should have streaming enabled (proxy_buffering off)."""
        # Validated via integration test with SSE response
        pass

    def test_rag_route_accepts_large_uploads(self):
        """RAG uploads should accept files up to 10GB."""
        # Validated via integration test with large file upload
        pass
```

#### Integration Tests

```bash
# Start gateway stack
cd gateway/compose
docker compose -f gateway.yml up -d

# Test health endpoint
curl http://localhost:8080/health

# Test LLM routing (requires LiteLLM backend)
curl -H "Authorization: Bearer sk-demo-key" \
     http://localhost:8080/gw/llm/v1/models

# Test streaming
curl -N http://localhost:8080/gw/llm/v1/chat/completions \
     -H "Authorization: Bearer sk-demo-key" \
     -H "Content-Type: application/json" \
     -d '{"model": "rivault-gpt-oss-static", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'

# Cleanup
docker compose -f gateway.yml down
```

---

## Integration Testing

Integration tests verify that components work together correctly.

### Docker Compose Integration Tests

**Location**: `umrs/docker/docker-compose.yml`

```bash
# Start full stack
cd umrs/docker
docker-compose up -d

# Run integration tests
./test_umrs_api.sh

# Cleanup
docker-compose down -v
```

### Test Scenarios

1. **UMRS + MinIO + PostgreSQL**
   - Upload file to S3
   - Verify embedding generated
   - Query via pgvector

2. **UMRS + Embedding Worker**
   - Submit processing job
   - Verify worker receives task
   - Check embeddings stored

3. **Gateway + LiteLLM + vLLM**
   - Send chat completion request
   - Verify response format
   - Check tool calling works

---

## End-to-End (E2E) Testing

E2E tests validate complete user workflows.

### Test Files

| File | Purpose |
|------|---------|
| `tests/test_e2e_smoke.py` | Quick validation of core functionality |
| `tests/test_e2e_pytest.py` | Comprehensive E2E test suite |
| `tests/test_rag_e2e.py` | RAG-specific workflows |
| `tests/test_mock_e2e.py` | E2E with mocked external services |

### Running E2E Tests

```bash
# Quick smoke test
python tests/test_e2e_smoke.py

# Full E2E suite
python tests/run_e2e_tests.py

# With pytest
pytest tests/test_e2e_pytest.py -v
```

### E2E Test Example

```python
"""E2E test for RAG workflow."""
import pytest

class TestRAGWorkflow:
    """End-to-end RAG functionality tests."""

    @pytest.fixture
    def setup_data(self):
        """Upload test documents before RAG queries."""
        # Upload documents to S3
        # Wait for embeddings
        yield
        # Cleanup

    def test_document_upload_and_query(self, setup_data):
        """Upload document, generate embeddings, query via RAG."""
        # 1. Upload document
        response = upload_document("test_doc.txt")
        assert response.status == "success"

        # 2. Wait for processing
        wait_for_embeddings(timeout=60)

        # 3. Query via RAG
        answer = rag_query("What is in the document?")
        assert "expected content" in answer.lower()

    def test_image_similarity_search(self, setup_data):
        """Upload images and perform similarity search."""
        # Upload test images
        # Search for similar images
        # Verify results
```

---

## Kubernetes Testing

K8s tests validate deployment on Kubernetes clusters.

### Setup

```bash
# Create Kind cluster with local registry
./tests/setup_kind_cluster.sh

# Or use existing cluster
export KUBECONFIG=~/.kube/config
```

### Running K8s Tests

```bash
# Full K8s test suite
./tests/run_k8s_tests.sh

# Test only (cluster must exist)
./tests/run_k8s_tests.sh --test-only

# Cleanup
./tests/run_k8s_tests.sh --cleanup
```

### K8s Test Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Kind Cluster                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   MinIO     │  │  PostgreSQL │  │   RiVault   │     │
│  │  (S3 mock)  │  │  (pgvector) │  │  (LiteLLM)  │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
│  ┌─────────────┐  ┌─────────────┐                       │
│  │ UMRS Server │  │   Embed     │                       │
│  │             │  │   Worker    │                       │
│  └─────────────┘  └─────────────┘                       │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Test Job Pod                        │   │
│  │  - Runs test_rag_end_to_end.sh                  │   │
│  │  - Validates all services                        │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### K8s Test Example

```python
"""K8s E2E tests."""
import subprocess
import pytest

class TestK8sDeployment:
    """Tests for Kubernetes deployment."""

    def test_all_pods_running(self):
        """All required pods should be running."""
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", "umrs-system", "-o", "json"],
            capture_output=True
        )
        pods = json.loads(result.stdout)
        
        required = {"minio", "postgres", "umrs-server", "umrs-embed-worker"}
        running = {p["metadata"]["name"].split("-")[0] for p in pods["items"]
                   if p["status"]["phase"] == "Running"}
        
        assert required.issubset(running)

    def test_service_endpoints_accessible(self):
        """Services should be accessible via ClusterIP."""
        services = ["umrs-server:8000", "minio:9000", "postgres:5432"]
        for svc in services:
            # Port-forward and test connectivity
            pass
```

---

## Mock Testing

Mock tests allow testing without real infrastructure.

### Mock Components

| Mock | Purpose | Location |
|------|---------|----------|
| `mock_litellm.py` | Simulates LiteLLM responses | `tests/docker/` |
| `mock_slurm/` | Simulates Slurm scheduler | `tests/docker/mock_slurm/` |
| `Dockerfile.slurm-mock` | Mock Slurm environment | `tests/docker/` |
| `Dockerfile.compute-mock` | Mock compute node | `tests/docker/` |

### Using Mocks

```python
"""Tests using mock services."""
import pytest
from unittest.mock import Mock, patch

class TestWithMocks:
    """Tests using mocked external services."""

    @patch('umrs.rivault_service.RivaultClient')
    def test_rag_with_mock_llm(self, mock_client):
        """Test RAG with mocked LLM responses."""
        mock_client.return_value.chat.return_value = {
            "choices": [{"message": {"content": "Mocked response"}}]
        }
        
        result = rag_service.query("test question")
        assert result == "Mocked response"

    @patch('umrs.embedding_worker.load_model')
    def test_embedding_with_mock_model(self, mock_load):
        """Test embedding generation with mocked model."""
        mock_model = Mock()
        mock_model.encode.return_value = [0.1] * 768
        mock_load.return_value = mock_model
        
        worker = EmbeddingWorker()
        embedding = worker.embed_text("test")
        assert len(embedding) == 768
```

### Mock Slurm Testing

```bash
# Build mock Slurm image
docker build -t slurm-mock -f tests/docker/Dockerfile.slurm-mock tests/docker/

# Run tests with mock Slurm
docker run --rm slurm-mock pytest tests/test_slurm_e2e.py
```

---

## Test-Driven Development Guidelines

### TDD Workflow

```
1. Write failing test → 2. Write minimal code → 3. Refactor → Repeat
```

### For New Features

1. **Understand the requirement**
   - What should the feature do?
   - What are the edge cases?
   - What errors should be handled?

2. **Write the test first**
   ```python
   def test_new_feature():
       """New feature should do X when Y."""
       result = new_feature(input_y)
       assert result == expected_x
   ```

3. **Run test (should fail)**
   ```bash
   pytest tests/test_new_feature.py -v
   # Expected: FAILED
   ```

4. **Implement minimum code**
   ```python
   def new_feature(input_y):
       return expected_x  # Simplest implementation
   ```

5. **Run test (should pass)**
   ```bash
   pytest tests/test_new_feature.py -v
   # Expected: PASSED
   ```

6. **Refactor and add edge cases**

### For Bug Fixes

1. **Write test that reproduces the bug**
   ```python
   def test_bug_123_edge_case():
       """Bug #123: System crashes when input is empty."""
       # This should not raise an exception
       result = process_input("")
       assert result is None
   ```

2. **Verify test fails with current code**

3. **Fix the bug**

4. **Verify test passes**

5. **Add regression test to CI**

### Test Naming Conventions

```python
# Good test names
def test_upload_file_creates_s3_object():
def test_embedding_worker_handles_corrupt_image():
def test_rate_limiter_rejects_after_threshold():

# Bad test names
def test_upload():
def test_1():
def test_feature_works():
```

---

## Running Tests

### Prerequisites

```bash
# Install test dependencies
pip install -r tests/requirements.txt

# For K8s tests
# - Docker
# - Kind (https://kind.sigs.k8s.io/)
# - kubectl
```

### Quick Reference

| Test Type | Command | Duration | Description |
|-----------|---------|----------|-------------|
| Unit tests only | `make test-unit` | Seconds | Standalone tests, no services required |
| All tests | `make test` | 10-15 min | Full test suite including E2E |
| Unit tests (legacy) | `pytest umrs/tests/ -v` | Seconds | UMRS-specific tests |
| Integration | `./umrs/docker/test_umrs_api.sh` | Minutes | UMRS API integration |
| E2E smoke | `python tests/test_e2e_smoke.py` | Minutes | Quick E2E verification |
| K8s E2E | `./tests/run_k8s_tests.sh` | 5-10 min | Kubernetes tests |

> **Recommended for development**: Use `make test-unit` for rapid feedback during development. It runs all standalone unit tests without requiring Docker or external services.

### Unit Test Coverage

The `make test-unit` command runs tests from the following directories:

| Package | Directory | Tests | Coverage |
|---------|-----------|-------|----------|
| r4 | `r4/tests/` | 35+ tests | Client, Config, Errors, RAG, Jupyter |
| gateway | `gateway/tests/` | 75+ tests | Contracts, Files, RateLimit, Security |
| mcp-servers | `mcp-servers/tests/` | 5+ tests | Base server, Registry |
| core tests | `tests/test_*.py` | 130+ tests | Extensibility, Model control, n8n, Tools |

**Key test modules:**

- **test_client.py**: ChatMessage, ChatResponse, Client creation and usage
- **test_config.py**: Configuration from environment, validation, HPC detection
- **test_errors.py**: Custom exception hierarchy (R4Error, AuthenticationError, etc.)
- **test_rag.py**: RAG queries, sources, responses, file uploads
- **test_files.py**: Presigned URL detection, MIME type guessing
- **test_ratelimit.py**: Token bucket algorithm, per-project rate limiting
- **test_security_contracts.py**: Security invariants (auth stripping, URL allowlist, etc.)

### Test with Coverage

```bash
pytest tests/ --cov=umrs --cov-report=html
open htmlcov/index.html
```

### Test Specific Component

```bash
# UMRS only
pytest tests/ -k "umrs" -v

# Embedding only
pytest tests/ -k "embedding" -v

# K8s only
pytest tests/test_k8s_e2e.py -v
```

---

## Writing New Tests

### Template: Unit Test

```python
"""Unit tests for [MODULE_NAME]."""
import pytest
from unittest.mock import Mock, patch

class Test[ClassName]:
    """Tests for [ClassName]."""

    @pytest.fixture
    def setup(self):
        """Set up test fixtures."""
        # Create test objects
        yield
        # Cleanup

    def test_[method]_[scenario]_[expected_result](self, setup):
        """[Method] should [expected behavior] when [scenario]."""
        # Arrange
        input_data = ...
        
        # Act
        result = module.method(input_data)
        
        # Assert
        assert result == expected
```

### Template: Integration Test

```python
"""Integration tests for [COMPONENT]."""
import pytest
import subprocess
import time

class Test[Component]Integration:
    """Integration tests for [Component]."""

    @pytest.fixture(scope="class")
    def start_services(self):
        """Start required services."""
        subprocess.run(["docker-compose", "up", "-d"])
        time.sleep(10)  # Wait for services
        yield
        subprocess.run(["docker-compose", "down", "-v"])

    def test_[workflow]_integration(self, start_services):
        """Test [workflow] across multiple services."""
        # Test implementation
```

### Template: E2E Test

```python
"""E2E tests for [FEATURE]."""
import pytest

@pytest.mark.e2e
class Test[Feature]E2E:
    """End-to-end tests for [Feature]."""

    @pytest.fixture(scope="module")
    def setup_environment(self):
        """Set up complete test environment."""
        # Deploy services
        # Load test data
        yield
        # Cleanup

    def test_complete_[workflow](self, setup_environment):
        """Test complete [workflow] from start to finish."""
        # Step 1: User action
        # Step 2: System response
        # Step 3: Verify outcome
```

---

## CI/CD Integration

### GitHub Actions Example

```yaml
# .github/workflows/test.yml (an example, not a workflow of this repository)
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: pip install -r tests/requirements.txt
      - name: Run unit tests
        run: pytest tests/ -v --ignore=tests/test_k8s_e2e.py

  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
      minio:
        image: minio/minio
    steps:
      - uses: actions/checkout@v3
      - name: Run integration tests
        run: ./umrs/docker/test_umrs_api.sh

  k8s-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Create Kind cluster
        run: ./tests/setup_kind_cluster.sh
      - name: Run K8s tests
        run: ./tests/run_k8s_tests.sh --test-only
```

### Pre-commit Hooks

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest-check
        name: pytest-check
        entry: pytest tests/ -v --ignore=tests/test_k8s_e2e.py -x
        language: system
        pass_filenames: false
        always_run: true
```

---

## Appendix: Test Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UMRS_HOST` | UMRS server hostname | `localhost` |
| `UMRS_PORT` | UMRS control port | `5555` |
| `S3_ENDPOINT` | MinIO/S3 endpoint | `http://localhost:9000` |
| `PGHOST` | PostgreSQL host | `localhost` |
| `PGPORT` | PostgreSQL port | `5432` |
| `PGUSER` | PostgreSQL user | `postgres` |
| `PGPASSWORD` | PostgreSQL password | `postgres` |
| `PGDATABASE` | PostgreSQL database | `umrs` |
| `SKIP_RAG_TESTS` | Skip RAG microservice tests | `false` |
| `EMBEDDING_DEVICE` | Device for embeddings | `cuda` |
| `K8S_NAMESPACE` | Kubernetes namespace | `umrs-system` |
| `REGISTRY_PORT` | Local registry port | `5001` |

---

## Quick Start for AI Agents/Copilots

When asked to implement or modify features in this codebase:

1. **Find existing tests** for the component being modified
2. **Write a failing test** that captures the new requirement
3. **Implement the feature** to make the test pass
4. **Run the test suite** to ensure no regressions
5. **Update documentation** if behavior changes

### Common Test Commands

```bash
# Run all tests quickly
pytest tests/ -v -x --ignore=tests/test_k8s_e2e.py

# Run specific test file
pytest tests/test_rag_e2e.py -v

# Run tests matching pattern
pytest tests/ -k "embedding" -v

# Run with verbose output
pytest tests/ -v --tb=long

# Run K8s tests (requires Kind cluster)
./tests/run_k8s_tests.sh
```

### Debugging Failed Tests

```bash
# Run single test with debug output
pytest tests/test_file.py::TestClass::test_method -v -s

# Drop into debugger on failure
pytest tests/ --pdb

# Show local variables on failure
pytest tests/ -l
```
