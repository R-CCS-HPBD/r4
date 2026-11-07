# ADR-013: Knowledge Portability and Compute/Knowledge Separation

**Status:** Accepted
**Date:** 2026-07-05
**Relates to:** ADR-006 (mini-RiVault container security), ADR-012 (Assistant Service),
INV-09 (RAG/MCP sandbox), INV-13 (knowledge portability)

## Context

ADR-006 established that inference containers run fully offline, with RAG and
MCP executing inside mini-RiVault containers (INV-09).  This coupling was
deliberate: it co-locates untrusted workloads (RAG retrieval, MCP tool calls)
with the inference engine inside a single sandboxed container.

However, the coupling creates two problems:

1. **Knowledge is not portable.**  RAG indexing and retrieval are tied to
   mini-RiVault containers running on GPU compute nodes.  A user who wants to
   pull a premade knowledge base to their local inference environment has no
   way to do so — the knowledge lives inside the platform's Postgres/S3 and
   can only be queried through a running mini-RiVault endpoint.

2. **RAG/MCP are coupled to GPU compute.**  RAG retrieval is a stateless,
   CPU-only, sub-100ms operation.  It does not need a GPU, a Slurm allocation,
   or a compute node.  Yet the current architecture requires a running
   mini-RiVault (with GPU) to serve RAG queries.

The key insight: **mini-RiVault is a security pattern, not a compute pattern.**
The same container isolation that protects GPU inference also protects RAG
retrieval and MCP tool execution.  But RAG doesn't need the GPU — it needs the
isolation.

## Decision

### 1. Separate Knowledge Services from Compute Services

Three deployment tiers, each with distinct characteristics:

| Tier | Where | GPU? | Lifecycle | Examples |
|------|-------|------|-----------|----------|
| **Inference** | Compute nodes (Slurm) | ✅ | Ephemeral (Slurm job, TTL) | vLLM, BentoML vision |
| **Knowledge** | Frontend server / login node | ❌ | Persistent service | RAG query engine, knowledge graph, science catalog |
| **Local** | User's machine | Optional | User-controlled | r4 SDK + knowledge bundle + local LLM |

**Inference tier:** GPU-bound, Slurm-allocated, ephemeral.  vLLM runs here.
Mini-RiVault containers on compute nodes only run the inference engine.

**Knowledge tier:** CPU-only, stateless, persistent.  RAG query engine, science
catalog, and knowledge graph run here.  Deployed as a firewalled, sandboxed
container on the frontend server or login node.  No GPU needed, no Slurm
allocation.

**Local tier:** User's machine, optional GPU.  User pulls a knowledge bundle
(`.kb`), runs local inference (Ollama, vLLM, external API), and gets
RAG-augmented responses without needing the platform.

### 2. Define Knowledge Bundle Format (`.kb`)

A knowledge bundle is a self-contained, portable archive of a project's
knowledge base:

```
my-project.kb/
├── manifest.json           # Bundle metadata, schema version, embedding model ref
├── documents/              # Raw text chunks (parquet or jsonl)
│   ├── chunks.parquet
│   └── metadata.parquet
├── embeddings/             # pgvector embeddings as portable columnar format
│   └── embeddings.parquet
├── graph/                  # Knowledge graph entities and relations
│   └── graph.jsonld
├── catalog/                # Science catalog metadata
│   └── catalog.json
└── bm25_index/             # Serialized BM25 index
    └── bm25.pkl
```

**Export:** `r4 knowledge export <project> --output my-project.kb`
- Exports documents from S3
- Exports embeddings from pgvector as Parquet
- Exports knowledge graph as JSON-LD
- Exports science catalog metadata as JSON
- Serializes BM25 index

**Import:** `r4 knowledge import <bundle>`
- Imports documents into local S3/filesystem
- Imports embeddings into local pgvector/SQLite
- Imports knowledge graph into local Postgres
- Rebuilds BM25 index

**Use with inference:** `r4 up --knowledge <bundle>`
- Loads knowledge bundle into RAG query engine
- Serves RAG-augmented requests against any LLM backend

### 3. Redefine Mini-RiVault as Security Pattern

Mini-RiVault is a **security pattern** (container isolation + egress control +
read-only mounts), not a **compute pattern** (GPU + model weights).  The
security pattern is applicable to any untrusted workload:

| Workload | GPU Required? | Container Isolation? | Egress Control? |
|----------|---------------|----------------------|-----------------|
| vLLM inference | ✅ | ✅ | ✅ (offline) |
| RAG retrieval | ❌ | ✅ | ✅ (offline) |
| MCP tool execution | ❌ | ✅ | ✅ (EgressGuard) |
| Knowledge indexing | ❌ | ✅ | ✅ (offline) |
| Model download | ❌ | ✅ | ✅ (controlled egress) |

The key invariant is **container isolation + egress control**, not co-location
with GPU inference.

### 4. Relax INV-09

**Current INV-09:** "RAG and MCP must run inside mini-RiVault."

**New INV-09:** "RAG and MCP run inside sandboxed containers (mini-RiVault
pattern).  The sandbox may be:
- Co-located with inference on compute nodes (current behavior), OR
- A standalone sandboxed service on the frontend/login node (new behavior).

The key invariant is container isolation + egress control, NOT co-location
with GPU inference."

### 5. Three-Mode RAG Deployment

RAG can be deployed in three modes, depending on the use case:

**Mode 1: Co-located (current behavior)**
- RAG query engine runs inside mini-RiVault container on compute node
- Advantage: co-located with vLLM, no network hop for retrieval
- Disadvantage: requires GPU allocation, tied to endpoint lifecycle

**Mode 2: Frontend-deployed (new behavior)**
- RAG query engine runs as persistent service on frontend server
- Advantage: no GPU needed, persists across endpoint lifecycles, serves multiple endpoints
- Disadvantage: network hop from compute node to frontend for retrieval

**Mode 3: Local (new behavior)**
- RAG query engine runs on user's machine, reads from imported `.kb` bundle
- Advantage: fully portable, works with any local LLM
- Disadvantage: user must manage their own infrastructure

## Consequences

### Positive

1. **Knowledge portability** — Users can export knowledge bundles and pull
   them to their local inference environment.  Premade knowledge bundles can
   be published and shared.

2. **Cost efficiency** — RAG/MCP don't need GPU allocations.  Running RAG on
   the frontend server (CPU-only) is significantly cheaper than allocating
   GPUs for CPU-only RAG workloads.

3. **Deployment flexibility** — RAG can run on frontend servers (no GPU
   needed) or compute nodes (co-located with inference).  Both use the same
   container isolation pattern.

4. **Local inference support** — Users can pull knowledge bundles and run
   with any LLM backend (Ollama, vLLM, external API) without needing the
   platform.

5. **Faster endpoint startup** — Mini-RiVault containers on compute nodes
   only run vLLM.  No RAG service to bootstrap during Slurm job startup.

### Negative

1. **Network hop for retrieval** — In Mode 2 (frontend-deployed), RAG queries
   incur a network hop from compute node to frontend server.  This adds
   latency (~1-5ms per query, depending on network topology).

2. **Operational complexity** — Three deployment modes means three operational
   patterns.  Operators need to understand which mode is appropriate for
   which workload.

3. **Bundle format versioning** — The `.kb` bundle format needs to be
   versioned and backward-compatible.  Schema migrations may be needed when
   the bundle format changes.

### Neutral

1. **Security model unchanged** — The security model (container isolation +
   egress control) is unchanged.  The change is about *where* the container
   runs, not *how* it's secured.

2. **Gateway routing unchanged** — The Gateway still routes `/gw/rag/*` and
   `/gw/chat/*` to the appropriate service.  The routing logic doesn't need
   to change.

## Implementation Plan

### Phase 1: Knowledge Bundle Format (`.kb`)
- Define bundle manifest schema (`manifest.json`)
- Implement export: `r4 knowledge export <project> --output <bundle>`
- Implement import: `r4 knowledge import <bundle>`
- Add `r4 up --knowledge <bundle>` flag

### Phase 2: Frontend-Deployed RAG
- Deploy RAG query engine as persistent service on frontend server
- Add `/gw/rag/query` route for direct RAG queries (no mini-RiVault needed)
- Update Assistant Service to support both co-located and frontend-deployed RAG

### Phase 3: Local Inference Support
- Add `r4 up --local` flag for local inference mode
- Implement local RAG query engine that reads from `.kb` bundle
- Add `r4 up --model ollama/llama3 --knowledge my-project.kb` workflow

### Phase 4: Premade Knowledge Marketplace
- Publish curated knowledge bundles (e.g., "materials science 2024")
- Add `r4 knowledge pull <bundle-name>` command
- Add `r4 knowledge list --remote` command

## Alternatives Considered

### Keep RAG Coupled to Compute Nodes (Current)

**Pros:** Simple deployment model, co-located with inference.
**Cons:** Knowledge not portable, RAG requires GPU allocation, RAG tied to
endpoint lifecycle.

### Move RAG to Gateway

**Pros:** Single point of access, no GPU needed.
**Cons:** Violates ADR-003 (Gateway is a thin proxy), turns Gateway into a
stateful orchestrator, creates a single point of failure for RAG augmentation.

### Move RAG to a Separate Microservice (Outside Container)

**Pros:** No GPU needed, scalable.
**Cons:** Violates INV-09 (container isolation), bypasses egress control,
creates an uncontained attack surface.

## References

- [ADR-006: Offline Inference Container Security Model](006-mini-rivault-container-security.md)
- [ADR-012: Assistant Service and Two Public Conversational Surfaces](012-assistant-service-and-conversational-surfaces.md)
- [CONSTITUTION.md](../../CONSTITUTION.md) — INV-09, INV-13
- [Knowledge Portability Design](../architecture/design/knowledge-portability.md)
