# R4 Anti-Roadmap

> What we will **not** build, merge, or accept into the r4 + UMRS core.
>
> This is a living document. Strong user demand **and** strong technical
> rationale can change it — but the default answer to everything below is
> "no, and here is why."

## Purpose

Explicit scope guardrails prevent drift, reduce review cost, and keep
contributors from building features that will never land. Inspired by
openclaw's `VISION.md` "What We Will Not Merge" section.

---

## Scope Invariants

### Parallel orchestration in r4 or gateway

Do not add local job stores, workflow registries, endpoint provisioners,
or alternative state machines in `r4/` or `gateway/`.

UMRS is the implementation owner for:
- Durable jobs and retry/idempotency semantics
- Endpoint provisioning and registry contracts
- Workflow registration, persistence, and execution
- Pipeline and workflow definition models
- RAG execution/indexing semantics

r4 owns the *integration layer* (CLI, SDK, TUI, Companion, chat,
Jupyter, MCP wrappers, agent). gateway owns auth, routing, policy, and
audit.

### Parallel vector index in r4

Hybrid retrieval, BM25, knowledge graphs — all live in UMRS. r4 and
gateway are consumers, never alternative indexers.

### Agent-hierarchy frameworks

Manager-of-managers, nested planner trees, multi-level planning
architectures — these do not become a default architecture. Sub-agents
(Phase 2.4 of the implementation plan) are **flat tasks** backed by UMRS
durable jobs, not recursive planners.

### Re-implemented MCP transport

r4 uses the Hatchling runtime already integrated with UMRS. Do not
re-implement MCP stdio/SSE/HTTP transports directly in r4.

### 50+ provider adapters in core

The `Provider` interface (Phase 0.2) lets users add their own provider.
Only `LiteLLMProvider`, `OllamaProvider`, and `OpenAICompatibleProvider`
ship by default (`MiniRivaultProvider` never shipped — retired without an
implementation 2026-10-19, cleanup C-2(ii)). Everything else is a
user-installed plugin.

---

## Feature Categories We Will Not Merge

### Commercial service wrappers without security/policy value

A wrapper around a commercial API that doesn't add security, audit, or
HPC-relevant behaviour beyond what `OpenAICompatibleProvider` already
offers.

### MCP work that duplicates existing MCP, plugin, or Hatchling paths

If the capability already exists via the Hatchling MCP runtime, UMRS
plugins, or the r4 MCP client, adding a parallel path increases
maintenance without adding value.

### Full-doc translation sets

Deferred; we plan AI-generated translations later, not manual bulk
translation PRs.

### Wrapper channels without a clear capability or security gap

A new messaging channel (Slack, Discord, Telegram…) integration that
merely wraps an already-supported webhook/n8n path without adding
authentication, pairing, or security enforcement.

### Runtime downloads inside inference containers

Model files must be pre-staged or downloaded via controlled UMRS jobs.
No runtime `transformers.AutoModel.from_pretrained()` calls inside
mini-rivault inference containers.

### SSL between internal platform components

HTTP-only inside the platform boundary. Document firewall rules per
deployment instead.

### Direct file uploads to vLLM/LiteLLM

Files always go through UMRS → S3 → presigned URL. The gateway performs
the presigned-URL fetch to centralise auditing, size enforcement, and
PII control.

### Langfuse / third-party observability as a hard dependency

Our observability stack is OTel + Prometheus + Loki. Third-party
integrations are optional plugins, never core dependencies.

---

## How to challenge this list

Open an issue tagged `anti-roadmap-review` with:

1. The specific bullet you want to revisit.
2. The user demand (who needs this, how many, what's the workaround today).
3. The technical rationale (why the existing alternative is insufficient).
4. The maintenance cost (who will own it long-term).

The maintainers will evaluate and respond within one sprint cycle.
