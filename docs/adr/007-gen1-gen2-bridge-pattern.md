# ADR-007: Gen1/Gen2 Bridge Coexistence Pattern

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** ADR-001 (dual transport)

## Context

UMRS has two architectural generations running simultaneously:

- **Gen1** (synchronous ZMQ): The original server loop in `server_umrs.py`.
  Handles S3 object processing, file transfers, real-time embedding, and
  legacy client commands. Runs on the main thread.
- **Gen2** (async durable jobs): The modern orchestration system in `jobs/`,
  `orchestrator/`, `api/`. Handles job lifecycle, endpoint provisioning, model
  registry. Runs on daemon threads.

Bridge components connect Gen1 to Gen2:
- `DurableJobRuntime` starts the Gen2 async loop alongside Gen1
- `DurableJobCommandBackend` translates ZMQ JOB_* commands into durable job operations
- Handler dispatchers (`job_tracking_handler.py`, `model_registry_handler.py`,
  `workflow_registry_handler.py`) route ZMQ commands to Gen2 services

A contributor might assume this bridge pattern is incomplete migration and attempt
to remove Gen1 or consolidate everything into Gen2.

## Decision

Gen1 and Gen2 coexist indefinitely. The bridge is **load-bearing architecture**,
not tech debt.

### Why Gen1 Cannot Be Removed

1. **ZMQ data-plane**: Binary file transfers over ZMQ are faster than HTTP for
   HPC-local operations. The S3 processing loop runs on ZMQ.
2. **Real-time inference**: Embedding and object processing threads are managed
   by Gen1's thread pool with backpressure (`core/thread_pool.py`).
3. **Existing clients**: `client_umrs.py` and r4's ZMQ transport use JOB_*,
   MODEL_*, WORKFLOW_* commands over ZMQ.

### Why Gen2 Cannot Replace Gen1

Gen2 is HTTP/async-based and handles control-plane operations naturally. But
ZMQ's binary streaming capabilities (zero-copy, multi-part messages) have no
HTTP equivalent without significant complexity.

## Consequences

- `server_umrs.py` remains the main entry point, running the ZMQ loop
- `DurableJobRuntime` starts as a daemon thread alongside the ZMQ loop
- Bridge handlers translate between the two worlds
- New **control-plane** features go into Gen2 (HTTP API + durable jobs)
- New **data-plane** features continue to use Gen1 (ZMQ)
- The bridge handlers cannot be removed while ZMQ clients exist

## Alternatives Considered

- **Full migration to HTTP**: Would require replacing ZMQ binary streaming with
  HTTP multipart/chunked, losing zero-copy performance. Not justified.
- **Full migration to ZMQ**: Would require implementing REST-like routing in
  ZMQ. HTTP is the standard and integrates with Gateway, LiteLLM, etc.
- **gRPC for data-plane**: Would add protobuf dependency and build step. ZMQ
  is simpler and already deployed.
