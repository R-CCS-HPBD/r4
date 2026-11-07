# Integration Policy & Third-Party Frameworks

This document defines how the R4 Platform integrates with third-party agent frameworks, orchestration libraries, and community tools.

## Data Transfer Architecture (Phase 2l, 2026-04-27)

The platform supports **two transport tiers** for client → UMRS data, chosen by deployment topology rather than by configuration drift:

### Tier A — External users (HTTP via Gateway)

Default for any client that **does not** have SSH access to the cluster.

```
client ── HTTPS PUT ──▶ Gateway ── streaming ──▶ UMRS ── MinIO creds ──▶ S3
                       (auth, rate limit,
                        audit, size cap)
```

* **Auth:** Bearer token validated by Gateway
* **Audit:** Every byte traverses the Gateway audit pipeline
* **Streaming:** `proxy_request_buffering off` + chunked transfer in NGINX (no temp files)
* **Presigned URLs:** **Not** used for client uploads — they are reserved for compute-node ↔ S3 traffic so MinIO credentials stay off compute nodes

### Tier B — SSH-tunnel users (ZMQ direct to UMRS)

For users with cluster SSH access who want maximum throughput for large scientific data.

```
client ── SSH tunnel (-L 5555:127.0.0.1:5555) ──▶ UMRS ZMQ (PULL/ROUTER)
                    │
        SSH = the auth gatekeeper
```

* **Auth:** Existing SSH access on the cluster is the credential
* **Bind invariant:** UMRS binds ZMQ sockets to **`127.0.0.1` only** (not `0.0.0.0`) when `UMRS_TUNNEL_MODE=true` so the only way to reach port 5555 is through an SSH tunnel
* **Selection:** r4 SDK chooses ZMQ when `UMRS_TUNNEL_MODE=true` *or* `UMRS_SERVER_HOST=127.0.0.1`; falls back to Tier A on connect failure
* **Audit:** Captured in UMRS job logs (Gateway is not on the path)

> **Hard rule:** ZMQ on UMRS MUST NOT bind to `0.0.0.0` in production. The SSH-tunnel boundary is the security boundary; binding wider would let any cluster user reach UMRS without an SSH session of their own.

---

## CLI Ownership Boundary

The platform exposes three command-line entrypoints. They are not interchangeable; each exists for a different trust boundary.

| CLI | Owner | Audience | Allowed Scope | Examples |
| --- | --- | --- | --- | --- |
| `r4` | R4 platform UX | End users, notebook users, workflow authors | Gateway-mediated user workflows only | `r4 chat`, `r4 model list`, `r4 endpoint up`, `r4 workflow list`, `r4 job status` |
| `umrs` | UMRS transport/debug client | Operators and developers validating UMRS internals | Direct UMRS control-plane diagnostics and migration helpers; no default admin authority | `umrs ping`, `umrs job list`, `umrs model list`, `umrs workflow run`, `umrs monitor` |
| `umrs-server admin` | UMRS server/operator tooling | Site admins on trusted hosts | Offline user, token, Slurm account, and daemon-local administration | `umrs-server admin bootstrap`, `umrs-server admin user add`, `umrs-server admin token generate`, `umrs-server admin slurm accounts` |

Hard rules:

- `r4` stays thin: it may wrap Gateway and UMRS APIs for user experience, but it must not own durable workflow state, model registry state, project membership, or admin-token storage.
- `umrs` is not the admin CLI. Legacy `umrs admin ...` is disabled permanently; do not add an escape hatch or migration bypass.
- `umrs-server admin` is the only supported token/user/Slurm enrollment surface. It must run on a trusted operator host or through an explicit operator SSH path.
- New commands that mutate operator-owned state must be added under `umrs-server admin`, not `r4` or `umrs`.
- New commands that are part of normal research workflows should be exposed first through Gateway/UMRS APIs and then wrapped by `r4`.

---

## Core Principle: Contract-First, Framework-Agnostic

The platform provides **stable contracts** (Gateway APIs, MCP protocol, RAG endpoints) that any agent framework can consume. We do NOT mandate a specific orchestration framework.

The codebase mirrors that policy in its package layout: `r4-contracts` contains the stdlib-only public DTOs and pre-flight validators, `r4` wraps those contracts for user experience, Gateway owns the external policy edge, and UMRS remains the authoritative server-side implementation owner. See [Library Group Structure](LIBRARY_GROUP_STRUCTURE.md) for the import rules.

```
┌─────────────────────────────────────────────────────────────────┐
│                     User Agent Code                              │
│   (LangChain, LlamaIndex, mcp-use, custom loops, etc.)         │
└─────────────────────────────────────────────────────────────────┘
                              │
                    Uses standard contracts
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Platform Gateway                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ /gw/llm/*    │  │ /gw/mcp/*    │  │ /gw/rag/*    │          │
│  │ OpenAI API   │  │ MCP Protocol │  │ RAG Endpoints│          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              │
                    Platform internals
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  UMRS (RAG) │ Mini-RiVault (LLM) │ MCP Servers │ S3/PGVector  │
└─────────────────────────────────────────────────────────────────┘
```

## What We Provide

### 1. Stable API Contracts

| Contract | Description | Stability |
|----------|-------------|-----------|
| `/gw/llm/v1/*` | OpenAI-compatible chat completions | Stable |
| `/gw/rag/*` | RAG ingestion, search, query | Stable |
| `/gw/mcp/*` | MCP tool proxy and registry | Stable |
| JWT auth | Token-based authentication | Stable |
| Project scoping | Multi-tenant isolation | Stable |

### 2. r4 SDK Primitives

The `r4` SDK provides low-level primitives that work with any framework:

```python
from r4 import Client, auth

# Authentication works everywhere
client = Client()

# These primitives can be composed with any framework
client.chat(messages=[...])         # LLM
client.rag.search(query="...")      # RAG search
client.rag.query(query="...")       # RAG + LLM
client.mcp.call_tool(name, args)    # MCP tools
```

### 3. Optional Framework Adapters

We provide **optional** adapters for popular frameworks via extras:

```bash
# Core SDK only
pip install r4

# With mcp-use integration
pip install r4[mcp-use]

# With LangChain integration  
pip install r4[langchain]

# With LlamaIndex integration
pip install r4[llamaindex]

# All integrations
pip install r4[all-integrations]
```

## Officially Supported Integrations

### mcp-use (Optional)

[mcp-use](https://github.com/pietrozanini/mcp-use) provides Python/TypeScript utilities for MCP agent interactions.

**Status**: Optional adapter (`r4[mcp-use]`)

**Use case**: Users who want to leverage mcp-use's agent patterns while connecting to our Gateway.

```python
from r4.integrations.mcp_use import create_gateway_mcp_client

# Creates an mcp-use compatible client pointing at Gateway
mcp_client = create_gateway_mcp_client(
    gateway_url="https://gateway.example.com",
    project_id="my-project",
)

# Use with mcp-use agent patterns
from mcp_use import Agent
agent = Agent(client=mcp_client)
```

**Why optional, not core**:
- May encourage patterns unsuitable for HPC-secure environments
- Platform retains control over tool allowlisting, audit, egress

### LangChain (Optional)

**Status**: Optional adapter (`r4[langchain]`)

**Use case**: Users building complex chains/agents with LangChain.

```python
from r4.integrations.langchain import (
    R4LLM,
    R4Retriever,
    R4Toolkit,
)

# LangChain-compatible LLM wrapper
llm = R4LLM(gateway_url="...", project_id="...")

# LangChain-compatible retriever
retriever = R4Retriever(gateway_url="...", collection="my-docs")

# Use in LangChain chains
from langchain.chains import RetrievalQA
qa = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)
```

### LlamaIndex (Optional)

**Status**: Optional adapter (`r4[llamaindex]`)

**Use case**: Users building RAG applications with LlamaIndex.

```python
from r4.integrations.llamaindex import (
    R4LLM,
    R4VectorStore,
)

# Use platform as LlamaIndex backend
from llama_index.core import VectorStoreIndex
index = VectorStoreIndex.from_vector_store(
    R4VectorStore(collection="my-docs")
)
```

## Licensing Policy

### Core Platform: MIT

All core platform components are MIT licensed:
- `r4` SDK
- Gateway
- UMRS
- MCP servers

### AGPL Dependencies: AVOID in Core

**Policy**: Do not include AGPL-licensed dependencies in core platform packages.

**Rationale**: 
- Many organizations cannot accept AGPL for network services
- Research institutions often have strict licensing requirements
- Users may need to run modified versions privately

**If you want to use AGPL tools**:
- Keep them as external/community options
- Document clearly in `OPTIONAL_INTEGRATIONS.md`
- Never import them from core packages

### Dependency License Check

Before adding any dependency, verify:

```bash
# Check license of a package
pip show <package> | grep License

# Use license checker
pip install pip-licenses
pip-licenses --packages <package>
```

## Custom RAG Pipelines

> **Status 2026-10-10 (ADR-093): not implemented.** There is no
> `client.rag.register_pipeline`, and `client.rag.upload(file_path, *,
> project_id, metadata)` takes no `files`, `collection` or `pipeline`
> arguments. What exists today is one server-side retrieval core:
> `client.rag.retrieve(query, project_id=...)` (`GET /gw/rag/retrieve`)
> returns scored passages and a sufficiency status. Its lanes, cut and
> reranker are set by the site's `UMRS_RAG_*` knobs and by the reading
> model's size profile, not per pipeline. See
> [ADR-093](../../adr/093-retrieval-for-small-local-models.md) and the SDK
> reference. The YAML and calls below are a proposal kept for design
> discussion.

Users can define custom RAG pipelines while keeping the platform as data plane:

### 1. Pipeline Definitions (YAML)

```yaml
# my-pipeline.yaml
name: domain-specific-rag
version: "1.0"

ingestion:
  chunker:
    type: recursive
    chunk_size: 1000
    overlap: 200
  
  preprocessors:
    - type: metadata_extractor
      fields: [title, author, date]
    - type: custom
      module: my_org.preprocessors.domain_parser

embedding:
  model: BAAI/bge-large-en-v1.5
  
retrieval:
  reranker:
    model: BAAI/bge-reranker-large
    top_k: 10
```

### 2. Register Pipeline

```python
from r4 import Client

client = Client()
client.rag.register_pipeline(
    name="domain-specific-rag",
    config_path="my-pipeline.yaml",
)
```

### 3. Use Custom Pipeline

```python
# Ingest with custom pipeline
client.rag.upload(
    files=["paper1.pdf", "paper2.pdf"],
    collection="domain-papers",
    pipeline="domain-specific-rag",
)

# Query uses the pipeline's retrieval config
results = client.rag.query(
    query="What are the key findings?",
    collection="domain-papers",
)
```

## Custom MCP Servers

### Mode 1: User-Hosted MCP Servers

Users can run their own MCP servers and register them with Gateway:

```python
from r4 import Client

client = Client()

# Register user's MCP server
client.mcp.register_server(
    name="my-custom-tools",
    url="http://my-server:8080",
    description="Project-scoped custom MCP server",
    metadata={"project_scope": "my-project"},
)
```

**Requirements for user MCP servers**:
- Must be reachable from Gateway (or provide SSH tunnel)
- Must implement MCP protocol
- Tools are audited via Gateway

### Mode 2: Platform-Provided MCP Servers

Platform provides curated, security-hardened MCP servers:

| Server | Tools | Security |
|--------|-------|----------|
| `filesystem` | read_file, write_file, list_dir | Sandboxed to project dirs |
| `database` | query_sql | Read-only, parameterized |
| `webfetch` | fetch_url | Domain allowlist |
| `code` | run_python, run_shell | Isolated container |

## BYO Agent Framework

**Any agent framework works** if it speaks our contracts:

```python
# Example: Custom agent loop
from r4 import Client

client = Client()

def my_agent_loop(task: str):
    messages = [{"role": "user", "content": task}]
    
    while True:
        # Call LLM via Gateway
        response = client.chat(
            messages=messages,
            tools="auto",
        )
        
        # Check for tool calls
        if response.tool_calls:
            for call in response.tool_calls:
                # Execute via Gateway MCP
                result = client.mcp.invoke(call.name, **call.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.result if result.success else f"Error: {result.error}",
                })
        else:
            # No more tool calls, return final answer
            return response.content
```

## Summary

| Aspect | Policy |
|--------|--------|
| Core platform | Framework-agnostic, contract-first |
| SDK | Primitives + optional adapters |
| Official integrations | mcp-use, LangChain, LlamaIndex (all optional) |
| Licensing | MIT core; avoid AGPL in core |
| Custom RAG | Pipeline definitions + plugin interface |
| Custom MCP | User-hosted or platform-provided |
| Agentic patterns | BYO framework, use our contracts |

## Related Documentation

- [Gateway API Reference](../gateway/docs/API.md)
- [MCP Servers Guide](../mcp-servers/README.md)
- [r4 SDK Reference](../r4/README.md)
- [Security Model](./SECURITY.md)
