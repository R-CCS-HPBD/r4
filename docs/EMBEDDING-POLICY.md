# Embedding policy — models per modality, chunking, re-embedding (D-15)

**Status:** ACTIVE (2026-09-23; updated 2026-10-09: bundled default text embedder, per-modality failure rule; **updated 2026-10-10**: chunking corrected, chunk schemes and the same-model reindex repair, [ADR-093](adr/093-retrieval-for-small-local-models.md) D-11) · **Design:** [R4-UMRS-DESIGN.md](R4-UMRS-DESIGN.md) gap rows D-14/D-15 (ROADMAP 4.1)

This document is the single embedding-policy statement the design asks for:
which models produce vectors per data type, how content is chunked, how
vectors are versioned, and when re-embedding runs.

## Models per modality

| Modality | Producing models | Surface |
|---|---|---|
| **Text (knowledge spine, and the RAG query encoder)** | `intfloat/e5-base-v2` (768-d) **bundled in the umrs-server image** at `/opt/umrs/models/e5-base-v2`, revision pinned (maintainer decision 2026-10-09: text embedding works out of the box). `UMRS_KNOWLEDGE_EMBED_MODEL` points a site at another staged model; the RAG service encodes queries with the same model unless `UMRS_RAG_EMBEDDING_MODEL` says otherwise. `all-MiniLM-L6-v2` is only the RAG service's last fallback | `umrs/src/umrs/knowledge/text_embedder.py` (staging + resolution), `jobs/knowledge_adapters.py`, `api/rag_service.py` |
| **Text (bundle manifest lane)** | `intfloat/e5-base-v2` (default; env-overridable) | bundle manifests carry the embedding-model id; catalog create pins embedding model ids (registry-resolved, fail-open — ADR-068) |
| **Images** | MAE / HMAE / ResNet-50 encoders (tomography + vision workflows) | `umrs/src/umrs/inference/{masked_autoencoder,hmae_inference,resnet_50}.py`; vision-service adapters under `umrs/src/umrs/vision_service/` |
| **Video** | detection only — no video embedding models yet (named gap) | `umrs/src/umrs/data_catalog/modality.py` |

**One embedder per modality, or a named failure (2026-10-09).** Knowledge ingest
files each artifact by modality (`data_catalog/modality.py`: suffix, then magic
bytes). Text, code and structured files embed with the text model; PDF and PPTX
embed through their extracted text (pypdf / python-pptx, now in the image); a
document with no text layer fails ("no extractable text"). Images, video, audio
and binary files have **no embedder in the knowledge lane** — the image encoders
above serve the vision workflows, not the spine — so their understand and embed
steps fail by name ("no embedding model for image content") instead of embedding
their bytes as text, which is what happened before. A site with no text embedder
fails its embed jobs; it is no longer a quiet no-op. An embedding space is the
model's basename, so the bundle, a copy at `/models/e5-base-v2` and the hub id
`intfloat/e5-base-v2` rank together (`text_embedder.embedding_space`).

Both text defaults are 384-dim-class sentence encoders; the node-embedding
lane also accepts the E5 768-dim class. **Cross-dimension queries fail
loudly in Postgres by design** — the `dimension` column and the
`(node_id, model_name)` key keep models from mixing (migration 0075).

## Chunking rules

**Corrected 2026-10-10 (ADR-093 D-11).** This section used to say that the
understand job writes the spine's chunk nodes and that the text lane uses
the spaCy chunker `chunk_text_by_max_tokens`. Neither was true: the
understand job writes the file node and the extracted entities, and every
spine chunk was a fixed token window cut by `KNOWLEDGE_EMBED`.

**Knowledge spine (`KNOWLEDGE_EMBED`).** The embed job cuts each artifact's
text in the embed lane — the in-process embedder or the `embedding-job`
sandbox (`knowledge/embed_worker.py`, CONSTITUTION D-12) — never in the
server, with the site's scheme `UMRS_KNOWLEDGE_CHUNK_SCHEME`
(`knowledge/chunking.py`; an unknown name fails the job by name):

| Scheme | Cut | Embedded text |
|---|---|---|
| `fixed-v0` (default until the ADR-093 S7 gate) | windows of `UMRS_KNOWLEDGE_EMBED_CHUNK_TOKENS` (256) model tokens (`shared/utils._chunk_text_without_nlp`); edges fall mid-sentence and the last window is often a fragment | `passage: ` + body |
| `sent-v1` | a deterministic regex splits on `. ! ?` (abbreviation guard: decimals, `Fig.`, `et al.`, initials, `No. 5`), on `。！？；`, and at markdown headings, blank lines and list items; sentences pack to 200 tokens, at most 256; a body of 384 tokens or less stays one chunk; the previous chunk's last sentence overlaps into the next when it is 48 tokens or less; a tail under 48 tokens merges back; a sentence over 256 tokens falls back to windows; a packed chunk never passes 256 tokens; a heading starts a chunk | `passage: ` + header + newline + body, the header being `{title} › {section}` (at most 32 tokens, no LLM; the title is the artifact's metadata title, else the level-1 heading on the first line of a markdown text, else its source name; in a code, config or plain-text file a `#` line is neither the title nor a section, and the embed lane is told so per text — 2026-10-10 review) |

For both schemes:

- every chunk is `text[start:end]` of the artifact's text and `chunk_md5`
  is the body's md5, so hydrate, the `search_text` backfill and the export
  verify it against the artifact. The artifact's text is its UTF-8 bytes
  with NUL dropped, or for a PDF (and a PPTX, by its source name) the
  catalog extractor's text; every raw-slice reader, R4's offline bundle
  reader included, decodes it the same way (2026-10-10 review);
- node ids are `node-chunk-{scope}{artifact}-{i}` (the neighbour merge
  derives neighbours from them) and the node content records
  `chunk_scheme`; `sent-v1` adds `header`, `section`, `num_tokens` and
  `header_chars`;
- `search_text` (migration 0120) holds the body for `fixed-v0` and
  header + newline + body for `sent-v1`; `search_text[header_chars:]` is
  always the body. Every later writer of the column — the backfill
  (`python -m umrs.knowledge.retrieval.backfill`) and the bundle import —
  rebuilds it by the same rule (`chunking.search_text_of`: `content.header`,
  a newline, the md5-verified body) and writes nothing when the node's
  header and `header_chars` disagree;
- **honest writes:** a re-embed that yields n chunks deletes the artifact's
  chunks n.. in that graph scope, their edges and vectors with them; at most
  `UMRS_KNOWLEDGE_EMBED_MAX_CHUNKS` (128) chunks are indexed per artifact,
  and the stamp records `chunk_scheme`, `chunks_total` and `chunks_indexed`
  in the artifact's `metadata` (an artifact without `chunk_scheme` was cut
  `fixed-v0`). The scheme recorded is the one the worker reports, so an
  older worker image that ignores the request's scheme is recorded as
  `fixed-v0`;
- **one current vector per space:** the same write deletes the written
  chunks' vectors held under another model name when that name is in the
  writer's space (the same model staged under another path, e.g.
  `/models/e5-base-v2` beside `/opt/umrs/models/e5-base-v2` — the dense
  lane matches both by basename and would rank the old vector of the old
  text) or when the row records another text's md5 (integrator,
  2026-10-10). Vectors of other spaces whose md5 still matches stay.

**Other lanes (unchanged).**

- **RAG uploads (`/rag/upload`):** the RAG service windows text with the
  same fixed chunker (`api/rag_service.py`, `UMRS_RAG_CHUNK_MAX_TOKENS`) and
  mints chunk nodes `…-chunk{i}` (`knowledge/rag_upload_mint.py`).
- **Embedding API (`inference/openai_api.py`):** `flatten_text_chunks` —
  spaCy sentence chunks (`chunk_text_by_max_tokens`) when a pipeline is
  loaded, fixed windows otherwise.
- **Source code (pipeline SDK `CodeStep`):** line-count chunks (default
  `chunk_size: 100`, per-language extraction with imports/docstrings
  flags).

## Vector stores and version tracking

| Table | Kind | Version tracking | ANN index |
|---|---|---|---|
| `memory_vectors` (0010) | scientific-memory vectors | `vector_version_store` records the producing model per vector; re-embed jobs per model bump | **HNSW** (live) |
| `knowledge_node_embeddings` (0075) | node-keyed graph vectors | `model_name` + `dimension` + `content_hash` per row; artifact-level stamps via `embedding_metadata` (`target_model`, staleness selection) | **None yet — gated** (see below) |

Re-embedding policy (corrected 2026-09-24): an artifact is stale when its
stamp is not `current` or names another model. **Only `KNOWLEDGE_EMBED`
stamps** — each artifact is marked `current` with the producing model and the
real vector dimension *after* its vectors are written
(`embedding_metadata.stamp_artifact_embedded`). The **`KNOWLEDGE_REINDEX`
durable job** resolves its `target_model` to the site's staged embedder
(`UMRS_KNOWLEDGE_EMBED_MODEL`, matched by basename — any other model is
refused by name), sweeps the stale artifacts and enqueues one `KNOWLEDGE_EMBED`
job per project over them (the `embedding-job` sandbox lane when the site
opted in), then runs the index maintenance seam. Until 2026-09-24 it stamped
swept artifacts `current` without recomputing any vector — metadata that
claimed a re-embed that never happened.

Same-model repair (2026-10-10, ADR-090 D-4, ADR-093 D-11): an artifact is
also stale when it was cut by another chunk scheme than the site's
(`COALESCE(metadata->>'chunk_scheme', 'fixed-v0')`), read from the artifact
stamp **or** from any of its chunk rows in its project
(`content->>'chunk_scheme'`). A reindex sweeps the job's own project: a spec
`project_id` naming another project is refused, and so is a job without a
project, so a site re-chunk is one job per project. A spec's `chunk_scheme`
must be the site's (it is refused by name otherwise: the re-embed cuts with
the site's scheme, so the sweep would never converge). Each artifact is
re-embedded into every graph of its project that holds its file node or its
chunks, one `KNOWLEDGE_EMBED` job per (project, graph), so a re-chunk
replaces the chunks in place, in the id scope the artifact's file node
carries (an imported graph's included), and the honest write deletes the
rest. An artifact with no file node in the target graph fails by name and
writes nothing. The index
maintenance seam is **skipped** for `knowledge_node_embeddings`: its 0075
column has no dimension, so no ANN index can exist on it (the HNSW gate
below), and the call used to fail every reindex after the re-embed jobs were
already enqueued. To switch a project: set `UMRS_KNOWLEDGE_CHUNK_SCHEME`
in the UMRS environment (a container reads it only when recreated), submit
`KNOWLEDGE_REINDEX` under that project with the site model and the table
`knowledge_node_embeddings`. One job sweeps at most `batch_limit` artifacts
(default 500), so a large project needs a larger limit or repeated jobs until
no chunk is left on the old scheme. The retrieval null needs no refresh: it
is computed in process and keyed on the scope's vector count and last
re-embed, so the re-embed invalidates it (*corrected 2026-10-10*).
`python -m umrs.knowledge.retrieval.calibrate --project P` only prints it.

## The HNSW gate (recorded decision)

`knowledge_node_embeddings` declares its `vector` column **without** a fixed
dimension: models in play differ (E5 ≈ 768–1024, MiniLM/hash-fallback 384),
and pgvector ANN indexes require a fixed-dimension column (confirmed on the
deployed pg15/pgvector build — migration 0075's note). Nearest-node queries
run exact cosine scans today. **When the P2 decision pins one canonical
model**, a follow-up migration adds the per-model typed shadow column +
HNSW index. Until then, no index is added — a silently-wrong ANN index
would be worse than an exact scan.

## Named gaps (kept honest)

- Video embedding models + per-modality chunking (D-14 video leg: Absent).
- Fixed-dimension column + HNSW on `knowledge_node_embeddings` — gated on
  the P2 canonical-model decision.
- Extending `vector_version_store`-style per-vector version rows to
  `knowledge_node_embeddings` (today it carries model/dimension/hash per
  row — full version rows follow with the P2 decision).
