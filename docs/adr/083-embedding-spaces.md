# ADR-083: Embedding spaces — a registry, one partial HNSW per space on the untyped column, local query encoders, calibrated INSUFFICIENT

**Status:** Proposed (2026-10-03)
**Date:** 2026-10-03
**Relates to:** ADR-031 (R6.1 boundary kept: generation owns model identity, vectors are derived state; this ADR makes that identity a table), ADR-027 (the spine read verb gains a working vector lane), [EMBEDDING-POLICY](../EMBEDDING-POLICY.md) (amended: its "HNSW gate" is replaced), ADR-068 (encoders behind the one inference API), ADR-062 (locality), ADR-076 (decision model as an optional sufficiency check), ADR-081/082, gap rows D-14, D-15, D-51, **D-60**

## Context

Background, method and report: [ADR-081](081-typed-knowledge-spine.md) and
kg-multimodal-investigation-2026-10-03.md.
Verified at umrs `d28978ad`:

- **The vector column cannot be indexed.** `knowledge_node_embeddings`
  (0075) stores an untyped `vector`; `CREATE INDEX … USING hnsw` fails with
  "column does not have dimensions" — KG-18, TC-12. EMBEDDING-POLICY's
  recorded gate waits for one canonical model and then a typed shadow column
  (`docs/EMBEDDING-POLICY.md:54-63`), which cannot hold text, image and
  slice spaces at once.
- **The agent's vector lane is unreachable.** `UnifiedSearch` calls
  `search_embeddings(query, project_id, limit)` with no query vector
  (`search_api/__init__.py:258-263`); the similarity branch runs only when a
  vector is passed (`api/knowledge_query.py:260-263`) and has no
  `model_name` or dimension predicate (`:293-304`); without a vector the
  "embeddings" lane is an ILIKE over model name and label —
  KG-09, ING-12, MM-09. `store.nearest_node_ids` (`store.py:1073`) has no
  caller. HMAE CLS and e5-base-v2 are both 768-d, so two models in one
  query would mix silently instead of failing.
- **No image or volume vectors exist in use.** The imaging demos set
  `store_embeddings: "no"` (in the HMAE demo's `experience.json`; that demo
  is no longer in this repository);
  HMAE features live only as S3 `.pt/.pkl`
  (`vision_service/default_adapters/hmae_adapter.py:298`); the ResNet
  "embedding" is 1000-d ImageNet logits (`inference/resnet_50.py:70`) — MM-08.
  D-14 ("Partial (image)") and EMBEDDING-POLICY's image row overstate this.
- **Insufficiency never fires.** Natural-language queries miss the
  whole-phrase ILIKE ('FAILED' 45 hits, 'job FAILED' 0 — KGC-02); every
  READY catalogue row scores 0.25 with no token match
  (`data_catalog/scoring.py:73-74`), so the agent's `knowledge_insufficient`
  signal never fires (LIVE-06). The Verb-First panel design measured that a
  nonsense query scores inside the cosine range of relevant ones: absolute
  cosine cannot decide sufficiency.
- **Japanese.** 'simple' FTS does not segment Japanese, and `pg_bigm` is not
  in the deployed image. Reports in the example-project-b corpus are partly Japanese.
- **Four unreconciled vector planes** already exist (0075 node vectors,
  `sat_embeddings` 0060 with a hash-vector fallback and no caller, the A2L
  `vector(1536)` table, per-experience `VECTOR(n)` tables) — critic.

## Decision

**D-1 — A space registry.** `knowledge_embedding_spaces` (`space_id`,
`model_name` UNIQUE = the value stored in 0075 `model_name`, `model_ref`,
`revision` = the pinned snapshot hash, `dimension`, `modalities`, `metric`,
query and document prefixes, `locality` CHECK `'site'`, `index_name`,
`legacy_names`, status `active|retired`). 0075 gains a `modality` column.
Writers refuse a vector whose `model_name` has no active space. Spaces are
registered by an admin (system tier).

**D-2 — One partial expression HNSW per space, on the untyped column.**

```sql
CREATE INDEX CONCURRENTLY hnsw_<space> ON knowledge_node_embeddings
  USING hnsw ((embedding::vector(<D>)) vector_cosine_ops) WHERE model_name = '<model_name>';
-- query shape: WHERE model_name = $1 ORDER BY embedding::vector(<D>) <=> $2::vector(<D>) LIMIT $k
```

Every vector query filters on one space's `model_name` (a missing filter is
a test failure), sets `hnsw.iterative_scan = relaxed_order`, over-fetches
`max(k, 100)`, re-sorts by exact cosine and joins `knowledge_nodes` for
graph, visibility and currency. The 0075 PK `(node_id, model_name)` stays;
keyframes, tiles and chunks are nodes (ADR-082), so no PK change is needed.

**D-3 — Initial spaces.** `me5b` = `intfloat/multilingual-e5-base`
(768-d text, Japanese and English, `query:`/`passage:` prefixes) replaces
e5-base-v2 and MiniLM for the KG; an image↔text space (SigLIP-base or
CLIP-B/16; the prototype used `openai/clip-vit-base-patch16`, 512-d) for
keyframes, evidence frames and tiles; HMAE CLS for slice↔slice
(image → image only, pinned to the checkpoint sha); V-JEPA2 vectors imported
from the video pipeline once they exist. `KNOWLEDGE_REINDEX` moves legacy
rows into `me5b`; old names stay in `legacy_names`.

**D-4 — Query encoders are site endpoints.** Encoders are model-server
endpoints behind the ADR-068 `/v1/embeddings` surface, locality `site`
(ADR-062, D-51), with a 200 ms budget. When an encoder is down the vector
lane reports `unavailable`; it never falls back to a lexical match labelled
"embeddings". Ingest-side encoding runs in the `embedding-job` profile.

**D-5 — Calibrated INSUFFICIENT.** A search result is sufficient only when
it has a lexical or alias hit, or a top vector score above the space's
null threshold for that graph (mean + 3σ of the exact top-1 cosine of 30
seeded nonsense strings, recomputed after every reindex), or a reranker /
D-57 sufficiency check says so. A lane with no vectors in the graph counts
as no evidence. The catalogue floor applies only on a token match.

**D-6 — Policy amendment.** EMBEDDING-POLICY's "HNSW gate" and its named
gap "fixed-dimension column + HNSW — gated on the P2 canonical-model
decision" are replaced by D-1/D-2; its model table lists spaces, not
defaults. The R6.1 boundary in ADR-031 is unchanged: the registry is the
generation side's model identity made durable, and vectors stay derived.

### Why

- A typed column forces one model; science needs at least three spaces
  (text, image↔text, slice↔slice), two of them possibly the same dimension.
  The partial expression index keeps 0075 and makes mixing impossible by
  construction. The judge verified it on PG 16.15 + pgvector 0.8.6: an Index
  Scan with 768-d and 512-d vectors in one column.
- Calibration per space and graph is the only honest insufficiency signal
  the prototype found; absolute thresholds failed in both directions.

## Consequences

- Positive: text → image search over keyframes and evidence frames,
  slice neighbours, cross-lingual text search, ANN at 10⁵–10⁶ vectors per
  project, and an honest "not in the knowledge base" for the agent.
- Cost: encoder endpoints to serve (rccs listed no embedding or vision
  endpoint on 2026-10-02, only Bespoke-Nimble-9B, so the lane reports
  `unavailable` until W6); a reindex per model change; one index build per
  space.
- Known limits from the prototype: HNSW recall@10 was 0.66 at
  `ef_search` 100 and 0.90 at 400 on the example-project-b text space, below the
  0.95 receipt — hence over-fetch plus exact re-sort; CLIP barely separates
  CT slices (cosine 0.994–0.9995 between any two), so CT volume data needs the
  HMAE space; the mean + 3σ gate is strict for short Japanese queries
  against English text (a two-word Japanese query scored 0.806 against 0.841
  and was flagged, while its English equivalent matched lexically) — a reranker is
  needed.
- NOT in scope: clip-level video embeddings (keyframes only); halfvec;
  partitioning and multi-instance pgvector; merging the other vector planes
  (follow-up cleanup).

## Alternatives Considered

- **One canonical model + typed shadow column** (the current
  EMBEDDING-POLICY gate) — one modality only; image and slice spaces would
  need more columns or tables.
- **Observation Spine** — re-platform 0075 to halfvec with a union view:
  highest migration cost for the same query plan.
- **A table per space** (`VECTOR(n)` per model) — a fifth vector plane in a
  tree that already has four unreconciled ones.
- **Do nothing** — exact scans only, a vector lane the agent cannot reach,
  and an insufficiency signal that never fires (arm B: Haiku 0.546 vs 0.519,
  hallucinations 9 → 12).

## Acceptance receipts

1. `EXPLAIN` of a filtered query shows an Index Scan on `hnsw_<space>`.
2. Recall@10 against exact search ≥ 0.95 per space on a built graph.
3. On the W1 measurement suite: nonsense queries flagged INSUFFICIENT at ≥ 0.9 and
   no relevant query wrongly flagged; Japanese queries measured separately.
4. A vector query without a space filter fails a test; an encoder outage
   shows `unavailable` in the lane map.

## Evidence (2026-10-03)

- Prototype (`~/tmp/kg-multimodal-bench-2026-10-03/proto/`): `me5b` (768)
  and `clipb16` (512) in one column with one partial HNSW each; the
  example-project-b corpus held tens of thousands of text and about a thousand
  image vectors. The verifier
  (`evidence/phase2/verify.md`) found and fixed: relaxed-order HNSW with
  `LIMIT k` returned non-nearest rows on small filtered graphs (CT-slice
  CLIP null mean 0.213 against an exact 0.263, so nonsense passed the
  gate); the example-project-a graph had never been embedded, so its gate was skipped.
- Cross-modal: a short descriptive text query for colour daylight footage
  returned 20/20 colour keyframes (colour frames were a small minority of the
  corpus, the rest infrared); each CT slice's nearest
  neighbour was an adjacent slice, matching exact numpy cosine.
- Latency: about 50–250 ms per search through a local encoder server;
  about 0.9 s worst case from the trigram lane.
- Benchmark (ADR-085 has the arms): Haiku cross-modal questions 0.30 files
  only, 0.20 with today's tools, 0.50 with the typed KG.

## Rollout-status (2026-10-03)

Proposed; nothing landed (see ADR-081 for what the investigation touched).
Gates wave W6 of the report's §7.3 (migration 0123, provisional); the
evidence moved embeddings after the structured and media waves. Open: reranker choice
(`bge-reranker-v2-m3` vs a D-57 listwise check); SigLIP vs CLIP for the
image↔text space; where the encoders run on rccs.

## Rollout-status (2026-10-09)

Partial — text only, without the registry (validation report §7):

- The vector lane is reachable. `GET /umrs/knowledge/query` gets the query vector from the
  RAG service's `POST /rag/embed` (the R6.1 query-time embedder) and ranks with
  `e.model_name = <that encoder's model>`; a vector without its model is refused —
  receipt 4's test is `umrs/tests/test_knowledge_query_semantic.py`.
- D-4 and D-5 semantics hold in that lane: encoder down → `unavailable`, never a lexical
  stand-in; a space with no vectors → no hits.
- **Interim against D-4:** the encoder is the RAG service, not an `/v1/embeddings` endpoint
  in the registry. `UMRS_KNOWLEDGE_QUERY_ENCODER_URL` is the seam to repoint.
- Not landed: D-1 registry, D-2 HNSW, D-3 spaces, D-5 calibration.

## Related

- [ADR-031](031-knowledge-layer-plane-ownership.md), [ADR-027](027-unified-knowledge-spine.md), [ADR-062](062-model-locality-data-class.md), [ADR-068](068-one-internal-inference-api.md), [ADR-076](076-decision-model-role.md)
- [ADR-081](081-typed-knowledge-spine.md), [ADR-082](082-multimodal-anchors-catalogue-plane.md), [ADR-085](085-agent-kg-verb-contract.md), [EMBEDDING-POLICY](../EMBEDDING-POLICY.md)
- docs/development/kg-multimodal-investigation-2026-10-03.md

## Rollout-status (2026-10-09, later)

- **A space is the model's basename** (`text_embedder.embedding_space`): the similarity lane
  matches `model_name = <encoder model>` or the same basename. That is the convention reindex and
  bundle import already used ("sites stage models under their own paths"). It lets the bundled
  `/opt/umrs/models/e5-base-v2`, a copy at `/models/e5-base-v2`, and the hub id
  `intfloat/e5-base-v2` rank together. It is an interim for D-1's registry with `legacy_names`.
- **A default text space ships with the image** (e5-base-v2, see ADR-090's third 2026-10-09
  block). Image, video and audio vectors are still absent from the spine: an upload of those
  modalities fails its embed step by name.

## Rollout-status (2026-10-12) — ADR-093 implements D-5 for text, as an in-process interim

- **D-5:** the dense lane's sufficiency is a calibrated null (`umrs/src/umrs/knowledge/retrieval/calibrate.py`).
  - 30 fixed probes are encoded per space.
  - z = (cos₁ − μ) / σ, cached per (project, graph set, space, count, last re-embed).
- **Measured on SciFact:** 29–30 of 30 nonsense probes are INSUFFICIENT on both chunk schemes, and false-INSUFFICIENT on evidence claims is at most 1.1% (ADR-093 eval, Phases 3–14).
- **The reranker choice for CPU sites is resolved:**
  - stand-alone pulls MiniLM on demand;
  - rccs stages `bge-reranker-v2-m3` (ADR-093 MD-3);
  - both are off by default, because S4 failed narrowly.
- **D-1/D-2 are untouched.** The space registry and a durable null remain this ADR's.
- **D-3** (a CJK lexical configuration and a Japanese gold set) is scheduled as the next retrieval wave (ADR-093 MD-8). Until then CJK queries get no lexical lift and fail closed.

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
