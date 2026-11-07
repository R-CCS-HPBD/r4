# ADR-001: ZMQ Data-Plane + HTTP Control-Plane Coexistence

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-07 (tunnel mode binding), SI-06 (wire-only inter-layer)

## Context

UMRS uses two transport protocols simultaneously:

- **ZMQ** (via `core/MessageQueue.py`): Handles binary data transfer, S3 object
  processing, and real-time HPC-local file operations. Runs on the main thread
  in a synchronous request-reply loop.
- **HTTP** (via `api/` FastAPI routes + `server/http_sidecar.py`): Handles
  control-plane operations — job CRUD, model registry, endpoint management, MCP
  registration. Runs on daemon threads.

A contributor might assume this is incomplete migration (ZMQ → HTTP) and attempt
to consolidate onto a single transport.

## Decision

Both transports are permanent and serve different purposes:

| Concern | Transport | Reason |
|---------|-----------|--------|
| File transfer (large payloads) | ZMQ | Streaming binary, no HTTP overhead |
| S3 object processing | ZMQ | Low-latency, direct memory access |
| Job lifecycle CRUD | HTTP | Standard REST, Gateway-compatible |
| Model registry | HTTP | Standard REST, Gateway-compatible |
| Endpoint provisioning | HTTP | Standard REST, async-friendly |
| MCP registration | HTTP | Standard REST, Gateway-compatible |

## Consequences

- `server_umrs.py` runs both a ZMQ loop (main thread) and HTTP sidecar (daemon thread)
- `DurableJobRuntime` bridges ZMQ commands to durable job operations
- ZMQ tunnel mode (`UMRS_TUNNEL_MODE=true`) solves **connectivity** on HPC clusters where ZMQ ports are firewall-blocked: it binds `127.0.0.1` only and SSH port-forwarding is the only path to reach the server. It is **not** required in direct-connect deployments (k8s, cloud VPC).
- ZMTP CURVE (`UMRS_ZMQ_AUTH_TYPE=curve`) solves **authentication**: cryptographic mutual auth + encryption at the ZMQ transport layer, recommended for direct-connect deployments where ZMQ ports are network-exposed. CURVE does not replace Gateway policy or UMRS command authorization.
- Tunnel mode and CURVE are complementary: HPC deployments bind loopback + optionally add CURVE for defense-in-depth; direct-connect deployments skip tunnel mode and use CURVE for auth.
- Gateway communicates with UMRS via HTTP only; Gateway never uses ZMQ
- r4 client uses HTTP for control-plane, ZMQ for data-plane (file uploads)

## Alternatives Considered

- **HTTP-only**: Would require HTTP multipart for large file transfers. Adds
  overhead, complexity, and latency for HPC-local operations.
- **ZMQ-only**: Would require custom request routing. HTTP is the standard for
  control-plane operations and integrates naturally with Gateway, LiteLLM, etc.
- **gRPC**: Considered but adds a dependency and build step (protobuf). ZMQ is
  already deployed and proven on RCCS.
