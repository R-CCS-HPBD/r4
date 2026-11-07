# ADR-026: sat_kg Disposition — Deprecate, Do Not Migrate, Do Not Remove

**Status:** Accepted
> **Date:** 2026-08-15
> **Relates to:** ADR-024 (knowledge module boundary), docs/knowledge-graph-convergence-2026-08-14.md, migration 0060 (sat_kg durability), migration 0069 (canonical multi-graph)

## Context

Two knowledge-graph stores coexist in UMRS:

| Aspect | Canonical (`knowledge_nodes`/`knowledge_edges`) | Satellite (`sat_kg_nodes`/`sat_kg_edges`) |
|--------|--------------------------------------------------|--------------------------------------------|
| Migration | 0055 + graph scoping 0069 | 0060 (K1 satellite durability) |
| Store | `KnowledgeStore` / `PgGraphSink` (compiled from IRs) | `knowledge_graph_api` — 25-relation typed-edge API (`InMemoryKnowledgeGraph` / `PgKnowledgeGraph`) |
| Scoping | project + graph_id | **none** (global, no project column) |
| Reachability | gateway `/gw/knowledge/*` | `/umrs/knowledge-graph/*` mounted in http_sidecar, **not** gateway-exposed |
| Consumers | ingest pipeline, K3 query, R4 client/CLI/Studio | direct-write API only; no r4 client methods; ADR-024 records the dormant `knowledge_graph/` satellite as the team-engine graph API home |

Verified consumers of `sat_kg_*` (2026-08-15): `umrs/knowledge_graph_api/pg_store.py` only; route consumers are tests plus direct writes via `api/knowledge_graph.py` (which the 0069 agent-mutation guard already covers). No production client path reaches it.

Duplication analysis: sat_kg duplicates 'typed edge graph in Postgres' but **not** semantics — canonical rows are compiler-derived and graph-scoped with registry/ontology bindings; sat_kg rows are writer-driven, unscoped, with a richer 25-relation vocabulary that the canonical compiler does not emit.

## Decision

**DEPRECATE.** Keep the tables, stores, and HTTP mounting exactly as they are (read-compat for existing rows and for the latent team-engine consumer); extend nothing; document the preference for the canonical store at every entry point.

- **Not MIGRATE:** sat_kg rows have no project column — automatic backfill into graph-scoped canonical storage is impossible without a manual, case-by-case project mapping. No consumer needs that mapping today.
- **Not REMOVE:** unsafe (existing durable rows; ADR-024's latent team-engine home), and unnecessary — the store costs no hot-path resources and is already invisible to external clients.
- New graph work targets the canonical store + registry (0069) only. If a real consumer later needs sat-style typed-edge queries at project scope, the exit path is a NEW canonical capability (richer relation vocabulary via graph-bound ontology, ADR-0069-family), not adoption of sat_kg.

## Consequences

- New contributors see one sanctioned graph store; sat_kg code paths carry a deprecation note at the API facade.
- Any future removal proposal must first inventory `knowledge_graph_api` importers (including the team-engine graph API need from ADR-024) and present a row-level migration for existing content.

## Rollout status (2026-09-11, exit wave A landed — consumer constructor flips)

- **Wave A done:** every `InMemoryKnowledgeGraph()`-by-default constructor on
  the consumer surface now resolves to
  `umrs/knowledge/canonical_graph_view.default_view(store=None)` — identical
  in-memory semantics (mirrored dicts, edge dedupe, direction-agnostic
  traversal) by construction, store-free wire today. Flipped consumers:
  `graph_reasoning.GraphNavigator`,
  `knowledge.knowledge_correction_controller`,
  `knowledge.knowledge_dashboard_controller`,
  `knowledge.research_readiness_controller`,
  `knowledge.adapters` (linker `fresh` view). Property-type hints widened to
  `InMemoryKnowledgeGraph | CanonicalGraphView`. Bug locomotive: the
  concurrent adapter constructor had already flipped before the leg
  completed.
- Plan lives at `docs/development/sat-kg-migration-plan-2026-09-11.md`
  (ACTIVE; Wave B type-DTO home DTO-domain decision exists next; Wave C is
  the sat facade + table tombstone migration — final).

### Wave B land (2026-09-11, `umrs/knowledge/graph_types.py`)

- **DTO home moved:** the three knowledge graph type vocabularies
  (`RelationType`, `KnowledgeNode`, `KnowledgeEdge`) moved byte-equivalent
  from `umrs/knowledge_graph_api/__init__.py` to
  `umrs/src/umrs/knowledge/graph_types.py`. Aliasing decision (plan open
  decision #2): **single transition alias on the facade itself** — the DEPRECATED facade re-exports its own names until wave C closes —
  no consumer sites changed anywhere inside or outside the tree, which
  keeps every current use line immutable byte-for-byte. More than the
  expected pieces: `canonical_graph_view` keeps a clean import of the home
  directly. Test-2 smoke on file watchers: facade-class identity,
  __post_init__ profile, and KnowledgeEdge state shape are pinned
  (`tests/knowledge` 757 green + 64 facade/canonical suites green).
### Wave C land (2026-09-11, sat facade + route family removed)

- **Full façade removal executed** — the deprecated surface no longer exists:
  `umrs/src/umrs/knowledge_graph_api/` (module + pg_store face) and
  `umrs/src/umrs/api/knowledge_graph.py` (tombstone-retired router family),
  plus the persistent PG face test files (`umrs/tests/test_knowledge_graph_api.py`,
  `umrs/tests/test_research_pg_stores_batch2.py`,
  `umrs/tests/test_sat_router_retired.py`).
- Consumer/type-vocabulary flips (every remaining import site): all
  `umrs.knowledge_graph_api`-consumers controlled output to
  `umrs.knowledge_graph_types` (types home from wave B); consumers whose
  construction was `InMemoryKnowledgeGraph()` moved to
  `canonical_graph_view.default_view(store=None)` (same in-memory mirror
  semantics). Mounting removals on `umrs.api.__init__` + `server/http_sidecar`
  cleanly dropped knowledge_graph_router; stray `register_project_graph` calls
  in `api/knowledge.py` removed (registry store was dead from the rose gate's
  own tombstone denial since R5).
- Lane registry (`umrs/shared/lanes.py`) and its premise test
  (`test_lane_registry.CLASSIFIED_FIXED`) updated to remove the deleted
  family; `test_surface_characterization` claims `knowledge-graph`-family as
  unserved correctly; `test_knowledge_project_api` smoke-annotations shrunk
  to knowledge+canonical only; every knowledge-graph admin decision sites
  kept honest across `test_knowledge_mutation_guard` + factories.
- **Table tombstone migration (2026-09-11, ops side):**
  `umrs/src/umrs/migrations/0089_drop_sat_kg.sql` — idempotent
  `DROP TABLE IF EXISTS sat_kg_edges / sat_kg_nodes`. **In satellite name
  scope: wastes knowing** `sat_embeddings` + `sat_knowledge_objects[versions,tombstones]` +
  `sat_tenants` were scoped out AFTER live-face verification — they belong to
  embeddings_api / knowledge_model / tenants (same prefix, different contract;
  verified before the migration). Their batch-2 conformance test
  (`umrs/tests/test_research_pg_stores_batch2.py`) was restored after the
  earlier full-file removal in wave B/C accidentally took those live faces;
  the sat graph trio inside it is permanently removed, everything else
  passes at minimum (20/20 on both memory/pg memory params when store
  available).
- **Gate evidence for the whole wave**: knowledge families 249 + 733 +
  canonical/adapters 64+81 green; surface-characterization, lane registry,
  correctness-of-route removal across `test_knowledge_project_api` ✓; full
  scoped local gate GREEN: complexity/C901/lizard/orphan/every other
  hard-fail validator ✓; umrs suites' only remaining fails are the
  pre-existing documented in-flight baseline classes (documented twice in
  this ledger's previous waves). No unexpected redimplify anywhere.
- Gate evidence: consumer families `graph_reasoning` 19, knowledge suites
  733, adapters 29, sat persistent face 26 green; full scoped local gate
  green. Complexity: two pre-existing hotspots in touched files split below
  both gates (`get_knowledge_summary`, `_categorize_node`) with behaviour
  preserved.
