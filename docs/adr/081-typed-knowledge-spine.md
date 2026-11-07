# ADR-081: Typed knowledge spine — classes beside the facet, a term registry with closure, validation in the store, claims with currency

**Status:** Proposed (2026-10-03)
**Date:** 2026-10-03
**Relates to:** ADR-049 (amended: its bounded inference legs move onto the write path), ADR-063 (kept: Postgres relational tables, no graph DB), ADR-031 (plane ownership, unchanged), ADR-050 (visibility), ADR-080 (access guard), ADR-015 / ADR-040 (egress, unchanged: D-8), ADR-082…085 (the rest of this set), gap rows **D-60** (new: typed multimodal knowledge spine), D-41, D-38, D-35, D-39

## Context

On 2026-10-03 the maintainer asked whether the R4 KG is good, how it makes
agentic work efficient, and what multimodal knowledge with an ontology must
look like, tested on three demo research corpora. The investigation
mapped the KG at umrs `d28978ad` (eight areas, each defect re-checked by an
adversarial verifier), ran a three-design panel and a judge, prototyped the
winner ("EIP-KG+") on the three corpora and benchmarked it. Report:
kg-multimodal-investigation-2026-10-03.md;
evidence: `~/tmp/kg-multimodal-bench-2026-10-03/evidence/{phase1,phase2}/`.

Verified at umrs `d28978ad` (ids are the phase-1 map ids):

- **The type system cannot hold science.** `knowledge_nodes.node_type` is a
  7-value CHECK (`migrations/0055_knowledge_store.sql:82`, mirrored in
  `knowledge/store.py:117-119`); `node_type='video'` raises CheckViolation,
  and every semantic type collapses to `entity`/`concept`, surviving only as
  a JSON key (`jobs/knowledge_adapters.py:1698-1701`,
  `knowledge/canonical_graph_view.py:717-721`) — KG-01. `relation_type` is
  free text: live writers emit seven literal relations plus unbounded LLM
  slugs (`knowledge_adapters.py:1643,1657,1898,1913,2244,3523,3690`;
  `finding_ingest.py:42-43`), none in the default ontology's three
  (`knowledge/ontology.py:37-39`) — KG-03.
- **No live write path validates.** The only validator,
  `PgGraphSink._validate_against_ontology` (`knowledge/pg_graph_sink.py:373-399`),
  has no caller in `src`; its tests use a MagicMock pool, and on real
  Postgres it FK-fails on never-created `dep::` targets
  (`pg_graph_sink.py:289-299, 433-439`) — KG-03, TC-04. ADR-049's "ALL gaps
  Closed" overstates this.
- **No ontology can be registered.** `OntologyRegistryStore.register/retire`
  (`knowledge/ontology_registry.py:147-224`) have no route or caller;
  `create_graph` stores any `ontology_id` unchecked
  (`api/knowledge_graphs.py:181-231`); 0091 has no `subtype_of` or
  `extraction_fields` columns, so registration drops them
  (`ontology_registry.py:69-77, 168-187`) — KG-04. Live rccs (read-only,
  2026-10-03): 18 graphs, `ontology_id` null on all, all falling back to the
  code-centric `r4-default`.
- **Identity is a string convention under a global PK.** `upsert_node`
  replaces `content` wholesale and keeps the first-insert type, label and
  project (`store.py:216-241`): a finding upsert wiped an artifact's
  `produced_by_job_id`, and a second project's write overwrote the first's
  content under the same id — KG-06. Semantic ids embed the artifact id, so
  one entity from two documents is two nodes (`knowledge_adapters.py:1850-1866`)
  — KG-07; 40-character slugs merge distinct events
  (`knowledge_adapters.py:1704-1708`) — ING-11.
- **No currency.** The corpora's older reports state conclusions newer ones
  overturned (DATA-04); the KG has no superseded or retracted statement, and
  `knowledge_events` cannot hold a write audit (`artifact_id` NOT NULL FK,
  `event_type` CHECK; `store.insert_event`, `store.py:858-891`, no caller).

A contributor might otherwise replace `knowledge_nodes` (about twenty
writers, 1,600+ KG tests and bundle v1.2 depend on it), add a graph store
(reverses ADR-063), or add a second validating writer (PgGraphSink again).

## Decision

**D-1 — A typed class beside the facet.** `knowledge_nodes` gains
`node_class` (FK `knowledge_terms`), `attrs` (declared datatype properties
only), `natural_key`, `invalidated_at/by`, `invalid_reason`, `proposal_id`,
and `search_text` with a generated `search_tsv`. `knowledge_edges` gains
`relation_class` (FK), `invalidated_at`, `proposal_id` and a unique
`(source_id, relation_class, target_id)`. The 7-value `node_type` CHECK
stays as a coarse facet, derived by the store from the class's bucket.
Legacy rows satisfy every new column with NULL.

**D-2 — A term registry with closure.** `knowledge_terms`: CURIE key,
unique IRI, kind class|relation|attribute, multiple parents, bucket,
domain/range, datatype and unit, transitive/functional/inverse flags,
aliases, SKOS mappings, `required_on`, origin standard|imported|minted,
`verified`, status/`replaced_by`. Terms are immutable (change = deprecate +
`replaced_by`). `knowledge_term_closure` is reflexive (minimum depth,
classes and relations) and is rebuilt in the registration transaction.
`knowledge_ontologies` gains `subtype_of`, `extraction_fields`, `iri_base`,
`prefixes`, `imports`, `profile`, `source_sha256`;
`knowledge_ontology_terms` materializes each record's admitted set,
transitive imports included.

**D-3 — Four layers, no reasoner.** L0 `std@1`: standard IRIs only where
verified (PROV-O, SOSA/SSN, Web Annotation, schema.org, ML-Schema, DCTERMS,
SKOS, OWL, BFO part-of, QUDT units, NCBITaxon). L1 `r4-core@2` under
`tag:r-ccs.riken.jp,2026:r4/core#` (RFC 4151; about 60 classes: assets,
documents, runs, observations, annotations, claims, sensors). L2 project
profiles (e.g. `proj-a@0.1`, `proj-b@0.1`, `proj-c@0.1`) import r4-core and mint only
under their own `iri_base`; a profile carries required attributes, path
rules, producer time bases, display columns and `kg_emit` maps. L3 imported
subsets (D-8). Inference stays the ADR-049 bounded legs (closure,
domain/range, transitive/functional/inverse). `r4-default@1` stays
resolvable; a null `ontology_id` resolves to `r4-core@2`.

**D-4 — One validation choke point.** A `TypingContext` (resolved once per
graph and terms version) runs inside `KnowledgeStore.upsert_typed /
upsert_node / upsert_edge` for every writer; the raw-SQL minting helpers
(`rag_upload_mint`, `legacy_corpus_canonize`) call the same pure
`prepare_row`; a CI fence fails any `INSERT INTO knowledge_nodes` elsewhere
that omits `node_class`. Rules: the class is admitted; `node_type` is its
bucket; each `attrs` key is declared, its domain closure admits the class,
datatype and unit check; a relation resolves by CURIE or alias and its
endpoints sit under its domain and range.

**D-5 — Warn, then enforce, per graph.** `knowledge_graphs.ontology_mode`
is `warn|enforce`. Warn writes `r4:Unclassified`, moves undeclared attrs to
`content.undeclared`, writes `r4:relatedTo` with `content.rel_raw`, and
counts the miss in `knowledge_vocab_gaps`. Enforce fails the whole batch
(`OntologyViolationError`). Default graphs stay in warn; a domain graph
flips to enforce once its gap ledger is empty. `missing_required` (pixel
size, instrument, camera model, subject taxon …) is the metadata request list
that feeds the ADR-066 ask-the-scientist path.

**D-6 — Identity, upsert, resolution.** New writers mint
`<cls3>:<base32(sha256(graph_ns|class|natural_key))[:10]>` (14 characters,
deterministic, salted by a per-graph `graph_ns` UUID that travels in
bundles). The upsert merges `content` and `attrs` (`||`), keeps class and
label unless the new value is non-empty, is guarded by project and graph,
and raises `NodeIdCollision` when no row returns; status is still written
only on INSERT. Before minting, resolution links by checksum, exact alias
(`knowledge_node_aliases`) or a term synonym inside the class closure;
fuzzy matches only propose `owl:sameAs` for human review.

**D-7 — Claims with currency.** `knowledge_claims` holds one row per
Claim-subclass node: kind (finding, decision, negative_result,
parameter_effect, measurement, retraction, assumption, open_question),
subject, property (a functional attribute or `r4:states`), qualifier,
value/unit, polarity, statement, `stated_at`, source section and span,
method, lifecycle current|candidate|superseded|retracted, `superseded_by`,
`revision_of`. One current claim per (graph, subject, property, qualifier).
Supersession is deterministic first (same subject and functional property,
later `stated_at`); for `r4:states` the qualifier is `sha1(statement)[:12]`
so free statements never compete, and similarity only proposes "possibly
supersedes". An established older claim outranks a proposed newer one
(the newer one is `candidate`).

**D-8 — OBO/OWL subset import.** `ONTOLOGY_IMPORT` (admin-only job) has
no egress. It parses bytes that are already landed: by default an uploaded
file; a PURL is fetched only as a `KNOWLEDGE_INGEST` source through the
existing `egress_fetch` allowlist (`knowledge_ingest.allowed_domains`), so
the CONSTITUTION §2 invariant "only `MODEL_DOWNLOAD` and `KNOWLEDGE_INGEST`
jobs have egress" (ADR-015, ADR-040) is unchanged. It extracts `bot`,
`descendants` or `curated` subsets and records source IRI, version, sha256
and licence. Ids come from the file; unconfirmed ones stay
`verified=false` and are never exported as fact.

**D-9 — Registration is human, audited, one transaction.**
`POST /umrs/knowledge/ontologies` (owner/admin, human only; CLI
`r4 knowledge ontology register|list|show|retire` and
`r4 knowledge graph-bind <graph> <id@v> --mode enforce`) takes a YAML
document and, in one transaction, checks prefixes, admitted
parents/domains/ranges, name clashes and changed redefinitions, upserts the
terms, rebuilds the closure and materializes the admitted set. Agents only
propose `add_term` / `declare_attribute` (ADR-084). Every write batch
appends a `knowledge_write_batches` row (writer, origin actor, ontology,
counts, violations) and bumps `knowledge_graphs.graph_version`.

DDL: judge-verified on PG 16.15 + pgvector 0.8.6 over migrations 0001–0117
(`~/tmp/kg-multimodal-bench-2026-10-03/evidence/judge/0118_0123_kg_proto.sql`;
production splits it into three migrations, numbers provisional — 0118 is taken by the hub copy markers, so they start at 0119 or later).

### Why

- Additive and reversible (NULL the columns); the judge scored it 67/80
  against 64 and 59, chiefly on fit and migration risk.
- One choke point closes the PgGraphSink gap for every writer at once.
- CURIE keys make the namespace choice (`tag:` now, w3id later) a metadata
  UPDATE, not a rewrite of every node row.
- It answers the design question "property graph vs RDF": a property graph
  in Postgres (ADR-063), exchanged as RDF (JSON-LD/Turtle export in the
  ADR-032 bundle addendum).

## Consequences

- Positive: class filters expand through the closure (`sosa:Observation`
  returns detection events, model outputs, segmentations); one project's class
  written into another's graph is refused; the gap ledger is the curation
  path; researchers get a "metadata the platform needs from you" list
  instead of guessed values.
- Cost: two type columns can drift (bucket derived in the store, writer
  fence, nightly mismatch count). The legacy backfill can misclassify
  (dry-run per id prefix, `r4:` + bucket fallback). The global `node_id` PK
  stays; graph-salted ids make collisions loud; a `(graph_id, node_id)` key
  is a later ADR. 'simple' FTS does not segment Japanese (ADR-083 lanes).
- Bloat guard: a class must back a filter, display column, walk relation
  or currency predicate; profiles stay at 10–20 classes.
- NOT in scope: an OWL reasoner; a graph or triple store; dropping the
  `node_type` CHECK; bundle r4-kb 1.3 (ADR-032 addendum, to be drafted);
  server-derived actor identity (separate architecture-review ADR); KG rows
  on ADR-077 bound Postgres (leg 2).

## Alternatives Considered

- **Observation Spine (standards-first)** — richest semantics, but six
  facet tables, a halfvec re-platform of 0075 and replacement writers;
  mapper records land established by fiat (D-35); its asset table keys
  upward into the catalogue (ADR-031). Grafted: claims with currency,
  admitted sets with imports, OBO import discipline, SOSA framing.
- **Verb-First (agent-first)** — weakest ontology (URN core, classes in
  JSONB, external terms with labels and is-a only) and a second writer
  beside `KnowledgeStore`. Its agent contract is grafted in ADR-085.
- **Do nothing** — today's tools, reproduced locally against the real umrs
  ingest chain and K3 routes, did not help the smaller model (0.546 vs
  0.519 with files only, 8 wins / 6 losses, n.s.; hallucinations 9 → 12,
  stale answers 2 → 4).
- **Replace `node_type` by `node_class`, or a graph database** — the first
  breaks bucket readers, the canonical view and bundle 1.2 for no gain over
  a derived facet; the second reverses ADR-063, while typed SQL served every
  prototype verb at 4–57 ms median in-verb latency.

## Acceptance receipts

1. An enforce-mode domain graph refuses an undeclared class, an undeclared
   attribute, a datatype violation and a relation domain/range violation
   through every writer (store paths, `rag_upload_mint`,
   `legacy_corpus_canonize`, `finding_ingest`, auto-ingest); the batch is
   atomic and `graph_version` is unchanged.
2. `r4-default@1` loads; warn-mode graphs accept every current writer.
3. A registered ontology round-trips `subtype_of` and `extraction_fields`.
4. A second current claim for one (subject, property, qualifier) is
   refused; a later `stated_at` supersedes deterministically.
5. The CI fence is red on a raw node INSERT without `node_class`.

## Evidence (2026-10-03)

- Prototype (`~/tmp/kg-multimodal-bench-2026-10-03/proto/`, throwaway
  container): enforce-mode loads of three project corpora; 0 violations;
  0 gap rows for two corpora, 3 for the third (two unresolved modules, one
  plan naming an item absent from the inventory; `evidence/phase2/loader_1.md`); 40/40
  validation cases and 20/20 sampled facts correct (`evidence/phase2/verify.md`);
  superseded and retracted claims in one corpus, each with a recorded basis.
- Benchmark (54 verified questions; arms in ADR-085): Haiku 4.5, the proxy
  for the ~27B site model, 0.519 → 0.611 with the typed KG (13 wins / 4
  losses, p ≈ 0.049), hallucinations 9 → 5; Opus 5.5 0.991 → 1.000.
  Supersession questions did not improve (0.58 / 0.50 / 0.58), so the claims
  layer's payoff is not yet shown.

## Rollout-status (2026-10-03)

Proposed; nothing landed. The investigation committed nothing and did not
modify repo code; the prototype ran in local throwaway containers. Live
access was read-only, except that read verbs mint default-graph registry
rows (LIVE-09): the investigation's own probes created an empty project
default graph (2026-10-02, owner `''`; id recorded privately).
Wave ids in ADR-081…085 are those of the report's §7.3 table (W0 fixes,
W1 measurement, W2 typed spine, W3 multimodal anchors + result adapters,
W4 verbs, W5 loop, W6 embedding spaces, W7 claims + portability), not the
judge's original order. W0 and W1 need no ADR; this ADR gates W2. Gap row
D-60 was added to the gap analysis on 2026-10-03 (counted Partial); it
could later be folded into D-41. Open for the maintainer: `tag:` vs w3id
namespace (recommended: `tag:` now, a `skos:exactMatch` later); ADR-077
leg-2 residency for bound projects' KG rows.

## Related

- [ADR-049](049-ontology-typed-contract.md), [ADR-063](063-de-facto-answers-recorded.md), [ADR-031](031-knowledge-layer-plane-ownership.md), [ADR-050](050-visibility-and-session-history-build.md), [ADR-066](066-ask-scientist-policy-pause-profile.md), [ADR-080](080-one-access-guard-shareable-resources.md)
- [ADR-082](082-multimodal-anchors-catalogue-plane.md), [ADR-083](083-embedding-spaces.md), [ADR-084](084-growth-governance.md), [ADR-085](085-agent-kg-verb-contract.md)
- docs/development/kg-multimodal-investigation-2026-10-03.md

## Rollout-status (2026-10-12) — ADR-093 adopted D-1's `search_text` early, for chunk rows

- **What landed** (ADR-093, branch `feat/adr-093-retrieval`):
  - `knowledge_nodes.search_text` is a writer-maintained column on chunk rows;
  - migration 0120 adds its expression GIN index;
  - K3's lexical lane and the retrieve verb read it.
- The rest of D-1 (the `node_class` term registry) is untouched.
- **Measured:** the lexical lane's ranking on SciFact (ADR-093 S3). The answer-path receipt for lexical inclusion is in the eval doc, Phase 9.
- **Deployment:** on rccs, 0120 needs `pg_trgm` from the privileged Ansible layer (ADR-093 MD-4).

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
