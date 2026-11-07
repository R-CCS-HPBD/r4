# ADR-024: Knowledge Module Boundary

**Status:** Accepted
> **Date:** 2026-08-02
> **Relates to:** K2 of `docs/development/the backbone proposals note (2026-07-31; archived 2026-10-19), ADR-013 (knowledge portability), ADR-017 (a2l bridge), K1 (satellite durability)

## Context

Five packages own knowledge-adjacent state in UMRS:

| Package | Role | Size/State |
|---------|------|-----------|
| `umrs/knowledge/` | Composition layer: IR, ingest, store, persistence, bundles, readiness, verification, project facade | ~18k LOC, production |
| `umrs/knowledge_model/` | Knowledge-object model store (versioned + tombstones, PG or memory) | satellite, durable since K1 |
| `umrs/knowledge_graph/` | Knowledge-graph node/edge API store | satellite, durable since K1 |
| `umrs/scientific_memory/` | Hypothesis/experiment memory store | satellite, durable since K1 |
| `umrs/evidence/` | Evidence item store | satellite, durable since K1 |

Before this ADR, nothing stated what may import what. Measured import graph
(2026-08-02):

- **Satellites import nothing** from `umrs.knowledge`, `umrs.api`, `umrs.server`,
  or each other.
- `umrs/knowledge/` imports satellites only via adapter/composition modules
  (`adapters.py`, controllers) plus one function-scoped lazy composition import
  (`knowledge/ingest.py → umrs.api.catalog_store`).
- `umrs/api/` routers and `umrs/server/http_sidecar` mount satellites and
  `knowledge/` services for external consumers.
- The a2l bridge (ADR-017) uses `knowledge/pg_graph_sink.py`, **not** the
  `knowledge_graph` satellite; that satellite currently has no production
  importers (dormant, kept as the team-engine/graph API home).

Risk without a documented boundary: `knowledge/` (18k LOC) and the satellites
grow import cycles (satellite → knowledge helpers → satellite store), making
the K3 query facade and future K1-style migrations untestable in isolation.

## Decision

### Layering

```
┌─────────────────────────────────────────────────┐
│  Consumers: umrs/api/*, umrs/server/* (mount)    │
├─────────────────────────────────────────────────┤
│  Composition: umrs/knowledge/                    │
│    may import: satellites, foundation, contracts │
│    may NOT import: umrs.api, umrs.server         │
├─────────────────────────────────────────────────┤
│  Satellites (leaf stores, import nothing):       │
│    knowledge_model, knowledge_graph,             │
│    scientific_memory, evidence                   │
└─────────────────────────────────────────────────┘
```

### Rules (enforced by `tools/import_boundary_validator.py`)

1. **Satellites are leaves.** `umrs.knowledge_model`, `umrs.knowledge_graph`,
   `umrs.scientific_memory`, `umrs.evidence` must not import `umrs.knowledge`,
   `umrs.api`, or `umrs.server` — module-level or lazy.
2. **Composition layer stays below the edge.** `umrs.knowledge` must not import
   `umrs.api` or `umrs.server`. Function-scoped *lazy composition imports*
   (dependency-injection fallbacks, e.g. default store factories) are allowed
   (`allow_lazy=True`), matching the `jobs/` precedent.
3. **Consumers mount, never get mounted.** New external surfaces go in
   `umrs/api/` (HTTP) or `umrs/server/` (ZMQ); never add API routes inside
   satellites or `knowledge/`.
4. **K3 query facade placement.** The unified knowledge query facade belongs to
   `umrs/knowledge/` or a new `umrs/api/` read route composing satellite and
   knowledge stores via the adapters — never inside a satellite.

### Non-goals

- `umrs/knowledge_graph` remains dormant by design; this ADR does not retire
  or rewire it. If a future batch adopts it as the graph API home, the same
  leaf rule applies.
- Satellite-internal structure (PG vs memory backend factories) unchanged.

## Consequences

### Positive

- Import cycles between `knowledge/` and satellites structurally impossible
  (hard-fail validator rules, same tier as the UMRS internal foundation rules).
- K1-style durability migrations stay testable: satellites carry zero upward
  deps, so their tests never drag in API/server layers.
- K3 facade has an unambiguous home and a clean dependency fan-in.

### Negative / costs

- One tolerated lazy composition import (`knowledge/ingest.py` meta-store
  fallback) — documented; eliminate only by moving the factory, which is a
  separate composition refactor.
- Validator rule additions are review-required changes (mutation policy).

### Validation

- `tests/test_import_boundary_validator.py` — K2 rule fixtures: satellite
  importing `umrs.knowledge` is caught; `knowledge/` module-level api import
  is caught; function-scoped lazy composition import is tolerated.
- Full-repo run: zero violations with the new rules enabled.
