# 03 — onyx (formerly Danswer)

> Source: [tmp/onyx/README.md](../../../tmp/onyx/README.md),
> [tmp/onyx/AGENTS.md](../../../tmp/onyx/AGENTS.md),
> directory walk of `backend/onyx/`, `backend/onyx/connectors/`,
> `backend/onyx/deep_research/`, `web/`.

## What it is

Onyx is an **open-source enterprise AI search and chat platform** — the
"application layer" on top of LLMs. Single curl-bash install gives you a
Next.js web app, FastAPI backend, Postgres, Redis, Vespa, model server, and
~50+ connectors.

## Philosophy

- **Application-layer, not infra layer**: Onyx assumes you bring the LLM
  (self-hosted Ollama / LiteLLM / vLLM, or hosted Anthropic/OpenAI/Gemini).
- **Connector-first**: ingestion of company documents is the primary
  on-ramp, not chat alone.
- **Two-tier deployment**: "Lite" (chat UI, <1 GB RAM) and "Standard" (full
  RAG, workers, model servers).
- **Two editions**: MIT Community Edition + closed Enterprise Edition with
  RBAC, SSO, analytics, whitelabel, custom code execution.
- **Strict typing** everywhere (Python + TypeScript).
- **Tasks always have expirations** — never enqueue Celery tasks without
  `expires=` (a hard rule in `AGENTS.md`).
- **Frontend-first network** — call `localhost:3000/api/...` (Next.js
  proxies), never go straight to the backend.
- **DB ops only under `backend/onyx/db/`** — no inline SQL elsewhere.

## Architecture

### Backend (Python 3.11 / FastAPI / SQLAlchemy / Alembic / Celery)

`backend/onyx/`:

| Module | Purpose |
|--------|---------|
| `auth/` | Authentication and authorization (OAuth2, SAML) |
| `chat/` | Chat orchestration and LLM interaction |
| `connectors/` (~55) | Source-data connectors (Slack, Confluence, GitHub, Notion, Jira, Google Drive, Salesforce, Zendesk, …) |
| `db/` | SQLAlchemy models, all DB ops |
| `document_index/` | Vespa integration |
| `federated_connectors/` | External-search connectors |
| `llm/` | LLM provider integrations |
| `server/` | API endpoints |
| `deep_research/` | Multi-step research loop (`dr_loop.py`) |
| `kg/` | Knowledge-graph processing |
| `tools/` | Agent tools (web search, code exec, file gen, etc.) |
| `mcp_server/` | First-class MCP server hosted by Onyx itself |
| `voice/` | TTS/STT |
| `image_gen/` | Image generation |
| `file_processing/` | Chunking, embedding, indexing |
| `secondary_llm_flows/` | Cheaper LLM for guard/format/intent |

### Background workers (Celery, multi-tenant)

- **Primary** — coordinates core tasks, connector lifecycle, vespa sync.
- **Docfetching** — pulls from connectors, watchdogs stuck connectors.
- **Docprocessing** — chunk → embed → write to Vespa → upsert metadata.
- **Light** — fast vespa ops, permission sync, group sync.
- **Heavy** — pruning.
- **KG** — knowledge-graph clustering.
- **Monitoring** — heartbeat, queue depth, memory.
- **User file processing** — user uploads.
- **Beat** — scheduler with `DynamicTenantScheduler` for multi-tenant.

Thread-pool workers, priority queues, Redis coordination, Postgres state.

### Frontend (Next.js 15 / React 18 / TS / Tailwind)

- `web/src/app/` (App Router pages), `web/src/components/`, `web/src/lib/`.
- Desktop app under `desktop/`, widget under `widget/`.

### Infrastructure

- Postgres + Redis + **Vespa** (vector + keyword index) + MinIO + model server.
- Helm / Terraform / Docker Compose.
- Multi-tenant Alembic schemas (`alembic_tenants/`).
- `cli/` for ops, `extensions/` for custom code, `examples/`, `tools/`.

## Feature surface (CE)

- **Agentic RAG** — hybrid (vector + keyword) retrieval plus an agent layer.
- **Deep Research** (`backend/onyx/deep_research/dr_loop.py`) — multi-step
  research producing in-depth reports.
- **Custom Agents** — bespoke instructions, knowledge, actions.
- **Web search** — Serper / Google PSE / Brave / SearXNG / Firecrawl / Exa
  + in-house crawler.
- **Artifacts** — generates downloadable docs / graphics.
- **Actions & MCP** — agents call external apps with flexible auth.
- **Code execution sandbox** — for analysis, charts, file edits.
- **Voice mode** — TTS + STT.
- **Image generation**.
- **Knowledge graph** built from indexed docs.

## Enterprise (closed)

- Sharing chats / agents across an org, SSO (Google / OIDC / SAML), SCIM
  user provisioning, RBAC, analytics, query history audit, custom code
  (PII redaction, query rejection, custom analysis), whitelabel.

## Standout ideas worth borrowing

1. **Connector framework** with a registry (`connectors/registry.py`,
   `connectors/factory.py`, `connectors/interfaces.py`) — UMRS only ingests
   uploaded files. A real connector model (Slack/Notion/Jira/GDrive/HF)
   would be a major user-facing win for r4 RAG.
2. **Deep Research loop** as a separate, bounded subsystem
   (`deep_research/dr_loop.py`) — distinct from chat. r4 has `r4 ask search`
   but it's flatter; a research loop with bounded depth + report artifact
   is more user-visible.
3. **Celery worker matrix** with explicit priority lanes (Light/Heavy/
   Doc-fetching/Doc-processing/KG/Monitoring/Beat) — much clearer than a
   single job queue. Even if we keep UMRS jobs, this *labelling*
   discipline + a `monitoring` worker is worth importing.
4. **`expires=` mandatory on every Celery task** — equivalent rule for
   UMRS jobs would prevent unbounded queue growth.
5. **Knowledge Graph processing** as a clustering pass after indexing —
   our RAG is currently flat embeddings; a KG layer is a known quality lift.
6. **Hybrid vector + keyword index (Vespa)** — vs. our pgvector-only path.
   Vespa is heavy; the *idea* of hybrid retrieval is what to import.
7. **Two-tier deployment** (Lite vs. Standard) — we already have multi-tier
   mini-rivault, but offering a "Lite r4" install for laptops/login-only
   users would dramatically reduce onboarding friction.
8. **Multi-tenant Alembic schema** (`alembic_tenants/`) — our project model
   is single-schema. Per-project schema isolation is a hard upgrade later;
   worth designing for now.
9. **`secondary_llm_flows/`** — explicit pattern of using a smaller / cheaper
   LLM for guarding, formatting, intent classification, leaving the big
   model for the actual answer. Big cost-saver on HPC.
10. **Frontend always proxies the backend** — single audit and policy point.
    Our gateway already does this; document it as a hard rule.
11. **Federated connectors** — search external systems live without
    indexing them. Useful for data we cannot legally index (NDA / PHI).
12. **First-class voice mode** on the server, not the client. Better UX for
    Jupyter / mobile clients than client-side STT.
