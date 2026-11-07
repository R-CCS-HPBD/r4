# ADR-027: Unified Knowledge Spine — One Write Verb, One Read Verb

**Status:** Proposed (P0 implemented 2026-08-22)
> **Date:** 2026-08-22
> **Relates to:** ADR-011 (data catalog), ADR-013 (.kb bundles), ADR-015 (ingest egress), ADR-024 (knowledge module boundary), ADR-025 (research sessions), ADR-026 (sat_kg disposition), three-tier access boundary (2026-08-21, `umrs/knowledge/access_scope.py`), migration 0069 (canonical multi-graph), K3 facade (`umrs/api/knowledge_query.py`)

## Context

Three knowledge stores coexist with three write paths and three read paths:

| Store | Write path | Read path | Scope |
|-------|-----------|-----------|-------|
| Rag chunk corpus (pgvector inside RAG service) | `POST /umrs/rag/upload` | `POST /umrs/rag/query` | body `project_id` == verified project only |
| Canonical knowledge graph (`knowledge_nodes/edges`, 0069) | `knowledge_ingest` durable jobs | `/umrs/knowledge/*`, `knowledge_query` | three-tier (system/project/user) |
| Data catalog (`meta_store`) | `r4 data` intake (ADR-011) | `/umrs/catalog/*` | three-tier |

Identified gaps (2026-08-22 analysis):

- **G1** — silos: no unified query crosses stores; K3 facade wires catalog backend only (KG/vector backends deferred).
- **G2** — three-tier predicates are not applied to `/umrs/rag/*` routes; no admin bypass, no system-shared corpus semantics, no `user:<uid>` owner tier.
- **G3** — weak scoping: `/rag/query` maps `project_id` to `xp_name`; unmapped projects silently fall back to any single embedding-enabled table (cross-project leakage possible). `/rag/upload` has the same silent fallback.
- **G4** — no feedback loop: rag chunks never mint graph nodes; knowledge ingest never populates the rag corpus; research loops read the graph and are blind to uploaded docs.
- **G5** — rag corpus lives inside the mini-RiVault RAG service (endpoint lifecycle coupling), contrary to ADR-013 compute/knowledge separation.
- **G6** — dead write surface: client `POST /gw/rag/index` (`client.rag.index()`) has no server route; calls fail opaque-404.
- **G7** — governance split: three unrelated governance mechanisms for "put knowledge into the platform".
- **G8** — no ADR covers the rag↔knowledge boundary (this document closes that).

## Decision

Converge on a **Unified Knowledge Spine**: the canonical multigraph (0069) is the system of record; pgvector and the catalog become indexes over the same nodes — not independent stores.

- **D1 — Chunks are graph nodes.** Ingestion mints `document` + `chunk` nodes (`part_of` edges); embeddings live in pgvector keyed by `node_id`. pgvector becomes a rebuildable index, not a store.
- **D2 — One write verb:** `knowledge_ingest(source)` for files, URLs, inline text, catalog objects, tool results, loop findings, `.kb` imports. `r4 rag upload` folds in as a facade alias.
- **D3 — One read verb:** `knowledge_query(q, mode=semantic|graph|catalog|hybrid)` with fan-out + explicit coverage/honesty map (extend the K3 facade) and per-hit provenance (`graph_id, node_id, score, tier`).
- **D4 — Three-tier scope everywhere**, including the migrated rag routes (predicates in `umrs/knowledge/access_scope.py`).
- **D5 — Custom knowledge = user-tier graphs or provider backends.** Users ingest into personal graphs; external corpora register as query backends in the facade availability map (provider SPI, P3).
- **D6 — Accumulation loop closed.** Research-loop findings ingest back with provenance (`source_loop`, ADR-025 `session_id`).

## Phasing

| Phase | Change | Gaps |
|-------|--------|------|
| **P0** (this change) | Fail-closed project→index mapping in `rag_service`; three-tier predicates on `/umrs/rag/*` proxy; retire `client.rag.index()` client path | G2, G3, G6 |
| **P1a** (landed 2026-08-22) | Uploaded docs mint canonical nodes: `/rag/upload` returns `chunk_details`; proxy mints artifact + `file` document node + `entity` chunk nodes + `part_of` edges into the project's default graph (best-effort, capped at 100 chunks; never fails the upload). Embeddings stay in the rag table for now | G4 (upload path) |
| **P1b** | `knowledge_ingest` owns all ingestion incl. upload facade; node-keyed pgvector in central Postgres; `r4 rag` → `r4 knowledge` CLI fold | G1, G5, G7 |
| **P2** | K3 facade gains vector + graph backends over canonical tables; `/umrs/rag/*` proxy → facade; CLI fold (`r4 rag` → `r4 knowledge` aliases) | G1, G7 |
| **P3** | Knowledge-provider SPI (custom MCP backends with per-provider governance) | extensibility |
| **P4** | Loop findings auto-ingest; `.kb` round-trip incl. node-keyed embeddings | accumulation |

## P0 specifics (implemented with this ADR)

1. **rag_service fail-closed** (`umrs/api/rag_service.py`): unmapped `project_id` on `/rag/query` and `/rag/upload` now returns **404 "No RAG index for project '…'"** instead of silently selecting another project's table. Escape hatch: `UMRS_RAG_LEGACY_TABLE_FALLBACK=true` restores the deprecated any-table behavior (one release, then removed).
2. **rag proxy three-tier** (`umrs/api/rag.py`): every route resolves identity then applies `project_readable` / `project_writable` from `access_scope.py`. Effects: admin bypass; `system` corpus readable by any verified caller, writable only by admins; `user:<uid>` namespaces owner-only.
3. **Dead client path retired** (`r4/rag.py`): `client.rag.index()` now raises `R4Error` immediately explaining the route never existed server-side and pointing at `rag.upload()` / `knowledge_ingest`. The `IndexResult` type is kept for import compatibility and marked deprecated. TUI `rag_index_files` surfaces the retirement message instead of an opaque 404.

## Consequences

- Behavior change (loud, intended): previously-working requests against unmapped projects now 404; cross-project admins regain correct semantics. Rollout note goes in the release notes.
- `rag.index()` is retired without removal per mutation-safety (additive-first): signature stays, behavior is a descriptive exception. Physical removal candidate after one release.
- P1–P4 remain open; each lands as its own change set against this contract.

## Rollout status (2026-09-07, WP-15 round 1)

P1b item ledger (the package's ratified remedy was "node-keyed central
corpus + facade backend wiring"):

- **Node-keyed pgvector in central Postgres — LANDED earlier, verified.**
  Migration 0075 (`knowledge_node_embeddings`, ADR-027 G5/P1b foundation,
  untyped vector column with the documented typed-shadow + HNSW follow-up)
  plus live writers: `knowledge/store.py`, `node_embedding_backfill.py`,
  `rag_upload_mint.py` (upload mints chunk rows + vectors best-effort).
- **Facade backend wiring — LANDED this round.** `_RAGSearchBackend` in
  `umrs/api/knowledge_query.py`: the `rag` scope now answers from the
  node-keyed central corpus (`knowledge_nodes`, migration 0055) instead of
  requiring a deployed mini-RiVault instance. Honesty contract: fixed
  recency-ranked score (no relevance model — same convention as the
  embeddings table-reader), `metadata.lane: central-corpus`,
  `metadata.corpus_venue: central-pg`, snippets extracted from node content
  JSONB with a content-hash pointer fallback (no fabricated text), wiring
  gated on the corpus table's presence with named honesty reasons (the
  unwired default no longer blames the endpoint). Pins:
  `umrs/tests/test_knowledge_query_rag_backend.py` (10) + script-sequence
  updates in `test_knowledge_query_graphs.py`.
- **`knowledge_ingest` owns the upload facade — OPEN, named.** `/umrs/rag/upload`
  still proxies bytes to a discovered mini-RiVault endpoint
  (`_discover_rag_endpoint`) and merges the response with central minting.
  Moving ownership needs a bytes-to-central-ingest path (KnowledgeIngester
  consumes paths, not inline bytes) plus the endpoint-corpus query
  continuity question — its own change set, deliberately not mixed into
  this facade wiring.
- **CLI fold (`r4 rag` → `r4 knowledge` aliases) — OPEN**, P2 change set.

## Rollout status (2026-10-09, P2 read leg — answers from the spine)

Found on the stand-alone stack: uploads feed only the spine (WP-15), while
`POST /umrs/rag/query` read only the per-experience store, so an uploaded
document could be searched but never answered from ("No documents retrieved").
Landed (validation report §8):

- **K3 vector lane is semantic** — the query is embedded by the RAG service
  (`POST /rag/embed`, R6.1) and ranked in one space (ADR-083 rollout block).
- **`/umrs/rag/query` answers from the spine** (`api/rag_spine.py`): top-k
  passages at or above the request threshold; each passage's full chunk text
  rebuilt from its artifact and md5-checked against the chunk node (else the
  stored excerpt; a passage with neither is dropped); the answer from the chat
  model the endpoint registry resolves (gap D-63). Sources carry
  `metadata.corpus_venue: spine` and the document's `source_name`.
- **`UMRS_RAG_QUERY_FACADE`**: `auto` (default) = the spine whenever it has
  passages, else the endpoint corpus; `spine`; `endpoint` = the proxy alone,
  as before.
- **Consequence for a project with both corpora:** under `auto` the spine
  answers whenever it has passages, so standing endpoint-corpus data is not
  consulted. Remedy: canonize the legacy corpus into the spine
  (`python -m umrs.knowledge.legacy_corpus_canonize`, dry-run first), or set
  `UMRS_RAG_QUERY_FACADE=endpoint`. Chunk nodes minted by the canonizer and by
  the pre-WP-15 upload mint carry offsets but no artifact link or excerpt, so
  their passages are dropped until a text source is added — recorded, not fixed.
- Still open: `/umrs/rag/documents` and `/stats` proxy the endpoint corpus;
  the `r4 rag` → `r4 knowledge` CLI fold.

## Rollout status (2026-10-09, later — the spine is the default)

Maintainer decision 2026-10-09: the deployments are in development and the legacy per-experience
corpora "can be cleaned". `UMRS_RAG_QUERY_FACADE` therefore defaults to **`spine`**: the spine
only, with an honest "no knowledge in this project answers this". `auto` and `endpoint` remain as
opt-ins. The earlier "Consequence for a project with both corpora" no longer needs a canonize step
before deploy; operators may drop the legacy tables.

## Rollout status (2026-10-09, latest — the research loop reads the spine)

G4's loop leg ("research loops … are blind to uploaded docs") is closed for the server engine.
`api/loop.py` built `AutonomousResearchLoop` with no knowledge store. So in real mode every
iteration retrieved nothing and stopped at the evidence gate, in every deployment (plan §3c.2).
The engine now carries `rag_spine.SpineKnowledgeStore`, bound to each loop's project. Its
`search_artifacts` is D3's semantic lane, the same `retrieve_passages` + `attach_texts` that
answers `r4 knowledge query`. Live on the stand-alone stack:
`r4 project loop start default "Does sodium exchange raise ZSM-5 CO2 uptake?" --workflow
demo08_yield_eval` ran one real iteration. The uploaded zeolite note was the top evidence
(0.875), the experiment job ran, the evaluation reads "supports", and the finding was minted
(D6). Found on the way: every read on this lane borrowed a pooled connection in the wrong order
(`with pool.getconn() as conn:` with `putconn` inside it). psycopg2's pool closes a returned
connection once it already holds `minconn` idle ones, so the transaction then ended on a closed
connection. That happens every time at `minconn=0`, and on the shared pool under any overlap.
All eight sites now borrow, end the transaction, then return the connection
(validation report §11).
