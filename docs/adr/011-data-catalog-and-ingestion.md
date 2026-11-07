# ADR-011: Data Catalog and DATA_INGESTION

**Status:** Accepted (amended 2026-07-06 — Postgres metadata SoT)
> **Date:** 2026-06-25  
> **Accepted:** 2026-06-30 (Phase A1–A3: synthesizer scanner, ACL tests, demo 08)  
> **Supersedes:** None  
> **Superseded by:** None  

---

## Problem

Scientists use R4 (TUI, VS Code extension, Companion) to upload lab data — CSV
runs, microscopy images, PDF protocols, JSON outputs — through the gateway.
They then ask natural-language questions: *"Which run had the highest yield?"*,
*"Summarize the protein folding results from last week"*, *"What columns are in
the batch-3 CSV?"*

Today the platform can **store** those files but cannot **reason over them** in
the same agentic search flow that already covers RAG documents, code, and web
sources. After `POST /gw/files/upload`, objects sit in S3 with no catalog
memory, no automatic text extraction, and no hook into `r4 search`. The agent
has no tool to discover or read lab uploads alongside its other sources.

We need a thin vertical slice that closes the upload → discover → read loop. It
includes lightweight **perception** (image captioning + content summaries) by
reusing inference endpoints the platform already runs, while deferring heavier
machinery (embedding-based perception, multimodal vector indexes, workflow
dispatch from search, sharded indexes).

## Context

### What exists today

- **Upload works.** `POST /gw/files/upload` → UMRS `upload_file` writes to
  project-scoped S3 and returns the object key.
- **Agentic search works — for other sources.** `r4 search` can query RAG,
  code, and web via planner steps and executor tools.
- **Durable jobs work.** UMRS has a job/attempt framework (`INDEX_BUILD`,
  `WORKFLOW_RUN`, etc.) with crash recovery and idempotency.
- **RAG indexing is opt-in and separate.** Scientists must explicitly ingest
  documents into pgvector; uploaded lab files are not indexed automatically.

### What is missing

| Gap | Impact |
|-----|--------|
| No post-upload processing | Files are invisible to agents until manually ingested elsewhere |
| No greppable metadata | Agent cannot list or filter uploads by name, modality, or content summary |
| No byte-bounded raw read | Agent cannot open a specific CSV/PDF slice without downloading the whole object |
| No lab-data search scope | `SearchScope` has no mode that combines catalog + raw read + RAG while excluding web |

### Naming: three "catalog" concepts

Three distinct catalog concepts already exist and must not collide:

| Concept | Location | Purpose |
|---------|----------|---------|
| Workflow registry | `_catalog_path` in workflow handler | Registered workflow packages |
| Model registry | `model_catalog_entries` | Inference model metadata |
| **Data catalog (this ADR)** | Postgres `catalog_objects` + S3 blobs | Per-object upload metadata for agentic search |

## Decision

### Guiding principle: optimize for agentic search

The query surface is the agent. Storage serves the agent's tools, not the other
way around. The agent chooses per query among catalog grep, raw-file read
(byte-bounded), and optional pgvector/RAG — there is no single mandatory index
in the MVP.

### Why not index everything into RAG?

RAG stays an *optional tool*, not the default upload pipeline. Many scientist
questions are **navigate + read**, not semantic retrieval:

| Query shape | Example | Better tool |
|-------------|---------|-------------|
| Find by name/tag/modality | "batch-3 CSV", "microscopy from Tuesday" | Catalog grep |
| Inspect structure | "What columns are in this file?" | Raw read (byte-bounded) |
| Semantic similarity | "Experiments like this hypothesis" | RAG (when indexed) |

Mandatory RAG-on-every-upload also imposes costs the MVP avoids:

- **Lab data is often non-text** (CSV, HDF5, images, instrument dumps). Embedding it
  is meaningless without multimodal perception (CLIP/Whisper), which the MVP defers.
- **Ingest latency.** Upload can return immediately, but a RAG-only path is not
  searchable until chunk → embed → pgvector completes. The catalog writes a
  `pending` stub synchronously so the agent can list the object while enrichment runs.
- **Large files.** Chunking a multi-GB object produces thousands of vectors — expensive
  and often irrelevant when the user wanted one specific file or byte range.
- **Re-upload churn.** A changed etag implies re-embed, dedupe, and stale-chunk GC;
  catalog metadata keyed by etag is simpler to make idempotent (Full Vision adds GC).
- **Cost and ops.** Mandatory indexing embeds every upload on every change; the MVP
  stays GPU-free. Catalog sidecars in S3 do not require pgvector or an embedding model
  to be healthy for basic discoverability.
- **Agents need ground-truth file content**, not lossy chunks, for analysis — `lab_file_read`
  serves this; vector chunks are a summary layer, not a substitute.
- **Vector search is poor at exact/structured queries** (filter by modality/tag/owner,
  find by filename); metadata grep handles these cheaply and precisely.

The catalog is a **prerequisite for RAG, not a competitor:** ingestion extracts text
(`extracted_text_ref`), which is the substrate pgvector would later index. RAG is
composed in (`SearchScope.LAB_ONLY` = catalog + raw + RAG) and promoted to a
mandatory index only when grep-over-metadata stops scaling on natural-language corpora.

### Agent-navigable catalog

Store compact, greppable metadata in Postgres (`catalog_objects` table, migration `0054`).
Large blobs stay in S3 under `_data_catalog/`:

- `extracted/<encoded_s3_key>.txt` — extracted text (RAG substrate)
- `knowledge/<encoded_s3_key>.json` — Phase 4 structured knowledge

Legacy `_data_catalog/objects/*.meta.json` sidecars are **not** written on the hot path.
Use `python -m umrs.data_catalog.backfill` once per environment to import existing sidecars.

Catalog requires durable Postgres (`umrs_db`); `/umrs/catalog/*` returns 503 if the pool is
unavailable (same coupling as `/umrs/jobs/*`).

### Durable ingestion job

Add `JobType.DATA_INGESTION` following the `INDEX_BUILD` synchronous-backend pattern:

1. `upload_file` writes a `state=pending` row synchronously (object is listable immediately).
2. A durable job enriches the entry (text extraction) and flips to `ready` or `error`.
3. Idempotency key includes `object_etag` so re-uploads and job retries are safe.
4. `DELETE /gw/files/{id}` removes the S3 object **and** the catalog row.
5. `python -m umrs.data_catalog.reconcile --project-id=...` sweeps phantom rows.

Catalog writes live in **UMRS only** (gateway stays stateless per CONSTITUTION §1.4).
Agents reach catalog data via `/gw/catalog/*` routes (search + read), never via direct S3 creds.

### Perception layer (MVP): captioning + layered summarization

Ingestion adds a **best-effort perception step** that reuses
**already-deployed inference endpoints** (e.g. Kimi 2.x) via the in-process
`EndpointRegistry` — no new perception infrastructure. It augments, never
replaces, the deterministic extraction:

| Modality | Ground truth (always) | Perception layer |
|----------|-----------------------|------------------|
| Image | EXIF/format/size | Caption from a vision-capable chat model (opt-in; off by default) |
| Text / PDF / PPTX | Extracted text | LLM summary of the **extracted text** (on by default) |
| Structured (CSV/JSON) | Columns/keys + deterministic tags | LLM summary as a **separate** field (on by default); deterministic columns remain authoritative |
| Other binary | Filename/modality | None |

Captioning requires a *generative* vision-language model (it produces natural
language). The platform's BentoML vision service is for *scientific* tasks
(segmentation, classification, feature extraction) and emits artifacts, not
descriptions, so it is **not** used as a caption path. If no vision-capable chat
endpoint is available, image perception degrades to deterministic EXIF metadata.

The model writes captions/summaries into dedicated fields (see **Metadata schema**
below). Tags record provenance: `vision_captioned`, `llm_summarized`,
`perception_skipped`.

**Metadata schema (MVP).** Each `catalog_objects` row keeps deterministic and generative
layers separate:

| Field | Source | Use |
|-------|--------|-----|
| `deterministic_summary` | `extract_content` (truncate, EXIF, column headers) | Ground truth for display on structured files |
| `perception_summary` | Vision/LLM endpoint | Natural-language hint; may be absent when endpoint unavailable |
| `perception_model` | Endpoint used | Audit / cost attribution |
| `summary` | Derived | Primary greppable field (structured → deterministic; else perception if present) |
| `extracted_text_ref` | Deterministic extraction (+ caption prepended for images) | RAG substrate; raw text sample |
| `tags` | Deterministic + perception | Includes column headers, `llm_summarized`, `perception_skipped`, etc. |

**Ingestion order (deterministic first).**

1. Sample bytes → `detect_modality` + `extract_content` (always).
2. Write `deterministic_summary`, `extracted_text`, deterministic `tags`.
3. **Then** optional perception (never blocks step 1–2).
4. Merge into `summary` / `perception_*` without overwriting structured column tags.

**Failure and degradation (perception only).** Perception is best-effort inside the
synchronous `DATA_INGESTION` job. Deterministic extraction failures still set
`state=error` (download/extract hard-fail). Perception failures never do:

| Condition | Behavior | Catalog state | Tags |
|-----------|----------|---------------|------|
| `ENABLE_PERCEPTION=false` | Skip all perception (text + image) | `ready` | (none) |
| No ready endpoint in registry | Skip perception | `ready` | `perception_skipped` |
| Image toggle off (default) / over byte cap | Skip image caption | `ready` | `perception_skipped` (images only) |
| HTTP 4xx/5xx, timeout, malformed JSON | Log warning, skip | `ready` | `perception_skipped` |
| Registry unavailable | Skip perception | `ready` | `perception_skipped` |
| Download / deterministic extract error | Job fails | `error` | `error_note` set |

Upload path is unaffected: `trigger_post_upload_ingestion` never raises even if
the job enqueue fails.

**Invariants:**

- **Never fails ingestion.** No ready endpoint, over budget, or timeout →
  degrade to deterministic metadata, tag `perception_skipped`, still flip to `ready`.
- **Gated + bounded.** Defaults: text LLM summaries **on**
  (`UMRS_DATA_CATALOG_ENABLE_PERCEPTION=true`), image captioning **off**
  (`UMRS_DATA_CATALOG_ENABLE_IMAGE_PERCEPTION=false`). Either can be overridden;
  configurable model, max input bytes/tokens, per-object timeout.
- **No hallucinated structure.** LLM output never overwrites deterministic columns/tags.
- **Reuses billing.** Perception records token usage into ``llm_usage_log`` via
  the same ``UsageRecord`` / ``record_usage`` path as the LLM proxy (tagged
  ``extra.source=data_catalog_perception``). Calls still go direct to LiteLLM
  from the ingestion worker (not through the HTTP proxy) because ingestion is
  synchronous inside ``DATA_INGESTION``.

**Cost & tunability.** Image captioning runs a vision-language model per upload
and is **off by default**. Text LLM summaries are on by default because they
materially improve catalog grep at query time with modest chat-model cost.

- Image perception is opt-in (`UMRS_DATA_CATALOG_ENABLE_IMAGE_PERCEPTION=true`)
  without affecting text summarization.
- A specific (cheaper/smaller) model can be pinned via `UMRS_DATA_CATALOG_VISION_MODEL`
  / `UMRS_DATA_CATALOG_SUMMARY_MODEL` instead of auto-selecting any vision endpoint.
- **Future option:** route image captioning to a dedicated lightweight
  caption/classification model rather than a large general VLM, trading description
  richness for throughput and cost.

This is *generative* perception over existing endpoints. *Embedding-based*
perception (CLIP/Whisper + multimodal pgvector) stays in Full Vision.

### Gateway and R4 integration (MVP)

| Surface | Route | Purpose |
|---------|-------|---------|
| Gateway | `GET /gw/catalog/search` | Lexical grep over catalog metadata |
| Gateway | `GET /gw/catalog/read` | Byte-bounded raw object read with project ACL |
| Gateway | `GET /gw/catalog/stats` | Counts by state/modality (replaces S3 manifest) |
| R4 search | `lab_catalog_search`, `lab_file_read` tools | Planner steps wired to gateway routes |
| R4 search | `SearchScope.ALL` (default) | Catalog + raw + RAG + web + code when `-p` is set |
| R4 search | `SearchScope.LAB_ONLY` | Catalog + raw + RAG; no web/code |
| Companion | `lab_catalog_search`, `lab_file_read` | Direct gateway wrappers; same hit shape as R4 search |
| R4 CLI | `r4 search` (`lab_catalog_search`, `lab_file_read` planner steps) | Agentic multi-hop via gateway routes |

### Agent routing (MVP)

Same tool names on **Companion** and **R4 CLI search** (`r4 search`). VS Code and TUI do not expose catalog tools yet.

| Tool | When |
|------|------|
| `lab_catalog_search` | Grep uploaded lab data catalog (metadata only) |
| `lab_file_read` | Read file content by `s3_key` (`as=content\|extracted_text\|raw_bytes`) |
| `search_agentic` | Multi-hop: catalog + RAG + web + code (Companion) |
| `r4 search` | Same orchestrator from CLI |
| `rag_query` | Semantic search over indexed prose corpora |

**Uploaded lab file (default agent flow):**

```
1. lab_catalog_search(query)     ← search the catalog (metadata)
2. Read metadata; pick s3_key
3. lab_file_read(s3_key, as=suggested_read)   ← read the file
```

**Multi-hop** (catalog + RAG + web + code): `search_agentic` (Companion) or
`r4 search -p <project>` (CLI). Auto-chains `lab_file_read` for CLI convenience only.

**Indexed prose corpus:** `rag_query` — not the lab data catalog.

Surface routing in: R4 search planner, Companion `data_management` skill, and tool
descriptions in `r4/companion/tools.py`. Agent `as` maps to gateway `source`:
`content`→`auto`, `extracted_text`→`extracted`, `raw_bytes`→`raw`.

### MVP / Full Vision split

**MVP ships with:** text LLM summaries on by default; image captioning opt-in;
best-effort degradation when endpoints are unavailable.

**MVP ships without:** sharded tag indexes, `If-Match` rollup compaction, etag GC/tombstones,
embedding-based perception (CLIP/Whisper + multimodal pgvector), workflow dispatch from
search, push job completion.

Promote Full Vision items only when grep-over-meta stops scaling or embedding-based
perception is required.

### Agent bucket capabilities (MVP)

| Capability | Supported | Mechanism |
|------------|-----------|-----------|
| Navigate / discover | Yes | Catalog search + existing `file_list_platform` |
| Read object content | Yes | `lab_file_read` + `file_download_platform` |
| Upload new files | Yes | `file_upload_platform` → triggers ingestion |
| Arbitrary S3 writes | No | No admin creds; catalog metadata written by ingestion job only |
| Delete objects | No | Out of MVP scope |

### Bucket provisioning (unchanged)

Scientists do not create MinIO buckets manually. Admin enrolls the user; the user declares
experiences in client config and runs `umrs reload` to provision buckets.
This feature indexes objects in whatever bucket the upload targets.

### Catalog → knowledge layer contract (RC1)

The data catalog is the **artifact substrate** for RC1. The M3
``KnowledgeObject`` / graph layer sits above it; the catalog does not replace
structured extraction or graph linking.

| Catalog field | Knowledge layer use |
|---------------|---------------------|
| ``s3_key`` | Canonical artifact ID and provenance anchor (`metadata.s3_key`, `metadata.catalog_ref`) |
| ``detected_modality`` | Maps to knowledge ``artifact_type`` via ``modality_to_artifact_type()`` (`document`→`paper`, `structured`→`dataset`, `notebook`→`notebook`, `code`→`code`, …) |
| ``extracted_text_ref`` | Preferred byte source for ``understand()`` / ``verify()``; falls back to bounded S3 object read |
| ``summary`` / ``deterministic_summary`` | Catalog grep + verifier search; not ground truth for structured column names |
| ``state`` | Only ``ready`` objects are listed by ``CatalogArtifactAdapter`` (``pending`` optional; ``error`` excluded) |
| ``acl_scope`` | Must match ``project_id``; object key must be under ``{project_id}/`` |

**Ingestion paths (unified when S3 is configured):**

1. Gateway upload → ``DATA_INGESTION`` → ``ingest_object``
2. Batch → ``KnowledgeIngester`` → ``ingest_object``
3. ``ResearchProject.ingest_directory()`` → upload → batch ingest → ``CatalogKnowledgeStore``

**Phase B (shipped):** ``ResearchProject`` uses ``CatalogKnowledgeStore`` (wraps
``CatalogArtifactAdapter``) for ``understand()``, ``verify()``, and coverage.
Phase B does **not** cover ``run_research()`` (the autonomous loop) — loop
wiring is tracked in the RC1 Operational Roadmap (Phases 0.4 and 1).
Extracted knowledge is an in-memory overlay keyed by ``artifact_id``; artifact
bytes load from catalog on demand. Local-only tests without S3 keep
``InMemoryKnowledgeStore``.

**Stable artifact id:** ``{artifact_type}-{filename_stem}-{hash(s3_key) & 0xFFFFFF:X}``
(see ``artifact_id_for_meta()`` in ``data_catalog/artifacts.py``).

## Consequences

### Positive

- Scientists can upload data and ask questions in the same session without a separate ingest step.
- Image uploads become searchable by content (captions), not just filename/EXIF.
- Reuses durable job framework, existing S3 upload path, inference endpoints, and (optionally) pgvector HNSW.
- Core catalog extraction and search are GPU-free; text summarization uses existing chat endpoints; image captioning is opt-in.
- Catalog search uses Postgres FTS + GIN tag indexes (no S3 LIST on the hot path).
- Clear naming (`_data_catalog/` blobs vs `catalog_objects` rows) avoids registry collisions.

### Negative

- Catalog metadata depends on Postgres availability and backup discipline (same as jobs/RAG).
- Object delete must remove catalog rows; reconciliation CLI required for out-of-band S3 deletes.
- PDF/PPTX extraction depends on optional libraries; graceful degradation required.
- Ingestion samples large files (configurable byte cap); full-file semantic search still needs RAG or Full Vision.
- Perception couples ingestion to inference availability and adds GPU cost/latency per upload; mitigated by the flag, caps, and best-effort degradation (perception failure never blocks `ready`).
- Perception runs **inline** in the synchronous `DATA_INGESTION` backend, so a slow
  VLM call extends job duration (bounded by `UMRS_DATA_CATALOG_PERCEPTION_TIMEOUT`).
  High image volume may require async perception attempts (Full Vision).

## Validation

Automated checks (run from `ai4s-platform` repo root):

```bash
# UMRS data catalog unit tests
cd umrs && python -m pytest tests/test_data_catalog_*.py -q

# Gateway routes catalog to UMRS
cd gateway && python -m pytest tests/test_nginx_config.py -q -k catalog

# R4 LAB_ONLY scope
cd r4 && python -m pytest tests/test_search_core.py -q -k lab_only
```

**Smoke (staging, after deploy):**

1. Upload a CSV and a PNG via `POST /gw/files/upload`.
2. Confirm `DATA_INGESTION` job completes; `catalog_objects` row shows `state=ready` (or `GET /gw/catalog/stats`).
3. `GET /gw/catalog/search?q=batch` returns the CSV; tags include column headers.
4. `GET /gw/catalog/read?s3_key=<key>` returns byte-bounded content with project ACL enforced.
5. CSV entry has `llm_summarized` or `perception_skipped` when no chat endpoint; PNG has EXIF metadata only unless `ENABLE_IMAGE_PERCEPTION=true` (never `error` from perception alone).
6. `r4 search` with lab scope: `lab_catalog_search` discovers upload; ground-truth questions may auto-chain `lab_file_read`.

### Rollout and rollback

**Rollout:** apply migrations (`python -m umrs.migrations`), run
`python -m umrs.data_catalog.backfill` for legacy S3 sidecars, deploy UMRS,
then `python -m umrs.data_catalog.reconcile` once to drop phantom rows.

**Rollback (forward-only):** migration `0054` has no down script. To revert:
drop `catalog_objects`, redeploy the previous UMRS build that reads S3
`.meta.json` sidecars, and re-ingest or backfill metadata if sidecars were
removed.

## References

- [CONSTITUTION.md](../../CONSTITUTION.md) §1.4, §3
- [002-durable-job-attempt-separation.md](002-durable-job-attempt-separation.md)
- [009-research-loop-architecture.md](009-research-loop-architecture.md) — Research Loop needs lab-data discovery for closed-loop science
