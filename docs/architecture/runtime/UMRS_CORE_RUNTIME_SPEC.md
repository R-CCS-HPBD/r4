# UMRS Core Runtime Specification

*Established: 2026-05-13 — Final Convergence Phase*
*Status: CANONICAL — Permanent Reference*

## Purpose

This document defines the **UMRS Core Runtime** as a standalone platform with
stable operational boundaries. UMRS is independently deployable, operationally
authoritative, and does not require R4 or any upper-layer client to function.

---

## 1. Runtime Ownership Boundaries

### UMRS Owns (Exclusively)

| Domain | Scope | Authority |
|--------|-------|-----------|
| **Job Lifecycle** | Submission, scheduling, monitoring, reconciliation, retry, terminal states | UMRS is the single source of truth for job state |
| **Transport** | ZMQ control plane (ROUTER/DEALER) + data plane (PUSH/PULL) | Permanent infrastructure; never retires |
| **Persistence** | PostgreSQL job/attempt tables, advisory locks, commit pointers | All durable state lives in UMRS-owned schemas |
| **Reconciliation** | Boot-time crash recovery, stale-attempt marking, requeue | UMRS instance_id-based ownership |
| **Scheduling** | Job claim, backend dispatch, attempt creation, backoff | Single scheduler per UMRS instance |
| **Monitoring** | Active attempt polling, status transitions, readiness checks | Timeout-guarded per-attempt checks |
| **Endpoint Provisioning** | Mini-RiVault lifecycle, container launch, readiness polling | UMRS submits, manages, and tears down |
| **Model Registry** | Model metadata, ACL, download jobs, scope policies | Models are UMRS-managed assets |
| **Context Storage** | Conversations, messages, summaries, memory vectors | Durable context for LLM interactions |
| **Metrics** | Runtime metrics emission (scheduler, monitor, transport, bridge) | UMRS-internal observability |
| **Configuration** | Site config, profile system, runtime environment injection | `site.yaml` + env-driven configuration |

### UMRS Does NOT Own

| Domain | Owner | Integration Pattern |
|--------|-------|-------------------|
| CLI/UX | R4 (optional) | HTTP/ZMQ wire protocol |
| Gateway routing/auth | Gateway (optional) | HTTP proxy to UMRS sidecar |
| Workflow authoring | R4 (optional) | Submit via UMRS API |
| Agent tooling | R4 (optional) | Tool calls via UMRS endpoints |
| User-facing SDK | R4 (optional) | SDK wraps UMRS HTTP API |

---

## 2. Stable Runtime APIs

### Internal APIs (UMRS Process Boundary)

| API | Transport | Stability | Consumers |
|-----|-----------|-----------|-----------|
| ZMQ Control Plane | `tcp://*:5555` (ROUTER) | **PERMANENT** | umrs, R4 SDK |
| ZMQ Data Plane | `tcp://*:<data_port>` (PULL) | **PERMANENT** | umrs file transfers |
| HTTP Sidecar | `http://*:8082` (REST) | **STABLE** | Gateway, external tooling |

### Job System APIs (Internal Protocol)

| Operation | Interface | Guarantees |
|-----------|-----------|-----------|
| `create_job` | JobStore.create_job() | Idempotent via job_id |
| `claim_next_jobs` | JobStore.claim_next_jobs() | Advisory-lock protected |
| `mark_attempt_*` | JobStore state transitions | Atomic Postgres transactions |
| `reconcile_on_boot` | Reconciler.reconcile_on_boot() | Runs once per boot; idempotent |

### Extension Points

| Point | Mechanism | Stability |
|-------|-----------|-----------|
| New backend types | Implement `OrchestratorBackend` protocol | STABLE interface |
| New job types | Add to `JobType` enum + register in `backend_factory` | STABLE extension |
| New status checkers | Implement `BackendStatusChecker` protocol | STABLE interface |
| Site profiles | Add profile in `site.yaml` profiles section | STABLE configuration |
| HTTP routes | Register in `http_sidecar` app | STABLE extension |

---

## 3. Operational Authority Surfaces

### Process-Level Singletons

| Component | Thread | Lifecycle | Authority |
|-----------|--------|-----------|-----------|
| Control MessageQueue | Main thread | Process lifetime | ZMQ ROUTER socket owner |
| DurableJobRuntime | `umrs-durable-jobs` thread | Process lifetime | Asyncio loop + scheduler + monitor |
| HTTP Sidecar | `umrs-http-sidecar` thread | Process lifetime | REST API owner |
| ThreadPoolManager | Main thread (managed) | Process lifetime | Concurrency governor |
| StatusStore | Main thread | Process lifetime | Task status cache |

### Reconciliation Authority

| Scope | Mechanism | Timing |
|-------|-----------|--------|
| Stale attempt recovery | `umrs_instance_id` comparison | Boot-time only |
| Orphaned job requeue | Retry budget check | Boot-time only |
| Backend cleanup | **NOT performed** (by design) | N/A |

### Persistence Authority

| Store | Technology | Owner | Shared? |
|-------|-----------|-------|---------|
| Job/Attempt tables | PostgreSQL | UMRS exclusively | No |
| Model registry | PostgreSQL | UMRS exclusively | No |
| Context/Memory | PostgreSQL + pgvector | UMRS exclusively | No |
| Status cache | DiskCache (local) | UMRS process | No |
| S3 object storage | MinIO/S3-compatible | UMRS manages; shared reads | Object-level ACL |

---

## 4. Transport Ownership

### ZMQ Transport (PERMANENT)

```
┌──────────────────────────────────────────────────────┐
│  Control Plane: ROUTER socket (port 5555)            │
│  - Request/response for job commands                 │
│  - Auth via multipart frames                         │
│  - HWM: 10240 messages                              │
│  - Timeout: server=-1 (blocking), client=8000ms     │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  Data Plane: PULL socket (configurable port)         │
│  - File transfers, binary payloads                   │
│  - HWM: 10240 messages                              │
│  - Timeout: 8000ms send/recv                         │
│  - Linger: 0 (fire-and-forget on close)             │
└──────────────────────────────────────────────────────┘
```

**Invariant**: ZMQ transport is permanent infrastructure. It MUST NOT be
targeted for replacement, retirement, or simplification proposals.

### HTTP Sidecar (STABLE)

- Provides REST interface for Gateway integration
- Respects `UMRS_TUNNEL_MODE` for bind address
- Optional (can be disabled via `UMRS_HTTP_SIDECAR_ENABLED=false`)
- Does NOT replace ZMQ for data-plane operations

---

## 5. Scheduler Ownership

| Responsibility | Implementation | Guarantee |
|---------------|---------------|-----------|
| Job claim | `claim_next_jobs(limit=batch_size)` | Advisory lock exclusivity |
| Backend dispatch | `backend_factory(job_type)` | Exception-guarded |
| Attempt creation | `create_attempt(job_id, backend)` | Postgres-atomic |
| Submission | `backend.submit_job(job, attempt)` | Timeout-bounded |
| Failure handling | `mark_attempt_failed(retry_delay=30)` | Automatic requeue |
| Burst detection | 3+ consecutive failures → metric | Operational visibility |

**Invariant**: One scheduler per UMRS process. No external scheduler coordination.

---

## 6. Compatibility Guarantees

### Wire Protocol Stability

| Protocol | Version | Breaking Change Policy |
|----------|---------|----------------------|
| ZMQ Control messages | Colon-delimited string format | Must maintain backward compatibility |
| HTTP Sidecar REST | JSON request/response | Additive-only changes |
| Job spec schema | JSON dict in `spec` column | Additive-only; consumers ignore unknown fields |

### Configuration Stability

| Configuration | File | Breaking Change Policy |
|--------------|------|----------------------|
| Site profiles | `site.yaml` | New profiles additive; existing profiles immutable |
| Environment vars | Process environment | New vars additive; existing semantics preserved |
| Slurm scripts | `serve-model.slurm` | Backward-compatible parameter additions |

---

## 7. Deployment Invariants

| Invariant | Enforcement |
|-----------|-------------|
| Single `execution_authority` per job | JobStore constraint |
| Offline inference containers (`TRANSFORMERS_OFFLINE=1`) | Slurm script + boot assertion |
| Model download = only egress-enabled job type | EgressGuard policy |
| No internet in Mini-RiVault inference | Container network isolation |
| Pre-staged model paths only | `VLLM_MODEL_PATH` validation |
| Advisory locks prevent concurrent attempts | Postgres advisory lock |
| Attempt-scoped writes (`s3://…/jobs/<id>/attempt_<n>/…`) | Convention (enforced by backends) |
| Commit pointer = truth for outputs | JobStore.commit_attempt_id |

---

## 8. Metrics Ownership

| Metric Category | Emitter | Purpose |
|----------------|---------|---------|
| `scheduler_cycle_duration` | Scheduler | Performance tracking |
| `scheduler_jobs_claimed` | Scheduler | Throughput |
| `monitor_check` | Monitor | Status check outcomes |
| `transport_error` | MessageQueue | Transport health |
| `transport_timeout` | MessageQueue | HWM/timeout detection |
| `bridge_event` | DurableJobRuntime | Lifecycle tracking |
| `reconciler_run` | Reconciler | Boot recovery health |
| `reconciler_backlog` | Reconciler | Stale attempt count |
| `job_submitted` | Scheduler | Per-backend submission |
| `job_lost` | Reconciler | Crash recovery |
| `retry_burst` | Scheduler | Consecutive failure detection |

---

## 9. Permanent vs Transitional Classification

### Permanent (Never Retires)

- ZMQ MessageQueue transport
- DurableJobRuntime bridge
- Job/Attempt state machine
- Reconciler crash recovery
- Scheduler/Monitor loop architecture
- Advisory lock exclusivity model
- PostgreSQL persistence layer
- Site configuration system
- OrchestratorBackend protocol
- BackendStatusChecker protocol

### Transitional (Has Retirement Gate)

- Legacy endpoint aliases → retires after CLI v1.2 + 2 cycles
- `stale_name_validator` → retires 6 months post-cleanup
- DTO mirror equivalence check → retires after single-source migration

### Optional Integrations

- HTTP Sidecar (disabled by env var)
- Gateway (external; communicates via HTTP sidecar)
- R4 SDK/CLI (external; communicates via ZMQ or HTTP)
- n8n workflows (external; communicates via Gateway)

---

## 10. What This Spec Does NOT Cover

- R4 CLI/SDK internals (see `R4_CLIENT_ROLE_DEFINITION.md`)
- Gateway routing policies (see Gateway README)
- Workflow authoring (R4-owned)
- Agent/companion tooling (R4-owned)
- MCP server registration (R4-owned UX, UMRS-owned runtime)
- Frontend applications (separate concern)
