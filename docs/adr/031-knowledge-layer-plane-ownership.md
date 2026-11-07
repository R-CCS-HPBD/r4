# ADR-031: Knowledge layer plane ownership — catalogue, canonical spine, vector rows

**Status:** Accepted
- **Amendment (2026-08-23, same day):** Invariants 1–3 promoted from prose
  to **normative**; §Operational enforcement added — the amendment defines
  the mechanism that prevents drift rather than only the model that must
  hold. Conceptually unchanged.
- **Governing principle:** the canonical spine is the semantic authority;
  the catalogue is the physical-existence authority; vectors are derived
  retrieval state.
- **Date:** 2026-08-23
- **Related:** ADR-011 (data-catalog), ADR-013 (knowledge portability),
  ADR-015 (ingest egress), ADR-025 (sessions), ADR-024 (module boundary),
  ADR-027 (unified knowledge spine), `docs/development/drift-remediation-plan-2026-08-22.md`
  (catalogue bridge queue), `memories/repo/knowledge-catalog-three-tier-boundary-2026-08-21.md`.

## Context

After the sigma.js graph window landed (Wave A+B, 2026-08-23), the user asked
for the **hierarchical relation** between the catalogue and every other
input (knowledge graph / raw data / vector database). Right now the three
planes are linked only by ad-hoc joins:

- `/umrs/catalog/*` (metadata search) reads the S3-object metadata index
  — no knowledge-node awareness, no vector-row awareness.
- `/umrs/knowledge/graph` reads the canonical 0055 spine — nodes/edges minted
  only at ingest time, plus migration-0075 node vectors.
- Rag corpus tables (per-corpus DBs, `text` rows) own historical chunks —
  no canonical view, no catalogue view, no visibility of one another.

The 358 legacy chunks canonized on data0 (2026-08-23) show what happens when
the planes are not owned: three places carry the same facts with different
provenance, and no plane is authoritative about "what do we know about this
science asset?"

## Decision

Three planes, three explicit owners, **one shared document identity** and
**one canonical node identity**:

1. **Catalogue plane — S3-object metadata index** (owner: `umrs.data_catalog`).
   Catalogues *what physically exists*: filename/path, checksum, modality,
   perception/determinism fields. Indexed from S3/MinIO changes only.
   Never stores graph or vector facts — it *references* them.

2. **Canonical knowledge spine — graph registry** (owner: `umrs.knowledge.*`
   + `umrs.api.knowledge`). Catalogues *what the knowledge means*: nodes
   (documents, chunks, entities, edges) with the tier-scoped multi-graph
   algebra (0069 + three-tier gate). Owns the **document_id namespace**
   (`ragdoc-…`, incl. the legacy-prefixed `ragdoc-legacy-…` lanes) — the
   shared foreign key across planes.

3. **Vector plane — node embeddings.** Catalogues *how to find similar
   semantic content*. Ownership is deliberately split so generation can
   never own persistence:
   - canonical spine owns the semantic object being embedded;
   - the embedding subsystem owns embedding generation + model identity
     (`PrefixedSentenceTransformerEmbeddings`, R6.1 boundary);
   - `knowledge_node_embeddings` (0075) owns persisted vector facts, and
     rows reference `knowledge_nodes.node_id` first-class — never s3 keys;
   - per-corpus rag (`text`) tables are legacy/query storage, not owners.

### Hierarchy (read as: references flow DOWN the chain)

```
PHYSICAL EXISTENCE
    catalogue
        │ document_id
        ▼
SEMANTIC MEANING
    canonical knowledge spine
        │ node_id
        ▼
SEMANTIC RETRIEVAL
    vector / embedding rows
```

*No plane may key relationships upward.* A vector row never references
s3_key directly; a catalogue row never references a node_name; the spine
never references a raw-bytes row. Cursors and joins always ride the named
anchors (`document_id`, `node_id`).

### Invariant 1 (normative) — identity cardinality

```
one catalogue object
    → zero or one canonical document identity

one canonical document
    → zero or many knowledge nodes

one knowledge node
    → zero or many embedding rows
```

Implementations MUST NOT collapse the tiers into
`catalogue row ≡ document node ≡ vector row` (the abstraction this ADR
eliminates; the 358-row legacy canonization is the standing counter-example).

### Invariant 2 (normative) — legacy corpus subordination

```
legacy corpus row
      │
      ├── may participate in retrieval
      ├── may be deduplicated against canonical results
      │
      ✗ must not mint a new canonical document merely
        because it exists in the corpus
```

Legacy rag corpus may carry searchable content but does not establish
canonical knowledge identity.

### Invariant 3 (normative) — upload lifecycle states

Vectors are NOT a precondition of canonicality. The lifecycle is explicit:

```
catalogued
    ↓
document minted
    ↓
content canonicalized
    ↓
embedding pending
    ↓
embedding ready
```

A transient embedding failure marks `embedding_pending` — non-error, and it
must NEVER downgrade an otherwise valid catalogue/document write.

### Invariant 4 (normative) — consistency state is derived, never stored per-plane

> Consistency state belongs to the **asset/document lifecycle**, not to any
> individual plane.

Implementations MUST NOT scatter `catalogue.status` /
`knowledge.status` / `vector.status` and infer global truth by comparing
them. Instead the reconciliation job derives the state:

```
asset/document
       │
       └── consistency_state
              ├── COMPLETE
              ├── EMBEDDING_PENDING
              ├── PARTIAL           (logical write invariant violated)
              └── ORPHANED
```

Planes remain authoritative for their own data; the cross-plane consistency
state is strictly derived — no fourth competing “truth” is created.

```
catalogue ✓  canonical ✓  embedding ✗  →  EMBEDDING_PENDING
catalogue ✓  canonical ✗                →  PARTIAL
catalogue ✗  canonical ✓                →  PARTIAL
```

## Consequences

1. **Catalogue enrichment is a display-layer fetch, not a schema change.**
   `/umrs/catalog/search(+read)` now ships an optional `links` block per
   row, hydrated server-side by the document_id join:
   `knowledge.documents` (tying row → doc node), `knowledge_node_embeddings`
   (tying doc → vector rows, dims/model), `research sessions` (byte-count
   provenance). Lazy, cached, fail-open (returns no links on miss).
2. **Vector corpus tables (rag `text` etc.) are per-project implementation,
   not a plane.** They remain the legacy storage lane; the canonical owner
   of vector facts is the corpus-agnostic `knowledge_node_embeddings` (0075)
   plus query-time dedupe. New work keys/embeds against the canonical spine.
3. **Upload path** (gateway upload or ingest job) owns **one logical
   write path** (catalogue upsert → canonical mint → embedding generation,
   ordering per Invariant 3); §Operational enforcement names the
   consistency-state owner.
4. **Ingest egress (ADR-015) governance** applies where URLs are fetched;
   the ingest job is the egress owner for all three planes.
5. **Retirement of rag corpus as plane-owner** is still gated on the
   vector-corpus-rich deployments (bdrt equipment runs). It is NOT asked
   to go away today; per-project corpus ownership is a narrow, allowed
   exception, tracked by the residual memory.
6. **Catalogue stats** defaults now include per-plane row counts
   (artifact/s3 rows, graph nodes, vector rows) so the hierarchy fills up
   as mint/migrations converge.

## Operational enforcement (normative)

The invariants above hold only if something *checks* them continuously —
not merely when tests run.

### The reconciliation/check job (owner: server-side)

The three-plane consistency invariant is enforced by a **server-side
durable reconciliation/check job following the durable-job reconciler
pattern** (attempt-scoped reads; advisory lock against concurrent checkers).

The checker evaluates, per asset/document:

```
catalogue → document         (document_id link exists where expected)
document → canonical nodes   (mint plane content rows can carry)
node → embeddings            (pending count tracked, tolerated aging)
legacy corpus → canonical    (subordination invariant — never auto-mint)
```

The checker is **authoritative for consistency state**; it classifies each
asset as COMPLETE / EMBEDDING_PENDING / PARTIAL / ORPHANED per Invariant 4.
UI and API surfaces render read-side projections of that state only and
MUST NOT act as the consistency gate.

### The fence (owner: repo tooling + CI)

A behavior test proves an *existing* path obeys the rule. A **fence**
prevents new paths from introducing the violation. ADR-031's forbidden
relationship is architectural (`catalogue ──X──> s3_key ──X──> vector`),
so enforcement is two-layer:

1. **Static dependency validator** (new `tools/` rule, added to the
   enforcement sweep beside `contracts_fence` and
   `stale_name_validator`): scans query-builders and catalog/knowledge
   modules for cross-plane joins on `s3_key` and for embedding lookups
   that don't anchor on `knowledge_nodes.node_id`. The rule reasons about
   *dependency direction*, never about one specific SQL implementation:
   ```
   catalogue plane      may depend on document_id
   catalogue plane      MUST NOT use vector corpus identifiers
   catalogue plane      MUST NOT join embeddings through s3_key
   ```

2. **The §Invariant tests below are the CI-fence half** — behavior pins
   that keep the current paths to the anchor-direction and cardinality
   fences honest.

A path that passes code review but violates the dependency-direction rule
fails the validator; code that drifts behavior fails the test. One
mechanism alone is not enough.

## Alternatives considered

- **Fold catalogue into the canonical spine registry.** Rejected — the
  spine is row-heavy (chunks, entities); keeping its S3-parity meant
  duplicating object metadata on every doc row. Planes stay separate,
  linked by `document_id` join, so each schema stays lean.
- **Make the vector plane a first-class plane owner.** Rejected per
  R6.1 boundary: vectors are derived from canonical content, no separate
  facts. Plane 3 exists only to serve similarity — read-side.
- **Migrate rag corpus tables wholesale.** Deferred — 358-row canonization
  is evidence the spine knows how those chunks relate. Deleting the rag
  tables now would break bdrt's equipment-run archives; per-project
  exception stays until business-class deployment validation clears.

## Implementation order (additive wave, no schema rewrite)

1. Task: `catalog/search` + `catalog/read` ship `links` block (lazy join
   on `document_id` → doc node → vector rows). Webview card renders
   the badge. Cheap; zero schema change.
2. Task: the server-side reconciliation/check job as the consistency
   owner per §Operational enforcement, first classified as EMBEDDING_PENDING-
   safer; the tools/ dependency validator lands in the same wave.
3. Task: ADR-027 P1a proxy mint retires when no P1a-only uploads
   still land in the field — then the canonical owner is exact.

## Invariant tests (the CI-fence half of §Operational enforcement)

- **Anchor-direction fence:** catalogue rows may reference `document_id`;
  embedding rows may reference `node_id`; **no catalogue→vector path may
  flow through `s3_key`** — verified in the catalog query code-behind.
  Concretely: `catalog.search` hydrate path must go
  `s3_object → document_id → canonical doc → embeddings`, never
  `s3_object → vector corpus`.
- **Cardinality fence:** no code path may mint a canonical document node
  for a corpus row that lacks its `document_id` mint (the 358-row lesson).
- `catalog.search` response includes `links` for rows with minted doc
  nodes (mock-pinnable; current 200-row corpus doesn't require it yet).
- `catalog.read` on an un-upgraded s3_object with no canonical doc:
  links absent and NOT an error.
- `/umrs/knowledge/query` — embeddings lane — with both
  canonical-vector-backed AND legacy-corpus-only corpora:
  dedup of results, verified by row count delta (not qualitative).
