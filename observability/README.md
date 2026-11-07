# Observability Stack (O-001 ~ O-004)

Platform monitoring, logging, and distributed tracing for RiVault.

This directory currently ships the observability stack assets: Compose and
Kubernetes manifests, Grafana dashboards, and Prometheus alert rules. The
metric names below are emitted by the platform code, chiefly
[umrs/src/umrs/metrics.py](../umrs/src/umrs/metrics.py) (2026-11-07: the
Python gateway that used to emit the `rivault_*` series was removed by
ADR-014); the companion linter (`tools/check_metrics_coverage.py`) keeps the dashboards
and the code definitions in sync. As of 2026-04-25 all 22 dashboard
metrics have a code-side emitter.

> **Coverage check.** Run `python tools/check_metrics_coverage.py` from the
> repo root to see which dashboard metrics are still missing a code
> emitter. Exits 1 if any are missing, 0 if dashboards and code are in
> sync. The check runs in CI; new dashboard panels referencing a fresh
> metric name MUST add a constructor in
> [umrs/src/umrs/metrics.py](../umrs/src/umrs/metrics.py) (or the module
> that owns the metric) in the same change.

## Components

| Component | ID | Purpose | Port |
|-----------|-----|---------|------|
| Prometheus | O-001 | Metrics collection | 9090 |
| Grafana | O-002 | Dashboards & visualization | 3000 |
| Loki | O-003 | Centralized logging | 3100 |
| Jaeger | O-004 | Distributed tracing (**planned** — not yet in compose.yaml) | 16686 |

## Quick Start

### Docker Compose (Development)

```bash
# Start all observability services
docker compose -f compose.yaml up -d

# Access dashboards
open http://localhost:3000  # Grafana (admin/admin)
open http://localhost:9090  # Prometheus
open http://localhost:16686 # Jaeger
```

> **Note (bug.memo OBS2, 2026-04-25):** this stack runs on its own
> `rivault-observability` Docker network. To let Prometheus scrape the
> platform services in the e2e harness (which uses the `e2e` network),
> attach Prometheus to that network after startup:
>
> ```bash
> docker network connect e2e rivault-prometheus
> docker network connect e2e rivault-loki        # optional, for log shipping
> ```
>
> Alternatively, declare the `e2e` network as `external: true` in a local
> compose override and add it to the Prometheus / Loki / Jaeger services.

### Site-managed deployment

On a shared front host, bind the Grafana, Prometheus and Jaeger UIs to
`127.0.0.1` and reach them through an SSH tunnel; set a real Grafana admin
password (never the `admin` default):

```bash
ssh -L 3000:127.0.0.1:<grafana-port> -L 9090:127.0.0.1:<prometheus-port> \
    -L 16686:127.0.0.1:<jaeger-port> <front-host>
open http://localhost:3000   # Grafana
open http://localhost:9090   # Prometheus
open http://localhost:16686  # Jaeger
```

### Kubernetes

```bash
# Apply all manifests
kubectl apply -k k8s/

# Port forward to access locally
kubectl port-forward svc/grafana 3000:3000 -n observability
kubectl port-forward svc/prometheus 9090:9090 -n observability
kubectl port-forward svc/jaeger 16686:16686 -n observability
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Grafana (O-002)                          │
│                   Dashboards & Alerting                         │
└───────────┬───────────────────┬──────────────────┬──────────────┘
            │                   │                  │
            ▼                   ▼                  ▼
┌───────────────────┐  ┌───────────────┐  ┌───────────────────────┐
│ Prometheus (O-001)│  │ Loki (O-003)  │  │   Jaeger (O-004)      │
│    Metrics        │  │     Logs      │  │      Traces           │
└─────────▲─────────┘  └───────▲───────┘  └───────────▲───────────┘
          │                    │                      │
          │                    │                      │
    ┌─────┴─────┬─────────────┴──────────────────────┴─────┐
    │           │                                          │
    ▼           ▼                                          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ Gateway│ │  UMRS  │ │ LiteLLM│ │  vLLM  │ │postgres│ │ minio  │
└────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
```

## Metrics (Prometheus)

### Platform Metrics (Target Schema For Dashboards And Alerts)

> ADR-014 (2026-09-11): the rows below marked ~~struck~~ were emitted only by
> the retired Python gateway; they are retired until the NGINX+sidecar edge
> (or UMRS) instruments the equivalents — alert rules using them have been
> pruned from `observability/prometheus/alerts.yaml`. `rivault_llm_*` and
> `rivault_rag_*`/`rivault_job_*` remain the target schema.

| Metric | Type | Description |
|--------|------|-------------|
| ~~`rivault_gateway_requests_total`~~ | Counter | Total gateway requests |
| ~~`rivault_gateway_request_duration_seconds`~~ | Histogram | Request latency |
| ~~`rivault_endpoints_active`~~ | Gauge | Active inference endpoints |
| ~~`rivault_endpoints_provisioning`~~ | Gauge | Endpoints being provisioned |
| `rivault_llm_tokens_total` | Counter | Total tokens processed |
| ~~`rivault_llm_request_duration_seconds`~~ | Histogram | LLM request latency |
| `rivault_rag_queries_total` | Counter | Total RAG queries |
| `rivault_job_duration_seconds` | Histogram | Job execution time |

### vLLM Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `vllm:num_requests_running` | Gauge | Active requests |
| `vllm:num_requests_waiting` | Gauge | Queued requests |
| `vllm:kv_cache_usage_perc` | Gauge | KV cache utilization |
| `vllm:avg_prompt_throughput` | Gauge | Tokens/sec (input) |
| `vllm:avg_generation_throughput` | Gauge | Tokens/sec (output) |
| `vllm:request_success_total` | Counter | Successful requests |
| `vllm:num_preemption_total` | Counter | Preempted requests |
| `vllm:num_requests_swapped_total` | Counter | Swapped requests |

### LLM Error Metrics (LiteLLM)

| Metric | Type | Description |
|--------|------|-------------|
| `litellm_total_requests` | Counter | Total requests by `status_code`, `model` |
| `litellm_total_errors` | Counter | Total errors by `exception_status`, `model` |
| `litellm_remaining_requests_total` | Counter | Remaining requests in router |
| `litellm_deployment_failure_total` | Counter | Deployment failures |

**Error rate formula:**
```promql
sum(rate(litellm_total_errors[5m]))
  / sum(rate(litellm_total_requests[5m]))
```

**vLLM error rate formula:**
```promql
1 - (
  sum by (model_name) (rate(vllm:request_success_total[5m]))
  / sum by (model_name) (rate(vllm:request_success_total[5m]) offset 5m)
)
```

A dedicated `vllm_kv_cache` scrape job is defined in
`prometheus/prometheus.yaml`.  Point it at each vLLM instance's
`/metrics` endpoint to surface KV cache and queue data.

## Logging (Loki)

### Log Labels

| Label | Description |
|-------|-------------|
| `app` | Application name (gateway, umrs, vllm) |
| `env` | Environment (dev, staging, prod) |
| `project_id` | Project identifier |
| `user_id` | User identifier |
| `job_id` | Job identifier |
| `request_id` | Correlation ID |

### LogQL Examples

```logql
# All gateway errors in the last hour
{app="gateway"} |= "error" | json

# Slow LLM requests (>5s)
{app="gateway"} | json | line_format "{{.duration}}" | duration > 5s

# User activity
{app="gateway"} | json | user_id = "user-123"

# Job failures
{app="umrs"} |= "job_status=FAILED"
```

## Tracing (Jaeger)

### Span Names

| Service | Span | Description |
|---------|------|-------------|
| gateway | `gateway.request` | Root span for HTTP request |
| gateway | `gateway.auth` | Authentication check |
| gateway | `gateway.route` | Route selection |
| umrs | `litellm.proxy` | Upstream forwarding from UMRS to Mini-RiVault LiteLLM |
| umrs | `umrs.endpoint.provision` | Endpoint provisioning |
| umrs | `umrs.job.submit` | Job submission |
| umrs | `umrs.rag.query` | RAG query execution |

### Trace Propagation

When OpenTelemetry is enabled in the deployed services, W3C Trace Context
(`traceparent` header) is the intended correlation format.

```python
# Example: Add tracing header in requests
import opentelemetry.trace as trace

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("my_operation") as span:
    span.set_attribute("user_id", user_id)
    # ... operation
```

## Dashboards (Grafana)

### Available Dashboards

1. **Platform Overview** (`dashboards/platform-overview.json`)
   - Request rates, latency, error rates
   - Active endpoints and jobs
   - Resource utilization

2. **RiVault Overview** (`dashboards/rivault-overview.json`)
    - Platform-level RiVault request and endpoint views
    - Error-rate and throughput panels wired to the target metric schema

3. **LLM Performance** (`dashboards/rivault-llm.json`)
   - Token throughput
   - Queue depth
   - GPU cache utilization
   - Model-specific metrics

4. **Job Monitoring** (`dashboards/rivault-jobs.json`)
   - Job states and transitions
   - Duration histograms
   - Failure rates by type

5. **RAG Analytics** (`dashboards/rivault-rag.json`)
   - Query latency breakdown
   - Embedding throughput
   - Vector search performance

## Alerting

Prometheus alert rules in this directory assume the target metric schema above.
Validate instrumentation coverage in your deployment before enabling paging or
hard SLO thresholds.

### Critical Alerts

| Alert | Condition | Action |
|-------|-----------|--------|
| `GatewayHighErrorRate` | Error rate > 5% | Page on-call |
| `EndpointProvisioningFailed` | Provisioning timeout | Investigate backend |
| `LLMHighLatency` | p99 > 30s | Check GPU capacity |
| `DiskSpaceRunningLow` | <10% free | Expand storage |

### Alert Configuration

See `prometheus/alerts.yaml` for full alert definitions.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_RETENTION_TIME` | `15d` | Metrics retention |
| `LOKI_RETENTION_PERIOD` | `168h` | Log retention (7 days) |
| `JAEGER_TTL` | `72h` | Trace retention |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Initial admin password |

## Integration with Platform

### Gateway Integration

HISTORICAL EXAMPLE (ADR-014): the code below lived in the retired Python
gateway (`gateway/main.py`, removed 2026-09-11). Edge-side emission on the
NGINX+auth-sidecar stack is tracked under G-P11-2/G-P1-4 until implemented:

```python
# (retired) gateway/main.py
from prometheus_client import Counter, Histogram
from opentelemetry import trace

REQUEST_COUNT = Counter(
    "rivault_gateway_requests_total",
    "Total requests",
    ["method", "path", "status"]
)

REQUEST_LATENCY = Histogram(
    "rivault_gateway_request_duration_seconds",
    "Request latency",
    ["method", "path"]
)
```

### UMRS Integration

UMRS exports job and endpoint metrics:

```python
# In umrs/metrics.py
ENDPOINTS_ACTIVE = Gauge(
    "rivault_endpoints_active",
    "Active inference endpoints",
    ["model", "project_id"]
)

JOB_DURATION = Histogram(
    "rivault_job_duration_seconds",
    "Job execution time",
    ["job_type", "status"]
)
```

## Troubleshooting

### No Metrics in Prometheus

1. Check scrape targets: `http://prometheus:9090/targets`
2. Verify service discovery: Check pod annotations/labels
3. Check firewall rules for metrics port (default: 8000)

### Logs Not Appearing in Loki

1. Verify Promtail/Fluent Bit is running
2. Check log format (must be JSON or parseable)
3. Verify label selectors in Loki config

### Traces Missing

1. Check OpenTelemetry SDK initialization
2. Verify Jaeger collector is reachable
3. Check sampling rate (default: 1% in production)
