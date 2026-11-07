# Grafana Dashboards — UMRS Metrics

These dashboards visualise `umrs_*` metrics scraped by Prometheus from the
UMRS HTTP sidecar `/metrics` endpoint (`umrs/src/umrs/server/http_sidecar.py`).

All metrics are defined in `umrs/src/umrs/metrics.py` (the `UMRSMetrics` class)
and originate from the running UMRS server — no Python Gateway is involved
(umrs-front uses NGINX + auth sidecar).

## Access

```
URL:       http://<front-host>:<grafana-port>
Username:  <admin-user>
Password:  (set via umrs_front_grafana_admin_password in group_vars)
```

All dashboards are under the **RiVault** folder.

---

## Dashboards

### 0. vLLM Runtime Signals

- **UID**: `vllm-kv-cache`
- **Refresh**: 30s
- **Purpose**: Real-time vLLM health — KV cache utilization, request queue depth, and token throughput.
- **Source**: Direct scrape of vLLM `/metrics` endpoints via the `vllm_kv_cache` Prometheus job.

| Section | Panel | What it tells you |
|---------|-------|-------------------|
| **Cache & Queue** | GPU KV Cache Usage | KV cache fill — >95% means eviction pressure |
| | Requests Running vs Waiting | Queue backlog — waiting >0 means GPU is saturated |
| **Throughput** | Prompt Throughput | Input tokens/sec |
| | Generation Throughput | Output tokens/sec |
| **Capacity Snapshot** | Queued Requests | Real-time queue depth |
| | Max KV Cache Usage | Peak cache utilization across instances |
| | Running Requests | Active in-flight requests |

**When to look**: During inference. Rising queue depth or cache pressure
indicates the need for more GPUs or shorter context windows.

---

### 1. UMRS Platform Overview

- **UID**: `umrs-overview`
- **Refresh**: 30s
- **Purpose**: At-a-glance health of both UMRS clusters and key subsystems.

| Section | Panel | Query | What it tells you |
|---------|-------|-------|-------------------|
| **System Health** | UMRS RCCS | `up{job="umrs-rccs"}` | Is the RCCS Cloud UMRS server alive? (1 = up, 0 = down) |
| | UMRS AI4S | `up{job="umrs-ai4s"}` | Is the AI4S UMRS server alive? |
| | Reconciler Runs | `sum(umrs_reconciler_runs_total)` | How many reconciler passes have executed? |
| | Scheduler Cycles | `sum(umrs_scheduler_cycles_total)` | How many scheduler poll cycles? |
| | Retry Attempts | `sum(umrs_retry_attempts_total)` | Total job retries — rising = infrastructure trouble |
| | Reconciler Backlog | `umrs_reconciler_backlog_size` | Stale attempts found at boot — should be 0 |
| **Scheduler & Reconciler** | Scheduler Cycle Duration | `umrs_scheduler_cycle_duration_seconds_bucket` P50/P95 | How fast the scheduler loop completes |
| | Reconciler Duration | `umrs_reconciler_duration_seconds_bucket` P50/P95 | How fast reconciler passes complete |
| **Bridge & Transport** | Bridge Events Rate | `umrs_bridge_events_total` by bridge & event | Start/stop/failure events on DurableJobRuntime bridge |
| | Bridge Commands Rate | `umrs_bridge_commands_total` by bridge & command_class | JOB_SUBMIT, JOB_LIST etc. dispatched through ZMQ bridge |

**When to look**: Daily. Check that RCCS and AI4S are green (= 1). A non-zero
backlog or rising retry count signals problems.

---

### 2. UMRS LLM Metrics

- **UID**: `umrs-llm`
- **Refresh**: 30s
- **Purpose**: Token consumption, request rates, and latency for LLM inference
  requests proxied through UMRS → LiteLLM → vLLM.

| Section | Panel | Query | What it tells you |
|---------|-------|-------|-------------------|
| **Token Usage** | Total Requests | `sum(umrs_llm_requests_total)` | Total LLM chat/completion requests |
| | Prompt Tokens | `sum(umrs_llm_tokens_total{direction="prompt"})` | Total input tokens sent to models |
| | Completion Tokens | `sum(umrs_llm_tokens_total{direction="completion"})` | Total output tokens generated |
| | Total Tokens | `sum(umrs_llm_tokens_total)` | Sum of prompt + completion tokens |
| | Median Latency | `histogram_quantile(0.50, …)` P50 | Typical round-trip time for LLM requests |
| | P95 Latency | `histogram_quantile(0.95, …)` P95 | Worst-case tail latency — >30s is a problem |
| **Token Usage Over Time** | Token Rate by Model | `rate(umrs_llm_tokens_total...)` by model | Tokens/second per model (prompt + completion stacked) |
| | Tokens per Hour | `increase(umrs_llm_tokens_total...[1h])` | Tokens consumed in the last hour |
| **Request & Latency** | Request Rate by Model | `rate(umrs_llm_requests_total...)` by model | Requests/second — streaming vs. oneshot |
| | LLM Request Latency Distribution | `umrs_llm_request_duration_seconds_bucket` P50/P95/P99 | Latency percentiles over time |
| **Per-Cluster Breakdown** | Request Rate by Cluster | `rate(umrs_llm_requests_total)` by `umrs_cluster` | Which cluster is handling requests? |
| | Token Rate by Cluster | `rate(umrs_llm_tokens_total)` by `umrs_cluster` | Token throughput per cluster |

**When to look**: Hourly or after provisioning new models. Spikes in P95 latency
may indicate GPU contention. Token counts feed billing calculations (also
available via `/gw/usage/me`).

**Metric emission path**: `umrs/api/llm_usage.py:record_usage()` → `METRICS.llm_tokens()` → Prometheus.

---

### 3. UMRS Jobs & Bridge

- **UID**: `umrs-jobs`
- **Refresh**: 30s
- **Purpose**: Durable job lifecycle and bridge/transport health. Tracks jobs
  from submission through completion or failure.

| Section | Panel | Query | What it tells you |
|---------|-------|-------|-------------------|
| **Job Lifecycle** | Jobs Submitted | `sum(umrs_job_total{event="submitted"})` | Total jobs submitted |
| | Jobs Failed | `sum(umrs_job_total{event="failed"})` | Total terminal failures |
| | Retry Attempts | `sum(umrs_retry_attempts_total)` | Total retries — high = infrastructure issue |
| | Reconciler Backlog | `umrs_reconciler_backlog_size` | Stale attempts at boot — should be 0 |
| **Jobs Over Time** | Job Event Rate | `rate(umrs_job_total...)` by event | Submitted/failed/completed rate |
| | Jobs Submitted by Backend (hourly) | `increase(umrs_job_total{event="submitted"}[1h])` by backend | Which backend (slurm/k8s/compose) is busy? |
| **Bridge Events & Commands** | Bridge Events Rate | `rate(umrs_bridge_events_total...)` by event | gen1_gen2_runtime bridge start/stop/error events |
| | Bridge Commands Rate | `rate(umrs_bridge_commands_total...)` by command_class | JOB_SUBMIT, JOB_LIST, JOB_CANCEL etc. |
| **Transport Health** | Transport Errors | `rate(umrs_transport_errors_total...)` by transport & operation | ZMQ send/recv errors — rising = network trouble |
| | Transport Reconnects & Timeouts | `rate(umrs_transport_reconnects_total...)` + timeouts | Transport layer flapping |

**When to look**: After deploys or when jobs are failing. Rising bridge events
or transport errors indicate infrastructure instability.

---

### 4. UMRS Scheduler

- **UID**: `umrs-scheduler`
- **Refresh**: 30s
- **Purpose**: Deep dive into scheduler and reconciler performance. Diagnose
  job starvation, slow polling, and reconciler bottlenecks.

| Section | Panel | Query | What it tells you |
|---------|-------|-------|-------------------|
| **Scheduler Overview** | Scheduler Cycles | `sum(umrs_scheduler_cycles_total)` | Total poll cycles |
| | Cycle Duration P50 | `histogram_quantile(0.50, …)` P50 | Typical cycle time |
| | Cycle Duration P95 | `histogram_quantile(0.95, …)` P95 | Worst-case cycle time — >5s is a problem |
| | Job Failures | `sum(umrs_job_total{event="failed"})` | Total failures across all backends |
| | Reconciler Backlog | `umrs_reconciler_backlog_size` | Stale attempts — non-zero means crash recovery needed |
| | Job Retries | `sum(umrs_retry_attempts_total)` | Total retries |
| **Scheduler Performance** | Scheduler Cycle Duration | `umrs_scheduler_cycle_duration_seconds_bucket` P50/P95/P99 | Cycle latency over time |
| | Jobs Claimed Rate | `rate(umrs_scheduler_jobs_claimed_count[5m])` | Jobs/second being picked up |
| **Scheduling Delays** | Job Schedule Delay | `umrs_scheduler_delay_seconds_bucket` P50/P95 | Time between job ready and job scheduled |
| | Reconciler Duration | `umrs_reconciler_duration_seconds_bucket` P50/P95 | Reconciler pass latency |
| **Scheduler Cycles Over Time** | Scheduler Cycles by Outcome | `rate(umrs_scheduler_cycles_total...)` by outcome | ok vs. submission_error vs. empty cycles |
| | Reconciler Items Processed (hourly) | `increase(umrs_reconciler_items_total[1h])` by action | How many items requeued, marked_lost, etc. |

**When to look**: Weekly, or when users report slow job submission. High
schedule delays or cycle duration spikes mean the scheduler is backing up.

---

## Metric Families Used

| Metric Family | Labels | Emitted By |
|--------------|--------|------------|
| `umrs_job_total` | `backend`, `event` | `umrs/server/job_tracking_handler.py`, `umrs/server/durable_job_runtime.py` |
| `umrs_reconciler_runs_total` | `outcome` | UMRS reconciler loop |
| `umrs_reconciler_duration_seconds` | `outcome` | UMRS reconciler |
| `umrs_reconciler_items_total` | `action` | UMRS reconciler |
| `umrs_reconciler_backlog_size` | — | Reconciler at boot |
| `umrs_scheduler_cycles_total` | `outcome` | UMRS scheduler loop |
| `umrs_scheduler_cycle_duration_seconds` | — | Scheduler poll duration |
| `umrs_scheduler_delay_seconds` | — | Job ready-to-scheduled delay |
| `umrs_scheduler_jobs_claimed` | — | Jobs claimed per cycle |
| `umrs_bridge_events_total` | `bridge`, `event` | `umrs/server/durable_job_runtime.py` |
| `umrs_bridge_commands_total` | `bridge`, `command_class` | `umrs/server/job_tracking_handler.py` |
| `umrs_transport_errors_total` | `transport`, `operation` | ZMQ transport layer |
| `umrs_transport_reconnects_total` | `transport` | ZMQ transport layer |
| `umrs_transport_timeouts_total` | `transport`, `operation` | ZMQ transport layer |
| `umrs_retry_attempts_total` | `backend` | UMRS job retry |
| `umrs_llm_requests_total` | `model`, `streaming` | `umrs/api/llm_usage.py` |
| `umrs_llm_tokens_total` | `model`, `direction` | `umrs/api/llm_usage.py` |
| `umrs_llm_request_duration_seconds` | `model` | `umrs/api/llm_usage.py` |

## How Metrics Flow

```
UMRS Server (python process)
  │
  ├── scheduler loop     → METRICS.scheduler_cycle()     ┐
  ├── reconciler loop    → METRICS.reconciler_run()       │
  ├── job tracking       → METRICS.job_submitted()        ├── prometheus_client
  ├── bridge commands    → METRICS.bridge_command()       │   Counter / Histogram
  ├── transport layer    → METRICS.transport_error()      │   (in-process registry)
  ├── LLM usage record   → METRICS.llm_tokens()          ┘
  │
  └── /metrics endpoint  → prometheus_client.generate_latest()
                              ↓
                         Prometheus (podman container)
                         scrape every 15s at host.containers.internal:8000/8002
                              ↓
                         Grafana dashboards refresh every 30s
```

## Adding a New Dashboard

1. Create a JSON file in this directory following the [Grafana dashboard JSON model](https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/view-dashboard-json-model/).
2. Set `"datasource": {"type": "prometheus", "uid": "prometheus"}` on every panel.
3. Use only `umrs_*` metric names from the table above (no `rivault_*` — those are defined in the Python Gateway which is not deployed).
4. Give it a unique `"uid"` (e.g., `"umrs-foobar"`).
5. Keep the JSON in `observability/grafana/dashboards/` (Grafana provisions this directory) and restart Grafana in your deployment, for example:
   ```bash
   docker compose -f observability/compose.yaml restart grafana
   ```
   On a managed deployment, land the file in the repository and let the deployment tooling restart Grafana; do not copy files into a host checkout by hand.
