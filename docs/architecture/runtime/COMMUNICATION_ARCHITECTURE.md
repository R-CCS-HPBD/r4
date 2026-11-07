# Communication Architecture — R4 Platform

> This document provides a detailed explanation of every communication path in the
> R4 Platform, the transport method chosen for each, and the reasoning
> behind those choices across different deployment scenarios.

## Table of Contents

- [1. Communication Overview](#1-communication-overview)
- [2. Transport Methods](#2-transport-methods)
- [3. Deployment Scenarios](#3-deployment-scenarios)
- [4. Communication Catalogue](#4-communication-catalogue)
- [5. Operation-to-Transport Matrix](#5-operation-to-transport-matrix)
- [6. Configuration Reference](#6-configuration-reference)
- [7. Fallback and Resilience](#7-fallback-and-resilience)
- [8. Security Considerations](#8-security-considerations)
- [9. Decision Rationale](#9-decision-rationale)

---

## 1. Communication Overview

The platform has three principal communication layers.

```
┌────────────────────────────────────────────────────────────────────┐
│   User / Application Layer                                         │
│   (r4 CLI, r4 SDK, Jupyter, n8n workflows, MCP Apps)               │
└────────────────┬───────────────────────────┬───────────────────────┘
                 │  HTTP (Gateway)           │  ZeroMQ (direct)
                 ▼                           ▼
┌────────────────────────────┐  ┌────────────────────────────────────┐
│   Platform Gateway         │  │  UMRS Server (ZeroMQ)              │
│   (NGINX + Auth Sidecar)   │  │  Control: configurable port        │
│   Port 8080                │  │           (DEALER↔ROUTER)          │
│                            │  │  Data:    dynamic (PUSH↔PULL)      │
│  ┌──────────────────────┐  │  │                                    │
│  │ /gw/llm/*   → UMRS   │  │  │  Wire protocol:                    │
│  │ /gw/rag/*   → UMRS   │  │  │  "{CMD}:{client_id}:{payload}"     │
│  │ /gw/mcp/*   → UMRS   │  │  │                                    │
│  │ /gw/jobs/*  → UMRS   │  │  │  Response: JSON string             │
│  │ /gw/models/*→ UMRS   │  │  └────────────────────────────────────┘
│  │ /gw/workflows/*→UMRS │  │
│  │ /gw/ops/*   → UMRS   │  │
│  └──────────────────────┘  │
└────────────────┬───────────┘
                 │ HTTP (internal)
                 ▼
┌────────────────────────────┐
│   UMRS HTTP Sidecar        │
│   (FastAPI, port 8082)     │
│   Mounts: endpoints, jobs, │
│   llm, mcp, models,        │
│   policy, rag routers      │
└────────────────┬───────────┘
                 │
                 ▼
┌────────────────────────────┐
│   Mini-RiVault             │
│   (vLLM + LiteLLM + RAG    │
│    + MCP Servers)          │
│   Provisioned on compute   │
│   nodes via Slurm/K8s      │
└────────────────────────────┘
```

### Two Transports, One Protocol Surface

| Transport | Protocol | Socket Pattern | Suited For |
|-----------|----------|----------------|------------|
| **HTTP (Gateway)** | REST/JSON over TCP | Request–Response | Policy enforcement, auth, audit, multi-tenant isolation, browser/CLI access |
| **ZeroMQ (Direct)** | Custom wire format over TCP | DEALER↔ROUTER (control), PUSH↔PULL (data) | High-throughput file transfers, low-latency HPC-local commands, binary streaming |

Both transports ultimately talk to the same UMRS server — the Gateway proxies HTTP
to the UMRS FastAPI sidecar, while ZeroMQ connects directly to the UMRS control loop.

---

## 2. Transport Methods

### 2.1 HTTP via Platform Gateway

The Gateway is an NGINX reverse proxy with an authentication sidecar. It sits
between every client and the UMRS backend.

**What the Gateway provides:**

| Capability | Description |
|------------|-------------|
| **Authentication** | JWT verification, API key validation, project-scoped tokens |
| **Audit logging** | Every request is logged with correlation ID, user identity, and timestamp |
| **Rate limiting** | Per-user and per-project request throttling |
| **Routing** | Single entry point (`/gw/*`) fans out to UMRS, LiteLLM, MCP servers |
| **Multi-tenant isolation** | `X-Project-ID` header scopes every request to a tenant |
| **CORS / Browser security** | Handles preflight, origin checks for MCP Apps |
| **Upload size enforcement** | NGINX `client_max_body_size` controls (default 10 GB) |

**Request lifecycle:**

```
Client                    NGINX              Auth Sidecar         UMRS Sidecar
  │                         │                     │                    │
  ├─── POST /gw/rag/query ──►                     │                    │
  │                         ├─── Verify JWT ──────►                    │
  │                         │◄── 200 OK ──────────┤                    │
  │                         ├─── Proxy to /umrs/rag/query ────────────►│
  │                         │◄── 200 + RAG results  ───────────────────┤
  │◄── 200 + RAG results  ──┤                     │                    │
```

**When to use:** Default for all deployments. Required when auth, audit, rate
limiting, or multi-tenant isolation is needed.

### 2.2 ZeroMQ Direct Transport

UMRS exposes a ZeroMQ `ROUTER` socket on a configurable control port (default
5555, set via `UMRS_CONTROL_PORT`) and dynamically allocated `PULL` sockets
(data channel, negotiated via `CREATE_DATA_QUEUE`). The r4 SDK connects as a
`DEALER` client. During `r4 auth login`, the client auto-discovers the control
port and other ZMQ parameters via `GET /gw/ops/connect-info`.

**Wire protocol:**

Commands are sent as plain strings:

```
{COMMAND}:{client_id}:{JSON_payload}
```

Example:

```
JOB_LIST:alice:{"project_id":"example-project-c","status":"running","limit":10}
```

The server responds with a JSON string on the same control channel.

**Available commands (matching UMRS `TaskCmd`):**

| Command | Category | Description |
|---------|----------|-------------|
| `HAND_CHECK` | Connectivity | Server readiness ping |
| `STATUS` | Status | Query server/task status |
| `FILE` | Data | Upload files via data channel |
| `PROCESS_OBJECTS` | Data | Process S3 event notifications |
| `UPLOAD_WORKFLOW` | Workflow | Upload workflow zip to server |
| `LIST_WORKFLOWS` | Workflow | List registered workflows |
| `RUN_WORKFLOW` | Workflow | Trigger workflow execution |
| `MODEL_REGISTER` | Model Registry | Register an existing model |
| `MODEL_LIST` | Model Registry | List available models |
| `MODEL_DOWNLOAD` | Model Registry | Initiate model download job |
| `JOB_SUBMIT` | Job Tracking | Submit a durable job |
| `JOB_CANCEL` | Job Tracking | Cancel a running job |
| `JOB_LIST` | Job Tracking | List jobs with filters |
| `EXPORT_EMBEDDINGS` | Embeddings | Export embedding vectors |
| `WAIT_OUTPUT` | Processing | Block until output is ready |
| `RECOMPUTE` | Processing | Trigger recomputation |
| `UPDATE` | Processing | Trigger update cycle |
| `CHECK` | Processing | Run consistency check |
| `RELOAD` | Processing | Reload server configuration |

**Data channel protocol:**

For file transfers, the client first negotiates a data port via `CREATE_DATA_QUEUE`
on the control channel, then streams binary chunks using `PUSH`→`PULL`:

```
Client                              UMRS Server
  │                                      │
  ├── CREATE_DATA_QUEUE:alice ──────────►│
  │◄── tcp://server:5557 ────────────────┤  (allocated data port)
  │                                      │
  ├── FILE:alice:3 ─────────────────────►│  (3 files coming)
  │                                      │
  ├── [binary chunk 1] ───── PUSH ──────►│  (data channel, port 5557)
  ├── [binary chunk 2] ───── PUSH ──────►│
  ├── [__END_OF_FILE__] ──── PUSH ──────►│
  │  (repeat for each file)              │
  │                                      │
  │◄── ACKNOWLEDGE ──────────────────────┤  (control channel)
```

**When to use:**

- File uploads/downloads of large scientific data (CT scans, simulation output)
- HPC-local environments where Gateway is unnecessary overhead
- Operations requiring sustained TCP connections (embedding export)
- Environments with SSH tunnel constraints (tunnel mode)

### 2.3 Comparison Summary

| Aspect | HTTP (Gateway) | ZeroMQ (Direct) |
|--------|---------------|-----------------|
| Auth/AuthZ | ✅ JWT, API key, project scoping | ❌ None — trusts the network |
| Audit | ✅ All requests logged | ❌ No centralized audit |
| Rate limiting | ✅ Per-user/project throttling | ❌ None |
| Multi-tenant | ✅ Project isolation enforced | ❌ Client self-identifies |
| File transfer | ⚠️ HTTP multipart (size limits) | ✅ Chunked binary streaming |
| Latency | ~5-20 ms overhead (proxy hop) | ~1-2 ms (direct connection) |
| Connection model | Stateless (new connection per request) | Persistent (long-lived sockets) |
| Protocol overhead | HTTP headers, JSON serialization | Minimal (raw bytes + frame headers) |
| Browser support | ✅ Full (REST API) | ❌ Not applicable |
| Firewall friendly | ✅ Single port (8080) | ⚠️ Requires control port + dynamic data ports |

---

## 3. Deployment Scenarios

### 3.1 Cloud / Multi-Tenant (`R4_DEPLOY_MODE=cloud`)

```
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────────┐
│ User A   │──┐   │          │      │          │      │              │
│ (Proj X) │  │   │ Platform │  HTTP│   UMRS   │      │ Mini-RiVault │
│          │  ├──►│ Gateway  ├─────►│ Sidecar  ├─────►│  Cluster     │
│ User B   │  │   │ (8080)   │      │ (8082)   │      │  (K8s/Swarm) │
│ (Proj Y) │──┘   │          │      │          │      │              │
└──────────┘      └──────────┘      └──────────┘      └──────────────┘
                  ▲ Auth, audit,
                    rate limit,
                    tenant isolation
```

**Characteristics:**

- Multiple users and projects share the platform
- Users may not be fully trusted (external collaborators, students)
- Network may be partially untrusted (VPN, institutional network)
- Compute resources are shared and must be rationed
- Regulatory requirements for access logging

**Transport rule:** **ALL traffic goes through HTTP Gateway.** ZeroMQ is used
only for file uploads (for efficiency), NOT for command operations.

**Rationale:**

Every operation must pass through the Gateway's policy enforcement layer because:

- **Auth**: Users authenticate via JWT. The Gateway validates tokens before
  forwarding. ZeroMQ has no built-in authentication mechanism.
- **Audit**: Compliance requires a log of who accessed what. Gateway logs every
  request with user identity, timestamp, and correlation ID.
- **Tenant isolation**: `X-Project-ID` headers prevent User A from accessing
  User B's models, jobs, or RAG indexes. ZeroMQ's `client_id` is self-reported
  and trivially spoofable.
- **Rate limiting**: Shared GPU resources require per-user throttling. Gateway
  enforces `RATE_LIMIT_RPS` per user.

The one exception is file upload: large scientific files (multi-GB CT scans) are
impractical over HTTP multipart. The ZeroMQ data channel provides chunked binary
streaming, which is significantly faster and has no upload size limits from the
reverse proxy. The client still authenticates via Gateway for the upload ticket.

### 3.2 HPC-Local / Single-Tenant (`R4_DEPLOY_MODE=hpc-local`)

```
┌──────────┐      ┌──────────────┐      ┌──────────────┐
│ Login    │ ZMQ  │              │      │              │
│ Node     ├─────►│  UMRS Server │─────►│ Mini-RiVault │
│ (r4)     │      │  (ZMQ: ctrl) │      │ on compute   │
│          │      │  (HTTP: 8082)│      | node via     │
│          │      │              │      │ Slurm        │
└──────────┘      └──────────────┘      └──────────────┘
                  ▲ Direct connection,
                    no Gateway needed
```

**Characteristics:**

- Single research group or individual on dedicated HPC allocation
- All users are trusted (authenticated by Slurm/SSH)
- Network is fully trusted (InfiniBand, internal cluster network)
- Dedicated compute resources (Slurm partition, GPU allocation)
- No need for multi-tenant isolation
- Low-latency communication preferred

**Transport rule:** **Direct ZMQ to UMRS for all eligible operations.** Gateway
is either absent or bypassed. HTTP sidecar available as a fallback.

**What becomes redundant on HPC:**

| Gateway Capability | Why It's Redundant on HPC |
|-------------------|---------------------------|
| JWT authentication | Slurm authenticates users via `SLURM_JOB_USER`. SSH handles login-node identity. |
| Rate limiting | Resources are allocated by Slurm, not shared dynamically. A user's allocation is their budget. |
| CORS / browser headers | No browser clients on HPC login nodes. |
| TLS termination | Internal cluster network (InfiniBand/Omni-Path) is trusted, not exposed to the internet. |
| Multi-tenant isolation | Single-tenant. One user or research group per deployment. |
| API versioning | Direct SDK-to-UMRS contract, version matched at deploy time. |

**What remains valuable even on HPC:**

| Capability | Why It Still Matters |
|------------|---------------------|
| Audit logging | Research reproducibility — knowing which model was queried, when, and with what parameters. Solved by UMRS-side logging, not Gateway. |
| Service discovery | Knowing which compute node hosts the active Mini-RiVault. UMRS endpoint registry handles this. |
| Retry / circuit-breaker | Slurm jobs can fail. UMRS durable job tracking replaces Gateway retry logic. |

**Rationale for direct ZMQ:**

- **Lower latency**: Eliminates the NGINX→Auth→UMRS HTTP hop. For interactive
  HPC workloads (model probing, status checks), the 5-20 ms Gateway overhead
  per request matters.
- **Simpler topology**: One less service to deploy and maintain on HPC. Login
  nodes often have limited software stacks.
- **SSH tunnel friendly**: ZeroMQ's persistent TCP connections work well through
  SSH tunnels (`ssh -L <port>:compute-node:<port>`). HTTP's per-request connections
  create more tunnel overhead.
- **Matched with existing UMRS CLI**: The `client_umrs.py` CLI already uses ZMQ
  for all operations. The r4 SDK now speaks the same protocol.

### 3.3 Hybrid (`R4_DEPLOY_MODE=hybrid`) — Default

```
┌──────────┐      ┌──────────┐      ┌──────────────┐
│          │ HTTP │ Platform │  HTTP│              │
│ Client   ├─────►│ Gateway  ├─────►│  UMRS        │
│ (r4)  │      │ (8080)   │      │  Sidecar     │
│          │      └──────────┘      │  (8082)      │
│          │                        │              │
│          │  ZMQ (data-plane only) │  ZMQ: ctrl   │
│          ├───────────────────────►│  Data: dynamic│
└──────────┘                        └──────────────┘
                  ▲ HTTP for control    ▲ ZMQ for data
                    (auth, commands)      (file transfer,
                                          status, embeddings)
```

**Characteristics:**

- Gateway handles authentication and policy for command operations
- ZeroMQ handles data-plane operations that benefit from efficient binary transfer
- Best of both worlds: security where it matters, performance where it counts
- Suitable for environments transitioning from cloud to HPC or vice versa

**Transport rule:** **Gateway HTTP for control-plane operations. ZMQ for data-plane
operations (file transfer, embedding export, status polling).**

**Rationale:**

- Administrative commands (job submission, model registration) involve write
  operations that need auth and audit → Gateway HTTP
- File transfers are bulk binary data where Gateway's HTTP multipart overhead
  and NGINX upload limits are painful → ZeroMQ
- Status polling is frequent and read-only → ZeroMQ saves HTTP overhead
- Embedding export can produce large serialized tables → ZeroMQ streaming

---

## 4. Communication Catalogue

Below is a detailed breakdown of every communication type in the platform, organized
by functional category.

### 4.1 LLM Inference (Chat / Completions)

| Attribute | Value |
|-----------|-------|
| **Operation** | `chat`, `chat_completion` |
| **SDK method** | `client.chat(...)`, `client.chat_completion(...)` |
| **Gateway route** | `POST /gw/llm/v1/chat/completions` |
| **UMRS involvement** | Gateway → UMRS discovers active endpoint → proxies to LiteLLM |
| **ZMQ eligible** | **No** — Always HTTP Gateway |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

LLM inference requests are the most security-sensitive operation in the platform.
They involve:

- Model access control (which users can use which models)
- Content policy enforcement (prompt safety, output filtering)
- Token-level billing and audit
- Streaming SSE responses that need HTTP/1.1 chunked transfer encoding

The Gateway discovers the active Mini-RiVault endpoint through UMRS, enforces
model access policy, then proxies the OpenAI-compatible request to LiteLLM.
ZeroMQ cannot replace this because: (a) it has no auth, (b) the client should
never know the Mini-RiVault node IP directly, and (c) the streaming protocol
is SSE-over-HTTP, not ZeroMQ frames.

### 4.2 RAG Operations

#### 4.2.1 RAG Query (Semantic Search + LLM Answer)

| Attribute | Value |
|-----------|-------|
| **Operation** | `rag_query` |
| **SDK method** | `client.rag.query(...)` |
| **Gateway route** | `POST /gw/rag/query` → UMRS sidecar → Mini-RiVault |
| **ZMQ eligible** | **No** — Always HTTP |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

A RAG query involves two steps: (1) retrieve relevant chunks from the vector
store, and (2) feed those chunks plus the user query to the LLM for answer
generation. RAG runs inside the Mini-RiVault container, so the Gateway proxies
to the UMRS sidecar which discovers the active Mini-RiVault endpoint and
forwards the request internally. The UMRS ZeroMQ server does not implement
a RAG query handler, so this operation is HTTP-only in all deployment modes.

#### 4.2.2 RAG Indexing

| Attribute | Value |
|-----------|-------|
| **Operation** | `rag_index` |
| **SDK method** | `client.rag.index(file_ids, ...)` |
| **Gateway route** | `POST /gw/rag/index` |
| **ZMQ command** | Not implemented (requires HTTP for presigned URL generation) |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

Indexing triggers an embedding pipeline on uploaded files. The files are
referenced by S3 keys, and UMRS generates presigned URLs for the embedding
worker inside Mini-RiVault. This presigned URL workflow requires the HTTP
sidecar, so ZeroMQ cannot replace it.

#### 4.2.3 RAG Document Management (List / Delete)

| Attribute | Value |
|-----------|-------|
| **Operations** | `list_documents`, `get_document`, `delete_document` |
| **SDK methods** | `client.rag.list_documents(...)`, `client.rag.delete_document(...)` |
| **Gateway routes** | `GET /gw/rag/documents`, `DELETE /gw/rag/documents/{id}` |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

Document management is CRUD on metadata. These are low-volume, security-relevant
operations (deletion requires authorization). HTTP is appropriate.

#### 4.2.4 RAG Retrieve and Passage Fetch (ADR-093, 2026-10-10)

| Attribute | Value |
|-----------|-------|
| **Operations** | `rag_retrieve`, `rag_passage` |
| **SDK methods** | `client.rag.retrieve(...)`, `client.rag.fetch_passage(node_id, ...)` |
| **Gateway routes** | `GET /gw/rag/retrieve`, `GET /gw/rag/passages/{node_id}` (the `/gw/rag/` block rewrites to `/umrs/rag/…`) |
| **UMRS handler** | `umrs/src/umrs/api/rag_retrieve.py` (`register` mounts both on the `/umrs/rag` router) |
| **ZMQ eligible** | **No** — HTTP only, like the rest of §4.2 |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

The retrieve verb is UMRS's single retrieval core. It returns scored
passages, a sufficiency status (`sufficient`, `partial`, `insufficient`) and,
on request, a trace. It makes no model call. `/gw/rag/query` (§4.2.1) and
the R4 companion's router both compose on it. The passage route returns one
chunk by the handle a retrieve result carries, under the same project access
check. If an older server lacks `/rag/retrieve`, the SDK falls back once to
`/gw/rag/query`.

### 4.3 File Transfer

#### 4.3.1 File Upload

| Attribute | Value |
|-----------|-------|
| **Operation** | `file_upload` |
| **SDK method** | `client.data.upload(file_path, ...)` |
| **Gateway route** | `POST /gw/data/upload` (HTTP fallback) |
| **ZMQ command** | `FILE` + binary data channel |
| **ZMQ eligible** | Yes — **preferred in all modes** |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | ZMQ (primary), HTTP (fallback) | Multi-GB files overwhelm HTTP multipart |
| HPC-Local | ZMQ | Direct, efficient, no proxy limits |
| Hybrid | ZMQ (primary), HTTP (fallback) | Performance-critical data plane |

**Explanation:**

This is the original and most important use case for ZeroMQ in the platform.
Scientific datasets (synchrotron CT scans, simulation output) range from
hundreds of megabytes to tens of gigabytes. HTTP multipart upload has several
problems at this scale:

1. **NGINX upload buffer**: Default `client_max_body_size` is 10 GB. Even with
   increased limits, NGINX buffers the entire request before forwarding.
2. **No resumability**: If an HTTP upload fails at 90%, the entire file must be
   re-uploaded. ZeroMQ's chunked protocol can be extended with checkpointing.
3. **Memory pressure**: HTTP proxies (NGINX, httpx on the Gateway side) buffer
   request bodies in memory. ZeroMQ streams chunks in 64 KB pieces.

The ZeroMQ data channel uses `PUSH`→`PULL` for fire-and-forget binary chunk
streaming. The control channel (`DEALER`→`ROUTER`) handles metadata and
acknowledgments. This separation means large binary data never touches the
control plane.

#### 4.3.2 File Download

| Attribute | Value |
|-----------|-------|
| **Operation** | `file_download` |
| **SDK method** | `client.data.download(file_id, ...)` |
| **Gateway route** | `GET /gw/data/download/{file_id}` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | ZMQ (primary), HTTP (fallback) | Same rationale as upload |
| HPC-Local | ZMQ | Direct access |
| Hybrid | ZMQ (primary), HTTP (fallback) | Data-plane operation |

### 4.4 Model Registry

#### 4.4.1 List Models

| Attribute | Value |
|-----------|-------|
| **Operation** | `model_list` |
| **SDK method** | `ModelClient.list_models(...)` |
| **Gateway route** | `GET /gw/models` |
| **ZMQ command** | `MODEL_LIST` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Multi-tenant model visibility scopes |
| HPC-Local | ZMQ | Single-tenant, all models visible |
| Hybrid | HTTP Gateway | Model scoping requires auth context |

**Explanation:**

Model listing is scope-aware: a user sees `system` models, their own `user`
models, and models in their `project` scope. In cloud mode, the Gateway
injects `X-Project-ID` and `X-User-ID` headers to enforce this scoping.
In HPC-local mode, there's a single tenant, so scoping is unnecessary and
ZeroMQ provides the model list directly from the UMRS registry.

> **HTTP fallback:** When ZMQ is unreachable (network error, misconfigured port,
> or pyzmq not installed), `r4 model list` automatically falls back to the
> HTTP Gateway route (`GET /gw/models`). The output uses ANSI styling (colored
> headers, status indicators) when writing to a terminal.

#### 4.4.2 Register Model

| Attribute | Value |
|-----------|-------|
| **Operation** | `model_register` |
| **SDK method** | `ModelClient.register_model(...)` |
| **Gateway route** | `POST /gw/models` |
| **ZMQ command** | `MODEL_REGISTER` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Write operation, needs auth + audit |
| HPC-Local | ZMQ | Trusted user, direct registration |
| Hybrid | HTTP Gateway | Write operations go through Gateway |

**Explanation:**

Model registration is a write operation that creates a persistent entry in the
UMRS model registry. In cloud mode, this must be authenticated and audited
(who registered what model, when). In HPC-local mode, the user is already
authenticated by SSH/Slurm.

#### 4.4.3 Download Model (Stage)

| Attribute | Value |
|-----------|-------|
| **Operation** | `model_download` |
| **SDK method** | `ModelClient.download_model(...)` / `ModelClient.stage_model(...)` |
| **Gateway route** | `POST /gw/models/{name}/download` |
| **ZMQ command** | `MODEL_DOWNLOAD` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Download jobs require egress policy enforcement |
| HPC-Local | ZMQ | Direct submission to UMRS job scheduler |
| Hybrid | HTTP Gateway | Egress-sensitive operation |

**Explanation:**

Model download is the **only operation with public egress** (downloading from
HuggingFace or external registries). The Gateway enforces egress policy and the
audit trail is critical. In HPC-local mode, the administrator has already
configured egress rules at the network level (firewall, proxy), so the ZeroMQ
path is acceptable.

### 4.5 Job Tracking

#### 4.5.1 List Jobs

| Attribute | Value |
|-----------|-------|
| **Operation** | `job_list` |
| **SDK method** | `client.jobs.list(...)` |
| **Gateway route** | `GET /gw/jobs` |
| **ZMQ command** | `JOB_LIST` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Project-scoped job visibility |
| HPC-Local | ZMQ | Single-tenant, all jobs visible |
| Hybrid | HTTP Gateway | Job scoping requires project context |

#### 4.5.2 Submit Job

| Attribute | Value |
|-----------|-------|
| **Operation** | `job_submit` |
| **SDK method** | `client.jobs.create(...)` |
| **Gateway route** | `POST /gw/jobs` |
| **ZMQ command** | `JOB_SUBMIT` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Write operation, needs auth + resource quota check |
| HPC-Local | ZMQ | Slurm handles resource allocation |
| Hybrid | HTTP Gateway | Control-plane operation |

**Explanation:**

Job submission creates a durable job with retry configuration. In cloud mode,
the Gateway enforces resource quotas (max concurrent jobs per project). In
HPC-local mode, Slurm itself enforces resource limits via partition policies,
so the Gateway check is redundant.

#### 4.5.3 Cancel Job

| Attribute | Value |
|-----------|-------|
| **Operation** | `job_cancel` |
| **SDK method** | `client.jobs.cancel(job_id)` |
| **Gateway route** | `POST /gw/jobs/{job_id}/cancel` |
| **ZMQ command** | `JOB_CANCEL` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Auth required (only owner can cancel) |
| HPC-Local | ZMQ | Trusted user |
| Hybrid | HTTP Gateway | Control-plane operation |

### 4.6 Workflow Management

#### 4.6.1 List Workflows

| Attribute | Value |
|-----------|-------|
| **Operation** | `workflow_list` |
| **SDK method** | `client.workflows.list_workflows(...)` |
| **Gateway route** | `GET /gw/workflows` |
| **ZMQ command** | `LIST_WORKFLOWS` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Workflow visibility may be scoped |
| HPC-Local | ZMQ | Single-tenant, all workflows visible |
| Hybrid | HTTP Gateway | Control-plane metadata |

#### 4.6.2 Run Workflow

| Attribute | Value |
|-----------|-------|
| **Operation** | `workflow_run` |
| **SDK method** | `client.workflows.trigger(workflow_id, ...)` |
| **Gateway route** | `POST /gw/workflows/{id}/trigger` |
| **ZMQ command** | `RUN_WORKFLOW` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Resource-consuming operation, needs auth |
| HPC-Local | ZMQ | Direct trigger on trusted environment |
| Hybrid | HTTP Gateway | Workflow execution is control-plane |

Current implementation note:
Only workflow catalog listing and execution acceptance are exposed today. The Gateway and UMRS HTTP sidecar support `GET /gw/workflows` and `POST /gw/workflows/{id}/trigger`, which normalize to the UMRS payload shape `{ "workflow_name": ..., "params": {...} }`. Execution-status polling, result retrieval, and cancellation are not yet part of the public workflow API.

#### 4.6.3 Upload Workflow

| Attribute | Value |
|-----------|-------|
| **Operation** | `workflow_upload` |
| **SDK method** | Via CLI `r4 workflow upload` |
| **Gateway route** | `POST /gw/workflows/upload` |
| **ZMQ command** | `UPLOAD_WORKFLOW` + binary data channel |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Write operation + file transfer |
| HPC-Local | ZMQ | Uses data channel for zip upload |
| Hybrid | HTTP Gateway (control) + ZMQ (file data) | Hybrid split |

**Explanation:**

Workflow upload is a two-phase operation: (1) register the workflow metadata,
(2) transfer the workflow zip file. ZeroMQ handles this natively because
the control channel negotiates the upload and the data channel streams the
binary content. HTTP requires multipart encoding.

### 4.7 MCP Tool Operations

| Attribute | Value |
|-----------|-------|
| **Operations** | `mcp_list_tools`, `mcp_call_tool`, `mcp_register_server` |
| **SDK methods** | `client.mcp.list_tools()`, `client.mcp.call_tool(...)` |
| **Gateway routes** | `GET /gw/mcp/tools`, `POST /gw/mcp/invoke` |
| **ZMQ eligible** | **No** — Always HTTP Gateway |
| **Transport in all modes** | HTTP Gateway |

**Explanation:**

MCP tool calls can trigger external network requests, file system access, and
arbitrary code execution inside the Mini-RiVault container. They are the
highest-risk operation in the platform. The Gateway ensures:

- **Tool allowlisting**: Only approved tools are callable
- **EgressGuard enforcement**: MCP servers' external access goes through the egress proxy
- **Audit trail**: Every tool invocation is logged
- **Rate limiting**: Prevents runaway tool call loops

These guarantees cannot be replicated over ZeroMQ without essentially rebuilding
the Gateway's policy engine as a ZeroMQ interceptor.

### 4.8 Status and Monitoring

| Attribute | Value |
|-----------|-------|
| **Operation** | `status` |
| **SDK method** | `UMRSTransport.get_status(...)` / `request_status_cmd(...)` |
| **Gateway route** | `GET /gw/status` |
| **ZMQ command** | `STATUS` |
| **ZMQ eligible** | Yes |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | HTTP Gateway | Consistent with other operations |
| HPC-Local | ZMQ | Low-latency polling |
| Hybrid | ZMQ | Read-only, high-frequency, data-plane |

**Explanation:**

Status checks are read-only, frequent (e.g., polling job completion every 5
seconds), and carry no security-sensitive data. ZeroMQ is ideal because:

- Persistent TCP connection avoids HTTP connection setup per poll
- Minimal frame overhead compared to HTTP headers
- No auth needed for read-only status in hybrid mode (status is not scoped)

### 4.9 Embedding Export

| Attribute | Value |
|-----------|-------|
| **Operation** | `export_embeddings` |
| **SDK method** | `UMRSTransport.request_export_embeddings(...)` |
| **ZMQ command** | `EXPORT_EMBEDDINGS` |
| **ZMQ eligible** | Yes — **ZMQ preferred in all modes** |

| Scenario | Transport | Reason |
|----------|-----------|--------|
| Cloud | ZMQ | Large serialized data, streaming |
| HPC-Local | ZMQ | Direct, efficient |
| Hybrid | ZMQ | Data-plane operation |

**Explanation:**

Embedding export serializes the entire vector index to CSV or JSON. This can
produce multi-hundred-megabyte responses. HTTP is poorly suited for such large
response bodies (timeout risks, proxy buffering). ZeroMQ allows a generous
120-second receive timeout and streams the response directly.

---

## 5. Operation-to-Transport Matrix

Summary table of all operations and their transport per deployment scenario.

| Operation | Cloud | HPC-Local | Hybrid | Category |
|-----------|-------|-----------|--------|----------|
| **Chat / Completions** | HTTP | HTTP | HTTP | Gateway-only |
| **MCP Tool Calls** | HTTP | HTTP | HTTP | Gateway-only |
| **MCP Server Registration** | HTTP | HTTP | HTTP | Gateway-only |
| **RAG Query** | HTTP | HTTP | HTTP | HTTP-only |
| **RAG Index** | HTTP | HTTP | HTTP | HTTP-only |
| **RAG Document CRUD** | HTTP | HTTP | HTTP | HTTP-only |
| **File Upload** | ZMQ* | ZMQ | ZMQ* | Data-plane |
| **File Download** | ZMQ* | ZMQ | ZMQ* | Data-plane |
| **Model List** | HTTP | ZMQ | HTTP | Control-plane |
| **Model Register** | HTTP | ZMQ | HTTP | Control-plane |
| **Model Download** | HTTP | ZMQ | HTTP | Control-plane |
| **Job List** | HTTP | ZMQ | HTTP | Control-plane |
| **Job Submit** | HTTP | ZMQ | HTTP | Control-plane |
| **Job Cancel** | HTTP | ZMQ | HTTP | Control-plane |
| **Workflow List** | HTTP | ZMQ | HTTP | Control-plane |
| **Workflow Run** | HTTP | ZMQ | HTTP | Control-plane |
| **Workflow Upload** | HTTP | ZMQ | HTTP | Control-plane |
| **Status** | HTTP | ZMQ | ZMQ | Data-plane |
| **Embedding Export** | ZMQ | ZMQ | ZMQ | Data-plane |

\* ZMQ with HTTP fallback when ZMQ is unavailable (pyzmq not installed,
server not reachable, or CURVE session bootstrap fails). Model list also
falls back to HTTP automatically.

**Legend:**
- **Gateway-only**: Operations that must always go through HTTP Gateway regardless
  of deployment mode, because they require policy enforcement.
- **Control-plane**: Operations that involve state mutations or scoped reads.
  Routed through Gateway in cloud/hybrid for auth+audit, direct ZMQ in HPC-local.
- **Data-plane**: Operations that primarily move bulk data or perform frequent
  read-only polling. Prefer ZMQ for efficiency.
- **HTTP-only**: Operations that inherently require HTTP (presigned URL generation,
  REST semantics).

---

## 6. Configuration Reference

### 6.1 Environment Variables

```bash
# Deployment mode: determines default transport per operation
# Values: cloud, hpc-local, hybrid (default: hybrid)
R4_DEPLOY_MODE=hybrid

# Transport override: force a specific transport regardless of deploy mode
# Values: gateway (always HTTP), direct (always ZMQ), auto (let deploy mode decide)
# Default: auto
R4_TRANSPORT_MODE=auto

# Enable/disable ZMQ transport entirely
# Default: true
R4_USE_UMRS_TRANSPORT=true

# UMRS ZeroMQ server coordinates
UMRS_SERVER_HOST=127.0.0.1    # UMRS server address
UMRS_CONTROL_PORT=5555         # Control channel port (auto-discovered via /gw/ops/connect-info)
UMRS_TIMEOUT_MS=8000           # Socket timeout in milliseconds
UMRS_TUNNEL_MODE=false         # Use SSH tunnel mode (bind to localhost)

# Gateway HTTP coordinates
R4_GATEWAY_URL=http://localhost:8080   # Gateway base URL
R4_API_KEY=sk-...                      # API key for Gateway auth

# User identity for UMRS operations
UMRS_USERNAME=alice
```

### 6.2 Python Configuration

```python
from r4 import Client
from r4.config import Config, DeployMode, TransportMode

# HPC-local deployment: direct ZMQ for everything
config = Config(
    deploy_mode=DeployMode.HPC_LOCAL,
    transport_mode=TransportMode.AUTO,
    umrs_server_host="10.0.1.50",
    umrs_control_port=5555,  # auto-discovered during r4 auth login
)
client = Client(config=config)

# Cloud deployment: all through Gateway
config = Config(
    deploy_mode=DeployMode.CLOUD,
    gateway_url="https://gateway.example.com",
    api_key="sk-my-key",
)
client = Client(config=config)

# Check what transport will be used for an operation
config.should_use_zmq("job_list")    # False in cloud, True in hpc-local
config.should_use_zmq("file_upload") # True in all modes
config.should_use_zmq("chat")        # False in all modes
```

### 6.3 CLI Usage

```bash
# Set deploy mode via environment
export R4_DEPLOY_MODE=hpc-local
export UMRS_SERVER_HOST=login-node.cluster.local
r4 job list

# Force Gateway for a session
export R4_TRANSPORT_MODE=gateway
r4 model list   # Uses HTTP even in hpc-local deploy mode

# Force direct ZMQ for a session
export R4_TRANSPORT_MODE=direct
r4 job list      # Uses ZMQ even in cloud deploy mode
```

---

## 7. Fallback and Resilience

Every ZMQ-eligible operation follows a **ZMQ-first, HTTP-fallback** pattern:

```python
def operation(self, ...):
    # 1. Check if ZMQ should be used for this operation+config
    if self._config.should_use_zmq("operation_name"):
        try:
            return self._operation_via_zmq(...)             # Try ZMQ
        except Exception as e:
            logger.warning(f"ZMQ failed, falling back to HTTP: {e}")

    # 2. Fall back to HTTP Gateway
    return self._operation_via_http(...)
```

**Fallback triggers:**

| Condition | Behavior |
|-----------|----------|
| `pyzmq` not installed | `ZEROMQ_AVAILABLE = False`, ZMQ never attempted |
| UMRS server unreachable | `connect()` returns `False`, falls back to HTTP |
| ZMQ timeout | `zmq.Again` exception caught, falls back to HTTP |
| ZMQ protocol error | Any exception caught, falls back to HTTP |
| `R4_USE_UMRS_TRANSPORT=false` | ZMQ entirely disabled |
| `R4_TRANSPORT_MODE=gateway` | ZMQ never attempted for any operation |

**No fallback from HTTP to ZMQ**: If Gateway HTTP fails, the operation fails.
This is intentional — in cloud/hybrid modes, Gateway failure means auth is
unavailable, and the operation should not silently bypass security.

---

## 8. Security Considerations

### 8.1 ZeroMQ Transport Security

ZeroMQ transport provides **no built-in security**:

- **No authentication**: The `client_id` field is self-reported by the client.
  A malicious client can impersonate any user.
- **No encryption**: Data travels as plaintext TCP. Use SSH tunnels or trusted
  networks.
- **No authorization**: UMRS does not check if `client_id=alice` is allowed to
  list `project_id=bobs-project`.

**Mitigation in HPC-local mode:**

- SSH authentication on login nodes provides user identity
- Trusted internal network (InfiniBand, internal IP range) prevents external access
- Slurm job ownership provides resource-level authorization
- Single-tenant deployment means no inter-user authorization needed

**Why this is acceptable:**

In HPC-local mode, the network boundary IS the security boundary. Only
authenticated users with SSH access can reach the UMRS ZeroMQ port. This is
the same trust model as running `srun` or `sbatch` — the Slurm tools also
have no per-command authentication because SSH already established identity.

### 8.2 Gateway Transport Security

Gateway HTTP provides **full security**:

- JWT verification on every request
- API key validation
- Project-scoped access control
- Rate limiting prevents abuse
- Audit logging for compliance

### 8.3 When to Use Which

| Your Environment | Recommended Mode | Rationale |
|------------------|-----------------|-----------|
| Shared cloud cluster, external users | `cloud` | Full Gateway security for all ops |
| Dedicated HPC allocation, SSH-only access | `hpc-local` | Slurm/SSH provides auth, ZMQ provides speed |
| Mixed: some trusted HPC, some external | `hybrid` | Gateway for commands, ZMQ for data |
| Development/testing | `hybrid` or `hpc-local` | Flexibility during development |

---

## 9. Decision Rationale

### Why Two Transports Instead of One?

**Neither HTTP nor ZeroMQ alone satisfies all requirements.**

HTTP excels at:
- Stateless request-response (REST semantics)
- Built-in auth (Bearer tokens, cookies)
- Browser compatibility
- Standard tooling (curl, httpx, Postman)
- Proxy and load balancer compatibility

ZeroMQ excels at:
- Binary streaming without serialization overhead
- Persistent connections for high-frequency polling
- Low-latency on trusted networks
- Multiplexed channels (control + data separation)
- SSH tunnel compatibility for HPC

Trying to do file transfers over HTTP leads to NGINX buffer exhaustion and
timeout issues. Trying to do authentication over ZeroMQ leads to reimplementing
half the Gateway.

### Why Not WebSocket?

WebSocket was considered as a middle ground but rejected because:

- Still requires the HTTP upgrade handshake (NGINX proxy complexity)
- UMRS already speaks ZeroMQ on the server side; WebSocket would require a
  translation layer
- No advantage over ZeroMQ for server-to-server communication
- Adds another protocol to maintain

### Why Not gRPC?

gRPC was considered for the direct transport but rejected because:

- UMRS is Python-native; gRPC adds protobuf compilation steps
- ZeroMQ is already deployed and tested in UMRS
- gRPC streaming is powerful but adds complexity (HTTP/2 requirements)
- The existing ZeroMQ wire protocol is simple and sufficient
- gRPC would require new server-side code; ZeroMQ works with existing
  `MessageQueue` and `TaskCmd` infrastructure

### Why Default to Hybrid?

Hybrid is the safest default because:

1. It preserves Gateway security for all command operations
2. It enables ZeroMQ for the one area where HTTP genuinely struggles (file transfer)
3. It works in both cloud and HPC without reconfiguration
4. It gracefully degrades — if ZMQ is unavailable, HTTP fallback handles everything

Users can tighten security with `R4_DEPLOY_MODE=cloud` or optimize performance
with `R4_DEPLOY_MODE=hpc-local` when their environment warrants it.

---

## Related Documents

- [Architecture Overview](../ARCHITECTURE.md) — System components and lifecycle
- [Integration Policy](INTEGRATION_POLICY.md) — Third-party framework integration
- [Server-Client Deployment](SERVER_CLIENT_DEPLOYMENT.md) — Deployment topology
- [Development Instructions](../.github/instructions/development.instructions.md) — Non-negotiable invariants
