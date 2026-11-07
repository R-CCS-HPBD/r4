# E2E Test Environment

This directory contains the infrastructure for running end-to-end tests locally without requiring actual HPC or Kubernetes clusters.

## Quick Start

```bash
# Run all E2E tests with mock environment
make e2e-test

# Or step-by-step:
make e2e-start      # Start mock services
make e2e-test       # Run tests
make e2e-stop       # Stop and cleanup
```

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    E2E Test Environment                        │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  slurm-mock  │    │ compute-mock │    │     Gateway      │  │
│  │              │◄──►│  (LiteLLM)   │◄──►│                  │  │
│  │ sbatch/squeue│    │   :11434     │    │     :8080        │  │
│  └──────────────┘    └──────────────┘    └────────┬─────────┘  │
│         │                                         │            │
│         │           ┌──────────────┐              │            │
│         └──────────►│     UMRS     │◄─────────────┘            │
│                     │    :8001     │                           │
│                     └──────┬───────┘                           │
│                            │                                   │
│              ┌─────────────┴─────────────┐                     │
│              │                           │                     │
│        ┌─────▼─────┐              ┌──────▼──────┐              │
│        │  Postgres │              │    MinIO    │              │
│        │  pgvector │              │     S3      │              │
│        │   :5432   │              │  :9000/9001 │              │
│        └───────────┘              └─────────────┘              │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

## Components

### Mock Slurm (`slurm-mock`)

Simulates Slurm commands without requiring a real Slurm cluster:

| Command | Description |
|---------|-------------|
| `sinfo` | Shows mock partition/node info |
| `sbatch` | Submits a "job" (creates job info files) |
| `squeue` | Lists running/pending jobs |
| `scancel` | Cancels a job |
| `sacct` | Shows job accounting data |

Jobs are tracked in `/var/spool/slurm/jobs.json`. When a job is "submitted", it:
1. Creates a job entry in state file
2. After `MOCK_SLURM_DELAY` seconds, transitions to RUNNING
3. Writes job info files to `/var/run/rivault/`

### Mock Compute Node (`compute-mock`)

Provides a lightweight LiteLLM mock that:
- Responds to `/health`, `/v1/models`, `/v1/chat/completions`
- Returns deterministic mock responses
- Supports streaming responses
- **Does NOT require GPU or model weights**

### Supporting Services

- **PostgreSQL + pgvector**: Real database for testing UMRS
- **MinIO**: S3-compatible storage
- **Gateway**: Real gateway service
- **UMRS**: Real UMRS service (configured for mock Slurm backend)

## Test Modes

### Local Mode (Default)

Uses the Docker Compose mock environment:

```bash
python tests/run_e2e_tests.py --mode local
# or simply
python tests/run_e2e_tests.py
```

### HPC Mode

Runs tests against real Slurm infrastructure (requires HPC access):

```bash
python tests/run_e2e_tests.py --mode hpc
```

### K8s Mode

Runs tests against a real Kubernetes cluster (Kind/minikube):

```bash
python tests/run_e2e_tests.py --mode k8s
```

## Test Suites

| Suite | Description |
|-------|-------------|
| `all` | Run all E2E tests |
| `slurm` | Slurm-specific tests only |
| `k8s` | Kubernetes-specific tests only |
| `rag` | RAG pipeline tests |
| `smoke` | Quick smoke tests |

```bash
# Run specific suite
python tests/run_e2e_tests.py --suite slurm
```

## Development

### Debug Mode

Keep environment running after tests for debugging:

```bash
python tests/run_e2e_tests.py --no-cleanup

# Or manually:
make e2e-start
# ... run tests manually ...
make e2e-logs  # View service logs
make e2e-stop
```

### Adding New Mock Services

1. Create Dockerfile in `tests/docker/`
2. Add service to `docker-compose.e2e.yaml`
3. Update test runner if needed

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_SLURM_DELAY` | 2 | Seconds before job becomes "RUNNING" |
| `MOCK_SLURM_COMPUTE_NODE` | compute-mock | Hostname of compute node |
| `LITELLM_MASTER_KEY` | sk-test-e2e | API key for LiteLLM |
| `E2E_TEST_MODE` | false | Enables E2E test behaviors |

## Troubleshooting

### Services not starting

```bash
# Check logs
docker compose -f docker-compose.e2e.yaml logs

# Check health
docker compose -f docker-compose.e2e.yaml ps
```

### Mock Slurm not responding

```bash
# Check job state
docker exec slurm-mock cat /var/spool/slurm/jobs.json

# Check job info files
docker exec slurm-mock ls -la /var/run/rivault/
```

### LiteLLM mock not responding

```bash
# Test directly
curl http://localhost:11434/health
curl -H "Authorization: Bearer sk-test-e2e" http://localhost:11434/v1/models
```
