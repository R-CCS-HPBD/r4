# Operational Determinism Gaps

*Generated: 2026-05-13 — Architecture Stewardship Phase*

## Purpose

This document identifies areas where UMRS runtime behavior is not fully
deterministic — where environment coupling, implicit assumptions, timing
sensitivity, or race conditions could produce different outcomes across
deployments or restarts.

---

## Classification Categories

| Category | Definition | Action Required |
|----------|-----------|-----------------|
| **BENIGN** | Non-determinism is harmless or by-design | Document only |
| **MONITOR** | Could mask issues; add observability | Add metrics/logging |
| **OPERATIONAL_RISK** | Could cause incorrect behavior in production | Add guardrails |
| **ARCHITECTURAL_RISK** | Fundamental design tension requiring resolution | Design review |

---

## 1. Environment Variable Coupling

| Variable | Component | Coupling Type | Risk | Category |
|----------|-----------|--------------|------|----------|
| `UMRS_DB_DSN` | JobStore, Reconciler | Hard dependency | Boot fails without it | BENIGN |
| `UMRS_TUNNEL_MODE` | MessageQueue | Binding address changes | Silent security change if unset | MONITOR |
| `UMRS_HTTP_SIDECAR_ENABLED` | HTTP Sidecar | Feature gate | Gateway breaks if unset on remote | OPERATIONAL_RISK |
| `UMRS_HTTP_PORT` | HTTP Sidecar | Port selection | Collision if default used on shared host | MONITOR |
| `LITELLM_MASTER_KEY` | Mini-RiVault | Auth token | Empty key = unauthenticated LiteLLM | OPERATIONAL_RISK |
| `VLLM_MODEL_PATH` | Serve-model script | Model location | Wrong path = boot failure | BENIGN |
| `TRANSFORMERS_OFFLINE` | Inference container | Network isolation | Missing = potential egress | ARCHITECTURAL_RISK |
| `SLURM_PARTITION` | Slurm backend | Resource allocation | Wrong partition = job rejection | BENIGN |
| `BASE_PORT` (implied) | MessageQueue | Port allocation | Collision on shared hosts | MONITOR |

### Environment Coupling Mitigations

| Risk | Current Mitigation | Gap |
|------|-------------------|-----|
| `UMRS_TUNNEL_MODE` unset | Defaults to `false` (binds 0.0.0.0) | ✅ Fixed: Warning emitted at ZMQ socket bind time |
| `LITELLM_MASTER_KEY` empty | Site.yaml allows `""` with `.env` override | ✅ Fixed: Boot assertion warns if empty |
| `TRANSFORMERS_OFFLINE` missing | Documented in instructions; set in Slurm script | No runtime assertion in container |
| `UMRS_HTTP_SIDECAR_ENABLED` unset | Defaults to enabled (`"true"`) | No gap (doc was incorrect; default is enabled) |

---

## 2. Implicit Filesystem Assumptions

| Assumption | Component | Risk | Category |
|-----------|-----------|------|----------|
| Model path exists and is readable | `SiteConfig.model_root` | Boot proceeds; first inference fails | OPERATIONAL_RISK |
| S3 config file exists at `~/.s3cfg` | `perform_initial_config()` | S3 operations fail | BENIGN |
| Per-user S3 config writable | User provisioning | Enrollment fails silently | MONITOR |
| Scratch directory writable | Mini-RiVault container | Container start fails | BENIGN |
| Singularity image exists | Slurm script | Job fails at container start | BENIGN |
| `/tmp` has sufficient space | ZMQ file transfer | Partial file writes | MONITOR |
| Config directory exists | `server_umrs.main()` | Boot fails with clear error | BENIGN |
| Log directory writable | Logging setup | Silently drops logs | MONITOR |

### Filesystem Mitigation Recommendations

| Gap | Proposed Additive Mitigation | Risk Level |
|-----|------------------------------|-----------|
| Model path not validated at boot | Add existence check in `DurableOrchestrationService.startup()` | Low |
| `/tmp` space not checked before file transfer | Add available-space metric | Low |
| Log directory creation | Already handled by logging framework | N/A |

---

## 3. Startup Ordering Dependencies

| Dependency | Must Be Ready Before | Failure Mode | Category |
|-----------|---------------------|-------------|----------|
| Postgres | Reconciler, JobStore, ModelStore | Boot halts | BENIGN (fail-fast) |
| Control MQ bind | Main event loop | Boot halts | BENIGN (fail-fast) |
| Reconciler completion | Scheduler start | Jobs may double-execute | BENIGN (sequential in code) |
| Scheduler start | Monitor start | No jobs to monitor | BENIGN (sequential in code) |
| HTTP Sidecar bind | Gateway routing | Gateway gets 502 | OPERATIONAL_RISK |
| Embedding model load | First embedding request | Request fails (deferred OK) | BENIGN |

### Ordering Invariants

The current boot sequence is **strictly sequential within Phase 2** (durable
runtime thread). This eliminates ordering races for critical components:

```
Postgres → Schema → JobStore → Reconciler → Scheduler → Monitor
```

**Gap**: HTTP Sidecar starts independently (Phase 3) after Phase 2 completes.
Gateway may attempt requests before sidecar is ready.

**Proposed mitigation**: Add `/health` endpoint that returns 503 until
DurableJobRuntime signals ready. Gateway checks health before routing.

---

## 4. Retry Timing Nondeterminism

| Source | Component | Nondeterminism Type | Category |
|--------|-----------|-------------------|----------|
| Backoff calculation | `JobStore.get_backoff_delay()` | Exponential with jitter | BENIGN (by design) |
| Scheduler poll interval | `Scheduler._run_loop()` | Fixed 1s + processing time | BENIGN |
| Monitor poll interval | `AttemptMonitor._check_loop()` | Fixed 5s + processing time | BENIGN |
| `retry_delay` parameter | `mark_attempt_failed()` | Caller-specified (30s default) | BENIGN |
| Postgres lock wait | Advisory lock acquisition | Non-blocking (instant fail) | BENIGN |
| ZMQ send timeout | MessageQueue | Fixed 8000ms | BENIGN |

### Timing Analysis

The retry system has bounded nondeterminism:
- **Minimum retry interval**: `retry_delay` (default 30s)
- **Maximum retry interval**: `max_backoff` (exponential cap)
- **Jitter range**: ±10% of calculated backoff

This is intentional — jitter prevents thundering-herd retries across multiple
jobs. No operational risk.

---

## 5. Timeout Interaction Risks

| Timeout A | Timeout B | Interaction | Category |
|-----------|-----------|------------|----------|
| DurableJobRuntime startup (30s) | Postgres connect timeout | If Postgres is slow, startup may timeout before connection establishes | MONITOR |
| Monitor check cycle (5s interval) | Backend status check (no timeout) | Hanging check delays all other monitoring | OPERATIONAL_RISK |
| ZMQ send timeout (8s) | Client request timeout (varies) | Client may timeout before ZMQ does, leaving orphaned message | BENIGN |
| DurableJobRuntime shutdown (15s) | Pending task cancellation | Tasks may not cancel within 15s → shutdown timeout | MONITOR |
| Scheduler interval (1s) | Job advisory lock | Lock failure on every tick for contested job | BENIGN |

### Critical Timeout Gap

**Monitor check without per-operation timeout**:

The AttemptMonitor calls `status_checker.check_status()` with no timeout wrapper.
If a backend status check hangs (e.g., unreachable Slurm controller), the entire
monitor cycle blocks indefinitely.

**Impact**: All other running attempts stop being monitored.

**Proposed mitigation** (additive, low-risk):
```python
# In monitor._check_attempt():
result = await asyncio.wait_for(
    self._status_checker.check_status(attempt, job),
    timeout=60.0
)
```

This is NOT implemented here — it requires a dedicated implementation PR with
tests.

---

## 6. Thread Lifecycle Coordination

| Coordination Point | Mechanism | Risk | Category |
|-------------------|-----------|------|----------|
| Main thread ↔ Durable thread boot | `_startup_complete` Event | None (clean synchronization) | BENIGN |
| Main thread → Durable thread shutdown | `call_soon_threadsafe()` + Future | Timeout if loop busy | MONITOR |
| Durable thread death | Daemon flag (dies with process) | Scheduler/Monitor silently stop | OPERATIONAL_RISK |
| HTTP Sidecar thread death | Daemon flag (dies with process) | Gateway gets 502 silently | OPERATIONAL_RISK |
| ThreadPool worker completion | `Future.result()` with timeout | Worker leak on timeout | MONITOR |

### Thread Death Detection Gap

If the `umrs-durable-jobs` thread dies due to an unhandled exception in the
event loop, the main thread has no mechanism to detect this. The scheduler and
monitor silently stop processing.

**Current behavior**: The daemon thread exits; main thread continues processing
ZMQ commands that don't require the durable runtime.

**Observable symptom**: Jobs stuck in QUEUED/RUNNING indefinitely.

**Proposed monitoring** (additive):
- Emit `bridge_event("durable_jobs", "thread_alive")` periodically from the durable thread
- Monitor absence of this metric as an alert condition

---

## 7. Race Condition Exposure Windows

| Race | Window | Impact | Category |
|------|--------|--------|----------|
| Two UMRS instances claim same job | Advisory lock acquisition | Second instance fails lock → skips | BENIGN (by design) |
| Monitor and Scheduler both touch same attempt | Sequential in same event loop | None (cooperative scheduling) | BENIGN |
| HTTP Sidecar read during Scheduler write | Different threads, Postgres isolation | Postgres MVCC handles correctly | BENIGN |
| Reconciler marks LOST while backend succeeds | Boot-time only (pre-scheduler) | Backend result ignored; new attempt created | MONITOR |
| Config file modified during boot | No file locking | Partial read possible | BENIGN (extremely unlikely) |
| Two file transfers to same path | ZMQ data channel | Last-write-wins on filesystem | MONITOR |

### Race Window Analysis

**Most significant race**: Reconciler marks an attempt LOST at boot, but the
backend job actually completed successfully between crash and restart.

**Impact**: The successful result is in the attempt-scoped S3 prefix, but the
attempt is marked LOST. A new attempt is created and re-executes the work.

**Mitigation**: This is by-design (attempt-scoped isolation). The "wasted" work
is the cost of crash-safe recovery. No data corruption occurs.

---

## 8. Summary of Operational Risks

| # | Gap | Severity | Proposed Additive Mitigation |
|---|-----|----------|------------------------------|
| 1 | Monitor check hangs block all monitoring | High | `asyncio.wait_for(timeout=60)` wrapper |
| 2 | Durable thread death undetected | Medium | Periodic liveness metric emission |
| 3 | HTTP Sidecar ready before DurableRuntime | Medium | Health endpoint with readiness gate |
| 4 | `LITELLM_MASTER_KEY` empty at boot | Medium | Non-empty validation in site config |
| 5 | `TRANSFORMERS_OFFLINE` not asserted in container | Medium | Startup assertion in inference container |
| 6 | Model path not validated at boot | Low | Existence check during startup |
| 7 | File transfer `/tmp` space | Low | Available-space metric |

### Implementation Priority

**Immediate (safe, additive, testable)**:
- None implemented in this review (documentation only)

**Next stewardship cycle**:
- Items 1-3 are highest-value operational improvements
- Items 4-5 are security hardening (already documented in security audit)
- Items 6-7 are defensive improvements

---

## 9. Design Philosophy

The UMRS operational model accepts bounded nondeterminism as a feature:

1. **Retry jitter is intentional** — prevents thundering herds
2. **Poll-based monitoring is intentional** — simpler than event-driven for Slurm
3. **No hot-reload is intentional** — boot-only config eliminates runtime race classes
4. **Sequential boot is intentional** — eliminates startup ordering races
5. **Fail-fast boot is intentional** — surfaces misconfig immediately

The gaps identified above are refinement opportunities, not fundamental design
problems. The system is operationally sound for its current deployment scale.
