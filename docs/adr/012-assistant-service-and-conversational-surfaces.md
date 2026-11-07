# ADR-012: Assistant Service and Two Public Conversational Surfaces

**Status:** Accepted
**Date:** 2026-07-04
**Relates to:** ADR-003 (three-component gateway), ADR-006 (mini-RiVault container
security), ADR-009 (research loop), INV-09 (RAG/MCP inside mini-RiVault),
Gateway-boundary invariant (mutation-safety)

## Context

Project knowledge produced by the research loop (`r4 project ingest/understand/run`)
lives in UMRS (Postgres + pgvector for embeddings, S3 for chunks, plus the
knowledge graph). Today RAG augmentation only happens on the SDK/CLI path
(`r4 chat` → `client.rag.inject_context()` → LiteLLM → vLLM).

Clients that speak plain OpenAI wire format (OpenWebUI, VSCode, Claude Desktop,
generic OpenAI SDKs) reach the model through the OpenAI-compatible endpoint
(`/gw/llm/v1/*` → LiteLLM → vLLM). That path has **no** access to project
knowledge, MCP, or tools. So a user who wants an OpenWebUI/Gradio front end backed
by their project knowledge has no server-side augmentation surface to point at.

The tempting fix is a "RAG shim" between OpenWebUI and LiteLLM, or to grow the
Gateway into a stateful chat orchestrator. Both are wrong for this platform:

- The Gateway is a thin proxy/policy layer and **must not** acquire durable state,
  retry/workflow, or orchestration logic (mutation-safety invariant, ADR-003).
- RAG and MCP **must** run inside mini-RiVault (INV-09, ADR-006), not at the edge.
- UMRS already owns persistent conversation state (`conversations`, `messages`,
  `conversation_summaries`, `memory_items`) and `POST /umrs/context/pack`.
  A new chat component must not duplicate that.

A "shim" also has no defined contract and predictably accretes into an
unbounded service. The missing piece is a **named component with a contract**,
placed on the correct side of the security boundary.

## Decision

### 1. Assistant Service lives inside mini-RiVault

The augmented conversational component is a first-class **Assistant Service** that
runs **inside the mini-RiVault container**, co-located with LiteLLM, vLLM, RAG, and
MCP. It is the compute/orchestration owner for assistant behavior (context
injection, tool routing, MCP, vision routing). It is **not** a gateway tier and
carries no durable state of its own.

It is named **Assistant Service** — not "Conversation Service" — because UMRS
already owns "conversation" as a data concept. The split:

- **UMRS** owns conversation *data* (conversations, messages, summaries, memory).
- **Mini-RiVault Assistant Service** owns assistant *execution*.

The Assistant Service **consumes** UMRS services (`/umrs/context/pack`, memory)
and never duplicates persistent state.

### 2. Two public conversational surfaces, defined by contract

Both are public and **parallel products — not successive generations**. Neither is
"legacy" or "the default"; each serves a distinct contract. The Assistant Service
is the **recommended interface for platform-native clients**; the OpenAI API is
the recommended interface for interoperability, benchmarking, and clients that
perform their own retrieval or agent orchestration.

#### OpenAI API — `/gw/llm/v1/*`

**Contract:**
- OpenAI-compatible
- Stateless
- No platform semantics
- No implicit context
- Stable interoperability surface

**For:** benchmarking, SDK/tool compatibility, external integrations, research
scripts, reproducibility, and clients that do their own retrieval/prompting/
agent orchestration.

#### Assistant API — `/gw/chat/*`

**Contract:**
- Assistant semantics
- Project-aware
- Context-aware
- Tool-aware
- MCP-aware
- Platform-native

**For:** OpenWebUI, `r4 chat`, the TUI, AI4S workflows, and IDE integrations
that want project intelligence.

#### Why two contracts, not one

The distinction is **not** simply "one injects RAG." Six months from now the
Assistant API will also inject workflow execution, permissions, project policies,
tool routing, provenance, citations, and execution traces. The Assistant API
becomes the platform's opinionated interface, while the OpenAI API remains a
compatibility layer.

To remain consumable by OpenAI-format clients (OpenWebUI etc.), the Assistant
API itself exposes an OpenAI-compatible request/response shape. The
**OpenAI-compatible contract** is what is public; **LiteLLM is a swappable
implementation** of that contract behind `/gw/llm/v1/*`. Keeping raw inference
public preserves the principle of least surprise: knowledge/MCP are never
implicitly injected into a vanilla OpenAI request.

### 3. Project context is per-request, not launch-bound

Endpoints are **not** pinned to a project at provisioning time. `project_id` is a
per-request field on the Assistant API. One warm endpoint serves projects A/B/C
without relaunching — important because endpoint startup on Slurm is expensive.

### 4. Endpoints expose a service manifest

Each endpoint exposes `GET /manifest` describing available capabilities
(`openai`, `chat`, `vision`, `knowledge`, `ui`, `metrics`, `health`, supported
models, embeddings, tools). Clients (TUI, VSCode, future front ends) discover
capabilities instead of hardcoding them. This generalizes the existing
`/gw/models` aggregation and `/health` autodiscovery patterns.

### Layering

```
                Clients
      ┌────────────┴────────────┐
OpenAI-compatible         Platform-native
      │                         │
      ▼                         ▼
 /gw/llm/v1/*              /gw/chat/*        (thin Gateway: auth, rate-limit, route)
      └────────────┬────────────┘
                   ▼
             Mini-RiVault (security boundary)
      ┌─────────────────────────────┐
      │ Assistant Service           │  ← inject_context, MCP, tools, vision routing
      │ LiteLLM → vLLM              │  ← swappable impl of OpenAI contract
      │ RAG · MCP · Vision          │  ← INV-09: inside the boundary
      └─────────────────────────────┘
                   │  consumes (never duplicates)
                   ▼
                 UMRS
      (conversation data, memory, knowledge graph, context/pack)
```

## Consequences

- The Gateway stays thin: both `/gw/llm/*` and `/gw/chat/*` are proxy/policy routes
  with no state or orchestration.
- RAG/MCP/tools stay inside mini-RiVault, consistent with INV-09 and ADR-006.
- No new persistent conversation store: the Assistant Service calls UMRS.
- Vanilla OpenAI clients keep working unchanged against `/gw/llm/v1/*`.
- UI front ends (OpenWebUI, Gradio) point at `/gw/chat/*` and pass `project_id`
  per request; a `--ui` co-launch flag can podman-launch a UI container against it.
- Adding future capabilities (agents, workflow execution, provenance, citations,
  execution traces) extends the Assistant API without touching the Gateway or the
  OpenAI contract.

### Incremental roadmap (additive-first)

1. Assistant Service inside mini-RiVault wrapping `inject_context()` +
   `/umrs/context/pack`, OpenAI-compatible, `project_id` per request.
2. `GET /manifest`.
3. `--ui openwebui|gradio` co-launch (compose already exists in
   `umrs/src/umrs/mini-rivault-llm/demo-llm.yaml`) pointed at `/gw/chat/*`.
4. Agents / tool execution / UI-plugin registry — deferred until there is a
   consumer.

## Amendment: Knowledge Portability and Deployment Flexibility (2026-07-05)

**Relates to:** ADR-013 (Knowledge Portability and Compute/Knowledge Separation)

### Deployment Flexibility

The Assistant Service supports three deployment modes:

**Mode 1: Co-located (deprecated 2026-07-05)**
- Assistant Service runs inside mini-RiVault container on compute node
- RAG retrieval is co-located with vLLM inference
- Advantage: no network hop for retrieval, fully self-contained
- Disadvantage: requires GPU allocation, tied to endpoint lifecycle, wastes
  compute node RAM on a stateless CPU-only proxy
- **Status:** Removed from `serve.slurm`. The compute-node launch block has
  been deleted; `RIVAULT_EXTRA_PROFILES=assistant` no longer triggers a
  co-located assistant. Use Mode 2 (frontend) for production.

**Mode 2: Frontend-deployed (production default)**
- Assistant Service runs as persistent CPU-only service on frontend server
  (umrs-front)
- RAG query engine runs on frontend server, reads from pgvector
- Advantage: no GPU needed, persists across endpoint lifecycles, serves
  multiple endpoints, RAG service is co-located on the same host
- Disadvantage: network hop from compute node to frontend for retrieval
- **Implementation:**
  `deploy/ansible/roles/front_end/templates/frontend-assistant-service.yaml.j2`

**Mode 3: Local (development/testing)**
- Assistant Service runs on user's machine, reads from imported `.kb` bundle
- User runs local inference (Ollama, vLLM, external API)
- Advantage: fully portable, works with any local LLM
- Disadvantage: user must manage their own infrastructure
- **Implementation:** `umrs/deploy/local/local-assistant-service.yaml`

### Rationale for Frontend-Only Deployment

The Assistant Service is a **stateless FastAPI proxy** — no DB, no GPU, no
model weights. It does two things:

1. RAG retrieval to augment context (calls the co-located RAG service)
2. Forward to upstream vLLM (via the Gateway's SSH tunnel)

Running this proxy on the compute node wastes scarce compute node RAM on a
stateless service that doesn't need GPU access. The frontend server (umrs-front)
has spare CPU/RAM capacity, and the RAG service is already co-located there.

The ~1-5ms SSH tunnel latency is negligible compared to LLM inference latency
(typically 100ms-10s+). The resource savings (freeing compute node RAM for KV
cache) are real and meaningful.

### Knowledge Bundle Support

The Assistant Service is **knowledge-bundle-aware** — it loads a `.kb` bundle at
startup and serves RAG-augmented requests against any LLM backend.

**Export:** `r4 knowledge export <project> --output my-project.kb`
**Import:** `r4 knowledge import <bundle>`
**Use with inference:** `r4 up --knowledge <bundle>`

## Amendment: Frontend-Only Deployment (2026-07-05)

**Decision:** The Assistant Service is no longer co-launched on the compute
node. It runs as a persistent CPU-only service on the frontend server
(umrs-front), where the RAG service is also co-located.

**Changes:**
- `umrs/deploy/ai4s/serve.slurm`: Removed the Assistant Service launch block.
  The `RIVAULT_EXTRA_PROFILES=assistant` env var no longer triggers a
  co-located assistant on the compute node.
- `umrs/deploy/ai4s/pool-ai4s-optimized.yaml`: Removed all
  `RIVAULT_EXTRA_PROFILES: "assistant"` lines.
- `umrs/deploy/ai4s/pool-glm52-v2-assistant.yaml`: Removed
  `RIVAULT_EXTRA_PROFILES: "assistant"` line.
- Frontend deployment manifest remains at:
  `deploy/ansible/roles/front_end/templates/frontend-assistant-service.yaml.j2`

**Rationale:** The Assistant Service is a stateless CPU-only proxy (FastAPI +
httpx). Running it on the compute node wastes scarce GPU-node RAM on a service
that doesn't need GPU access. The frontend server has spare capacity and the
RAG service is already co-located there. The ~1-5ms SSH tunnel latency is
negligible compared to LLM inference latency (100ms-10s+).

**Resource savings:** Freeing compute node RAM allows larger KV caches, which
directly improves throughput and maximum context length for all served models.

## Alternatives Considered

- **RAG shim between OpenWebUI and LiteLLM**: no defined contract, wrong side of
  the boundary for RAG/MCP, and predictably accretes into an unbounded service.
- **Chat orchestration in the Gateway**: violates the Gateway-boundary invariant
  (ADR-003, mutation-safety); turns the thin edge into a second orchestrator.
- **Single conversational API (deprecate raw OpenAI)**: breaks a shipped interop
  capability. Clients that do their own retrieval/agent logic need vanilla OpenAI
  semantics; implicit augmentation violates least surprise.
- **Launch-time project binding**: wastes expensive Slurm endpoint startup and
  prevents one endpoint from serving multiple projects.
- **Naming the service "Conversation Service"**: collides with UMRS's ownership of
  conversation *data*; "Assistant Service" keeps ownership unambiguous.
