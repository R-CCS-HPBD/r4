# ADR-025: Research Session Schema (Durable Session Identity)

**Status:** Accepted (schema + API landed 2026-08-06)
**Date:** 2026-08-06
**Relates to:** ADR-009 (research loop architecture), ADR-002 (durable job/attempt separation),
ADR-021 (workflow model unification), `r4/src/r4/runtime/session_view.py` (M5 aggregate view)

## Context

Milestone 5 shipped `research_session_view`: a read-only projection that answers
"what is happening in my research project right now?" by aggregating runtime
capabilities, loops, and knowledge on every call. It deliberately has **no durable
identity** — "session-ness" is recomputed per request and nothing survives a gateway
restart or a client switch.

The gaps this left:

1. **No stable thread identity.** A user investigating "seismic peak extraction"
   across two terminals and a VS Code webview re-derives the same loose grouping
   each time; nothing names it, titles it, or makes it shareable.
2. **Mixed-venue research is fragile.** In hybrid mode, UMRS loops persist in
   Postgres but local (SQLite) loops vanish with the client. There is no single
   durable record listing *all* loops of one investigation.
3. **Loop ↔ research intent linkage is implicit.** Loops record `research_goal`
   text but nothing connects a loop to the ongoing investigation a teammate or a
   later client should resume from.

## Decision

Introduce a **Research Session** as a first-class, durable entity owned by UMRS.

### D1 — Entity

A session is an umbrella over one thread of investigation:

```
ResearchSession
├── session_id            TEXT   PK   ("sess-…")
├── project_id            TEXT       owning project
├── title                 TEXT       human label; auto-derived from goal if unset
├── research_goal         TEXT       the investigation's intent
├── status                TEXT       active | paused | completed | archived
├── authoritative_runtime TEXT       "umrs" | "local" | "hybrid"
├── metadata              JSONB      free-form annotations
├── created_at / updated_at / archived_at
└── loops (research_session_loops)
      ├── session_id → FK cascade
      ├── loop_id
      ├── venue      TEXT            "umrs" | "local"  (local loops are referenced
      │                                             but never stored in UMRS itself)
      ├── loop_role  TEXT            "auto" | "attached"
      └── attached_at
```

### D2 — Ownership and home

UMRS owns the schema and the REST surface, following the established per-component
convention (jobs, MCP, model registry, governance):

- `umrs/src/umrs/research_sessions/schema.py` — `RESEARCH_SESSION_SCHEMA_VERSION = 1`,
  `ensure_schema(conn)` tracked in the `schema_versions` table with component
  `research_sessions`.
- `umrs/src/umrs/research_sessions/store.py` — psycopg store with sqlite3 fallback
  (same pattern as other stores; tests run without a Postgres pool).
- `umrs/src/umrs/api/research_sessions.py` — FastAPI router
  `POST/GET  /umrs/research/sessions`,
  `GET /umrs/research/sessions/{id}`,
  `PATCH /umrs/research/sessions/{id}` (title/status transitions),
  `POST /umrs/research/sessions/{id}/loops` (attach),
  `GET /umrs/research/sessions/{id}/loops`.
- Schema bootstrap: `_init_research_sessions_store(app, db_pool)` in the HTTP
  sidecar alongside the other `_init_*` helpers.

### D3 — Session ↔ loop linkage

`StartLoopRequest` gains an **optional** `session_id`. When present, loop start
auto-registers the loop under that session (`loop_role = "auto"`). Surfaces that
want grouping pass the same `session_id` to every loop they start; surfaces that
don't are unaffected (additive, backward compatible).

`r4.runtime.LoopSpec` gains the same optional field so `UmrsRuntime` and
`HybridRuntime` thread it through to the UMRS API and stamp it onto the returned
`LoopState.metadata` (view-layer annotation, same as the M4 venue stamp).

### D4 — Local loops are references, not rows

Hybrid/local venues never store loop state in UMRS (single-authority invariant):
the session table holds only a `(loop_id, venue="local")` reference. A session can
therefore name a local loop across clients, while loop execution truth stays local.

### D5 — SCHEMA_VERSION evaluation

New component (`research_sessions` @ version 1). **No bump** of jobs (4), model
registry (8), MCP (1), governance (4), or context (1) — the addition is purely
additive with no shared columns. The global `/ops/schema-versions` aggregation
picks it up automatically.

### D6 — What the view becomes

`session_view` keeps its shape (never fail, offline-graceful). When the resolved
runtime is UMRS-backed, loop items now include `session_id` when their loop was
started under a session, and the view gains a `sessions` section listing durable
session identities for the project. The aggregate fallback remains for offline and
local-only modes.

## Non-goals (named, reserved)

- Session-as-run bookkeeping (results, findings aggregation per session) — a later
  milestone builds projections **over** session membership, it is not part of the
  entity.
- Owner claim anchoring (`X-User-ID` → `owner_id` column with ACL) — v1 records the
  `metadata["created_by"]` hint when forwarded; ACL enforcement is gateway-side and
  already applied uniformly via nginx.
- Moving `session_view` from projection to materialization — the view stays a view.

## Consequences

- **Positive:** durable cross-client identity for an investigation; hybrid grouping
  survives restarts; the ACP companion/webview can address "the session" by id; loop
  lineage gains a real foreign key via `research_session_loops`.
- **Negative:** one more per-component schema + router to own; surfaces that skip
  `session_id` see no change (by design).
- **Compatibility:** additive only — migration file `0066_research_sessions.sql` +
  per-component `ensure_schema`; no existing table touched; loop start without
  `session_id` behaves exactly as before.
