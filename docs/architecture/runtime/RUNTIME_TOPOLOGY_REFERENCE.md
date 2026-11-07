# Runtime Topology Reference

*Generated: 2026-05-13 — Architecture Stewardship Phase*

## Purpose

This document maps the complete runtime topology of the UMRS server: boot
sequence, thread ownership, transport authority, reconciliation authority,
scheduler authority, persistence authority, metrics ownership, and config
authority. Each component is classified by its architectural permanence.

---

## Component Classification Legend

| Symbol | Class | Definition |
|--------|-------|-----------|
| **[P]** | Permanent Infrastructure | Architectural invariant; never retires |
| **[B]** | Compatibility Bridge | Gen1↔Gen2 coexistence layer |
| **[T]** | Transitional Abstraction | Will retire when migration completes |
| **[S]** | Operational Singleton | Single instance per UMRS process |
| **[D]** | Dynamically Loaded | Activated by config or job type |

---

## 1. Boot Sequence DAG

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        UMRS Server Boot Sequence                        │
└─────────────────────────────────────────────────────────────────────────┘

Phase 1: Pre-Asyncio Bootstrap (Main Thread)
═══════════════════════════════════════════════

  [1] CLI Args & Config Validation
       │
       ▼
  [2] StatusStore + ThreadDict (disk cache)
       │
       ▼
  [3] ThreadPoolManager (S3 concurrency)
       │
       ▼
  [4] perform_initial_config()
       ├── [4a] Control MessageQueue [P] — ZMQ ROUTER on port 5555
       ├── [4b] Server config from JSON
       ├── [4c] Per-user S3 credentials
       ├── [4d] CUDA device detection
       └── [4e] LocalEmbeddingBackend [D] — deferred model load
       │
       ▼
  [5] reconcile_status_on_startup() — S3 orphan check


Phase 2: Durable Job Runtime (Dedicated Thread: "umrs-durable-jobs")
════════════════════════════════════════════════════════════════════════

  [6] DurableJobRuntime.start() [B]
       │
       ├── Thread created (daemon=True)
       │    │
       │    ▼
       │   [7] asyncio.new_event_loop()
       │    │
       │    ▼
       │   [8] DurableOrchestrationService.startup()
       │        │
       │        ├── [8a] Postgres schema migration
       │        ├── [8b] JobService creation
       │        ├── [8c] Backend factory wiring
       │        └── [8d] Status checker wiring
       │        │
       │        ▼
       │   [9] JobService.startup()
       │        │
       │        ├── [9a] JobStore init [P] — Postgres connection
       │        ├── [9b] Reconciler boot [P] — mark stale, requeue
       │        ├── [9c] Model download recovery
       │        ├── [9d] Scheduler.start() [P] — asyncio task
       │        └── [9e] Monitor.start() [P] — asyncio task
       │        │
       │        ▼
       │   [10] _startup_complete.set() — signals main thread
       │    │
       │    ▼
       │   [11] loop.run_forever() — event loop serves scheduler + monitor
       │
       ▼
  Main thread unblocked (startup_complete received)


Phase 3: HTTP Sidecar (Dedicated Thread: "umrs-http-sidecar")
══════════════════════════════════════════════════════════════════

  [12] start_http_sidecar() — optional, env-gated
       │
       └── Uvicorn server on UMRS_HTTP_PORT (default 8082)


Phase 4: Main Event Loop (Main Thread)
══════════════════════════════════════════

  [13] server_event_loop() [P]
       │
       └── Blocking loop on Control MessageQueue (ZMQ ROUTER)
            ├── PROCESS_OBJECTS → TaskManager worker threads
            ├── FILE → Data MessageQueue [P] (ZMQ PUSH/PULL)
            ├── ENROLL_USER → credential provisioning
            └── ... (other command dispatch)
```

---

## 2. Thread Ownership Map

| Thread Name | Owner | Lifecycle | Components Hosted |
|-------------|-------|-----------|-------------------|
| **Main thread** | `server_umrs.main()` | Process lifetime | Control MQ loop, TaskManager dispatch, config |
| **umrs-durable-jobs** | `DurableJobRuntime` | Daemon; dies with process | asyncio loop, Scheduler, Monitor, JobStore, Reconciler |
| **umrs-http-sidecar** | `start_http_sidecar()` | Daemon; optional | Uvicorn (FastAPI/Starlette) REST API |
| **ThreadPool workers** | `ThreadPoolManager` | Pool lifetime | S3 uploads/downloads, embedding inference |

### Thread Communication

```
Main Thread                    Durable-Jobs Thread           HTTP Sidecar Thread
     │                               │                            │
     │── call_soon_threadsafe() ────►│                            │
     │                               │                            │
     │◄── _startup_complete.set() ───│                            │
     │                               │                            │
     │── shutdown coroutine ────────►│                            │
     │                               │                            │
     │                               │◄── shared db_pool ────────│
     │                               │                            │
     │── ThreadPool.submit() ───►[worker threads]                 │
```

---

## 3. Transport Authority

| Transport | Socket Type | Owner | Port | Class |
|-----------|-------------|-------|------|-------|
| Control plane | ZMQ ROUTER | Main thread (server) | BASE_PORT (5555) | **[P]** |
| Data plane | ZMQ PUSH/PULL | Main thread (server) | BASE_PORT+1..N | **[P]** |
| HTTP sidecar | TCP (Uvicorn) | HTTP sidecar thread | UMRS_HTTP_PORT (8082) | **[P]** |
| Postgres | TCP (asyncpg) | Durable-jobs thread | DB_PORT (5432) | **[P]** |

### Transport Invariants

- ZMQ control plane: ROUTER socket (server) ↔ DEALER socket (client)
- ZMQ data plane: PUSH (sender) → PULL (receiver)
- Both ZMQ channels are **PERMANENT** — never scheduled for retirement
- HTTP sidecar is additive (does not replace ZMQ; provides REST API for Gateway)
- `UMRS_TUNNEL_MODE=true` → ZMQ binds `127.0.0.1` only (SSH boundary)

---

## 4. Reconciliation Authority

| Reconciliation Type | Owner | Trigger | Scope |
|-------------------|-------|---------|-------|
| Boot crash recovery | `Reconciler` [P] | UMRS process start | All stale attempts from previous instance |
| Status reconciliation | `reconcile_status_on_startup()` | UMRS process start | S3 bucket orphans vs StatusStore |
| Attempt retry | `JobStore.mark_attempt_failed()` [P] | Runtime (on failure) | Single attempt → job requeue |
| Model download resume | `Reconciler` (model-specific) [P] | UMRS process start | In-progress downloads |

### Reconciliation Invariants

1. **Single authority**: Only the Reconciler marks attempts LOST at boot
2. **No backend cleanup**: Reconciler never cancels external jobs (Slurm/K8s)
3. **Idempotent**: Safe to call multiple times (processes each attempt once)
4. **Instance-scoped**: Only reconciles attempts from OTHER instance_ids

---

## 5. Scheduler Authority

| Decision | Owner | Mechanism | Class |
|----------|-------|-----------|-------|
| Job claiming | `Scheduler` [P] | Postgres `next_run_at <= now()` poll | Periodic (1s) |
| Attempt creation | `Scheduler` [P] | Advisory lock → create attempt | Per-job exclusive |
| Backend selection | `backend_factory` [D] | Job type → backend mapping | Config-driven |
| Retry backoff | `JobStore` [P] | Exponential backoff calculation | Per-job policy |
| Burst detection | `Scheduler` [P] | `_consecutive_failures >= 3` | Metrics emission |

### Scheduler Invariants

1. **Single scheduler per process**: No distributed scheduling
2. **Advisory-lock exclusivity**: Only one UMRS instance schedules a given job
3. **Backend-agnostic**: Scheduler doesn't know Slurm vs K8s vs Compose
4. **Non-blocking lock**: `pg_try_advisory_lock` — never waits, just skips

---

## 6. Persistence Authority

| Data | Owner | Storage | Class |
|------|-------|---------|-------|
| Job state machine | `JobStore` [P] | Postgres | Durable |
| Attempt history | `JobStore` [P] | Postgres | Durable |
| Advisory locks | `JobStore` [P] | Postgres (session-level) | Transient |
| Task status (legacy) | `StatusStore` | Disk JSON | Semi-durable |
| Thread tracking | `ThreadDict` | Disk JSON | Semi-durable |
| S3 credentials | `perform_initial_config()` | Filesystem (`~/.s3cfg_*`) | Durable |
| Model registry | `ModelStore` [P] | Postgres | Durable |
| Server config | `json_config` | JSON file | Durable |
| Metrics counters | `METRICS` | In-memory (Prometheus) | Volatile |

### Persistence Invariants

1. **Postgres is authoritative** for all job/attempt state
2. **S3 is authoritative** for all job output artifacts
3. **Disk cache** (StatusStore) is advisory only; rebuilt from S3 on reconciliation
4. **In-memory metrics** are fire-and-forget; loss is acceptable

---

## 7. Metrics Ownership

| Metric Category | Emitter | Class | Storage |
|----------------|---------|-------|---------|
| Job lifecycle (created, succeeded, failed) | `JobStore` [P] | Counter | Prometheus |
| Retry events (retry_attempt, retry_burst) | `Scheduler` [P] | Counter | Prometheus |
| Reconciler outcomes | `Reconciler` [P] | Counter | Prometheus |
| Transport errors/timeouts | `MessageQueue` [P] | Counter | Prometheus |
| Bridge events (startup, shutdown) | `DurableJobRuntime` [B] | Counter | Prometheus |
| Monitor check results | `AttemptMonitor` [P] | Counter | Prometheus |
| Backend submission time | `Scheduler` [P] | Histogram | Prometheus |

### Metrics Invariants

1. **Fire-and-forget**: Metric emission never blocks business logic
2. **Graceful fallback**: If `prometheus_client` unavailable, no-op stubs used
3. **No metric = no retirement**: Components without metrics cannot be retired

---

## 8. Config Authority

| Config Domain | Source | Owner | Reload? | Class |
|--------------|--------|-------|---------|-------|
| Site topology | `site.yaml` / env vars | Admin | No (boot-only) | **[P]** |
| Job behavior | `JobStore` defaults + job spec | UMRS | No | **[P]** |
| Backend selection | `SiteConfig.execution_targets` | Admin | No (boot-only) | **[P]** |
| ZMQ binding | `UMRS_TUNNEL_MODE` + `BASE_PORT` | Admin | No (boot-only) | **[P]** |
| HTTP sidecar | `UMRS_HTTP_SIDECAR_ENABLED` | Admin | No (boot-only) | **[P]** |
| Model paths | `SiteConfig.model_root` | Admin | No (boot-only) | **[P]** |
| Postgres DSN | `UMRS_DB_DSN` | Admin | No (boot-only) | **[P]** |
| Scheduler interval | `DurableOrchestrationService` constructor | Code | No | **[S]** |
| Monitor interval | `DurableOrchestrationService` constructor | Code | No | **[S]** |

### Config Invariants

1. **Boot-only**: No hot-reload of any configuration
2. **Env-first**: Environment variables override file-based config
3. **No overwrite**: `SiteConfig.inject_env()` never overwrites existing env vars
4. **Single source of truth**: Each config domain has exactly one authority

---

## 9. Component Permanence Map

### Permanent Infrastructure [P]

| Component | Location | Why Permanent |
|-----------|----------|---------------|
| MessageQueue (ZMQ) | `umrs/core/MessageQueue.py` | Architectural invariant (mutation safety rule) |
| JobStore | `umrs/jobs/store.py` | Durable state machine |
| Scheduler | `umrs/jobs/scheduler.py` | Job execution authority |
| Monitor | `umrs/jobs/monitor.py` | Attempt status authority |
| Reconciler | `umrs/jobs/reconciler.py` | Crash recovery authority |
| Control MQ loop | `server_umrs.py` (main loop) | Client command dispatch |
| HTTP sidecar | `umrs/server/http_sidecar.py` | Gateway REST interface |
| Site config | `umrs/shared/loaded_config_profiles.py` | Deployment topology |

### Compatibility Bridges [B]

| Component | Location | Purpose | Retirement |
|-----------|----------|---------|-----------|
| DurableJobRuntime | `umrs/server/durable_job_runtime.py` | Gen1↔Gen2 thread bridge | NEVER (permanent by policy) |
| job_tracking_handler | `umrs/server/job_tracking_handler.py` | ZMQ → durable job dispatch | NEVER (permanent by policy) |

### Transitional Abstractions [T]

| Component | Location | Retires When |
|-----------|----------|-------------|
| DTO mirror files | `umrs/skills/schema.py`, `umrs/recipes/schema.py`, etc. | Step 5 convergence |
| dto_equivalence_check | `tools/dto_equivalence_check.py` | After DTO mirrors removed |

### Operational Singletons [S]

| Component | Location | Constraint |
|-----------|----------|-----------|
| DurableOrchestrationService | `umrs/orchestrator/durable.py` | One per UMRS process |
| JobService | `umrs/jobs/service.py` | One per UMRS process |
| ThreadPoolManager | `server_umrs.py` | One per UMRS process |
| Embedding backend | `umrs/inference/embeddings.py` | One per UMRS process |

### Dynamically Loaded [D]

| Component | Location | Activation |
|-----------|----------|-----------|
| Slurm backend | `umrs/orchestrator/slurm.py` | `execution_authority=slurm` in site config |
| K8s backend | `umrs/orchestrator/k8s.py` | `execution_authority=k8s` in site config |
| Compose backend | `umrs/orchestrator/compose.py` | `execution_authority=compose` in site config |
| Vision adapters | `umrs/vision_service/default_adapters/` | Adapter registry auto-discovery |
| Inference models | `umrs/inference/loading.py` | Dynamic import by model type |

---

## 10. Shutdown Sequence

```
Signal received (SIGTERM/SIGINT)
  │
  ▼
[1] DurableJobRuntime.shutdown()
     ├── Send shutdown coroutine to event loop
     ├── Wait 15s for graceful stop
     ├── Cancel pending asyncio tasks
     ├── Stop event loop
     ├── Join thread (timeout)
     └── Close database pool
  │
  ▼
[2] HTTP Sidecar stops (daemon thread dies with process)
  │
  ▼
[3] Control MessageQueue closes
     ├── socket.close(linger=0)
     └── context.term()
  │
  ▼
[4] ThreadPool workers complete or are interrupted
  │
  ▼
[5] Process exits
```

### Shutdown Invariants

1. **Durable state committed**: All Postgres-committed state survives
2. **In-flight work lost**: Uncommitted operations are abandoned
3. **Reconciler recovers**: Next boot reconciles any orphaned attempts
4. **No backend cleanup**: External jobs (Slurm/K8s) continue running

---

## 11. Safety Implications for Future Work

### Safe Modifications (Low Risk)

- Adding new job types (new backend factory entries)
- Adding new metrics emissions
- Adding new HTTP sidecar endpoints
- Adding new config keys (with defaults)
- Adding new reconciler checks

### Requires Design Review

- Changing thread model (adding/removing threads)
- Changing scheduler algorithm (claim ordering, concurrency)
- Changing persistence authority (new state stores)
- Changing transport protocol (new socket types)
- Changing boot order (dependency graph changes)

### Forbidden Modifications

- Removing ZMQ transport
- Removing DurableJobRuntime bridge
- Removing advisory lock mechanism
- Changing execution_authority after job creation
- Adding hot-reload to boot-only config
- Running multiple schedulers per process
