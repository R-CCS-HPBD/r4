# Transport Parity Matrix — R4 Platform

> **Purpose:** Document operational parity (or intentional divergence) between the
> two communication transports: HTTP (Gateway-proxied) and ZeroMQ (direct).
>
> **Scope:** Every operation that can be exercised through both transports.
> Columns identify how each transport handles auth identity propagation, audit,
> error taxonomy, retry policy, and size limits.
>
> **Status:** DRAFT — operational parity is enforced by review memory (not automated
> checks). The matrix below records intentional divergences (marked **INTENTIONAL**)
> and gaps that need remediation (marked **GAP**).

---

## 1. Operation Parity Table

| # | Operation | Auth Identity Propagation | Audit Event | Error Taxonomy | Retry Policy | Size Limit |
|---|-----------|--------------------------|-------------|----------------|--------------|------------|
| 1 | **Job submit** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id from socket | HTTP: audit_store.insert<br>ZMQ: audit_store.insert | HTTP: JSON `{"error": "msg"}`<br>ZMQ: JSON string `{"error": "msg"}` | HTTP: client retries on 5xx<br>ZMQ: no auto-retry (caller handles) | HTTP: 10 GB (NGINX)<br>ZMQ: file chunked (no hard limit) |
| 2 | **Job status** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: no audit needed (read)<br>ZMQ: no audit needed | HTTP: 200/404/500<br>ZMQ: status code + message | HTTP: client retries on timeout<br>ZMQ: caller retries | HTTP: small JSON payload<br>ZMQ: small JSON payload |
| 3 | **Job cancel** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("cancel")<br>ZMQ: audit_store.insert("cancel") | HTTP: 200/404/500<br>ZMQ: status code | HTTP: client retries on 5xx<br>ZMQ: caller retries | N/A |
| 4 | **Endpoint list** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: no audit (read)<br>ZMQ: no audit | HTTP: 200/401/500<br>ZMQ: status code | HTTP: retries on 5xx<br>ZMQ: caller retries | HTTP: JSON array<br>ZMQ: JSON array |
| 5 | **Endpoint provision** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("provision")<br>ZMQ: audit_store.insert("provision") | HTTP: 200/400/409/500<br>ZMQ: status code | HTTP: client retries on 5xx<br>ZMQ: caller retries | HTTP: JSON body (small)<br>ZMQ: JSON body (small) |
| 6 | **Endpoint deprovision** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("deprovision")<br>ZMQ: audit_store.insert("deprovision") | HTTP: 200/202/404/500<br>ZMQ: status code | HTTP: retries on 5xx<br>ZMQ: caller retries | N/A |
| 7 | **File upload** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("upload")<br>ZMQ: audit_store.insert("upload") | HTTP: 413/500<br>ZMQ: size rejection | HTTP: retry upload<br>ZMQ: retry chunk | HTTP: 10 GB NGINX cap<br>ZMQ: chunked, no hard cap |
| 8 | **File download** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("download")<br>ZMQ: audit_store.insert("download") | HTTP: 404/500<br>ZMQ: status code | HTTP: retry download<br>ZMQ: retry chunk | HTTP: 10 GB NGINX cap<br>ZMQ: chunked, no hard cap |
| 9 | **Chat / LLM request** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("chat")<br>ZMQ: audit_store.insert("chat") | HTTP: 400/429/500/503<br>ZMQ: status code | HTTP: client retries on 429/503<br>ZMQ: caller retries | HTTP: JSON + streaming<br>ZMQ: binary data plane |
| 10 | **Model list** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: no audit (read)<br>ZMQ: no audit | HTTP: 200/500<br>ZMQ: status code | HTTP: retries on 503<br>ZMQ: caller retries | HTTP: JSON array<br>ZMQ: JSON array |
| 11 | **Context pack** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("context_pack")<br>ZMQ: audit_store.insert("context_pack") | HTTP: 400/500<br>ZMQ: status code | HTTP: retries on 500<br>ZMQ: caller retries | HTTP: JSON body (~50 KB)<br>ZMQ: binary data plane |
| 12 | **RAG query** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("rag_query")<br>ZMQ: audit_store.insert("rag_query") | HTTP: 400/500<br>ZMQ: status code | HTTP: retries on 500<br>ZMQ: caller retries | HTTP: small query body<br>ZMQ: small query body |
| 13 | **MCP tool call** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("mcp_call")<br>ZMQ: audit_store.insert("mcp_call") | HTTP: 400/500<br>ZMQ: status code | HTTP: retries on 500<br>ZMQ: caller retries | HTTP: JSON body<br>ZMQ: binary data plane |
| 14 | **Workflow submit** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("workflow_submit")<br>ZMQ: audit_store.insert("workflow_submit") | HTTP: 200/400/500<br>ZMQ: status code | HTTP: retries on 5xx<br>ZMQ: caller retries | HTTP: JSON body<br>ZMQ: JSON body |
| 15 | **Policy / governance check** | HTTP: JWT + `X-Project-ID`<br>ZMQ: client_id | HTTP: audit_store.insert("policy_check")<br>ZMQ: audit_store.insert("policy_check") | HTTP: 200/403/500<br>ZMQ: status code | HTTP: no retry (gate decision)<br>ZMQ: no retry (gate decision) | HTTP: small JSON<br>ZMQ: small JSON |
| 16 | **Health / readiness** | HTTP: none (public)<br>ZMQ: none (public) | HTTP: no audit<br>ZMQ: no audit | HTTP: 200/503<br>ZMQ: status code | HTTP: retries on 503<br>ZMQ: caller retries | N/A |

---

## 2. Intentional Divergences

The following differences between transports are **by design** and are tracked
here for auditability.

| Operation | Divergence | Rationale |
|-----------|------------|-----------|
| **Job submit / file I/O** | ZMQ supports unbounded file transfers via binary data plane; HTTP is capped at 10 GB by NGINX | ZMQ is for HPC-local large payload transfers; HTTP is for Gateway-mediated policy-enforced transfers |
| **Chat / LLM streaming** | HTTP supports SSE/streaming JSON; ZMQ uses binary data plane | HTTP streaming is the primary UX path; ZMQ streaming is for internal high-throughput |
| **Retry policy** | HTTP retries are handled by the client on 5xx; ZMQ has no auto-retry — the caller decides | ZMQ callers have domain knowledge about what's safe to retry (e.g., file uploads vs. status checks) |
| **Health check** | HTTP `/health` and `/readiness` are public endpoints accessible without auth | No auth is needed for infrastructure health probes |

---

## 3. Identified Gaps

| GAP | Description | Priority | Notes |
|-----|-------------|----------|-------|
| **G-001** | ZMQ audit events lack correlation IDs (HTTP has `X-Request-ID`) | P1 | Gap in distributed tracing parity |
| **G-002** | HTTP `X-Project-ID` propagation is not guaranteed over ZMQ when proxied through Gateway | P1 | May allow cross-tenant leakage in multi-tenant ZMQ deployments |
| **G-003** | No automated test verifying identical audit rows across both transports | P2 | Task 12 follow-up: add parametrized test |
| **G-004** | ZMQ error messages not normalized to match HTTP error taxonomy | P2 | Caller must parse different error formats |

---

## 4. Test Parity Plan

To convert the matrix into an automated check:

1. **Parametrized E2E test** — for each operation in Section 1, perform the same request over both transports (HTTP via Gateway, ZMQ direct) in the compose harness.
2. **Assert identical audit rows** — verify the audit store contains equivalent entries (minus transport tag).
3. **Assert equivalent error codes** — for the same induced failure (e.g., auth rejection), both transports must return equivalent error signals.
4. **Record intentional divergences** — gap G-001, G-002, etc. are tracked separately and become individual issues.

### Operations to Cover

| Priority | Operation | ZMQ Route | HTTP Route |
|----------|-----------|-----------|------------|
| P0 | Job submit + status | `TASK_CMD` / `TASK_STATUS` | `/umrs/jobs/` |
| P0 | Chat / LLM request | `CHAT_CMD` | `/umrs/chat/` |
| P0 | File upload + download | `FILE_CMD` (data plane) | `/umrs/files/` |
| P1 | Endpoint provision | `ENDPOINT_CMD` | `/umrs/endpoints/` |
| P1 | RAG query | `RAG_CMD` | `/umrs/rag/` |
| P1 | MCP tool call | `MCP_CMD` | `/umrs/mcp/` |
| P2 | Policy check | `POLICY_CMD` | `/umrs/policy/` |
| P2 | Workflow submit | `WORKFLOW_CMD` | `/umrs/workflows/` |

---

## 5. References

- [Communication Architecture](COMMUNICATION_ARCHITECTURE.md) — full transport details
- [Runtime Topology Reference](RUNTIME_TOPOLOGY_REFERENCE.md) — deployment topology
- [Operational Determinism Gaps](OPERATIONAL_DETERMINISM_GAPS.md) — known gaps
- [Mutation Safety Rules](../../.github/instructions/mutation-safety.instructions.md) — transport invariants

---

## 6. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-11 | Initial draft — 16 operations, 4 intentional divergences, 4 gaps | AI4S Platform hardening session |
