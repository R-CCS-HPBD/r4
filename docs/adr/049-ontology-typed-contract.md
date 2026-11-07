# ADR-049 — Ontology as typed contract: registry, inference legs, query projection (G-P1-6…9, WP-21)

**Status:** Proposed
- **Implements:** gaps G-P1-6 (no reasoning), G-P1-7 (no ontology registry table),
  G-P1-8 (vocabulary not ontology-driven), G-P1-9 (no query-time projection),
  filed 2026-09-13 after the COA (`aws/context-ontology-accelerator`) baseline audit
- **Related:** ADR-026 (sat_kg canonical projection), ADR-027 (RAG umbrella),
  migration 0069 (graph registry ontology binding)

## Context

The platform's ontology usage today: a frozen `Ontology` value object (id,
version, entity_types, relation_types) bound on graph registry rows, with
write-path validation in the compiler (`pg_graph_sink._validate_against_ontology`).
That is a *deny-list*, not an inference system. Against the COA baseline the
four gaps are:

- **G-P1-6 (largest)** — zero reasoning: no transitive closure, no declared
  inverse inference, no type subsumption. Only a static sat→canonical
  inverse *projection table* (migration utility) exists.
- **G-P1-7** — no `knowledge_ontologies` registry table; custom ontologies
  travel inline in graph provenance (un-versioned, un-queryable, unsharable).
- **G-P1-8** — the compiler emits relations from a hardcoded constant
  (`_EXTRACTION_RELATIONS`); the ontology only rejects. Custom ontologies
  cannot extend extraction.
- **G-P1-9** — traversal/search never consult the graph's bound ontology.

## Decision (normative)

Right-size to the platform's actual vocabulary instead of porting a reasoner:
the COA's HermiT/ELK class is disproportionate for a vocabulary of ~20
declared relations and 7 node types. Inference arrives as **computed legs
with explicit depth bounds and named provenance**, never a reasoner masquerade.

### Leg C — inference foundation (G-P1-6, FIRST; this wave)

1. Ontology value object gains `transitive_relations: frozenset[str]`
   (additive, default `{depends_on}`); serialization tolerant of absence.
2. `CanonicalGraphView.walk(start, relation_type, direction, max_depth)`
   — deterministic, cycle-safe BFS closure over a single declared relation.
   Memory-backed views compute over the mirror; store-backed views delegate
   to a PG recursive CTE (depth + path cycle guards) so closure does NOT
   require the mirror to be fresh. Cycle-safe by construction (path
   membership check), depth-capped by contract (never unbounded).
3. SQL shape is a pinned contract (recursive CTE with bind-arg depth guard
   and `NOT (node_id = ANY(path))`), executed through the store-worker
   (the pool stays on one loop per ADR arc 2026-09-13).

Nothing here fabricates inferred edges: closure results are RELATIONNAL
read-path results (node_id + depth), not graph mutations — the durable
store's row set stays exactly the written set.

### Leg B — query-time projection (G-P1-9)

Traversal and the K3 facade expose `graph.ontology` in responses; walk APIs
fail-closed on relations the bound ontology does not declare transitive
(`transitive_relations`). Follow C in a separate wave.

### Leg A — ontology registry table (G-P1-7)

`knowledge_ontologies` (id, version, entity_types, relation_types,
transitive_relations, description, owner/project, tombstone semantics like
asset_grants) + `ontology_for_graph` resolution prefers the registry;
inline-provenance remains only for the legacy path with a migration note.
Mutation-safety review precedes the schema, per the module docstring.

### Leg D — ontology-driven extraction (G-P1-8)

Compiler takes an optional ontology argument whose `relation_types`
extend the emitted set (additive on top of `_EXTRACTION_RELATIONS`);
validation keeps failing closed on anything else. This is the LAST leg,
because widening write output needs the registry (A) before it can bind.

## Consequences

- Reasoning exists as computed, bounded, provenance-honest read paths —
  matches platform scale and keeps every inferred claim re-derivable.
- Sequence C → B → A → D is not cosmetic: closure needs no new table (C
  first, directly on the canonical store), while ontology tables (A)
  gate extraction widening (D).
- A full reasoner port stays out of scope until a vocabulary exists that
  needs class-level subsumption — named explicitly, not silently deferred.

## Verification

- Leg C pins: deterministic BFS order/safety (cycle, depth cap, reverse
  direction, unknown start) over memory; recursive-CTE shape contract
  (guards present, binds ordered), plus a live-PG rehearsal receipt.
- Legs B/A/D land with their own pins in their own waves; the gate here
  is this ADR's legs recorded, not mass-unverified code.

## Rollout-status (2026-09-13, Legs C + B landed in one wave)

- **Leg C landed** (umrs `6f7421f0`): `Ontology.transitive_relations`
  (additive, default `{depends_on}`; serialized-absence tolerant);
  `CanonicalGraphView.walk()` — memory BFS + recursive-CTE store
  (`KnowledgeStore.walk_closure`) WITH SQL-level termination guards
  (bind-arg depth, path-membership cycle), routed through the store-worker;
  collapsed sat names fail CLOSED. 16 pins + live-PG rehearsal
  (cycle/cap/scoping/direction all correct).
- **Leg B landed** (same wave): constructor `ontology=` on the view;
  `walk()` refuses relations the bound ontology does not declare
  transitive (fail-closed, named error); `/umrs/knowledge/query` response
  carries `ontologies` blocks per selected graph (`ontology_error` named
  on unresolvable references, never smudged). 5 new pins (memory
  permissive-binding suite) + 1 live-PG parity pin + 1 facade block pin.
- Store-worker robustness completed en route: jsonb codecs registered
  in-worker, asyncpg PoolInit fixed via coroutine wrap, `result_value`
  for worker-routed reads; `_run_mutation` semantic split now explicit.
- Suites: umrs 4834/0 (136 skips incl. pg-infra autoskip), knowledge
  dir green under LIVE PG (dedicated rehearsal bench).
- **Remaining for the follow-up wave:** B-part-2 (projection on traversal
  surfaces beyond the query facade), Leg A (registry table), Leg D
  (ontology-driven extraction; gated on A).

## Rollout-status (2026-09-13, Leg A landed; G-P1-7 Closed)

- **0091 migration**: `knowledge_ontologies` (surrogate `record_id` PK,
  jsonb type sets, status CHECK) + partial live-index over untombstoned
  rows + project/status index. Applies cleanly on the rehearsal bench.
- **`OntologyRegistryStore`**: register → live coordinate → retire
  (tombstone, never DELETE) → re-register under the freed coordinate —
  live-PG lifecycle rehearsed end-to-end (`lab-live-onto` chain).
- **Resolution**: `ontology_for_graph(graph, registry=...)` prefers the
  registry's live row at the pinned (id, version) over inline provenance
  (legacy kept); unknown references still fail LOUD. `pg_graph_sink`
  wires the registry when a sync pool resolves; the query facade's
  ontology blocks resolve registry-first as well.
- **Pins (9)**: register/coordinate, live-coordinate conflict typed,
  retire-tombstone-row-kept, none-on-no-live, registry-beats-inline,
  inline-still-legacy, unknown-still-loud, defaults untouched.
- Small gate waves: q-query handler CCN back under threshold via
  `_ontology_blocks_for` extraction; F401 sweep.
- **G-P1-7 → Closed** in the register. Remaining legs: B-part-2
  (traversal surfaces beyond the query facade), D (extraction widening,
  gated on A — now unblocked, next wave).

## Rollout-status (2026-09-13, Legs D + B-part-2 landed; WP-21 gaps board refresh)

- **Leg D landed** (umrs, same day): `Ontology.extraction_fields`
  (tuple-of-pairs, hash-stable, serialization omission-tolerant); the
  sink splits `_upsert_base_edges`/`_upsert_extension_edges` (both under
  the CCN gate) with dep-style targets + deterministic `_ext_edge_id`
  ids + base-field shadow skip; the mis-declaration gate lives in
  `_validate_against_ontology` so inconsistency fails BEFORE truncation
  (never a mid-compile boom). 8 pins.
- **B-part-2 landed**: traversal detail `GET /umrs/knowledge/graphs/{id}`
  carries the resolved ontology identical block language to the query
  facade (3 pins: default, inline custom, unknown-named-not-smudged).
- Live-PG lifecycle rehearsal of the registry was already receipted at
  the Leg A wave; G-P1-7 and G-P1-8 are **Closed** in the register.
- Board: G-P1-6 Partial (subsumption + inverse read-expansion named as
  the follow-up reasoning leg); G-P1-9 Partial (type filtering on
  traversal rows); B-part-2 and D complete as scoped. WP-21's first
  substantive package wave is done.

## Rollout-status (2026-09-13, follow-up legs landed — WP-21 board complete, ALL gaps Closed)

- **G-P1-6 follow-up**: subtype lattice `Ontology.subtype_of` — declared
  lattice, transitive query expansion at parents (`query(node_type)`),
  cycle-safe by visited-set; subtype members outside `entity_types`
  refuse AT LOAD in `from_dict` (never silently widened). Sat inverse
  read-expansion on `get_related`: ANSWERED_BY family reads the canonical
  forward rows in the flipped role, dedupe-stable. Premise hold:
  exact-name reads byte-identical to pre-1.1.
- **G-P1-9 type filtering**: `query()`/relation-matched rows project
  through the bound ontology's `entity_types` for NON-default ontologies;
  the default ontology explicitly stays a no-op projection for sat
  semantic labels (premise law of the existing suite; narrow-vocab
  graphs filter honestly).
- Register: G-P1-6 and G-P1-9 flip to **Closed** — WP-21 board complete
  (4/4). 10 leg pins (test_ontology_reasoning) + suites green
  (umrs 4864/0; knowledge 784+10).
- Residual acceptance (documented, not carried as remaining gaps):
  subtype lattice has no registry-to-Leg A extraction
  (`extraction_fields` covers relations only); richer first-order
  subsumption inference remains the review-tier follow-up if a future
  vocabulary needs it — not overdue.
