# E2E Testing Guide

This guide explains how to use the end-to-end (E2E) testing infrastructure for the R4 Platform.

## Overview

The E2E test suite validates the complete platform integration across all components:

- **Gateway** - API gateway with authentication and routing
- **UMRS** - Unified Multimodal Retrieval System
- **LiteLLM** - LLM proxy (mocked in local tests)
- **Slurm** - Job scheduler (mocked in local tests)
- **MinIO** - Object storage
- **PostgreSQL** - Database with pgvector

## Quick Start

### Local Development (Mock Environment)

```bash
# Run full E2E test suite
make e2e-test

# Or step by step:
make e2e-start      # Start mock environment
make e2e-test-no-cleanup  # Run tests without teardown
make e2e-stop       # Stop and clean up
```

### Check Container Status

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

### View Logs

```bash
make e2e-logs  # Stream logs from all services
docker logs e2e-gateway  # Single service logs
```

## Test Environments

### 1. Local Mock Environment (Default)

Uses Docker Compose with mock services to simulate the full platform without requiring HPC resources.

**Services:**
| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| Gateway | e2e-gateway | 8080 | API Gateway |
| UMRS | e2e-umrs | 8001 | Model Registry |
| LiteLLM Mock | compute-mock | 11434 | Mock LLM responses |
| Slurm Mock | slurm-mock | 6817 | Mock job scheduler |
| MinIO | e2e-minio | 9000/9001 | Object storage |
| PostgreSQL | e2e-postgres | 5432 | Database |

**Authentication:**
- Gateway accepts `X-Project-ID` header for test authentication
- LiteLLM mock uses API key: `sk-test-e2e`

### 2. HPC Environment (Real Slurm)

For testing on actual HPC infrastructure with Slurm and Singularity.

```bash
# Set environment variables
export SLURM_TEST_MODEL_PATH=/models/rivault-gpt-oss
export SLURM_TEST_IMAGE=/opt/singularity/mini-rivault.sif
export SLURM_TEST_PARTITION=gpu

# For the canonical Gateway -> UMRS -> Slurm path
export R4_GATEWAY_URL=http://gateway:8080
export R4_API_KEY=<bearer-token>
export R4_PROJECT_ID=<project-id>
export SLURM_TEST_MODEL_NAME=<registered-model-name>

# Run HPC tests
./tests/run_hpc_tests.sh
```

**Prerequisites:**
- Slurm access (sbatch, squeue, sinfo)
- Singularity/Apptainer installed
- Pre-staged model weights
- GPU partition available
- Gateway reachable from the HPC test environment
- Valid Gateway bearer token and project ID for the Gateway-backed HPC suite

### 3. Kubernetes Environment

For testing with Kubernetes orchestration.

```bash
python tests/run_e2e_tests.py --mode k8s --suite all
```

## Test Files

| File | Description | Markers |
|------|-------------|---------|
| `test_mock_e2e.py` | Mock environment tests | `@pytest.mark.e2e` |
| `test_hpc_gateway_e2e.py` | Canonical R4 -> Gateway -> UMRS -> Slurm tests, including repeat provision/teardown cycle | `@pytest.mark.hpc` |
| `test_slurm_e2e.py` | Lower-level raw Slurm diagnostic tests with runtime offline-env verification | `@pytest.mark.hpc` |
| `test_k8s_e2e.py` | Kubernetes tests | `@pytest.mark.k8s` |
| `test_rag_e2e.py` | RAG workflow tests | `@pytest.mark.e2e` |
| `test_research_journey_e2e.py` | Full 5-step AI4S research journey (ingest→understand→verify→run→export) | `@pytest.mark.e2e` |

## Test Categories

### Health Checks
```python
class TestMockEnvironmentHealth:
    def test_gateway_health(self)      # Gateway /health endpoint
    def test_litellm_mock_health(self) # LiteLLM /health
    def test_minio_health(self)        # MinIO health check
    def test_slurm_mock_health(self)   # Slurm mock availability
```

### LLM Integration
```python
class TestMockLLMIntegration:
    def test_list_models(self)           # GET /v1/models
    def test_chat_completion(self)       # POST /v1/chat/completions
    def test_chat_completion_streaming(self)  # Streaming responses
```

### Gateway Routing
```python
class TestGatewayRouting:
    def test_gateway_routes_to_llm(self)   # /gw/llm/* → LiteLLM
    def test_gateway_routes_to_umrs(self)  # /gw/rag/* → UMRS
    def test_gateway_chat_via_route(self)  # Full chat through gateway
```

### Multi-User Isolation
```python
class TestMultiUserIsolation:
    def test_different_projects_isolated(self)
    def test_missing_project_id_rejected(self)
```

### Research Journey (TASK-047)
The 5-step research journey test validates the complete AI4S user workflow:

    Step 1: Ingest     — Import research artifacts (papers, code, datasets)
    Step 2: Understand — Extract structured knowledge, build knowledge graph
    Step 3: Verify     — Ask evidence-based questions about the knowledge
    Step 4: Run        — Execute the research workflow end-to-end
    Step 5: Export     — Generate final research report / knowledge bundle

Each step uses the `r4 project` CLI to interact with the Gateway/UMRS stack.
Individual steps can be run in isolation via `pytest -k test_step_N`.

## Running Specific Tests

```bash
# Run only health check tests
pytest tests/test_mock_e2e.py::TestMockEnvironmentHealth -v

# Run only the canonical Gateway-backed HPC tests
pytest tests/test_hpc_gateway_e2e.py -v -m hpc

# Run tests matching a pattern
pytest tests/test_mock_e2e.py -k "gateway" -v

# Run with specific marker
pytest tests/ -m "e2e" -v
pytest tests/ -m "hpc" -v  # Requires real HPC

# Run the full 5-step research journey (TASK-047)
pytest tests/test_research_journey_e2e.py::test_research_journey_full -v

# Run a specific step in isolation
pytest tests/test_research_journey_e2e.py -k "test_step_3" -v

# Skip slow tests
pytest tests/ -m "not slow" -v
```

## Environment Variables

### Local Testing
```bash
E2E_TEST_MODE=true           # Enable E2E mode
GATEWAY_URL=http://localhost:8080
UMRS_URL=http://localhost:8001
LITELLM_URL=http://localhost:11434
LITELLM_API_KEY=sk-test-e2e
```

### HPC Testing
```bash
SLURM_TEST_MODEL_PATH=/models/rivault-gpt-oss
SLURM_TEST_IMAGE=/opt/singularity/mini-rivault.sif
SLURM_TEST_PARTITION=gpu
SLURM_TEST_TIME=00:30:00
SLURM_TEST_GPUS=1
R4_GATEWAY_URL=http://gateway:8080
R4_API_KEY=<bearer-token>
R4_PROJECT_ID=<project-id>
SLURM_TEST_MODEL_NAME=<registered-model-name>
```

## Writing New Tests

### Basic Test Structure

```python
import pytest
import httpx


@pytest.mark.e2e
def test_my_feature(e2e_mode, gateway_headers):
    """Test description."""
    response = httpx.get(
        f"{GATEWAY_URL}/my/endpoint",
        headers=gateway_headers,
        timeout=10,
    )
    assert response.status_code == 200
    assert "expected_field" in response.json()
```

### Using Fixtures

```python
@pytest.fixture
def gateway_headers():
    """Headers for authenticated gateway requests."""
    return {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "X-Project-ID": "test-project",
    }


@pytest.fixture
def e2e_mode():
    """Skip if not in E2E mode."""
    if not is_e2e_mode():
        pytest.skip("Not in E2E test mode")
```

### Async Tests

```python
import asyncio


@pytest.mark.e2e
def test_concurrent_requests(e2e_mode, gateway_headers):
    async def make_request():
        async with httpx.AsyncClient() as client:
            return await client.get(url, headers=gateway_headers)

    results = asyncio.run(asyncio.gather(*[make_request() for _ in range(5)]))
    assert all(r.status_code == 200 for r in results)
```

## CI/CD Integration

The E2E tests run automatically in GitHub Actions:

```yaml
# .github/workflows/e2e.yml
on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run E2E Tests
        run: make e2e-test
```

## Troubleshooting

### Container Won't Start

```bash
# Check container logs
docker logs e2e-gateway

# Check port conflicts
lsof -i :8080

# Remove old volumes
docker compose -f docker-compose.e2e.yaml down -v
```

### Tests Timeout Waiting for Health

```bash
# Manually check service health
curl http://localhost:8080/health
curl http://localhost:11434/health

# Check container status
docker ps -a
docker inspect e2e-gateway --format '{{json .State.Health}}'
```

### Authentication Errors (401)

- Ensure `X-Project-ID` header is set for gateway requests
- Verify `LITELLM_API_KEY` matches `sk-test-e2e`

### Connection Refused

- Wait for containers to be healthy before running tests
- Use `make e2e-start` and wait for all "Healthy" status

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          Test Runner                             │
│                     (pytest + test_mock_e2e.py)                  │
└─────────────────────────────┬────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│    Gateway      │  │   Mock LiteLLM  │  │   Slurm Mock    │
│   :8080         │  │   :11434        │  │   :6817         │
└────────┬────────┘  └─────────────────┘  └─────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌──────┐  ┌──────┐
│ UMRS │  │MinIO │
│:8001 │  │:9000 │
└──────┘  └──────┘
```

## Related Documentation

- Development Guidelines
- HPC Deployment
- [UMRS Overview](../umrs/doc/umrs_overview.md)
