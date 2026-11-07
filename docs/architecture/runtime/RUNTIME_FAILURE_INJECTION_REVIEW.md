# Runtime Failure Injection Review

*Generated: 2026-05-13 — Architecture Stewardship Phase*

## Purpose

This document classifies every runtime failure mode in the UMRS durable job
infrastructure by recovery category. It enables operators to understand what
fails safely, what requires restart, and what requires manual intervention.

---

## Classification Categories

| Category | Definition | Operator Action |
|----------|-----------|-----------------|
| **SAFE_DEGRADATION** | System continues operating with reduced capability | Monitor; no action required |
| **RESTART_REQUIRED** | Process must restart to recover full functionality | Restart UMRS process |
| **OPERATOR_INTERVENTION** | Automated recovery insufficient; manual action needed | Follow runbook |
| **UNDEFINED_BEHAVIOR** | No explicit handling; outcome unpredictable | Requires code hardening |

---

## 1. Scheduler Failure Recovery

### Component: `umrs/src/umrs/jobs/scheduler.py`

| Failure Mode | Category | Behavior | Recovery Path |
|-------------|----------|----------|---------------|
| Backend submission fails | SAFE_DEGRADATION | Attempt marked FAILED; job requeued with 30s backoff | Automatic retry via `mark_attempt_failed()` |
| Advisory lock acquisition fails | SAFE_DEGRADATION | Job skipped this cycle; retried next scheduler tick | Automatic (next cycle) |
| Postgres connection lost during claim | RESTART_REQUIRED | `claim_next_jobs()` raises; scheduler loop catches, sleeps, retries | Postgres must recover; scheduler auto-retries |
| Backend factory raises for unknown job_type | UNDEFINED_BEHAVIOR | Exception propagates from `backend_factory(job.job_type)` uncaught within scheduling path | **Gap**: No try/except wrapping backend_factory call |
| 3+ consecutive submission failures | SAFE_DEGRADATION | `_consecutive_failures` counter triggers `METRICS.retry_burst()` emission | Monitor metric; investigate backend health |
| Scheduler tick takes longer than interval | SAFE_DEGRADATION | Next tick delayed; no job starvation (eventual processing) | Monitor scheduling latency metric |

### Scheduler Burst Detection

```
consecutive_failures < 3  →  normal retry (silent)
consecutive_failures >= 3 →  METRICS.retry_burst() emitted
consecutive_failures reset on successful submission
```

**Undefined gap**: `backend_factory()` call at scheduling time has no dedicated
exception handler. If the factory itself raises (e.g., misconfigured job type),
the job fails without proper attempt-state transition.

---

## 2. Reconciler Retry Propagation

### Component: `umrs/src/umrs/jobs/reconciler.py`

| Failure Mode | Category | Behavior | Recovery Path |
|-------------|----------|----------|---------------|
| Stale attempt found (different instance_id) | SAFE_DEGRADATION | Marked LOST; requeued if retries remain | Automatic |
| Retries exhausted after crash | SAFE_DEGRADATION | Job marked FAILED with `"exhausted retries after crash"` | Terminal state; operator reviews failed jobs |
| Postgres unavailable at boot | RESTART_REQUIRED | `reconcile_on_startup()` raises; DurableJobRuntime fails to start | Must restart after Postgres recovers |
| Partial reconciliation (error mid-loop) | SAFE_DEGRADATION | Per-attempt try/except; errors collected in `result.errors` | Unprocessed attempts retried next boot |
| Orphaned backend jobs (Slurm jobs still running) | OPERATOR_INTERVENTION | No backend cleanup performed; job runs to completion in parallel with new attempt | Manual `scancel` if resources wasted |
| Model download recovery | SAFE_DEGRADATION | HuggingFace downloads resume; others retry from scratch | Automatic (logged with recovery mode) |

### Reconciler Invariants

1. **Idempotent**: Already-processed attempts return early (no double-marking)
2. **No backend cleanup**: Reconciler NEVER calls `scancel`/`kubectl delete`
3. **Postgres-atomic**: All transitions in single transaction
4. **Order-independent**: Each stale attempt processed independently

### Reconciler Boot-Failure Impact

```
Postgres DOWN at boot
  → reconcile_on_startup() raises
  → DurableJobRuntime._startup() catches BaseException
  → _startup_error set, _startup_complete signaled
  → Main thread sees startup failure
  → UMRS process exits (or blocks if no exit handler)
```

---

## 3. Monitor Timeout Handling

### Component: `umrs/src/umrs/jobs/monitor.py`

| Failure Mode | Category | Behavior | Recovery Path |
|-------------|----------|----------|---------------|
| Status check returns "unknown" | SAFE_DEGRADATION | No action taken; retried next poll cycle | Automatic (next 5s cycle) |
| Status check raises exception | SAFE_DEGRADATION | Exception caught per-attempt; logged; continues to next | Automatic (next cycle) |
| Monitor stop timeout (10s) | SAFE_DEGRADATION | Task cancelled via `task.cancel()`; shutdown proceeds | Clean shutdown |
| Status checker hangs indefinitely | UNDEFINED_BEHAVIOR | Entire monitor cycle blocks; no per-check timeout | **Gap**: No `asyncio.wait_for()` on individual checks |
| Backend unreachable during check | SAFE_DEGRADATION | Backend status returns error; attempt stays in current state | Retried next cycle |
| Endpoint registry inconsistency | UNDEFINED_BEHAVIOR | Endpoint disappears between check and commit | **Gap**: No compare-and-swap on endpoint state |
| Many active attempts slow cycle | SAFE_DEGRADATION | Cycle takes longer than interval; next cycle delayed | Monitor scaling (no immediate fix needed) |

### Monitor Cycle Anatomy

```
Every 5 seconds:
  1. Query SUBMITTED/RUNNING attempts
  2. For each attempt:
     a. call status_checker.check_status()  ← CAN HANG (no timeout)
     b. dispatch on result status
     c. update persistent state
  3. Sleep until next interval
```

**Critical gap**: Individual `check_status()` calls have no timeout wrapper.
A single hanging backend check blocks all other attempt monitoring.

---

## 4. MessageQueue Reconnect Logic

### Component: `umrs/src/umrs/core/MessageQueue.py`

| Failure Mode | Category | Behavior | Recovery Path |
|-------------|----------|----------|---------------|
| Send timeout (zmq.Again) | SAFE_DEGRADATION | `transport_error` + `transport_timeout` metrics emitted; RuntimeError raised to caller | Caller must retry or fail gracefully |
| Recv timeout (zmq.Again) | SAFE_DEGRADATION | Returns `(None, None)` without raising; caller must check | Caller interprets as "no message" |
| ZMQError on send | SAFE_DEGRADATION | `transport_error` metric emitted; RuntimeError raised | Caller handles |
| Server restart while client connected | RESTART_REQUIRED | No automatic reconnect; next send/recv times out | Client process must reconnect or restart |
| HWM (high water mark) reached | UNDEFINED_BEHAVIOR | Behavior depends on socket type (PUSH blocks, DEALER drops) | **Gap**: No monitoring of queue depth |
| Multipart frame mismatch | SAFE_DEGRADATION | Parsing fails; returns error result | Caller sees malformed response |
| Socket closure during transfer | SAFE_DEGRADATION | File transfer cleanup in finally block; temp file removed | Caller retries transfer |
| Linger=0 on abrupt shutdown | UNDEFINED_BEHAVIOR | Queued messages may be lost silently | **Gap**: No delivery confirmation for fire-and-forget sends |

### Reconnect Architecture

**There is NO automatic reconnect in the current MessageQueue implementation.**

Design rationale:
- ZMQ control plane is server-initiated (ROUTER socket)
- Client connections are established once at startup
- If server restarts, clients must be restarted (by design — crash recovery
  is handled at the job/attempt level, not the transport level)
- The durable job system provides the retry semantics; transport is stateless

### Timeout Configuration

| Socket Type | Default Timeout | Behavior on Timeout |
|-------------|----------------|-------------------|
| Control (ROUTER) | 1000ms | Server-side: recv returns None |
| Data (PUSH/PULL) | 8000ms | Send: raises RuntimeError |
| File transfer | 8000ms | Same as data |

---

## 5. Bridge Degradation Behavior

### Component: `umrs/src/umrs/server/durable_job_runtime.py`

| Failure Mode | Category | Behavior | Recovery Path |
|-------------|----------|----------|---------------|
| Startup timeout (>30s) | RESTART_REQUIRED | `shutdown()` called; `DurableJobRuntimeError` raised | Fix root cause (Postgres, schema); restart |
| Startup exception | RESTART_REQUIRED | Error stored in `_startup_error`; re-raised to caller | Fix root cause; restart |
| Shutdown timeout (>15s) | OPERATOR_INTERVENTION | `bridge_event("shutdown_timeout")` emitted; raises | May have orphaned async tasks |
| Double shutdown call | UNDEFINED_BEHAVIOR | Second `shutdown()` may deadlock if first timed out | **Gap**: No re-entrance guard beyond `_lock` |
| Pending tasks on shutdown | SAFE_DEGRADATION | Tasks cancelled; in-flight work lost but jobs are durable | Jobs requeued on next boot via reconciler |
| Pool closure failure | SAFE_DEGRADATION | Exception suppressed in finally block; logged | Non-blocking; process exits cleanly |
| Thread death (unhandled exception) | RESTART_REQUIRED | Daemon thread exits; async loop stops; scheduler/monitor halt | Restart UMRS process |

### Bridge Lifecycle State Machine

```
CREATED
  │
  ├── start() ──► STARTING ──► RUNNING
  │                   │
  │                   ├── startup_error ──► FAILED (terminal)
  │                   └── startup_timeout ──► FAILED (terminal)
  │
  └── shutdown() ──► STOPPING ──► STOPPED
                        │
                        └── shutdown_timeout ──► OPERATOR_INTERVENTION
```

### Thread Safety Model

- `_lock` (threading.Lock) protects all state transitions
- `_startup_complete` (threading.Event) synchronizes boot
- Cross-thread communication via `call_soon_threadsafe()`
- **Invariant**: Only one thread modifies runtime state at a time

---

## 6. Startup Partial-Failure Survivability

### Boot Dependency Chain

```
Postgres available
  └── JobStore connects
       └── Reconciler runs (boot recovery)
            └── Scheduler starts (asyncio task)
                 └── Monitor starts (asyncio task)
                      └── HTTP Sidecar starts (optional, separate thread)
```

### Partial-Failure Matrix

| Failed Component | Impact | System State | Recovery |
|-----------------|--------|--------------|----------|
| Postgres DOWN | FATAL | Boot fails entirely | RESTART_REQUIRED after Postgres recovers |
| Schema migration fails | FATAL | Boot fails | OPERATOR_INTERVENTION (fix schema) |
| Reconciler finds errors | NON-FATAL | Boot continues; errors logged | Retry on next boot |
| Scheduler task creation fails | FATAL | Boot halts in JobService.startup() | RESTART_REQUIRED |
| Monitor task creation fails | FATAL | Boot halts in JobService.startup() | RESTART_REQUIRED |
| HTTP Sidecar bind fails | NON-FATAL | ZMQ still works; HTTP unavailable | Gateway cannot proxy HTTP routes |
| Control MQ bind fails | FATAL | Main loop cannot start | OPERATOR_INTERVENTION (port conflict) |
| Embedding model load fails | NON-FATAL | Embedding requests fail; other services work | SAFE_DEGRADATION (deferred load) |

### Design Principle

The system is **fail-fast at boot, fail-safe at runtime**:
- During boot: Any critical dependency failure stops the process immediately
- During runtime: Individual operation failures are isolated per-attempt

---

## 7. Undefined Behavior Inventory

These scenarios have no explicit handling and could produce unpredictable results:

| # | Scenario | Location | Risk Level | Proposed Mitigation |
|---|----------|----------|-----------|-------------------|
| 1 | ~~`backend_factory()` raises unexpected exception~~ | `scheduler.py` scheduling path | ~~Medium~~ | ✅ Fixed: Wrapped in try/except; emits `backend_factory_error` metric |
| 2 | ~~Status checker hangs indefinitely~~ | `monitor.py` check loop | ~~Medium~~ | ✅ Fixed: `asyncio.wait_for(timeout=60)` added |
| 3 | ZMQ HWM reached on PUSH socket | `MessageQueue.py` data send | Low | Existing `transport_timeout` metric covers this; `zmq.Again` = HWM signal |
| 4 | Linger=0 message loss | `MessageQueue.py` shutdown | Low | Acceptable for control; document for data |
| 5 | ~~Double shutdown on DurableJobRuntime~~ | `durable_job_runtime.py` | ~~Low~~ | ✅ Fixed: `_shutdown_called` re-entrance guard added |
| 6 | Endpoint state race in monitor | `monitor.py` provision check | Low | Requires optimistic compare-and-swap (design needed) |
| 7 | Clock skew affecting `next_run_at` | `store.py` job scheduling | Low | Use Postgres `now()` instead of Python `datetime.utcnow()` |

### Mitigation Priority

**Implemented (2026-05-13):**
- Item 1: `backend_factory()` try/except with `backend_factory_error` metric
- Item 2: `asyncio.wait_for(timeout=STATUS_CHECK_TIMEOUT)` on each status check
- Item 5: `_shutdown_called` flag prevents double-shutdown deadlock

**Future consideration (requires design):**
- Items 6-7 are low-probability edge cases

---

## 8. Failure Propagation Chains

### Chain A: Job Submission Failure

```
User submits job via Gateway
  → Gateway → UMRS API → JobStore.create_job()
      → Job: PLANNED → QUEUED
  → Scheduler.claim_next_jobs()
      → Advisory lock acquired
      → backend_factory(job_type) returns backend
      → backend.submit_job() FAILS
          → mark_attempt_failed(retry_delay=30)
          → _consecutive_failures++
          → [if >= 3] METRICS.retry_burst()
      → Job: QUEUED (with next_run_at = now + 30s)
  → [30s later] Scheduler claims again
      → Retry loop continues until max_attempts exhausted
```

### Chain B: UMRS Crash Mid-Job

```
UMRS process crashes while jobs are RUNNING
  → All in-flight async tasks terminated
  → ZMQ sockets close (linger=0, messages may be lost)
  → Postgres connections released (advisory locks freed)
  → Backend jobs continue running (Slurm/K8s unaffected)

  [UMRS restarts]
  → DurableJobRuntime._startup()
      → reconcile_on_startup()
          → Find attempts with old instance_id
          → Mark as LOST
          → Requeue if retries remain
      → Scheduler resumes
      → Monitor resumes
  → Jobs re-execute from scratch (attempt-scoped isolation)
```

### Chain C: Postgres Failure During Runtime

```
Postgres becomes unreachable during normal operation
  → Scheduler.claim_next_jobs() raises
      → Caught in _run_loop(); logged; sleep; retry
  → Monitor check raises on state update
      → Caught per-attempt; logged; continues
  → New job submissions fail at API level
      → Gateway returns 503

  [Postgres recovers]
  → Next scheduler/monitor cycle succeeds
  → No data loss (all state is in Postgres)
  → System self-heals
```

### Chain D: ZMQ Transport Failure

```
Client sends command via ZMQ control channel
  → MessageQueue.send_multipart() times out (zmq.Again)
      → RuntimeError raised
      → Client receives error response
  → Control message lost (linger=0)
  → Job state in Postgres is UNCHANGED (no partial writes)

  [Recovery]
  → Client retries the request
  → Server processes it fresh (idempotent via job_id)
```

---

## 9. Operational Recommendations

### Monitoring Alerts

| Metric | Threshold | Action |
|--------|-----------|--------|
| `retry_burst` events | > 0 in 5min window | Investigate backend health |
| `bridge_event{type="shutdown_timeout"}` | Any occurrence | Check for hanging async tasks |
| `transport_timeout` count | > 10/min | Check network/ZMQ health |
| `reconciler_run{outcome="error"}` | Any occurrence | Review reconciler logs |
| Job age > max expected duration | Per job type | May indicate monitoring gap |

### Recovery Runbook Summary

| Scenario | Immediate Action | Root Cause Investigation |
|----------|-----------------|------------------------|
| UMRS won't start | Check Postgres connectivity | Schema migration status |
| Jobs stuck in QUEUED | Check scheduler logs | Backend availability |
| Jobs stuck in RUNNING | Check monitor logs | Status checker health |
| ZMQ timeouts | Check network/firewall | Socket state, HWM |
| Bridge shutdown timeout | Force-kill process | Identify hanging tasks |

---

## 10. Design Philosophy

The UMRS runtime failure model follows these principles:

1. **Durable state is truth**: All job state lives in Postgres. Process crashes
   lose only in-flight operations, never committed state.

2. **Attempt-scoped isolation**: Each attempt writes to its own S3 prefix.
   Parallel attempts (from crash recovery) cannot corrupt each other.

3. **No backend cleanup on crash**: The system relies on isolation rather than
   cleanup. Orphaned backend jobs consume resources but cannot cause data
   corruption.

4. **Fail-fast boot, fail-safe runtime**: Critical failures at startup halt the
   process. Runtime failures are isolated per-operation and retried.

5. **Transport is stateless**: ZMQ provides delivery, not durability. The job
   system provides retry/recovery semantics independent of transport state.
