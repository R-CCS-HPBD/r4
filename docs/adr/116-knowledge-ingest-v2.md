# ADR-116: Knowledge ingest v2: whole-document parsing, ontology-guided extraction, entity-resolution proposals

**Status:** Accepted (2026-11-12, maintainer: "yes. please go with your suggested route. if you will need, you can use/add system models of r4/umrs. let's go get a nice updates for this part of r4/umrs functionalities."; details agent-decided under delegation)
**Date:** 2026-11-12
**Relates to:**
- [ADR-081](081-typed-knowledge-spine.md) (Proposed): this ADR lands a subset of D-5 (warn mode), D-6 (fuzzy matches only *propose* `same_as`) and D-9 (ontology registration surface). The typed-spine columns (D-1, D-2) stay open.
- [ADR-084](084-growth-governance.md) (Proposed): D-2 "judgements, including entity merges, land proposed" is the rule D-7 follows.
- [ADR-093](093-retrieval-for-small-local-models.md) (Accepted): the graph lane reads `content.aliases`; D-7 feeds it.
- [ADR-100](100-dependency-locks-cooldown-refresh.md): how the new libraries enter the lock and the server image (D-9).
- [ADR-106](106-service-model-tier.md): extraction may name a service-role alias such as `svc-fast` (D-2).
- [ADR-015](015-knowledge-ingest-egress.md) / CONSTITUTION offline-model rule: no model download at runtime (D-1, D-3, D-9).
- Investigation: kg-multimodal-investigation-2026-10-03.md findings KG-01, KG-02, KG-04, KG-06, KG-07.
- Gap rows: D-38 (ingest), D-60 (typed multimodal spine).

## Context

Verified 2026-11-12 on parent `b7bba75a`, umrs `cdcacff5` (paths under `umrs/src/umrs/`).

The knowledge graph is plain Postgres (`knowledge_nodes` / `knowledge_edges`, migration 0055) filled by the
KNOWLEDGE_UNDERSTAND job in `jobs/knowledge_adapters.py`. For prose, `_llm_extract_document` (:2892) sends
`text[:9000]` (:2921) to one chat endpoint with a fixed 13-type list (`_PROSE_EXTRACT_SYSTEM`, :2688), no
`response_format`, a greedy-regex JSON scrape and a synchronous `httpx.post` on the server event loop. Everything
after the first 9 000 characters of a paper is never read. PDFs are read with pypdf only (`data_catalog/extract.py:129`).

Node ids embed the artifact id (`node-sem-{scope}{slug}-{aid}`, :2969), so one concept in two documents is two nodes
and nothing links them (KG-07). The ontology code (`knowledge/ontology.py`, registry migration 0091) exists, but no
route or CLI registers an ontology (KG-04), `register()` drops `subtype_of` and `extraction_fields`, the only writer
that validates (`PgGraphSink`) has no production caller, and the canonical view projects by the 7-bucket
`node_type`, so a science ontology would project to zero nodes (KG-02).

A comparison with current open-source work (2026-11-12) found no library that should replace the store:
LightRAG is the closest fit (it runs on Postgres) but would be a second knowledge store; NVIDIA auto-ontology builds
a SQL semantic layer from database metadata, not a document KG; Microsoft GraphRAG is in maintenance mode. The
components worth adopting are Docling (layout-aware PDF parsing), GLiNER (cheap zero-shot entity tagging),
LinkML (ontology authoring format, as OntoGPT/SPIRES uses it) and the KGGen / neo4j-graphrag pattern of an
explicit entity-resolution stage.

## Decision

### D-1 Pluggable document parser for the understand stage only
`knowledge/doc_parse.py` defines the parser seam. `UMRS_KNOWLEDGE_DOC_PARSER=pypdf|docling` (default `pypdf`).
Docling runs with OCR off, from pre-staged artifacts (`UMRS_KNOWLEDGE_DOCLING_ARTIFACTS`, default
`/models/docling`), offline. When `UMRS_EMBEDDING_SANDBOX=podman` is on, it runs in the same sandbox launcher as the
embedder (same image, `--network=none`, read-only model mount) via `python -m umrs.knowledge.ingest_worker`;
otherwise in a worker thread. The sandbox is created by the host podman, so it mounts the host path of the model:
`UMRS_KNOWLEDGE_DOCLING_ARTIFACTS_HOST_DIR` / `UMRS_KNOWLEDGE_GLINER_MODEL_HOST_DIR` when set, else the server path
re-rooted from `/models` onto `UMRS_KNOWLEDGE_MODELS_HOST_DIR` (the umrs-front converge writes it), else the server
path itself. The parse runs inside what is left of the job's budget (D-2); a parse still running then is left
behind and the job goes on with the fallback text. Any failure falls back to the existing pypdf / python-pptx text
and names the reason in the job notes (`doc_parser_fallback:<reason>`). The parser output feeds extraction only; chunking and embedding
keep today's text, because retrieval hydration recomputes chunk offsets and md5s from it (`api/rag_spine.py`).
*(Amended 2026-11-13: Docling is now the default parser, its weights are baked into the image, and the
`umrs-docmodels` service runs it first; see "Amendment 2026-11-13" below.)*

### D-2 Whole-document windowed extraction
*(2026-11-14, from the first live run on rccs: the extractor had taken the first registry chat row, `svc-vision`
(Qwen3.6-35B-A3B), a reasoning model that spent the whole reply on thinking, so a 26-page paper gave 7 entities.
Endpoints are now ranked by ADR-106 role (`svc-fast`, then `svc-chat`, vision/OCR models last), and the JSON-mode
call sends `chat_template_kwargs: {enable_thinking: false}` (the plain retry after a 4xx sends neither field;
`UMRS_KNOWLEDGE_EXTRACT_THINKING=on` keeps thinking). Measured through the gateway: `svc-fast` read the same paper in
16 of 16 windows, 100 entities, 122 s. umrs `385d951a`; `r4 job describe` now prints the understand job's notes.)*
Extraction reads the whole text in windows packed from `sent-v1` sentence chunks
(`UMRS_KNOWLEDGE_EXTRACT_WINDOW_CHARS`, default 8000), capped by `UMRS_KNOWLEDGE_EXTRACT_WINDOWS_MAX` (default 24),
run with bounded concurrency (`UMRS_KNOWLEDGE_EXTRACT_CONCURRENCY`, default 4, never above 10) inside a total budget
(`UMRS_KNOWLEDGE_EXTRACT_JOB_BUDGET_S`, default 300) **per understand job**, shared by all its documents and their
Docling parses, off the event loop; each document's window budget is the smaller of
`UMRS_KNOWLEDGE_EXTRACT_BUDGET_S` (default 300) and what is left of the job's. A document that starts with less than
60 s of the job budget left reads one window (lite) outside the deadline and notes `extract_job_budget_exhausted`, so
a job holds the scheduler for about its budget plus one window call per remaining document. A Docling parse runs
within the time left too (`doc_parser_fallback:timeout` / `budget_exhausted`). (Amended 2026-11-12 after review: the
budget was per document, and the scheduler awaits `submit_job` inline, so a 20-document upload could hold every
other job for hours.) Windows dropped by the cap or the budget are named in the notes. The request asks for JSON mode (`response_format: json_object`) and
retries once without it if the endpoint refuses; the reply parser strips fences, takes the first balanced object
that carries an `entities` / `nodes` / `relations` / `edges` list, and tolerates a reasoning model's empty `content`.
A reply with no such object (cut off at `max_tokens`, or another shape) is a failed window, never an empty graph,
and is counted as `extract_window_unparsed:<n>` as well as `extract_windows_failed:<n>`. An entity without `name`
takes `label` or `id`, and a single-key map `{"GLiNER": "Model"}` reads as name -> type.
The call timeout covers a reply at the token cap (`UMRS_KNOWLEDGE_EXTRACT_MAX_TOKENS`, default 2500) at 25 tok/s,
at least 60 s (100 s by default); `UMRS_KNOWLEDGE_EXTRACT_TIMEOUT` overrides it. Only "no semantic payload at all" marks the run
degraded; parser fallback, pre-tagging and window-cap notes go to `extract_notes`. Window results merge by normalized name into one node per entity per document, carrying
`content.aliases` (surface variants), `content.evidence` (at most 5 `{window, quote ≤ 200 chars, parser}`) and
`content.extractor`. The extractor version becomes `2.0.0` and the "already understood" probe checks it, so
re-understanding an artifact re-extracts it. The probe also checks that the artifact's file node was written from
that IR, so an attempt that died after the new IR's insert is rebuilt on retry. After a re-extraction writes its
rows, the older IR's semantic rows that nobody reviewed (proposed, no `review`, no established or reviewed edge) and
were not written again are deleted.

An understand job carries `extract_mode` = `full` | `lite` | `off`. User uploads default to `full`; the automatic
ingest of workflow outputs defaults to `lite` (one window, today's cost) through
`UMRS_KNOWLEDGE_EXTRACT_AUTO_MODE`. `UMRS_KNOWLEDGE_EXTRACT_MODEL` may name an ADR-106 role alias (`svc-fast`).

### D-3 Optional GLiNER pre-tagging
When `UMRS_KNOWLEDGE_GLINER_MODEL` names a staged directory, each window is tagged first with labels taken from the
graph ontology's class names (non-abstract classes, the first 25 in sorted order; aliases are not used as labels),
and the candidates go into the prompt as hints. Tags are never written as nodes
on their own. Only Apache-2.0 GLiNER v2.1 weights are staged (default `urchade/gliner_medium-v2.1`); GLiREL and
SciPhi Triplex are not used (non-commercial licences). Loading is `local_files_only` under offline env; it runs in the
same sandbox or thread as D-1. Unset or unavailable means no pre-tagging, named in the notes.
*(Amended 2026-11-13: GLiNER is now on by default from the bundled directory, run first by the `umrs-docmodels`
service; `UMRS_KNOWLEDGE_GLINER_MODEL=off` turns it off. See "Amendment 2026-11-13" below.)*

### D-4 Ontologies authored in LinkML
The registry accepts a LinkML YAML subset: schema `name`/`id`/`version`/`description`/`prefixes`, `classes` (with
`is_a`, `aliases`, `description`, `abstract`, `annotations.r4_bucket`), `slots` / class `attributes` whose `range` is
a class (relation types with domain and range, `transitive`, `aliases`, `description`). Remote `imports` are refused
(only `linkml:types`). The server parses it with PyYAML into the existing `Ontology` (extended with optional fields);
the LinkML runtime is not a server dependency. Migration 0139 stores the full spec, the source text and its sha256,
which also stops `register()` dropping `subtype_of` / `extraction_fields`. A built-in `r4-science@1` LinkML schema
ships with umrs; graphs without an ontology use its vocabulary in the extraction prompt (it replaces the hard-coded
type list, a superset of it).

### D-5 Registration surface (subset of ADR-081 D-9)
`POST /umrs/knowledge/ontologies` (LinkML YAML or the plain ontology JSON), `GET /umrs/knowledge/ontologies`,
`GET /umrs/knowledge/ontologies/{id}/{version}`, `POST /umrs/knowledge/ontologies/{id}/{version}/retire`. Humans only:
a caller whose `x-r4-actor` is anything but `user` gets 403, as ADR-084 D-3 does for decisions. A project-scoped
ontology needs write access to the project; a global one needs an admin. Retiring needs the registering user or an
admin; the built-in ids cannot be retired or re-registered. Errors carry `x-platform-error-code`:
`ontology.invalid` 400, `ontology.forbidden` 403, `ontology.not_found` 404, `ontology.conflict` 409. Creating or
patching a graph validates its ontology coordinate; an id without a version pins the latest live version at bind
time. Each scope (a project, or global) has its own `id@version` namespace: migration 0139 replaces the 0091 live
index with one on `(ontology_id, version, coalesce(project_id, ''))`, and registering a coordinate already live in
the same scope gets 409 `ontology.conflict`. Show, bind and extraction take the project's record before the global
one, and a graph never resolves another project's record. Retiring a version blocks new bindings only: graphs
already bound to it keep resolving the retired record. Both registration formats meet the
same limits (256 KiB of source, 2000 terms, 50 aliases per term); YAML aliases are refused, and the vocabulary
block in the prompt is capped (a `vocabulary_truncated` note says so). The r4 CLI gains
`r4 knowledge ontology register|list|show|retire` and `--ontology ID[@VERSION]` on `graph-create`, plus
`r4 knowledge graph-bind GRAPH ID[@VERSION]`.

### D-6 Ontology-guided extraction in warn mode
The extraction prompt lists the graph ontology's classes (with descriptions) and relations (with domain → range).
Replies map to class names through name and alias matching. An unmatched type is stored as
`semantic_type = "unclassified"` with `content.type_raw`; an unmatched relation as `related_to` with
`content.rel_raw`. A document is never rejected for vocabulary (ADR-081 D-5 warn). The 7-value `node_type` bucket
comes from the class's `r4_bucket` annotation (default `entity`). Ontology projection in the canonical view also
matches `content.semantic_type` (KG-02).

### D-7 Entity-resolution proposals, never in-place merges
At the end of an understand job, each semantic node it wrote is compared with the `node-sem-*` nodes of the same
project and graph: equal normalized label or alias (`method: label`; keys shorter than 2 characters, such as `R` or
`ψ`, never match), or, only when `UMRS_KNOWLEDGE_ER_EMBEDDING=1`, label-embedding cosine at or above
`UMRS_KNOWLEDGE_ER_THRESHOLD` (default 0.97) with compatible types (`method: embedding`). *(Amended 2026-11-12 from
the wave's measurement, D-10: on 10 papers the label method's pairs named one entity about 94% of the time, the
embedding method's at cosine >= 0.95 about 17% (GPT-3 vs GPT-3.5, Wikidata vs Wikipedia), so the embedding method is
opt-in and stricter; it was on at 0.92.)* Label vectors are stored
under model name `<embedder>#label`, which the dense retrieval lane never reads. A match becomes a **proposed**
`same_as` edge `edge-sameas-{scope}{a}-{b}` (ids sorted) with `{score, method, job_id, origin_actor: "extracted"}`,
at most `UMRS_KNOWLEDGE_ER_MAX_PER_JOB` (default 200) per job; `UMRS_KNOWLEDGE_ENTITY_RESOLUTION=0` turns it off.
Nodes are never merged or rewritten. Establishing a `same_as` edge (a human, through the existing establish route)
copies each partner's label into the other's `content.aliases_reviewed`, which the graph lane matches together with
`content.aliases`; both nodes must be in the edge's project. The neighbour search leaves out the node itself and its
own artifact's nodes before taking the 10 nearest, runs 100 nodes per statement, and covers at most
`UMRS_KNOWLEDGE_ER_EMBED_MAX` (default 500) new nodes per job (a note names the cap; the label method covers every
node). Label vectors never become a bundle's embedding pin.

### D-8 Store fixes D-7 needs
`upsert_node` keeps the keys `review` and `aliases_reviewed` of an existing row unless the new content sets them
(the part of KG-06 that would undo a review). `establish_edge` stops writing `updated_at`, a column
`knowledge_edges` does not have.

### D-9 Dependencies and models
A new umrs extra `kg-ingest` (Docling, GLiNER) is folded into the `server` extra so the image carries the
libraries, entered through `uv lock` and `scripts/deps/lockctl.py` (ADR-100), only if it resolves without lowering
the transformers / sentence-transformers security floors. As built: `kg-ingest = ["docling>=2.150", "gliner>=0.2.30",
"protobuf>=4.25"]`, locked at docling 2.150.0, gliner 0.2.30, protobuf 7.36.1; no existing pin moved (transformers
5.17.0, sentence-transformers 6.0.1, torch 2.14.0). `protobuf` is there because GLiNER's DeBERTa-v3 backbone ships
only `spm.model`, and transformers converts it through SentencePieceExtractor, which needs protobuf (without it
staging fails with "Error parsing line" in `spm.model`); the server-image smoke imports `google.protobuf`, `docling`,
`docling.document_converter`, `docling.datamodel.pipeline_options`, `gliner` and `umrs.knowledge.ingest_models`.
40 packages are new to `uv.lock` and enter the image locks with five already locked ones (protobuf among them),
all MIT / Apache-2.0 / BSD; the image grows by about 131 MB (largest: docling-parse 34 MB,
rapidocr 32 MB, faker 11 MB, pypdfium2 9 MB). Two caps bind future refreshes: gliner 0.2.30 requires
`transformers<5.23`, and docling-ibm-models requires `huggingface_hub<2`, so a transformers bump to 5.23 or later
does not resolve until gliner relaxes its cap. Imports stay lazy and every stage degrades by name when a library or
model is missing. Weights are never bundled into the image and never downloaded at runtime: the maintainer stages
them with `python -m umrs.knowledge.ingest_models stage docling --dest DIR` (layout and TableFormer weights only,
about 670 MB) and `python -m umrs.knowledge.ingest_models stage gliner --dest DIR [--repo R] [--revision REV]
[--mount-path P] [--allow-licence]` (default `urchade/gliner_medium-v2.1`, about 755 MB; a repo or backbone whose
model card licence is not apache-2.0 or mit is refused unless `--allow-licence`; the backbone's config and tokenizer
are staged beside it, so the directory loads offline and names no hub repo) into the front's `/models`;
`python -m umrs.knowledge.ingest_models show` prints what the server would use. A staging error exits 2 with its
reason. Stand-alone: `./standalone.sh docling pull [dir]` and `./standalone.sh gliner pull [repo] [dir]` run the same
module. LightRAG is not added to the lock; it is used only in a separate bench venv.
*(Amended 2026-11-13: "Weights are never bundled into the image" no longer holds. The server image now carries
Docling's weights and a pinned GLiNER revision, the way it carries e5-base-v2; the no-download-at-runtime rule
stands. The `stage` commands remain for a custom directory. See "Amendment 2026-11-13" below.)*

### Environment reference (as built)
*(2026-11-13: the defaults of the first three rows changed; the amendment below has the new values and the new
variables.)*

Server process (all default to today's behaviour; the umrs-front compose maps the first five from
`UMRS_<FRONT>_KNOWLEDGE_<NAME>` in `.env`, which the converge writes from `umrs_front_knowledge_ingest_env`; the
stand-alone compose maps them from `env/local.env`):

| Variable | Default | Meaning |
|---|---|---|
| `UMRS_KNOWLEDGE_DOC_PARSER` | `pypdf` | `docling` turns on D-1; any other value reads as `pypdf` |
| `UMRS_KNOWLEDGE_DOCLING_ARTIFACTS` | `/models/docling` | staged Docling directory (server path) |
| `UMRS_KNOWLEDGE_GLINER_MODEL` | empty (off) | staged GLiNER directory (server path), D-3 |
| `UMRS_KNOWLEDGE_EXTRACT_MODEL` | empty (registry choice) | extraction model or ADR-106 alias |
| `UMRS_KNOWLEDGE_EXTRACT_AUTO_MODE` | `lite` | `extract_mode` of automatic workflow-output ingest |
| `UMRS_KNOWLEDGE_MODELS_HOST_DIR` | unset | host directory behind the server's `/models` (the converge writes `<repo>/umrs/deploy/umrs-front/models`) |
| `UMRS_KNOWLEDGE_DOCLING_ARTIFACTS_HOST_DIR` | unset | explicit host path of the Docling directory for the sandbox mount |
| `UMRS_KNOWLEDGE_GLINER_MODEL_HOST_DIR` | unset | explicit host path of the GLiNER directory for the sandbox mount |
| `UMRS_KNOWLEDGE_EXTRACT_WINDOW_CHARS` | 8000 | window size (500..200000) |
| `UMRS_KNOWLEDGE_EXTRACT_WINDOWS_MAX` | 24 | windows per document in `full` mode |
| `UMRS_KNOWLEDGE_EXTRACT_CONCURRENCY` | 4 | parallel windows (never above 10) |
| `UMRS_KNOWLEDGE_EXTRACT_JOB_BUDGET_S` | 300 | wall-clock budget per understand job (all documents and parses) |
| `UMRS_KNOWLEDGE_EXTRACT_BUDGET_S` | 300 | wall-clock window budget per document, cut to what is left of the job's |
| `UMRS_KNOWLEDGE_EXTRACT_MAX_TOKENS` | 2500 | reply token cap per window |
| `UMRS_KNOWLEDGE_EXTRACT_TIMEOUT` | derived: max(60, max_tokens / 25) s | per-call timeout override (at least 5 s) |
| `UMRS_KNOWLEDGE_LLM_EXTRACT` | on | existing switch; `0`/`false` turns semantic extraction off |
| `UMRS_KNOWLEDGE_ENTITY_RESOLUTION` | on | `0`/`false` turns D-7 off |
| `UMRS_KNOWLEDGE_ER_EMBEDDING` | off | `1`/`true` turns on the embedding method of D-7 |
| `UMRS_KNOWLEDGE_ER_THRESHOLD` | 0.97 | label-embedding cosine for `method: embedding` |
| `UMRS_KNOWLEDGE_ER_MAX_PER_JOB` | 200 | proposed `same_as` edges per job |
| `UMRS_KNOWLEDGE_ER_EMBED_MAX` | 500 | new nodes per job compared by embedding |

The ingest sandbox reuses the embedder's `UMRS_EMBEDDING_SANDBOX` (=`podman`), `_IMAGE`, `_TIMEOUT`, `_MEMORY` and
`_CPUS`, with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_HOME=/tmp/hf`; it mounts Docling at
`/models-ingest/docling` and GLiNER at `/models-ingest/gliner`, read-only.

### D-10 Measured, not assumed
The wave records a comparison of the old extractor, the new one and a LightRAG baseline on the same documents with
the same self-hosted model (entities, cross-document duplicates, share of the document read, ontology conformance,
LLM calls and time). The private benchmark corpus is never committed; only aggregate numbers are recorded.

### Why
- Reading only the first 9 000 characters is the largest single quality loss and costs nothing to fix except LLM
  calls, which D-2 bounds.
- Proposals instead of merges keep the ADR-084 rule that judgements are reviewed, and keep every per-document row
  and its provenance.
- Adopting libraries as components keeps one knowledge store and one ownership line (CONSTITUTION ownership table),
  which replacing the store with LightRAG or a graph database would break.

## Consequences

- Positive: whole papers are read; extracted entities carry evidence quotes and aliases; projects can register their
  own vocabulary in a standard format; duplicates across documents surface for review; better PDF structure when
  Docling is enabled.
- Cost: more LLM calls per document (bounded by D-2); a larger server image (D-9); more proposed rows in the review
  inbox (bounded by D-7's cap); two maintainer legs before Docling and GLiNER are live (stage models, set env).
  *(2026-11-13: since the amendment below, both are live once the rebuilt image is deployed; no staging leg.)*
- NOT in scope: the typed-spine columns and `knowledge_terms` (ADR-081 D-1/D-2), OWL/OBO import (D-8 there), OCR in
  the understand stage, image/figure anchors (ADR-082), routing `api/rag.py` uploads through understand, a reindex
  driver that re-extracts existing corpora, a proposal executor, agent tools for ontologies (read-only tools are a
  follow-up; registration stays human).

## Alternatives Considered

- **Replace the store with LightRAG** (on Postgres, no extension). Rejected: a second knowledge store with its own
  schema, ingest and access model; kept as a baseline instead.
- **Neo4j-backed libraries** (neo4j-graphrag, Graphiti, LLM Graph Builder). Rejected: a second database.
- **NVIDIA auto-ontology.** Rejected: it models SQL warehouses, not documents.
- **OntoGPT as a dependency.** Rejected for the server: a heavy dependency tree (OAK, LinkML generators); the SPIRES
  idea (schema-driven prompt, grounding by name and alias) is implemented directly.
- **Merging duplicates in place.** Rejected: destroys per-document provenance and bypasses review (ADR-084 D-2).
- **Docling text for chunks too.** Rejected for now: it would invalidate every stored chunk offset and md5.

## Rollout-status (2026-11-12)

Wave `wave/kg-ingest-v2` (ledger 280), umrs `47301435` + `bb4c2d6e` (D-7 defaults from the measurement below).

### Measured 2026-11-12 (D-10)

Bench: 10 public arXiv papers on LLM knowledge-graph construction (2304.02711, 2308.02357, 2311.08526, 2404.16130,
2408.09869, 2409.03284, 2410.05779, 2502.09956, 2505.23628, 2510.20345; 177 pages, about 65 000 characters each).
One self-hosted model for every arm: qwen2.5:14b behind an OpenAI-compatible server on one RTX 4000 Ada. OLD is umrs
`cdcacff5` on pypdf text; NEW is this wave on Docling text (staged artifacts, offline); NEW+GLiNER adds
`gliner_medium-v2.1` hints through the real `ingest_worker` tag op. Scripts and results stay outside the repository.

| Arm | Papers with entities | Entities | Relations | LLM calls | Share of text read | Types in vocabulary | Located entities first seen after char 9 000 |
|---|---|---|---|---|---|---|---|
| OLD | 4 / 10 | 144 | 135 | 10 | 14.5% | 100% (fixed list) | n/a (no evidence) |
| NEW | 10 / 10 | 1012 | 1008 | 97 | 100% | 98.2% | 82% (of 868) |
| NEW + GLiNER | 10 / 10 | 1271 | 973 | 97 (+10 tag) | 100% | 99.8% | 82% (of 944) |

- OLD failed on 6 papers with "replied with invalid JSON": the reply hit the 1 800-token cap and the greedy regex
  found no object. NEW names such windows (`extract_window_unparsed`): 14 of 97 windows, never a whole paper.
- GLiNER hints raised entities by 26% at no extra LLM calls, but `related_to` relations rose from 18% to 36%.
- Entity resolution on NEW's output: the label method gave 64 cross-paper pairs, about 94% naming one entity (misses:
  `R`, `ψ`, `ϕ`, now excluded by the 2-character rule). The embedding method at the old 0.92 default gave 228 more
  pairs; about 17% of those at cosine >= 0.95 named one entity and almost none below, hence the D-7 amendment.
- Docling: 0.5–5 s per paper on the GPU, fully offline, 50 tables and 306 headings recovered. On 2408.09869 it kept
  4 192 of pypdf's 7 322 words (text inside figures and page furniture dropped; references and appendix kept).
- Wall time is not compared: part of the NEW runs shared the GPU with the LightRAG arm.
- LightRAG baseline (added 2026-11-13; stopped, a cost result only). LightRAG 1.5.7 in a bench venv indexed the same
  Docling text with the same model and e5-base-v2 (`chunk_token_size=2000`, default gleaning, 4 concurrent calls).
  e5's 512-token limit split the papers into about 450 extraction chunks. In about 4.5 h it extracted 85 chunks with
  178 LLM calls (about 1.8 min per chunk); a full run projects to about 900 calls and 13 h, against NEW's 97 calls and
  about 70 min for all 10 papers. Two of the three papers in flight were marked failed after one chunk's client
  timeout (LightRAG drops the whole document then); part of the run shared the GPU with other work. No entity-quality
  comparison is claimed: too few papers finished.

Maintainer legs before the new stages are live:

1. Stage the models into the front's `models/` directory
   (`python -m umrs.knowledge.ingest_models stage docling|gliner --dest DIR`) and set
   `umrs_front_knowledge_ingest_env`; the converge writes `UMRS_KNOWLEDGE_MODELS_HOST_DIR`.
2. Stand-alone: dispatch the `standalone-image` workflow, then bump `UMRS_IMAGE_DEFAULT` in
   `deploy/standalone/standalone.sh` and the digest in `deploy/standalone/compose.yaml`. Until then the pinned image
   has no `umrs.knowledge.ingest_models`, and `./standalone.sh docling|gliner pull` stops with "predates ADR-116"
   (or use `STANDALONE_BUILD=1`).

## Amendment 2026-11-13: Docling and GLiNER on by default as the umrs-docmodels service

**Maintainer request (2026-11-13):** "add the necessary models for Docling and GLiNER as service models of r4/umrs
and make them on as default". The shape below was decided in the session under the 2026-09-24 delegation. It amends
D-1, D-3 and D-9 and adds rows to the environment reference.

### What changes

1. **Weights baked into the image (amends D-9).** The umrs server image stages both models at build time, the way it
   already stages e5-base-v2. The build argument `UMRS_BUNDLE_INGEST_MODELS=1` (default) writes Docling's layout and
   TableFormer weights to `/opt/umrs/models/docling` and GLiNER to `/opt/umrs/models/gliner_medium-v2.1`. Every
   repository is pinned to a commit in `knowledge/ingest_models_stage.py`: `GLINER_REVISION` and
   `GLINER_BACKBONE_REVISION` for GLiNER and its DeBERTa-v3 backbone, `DOCLING_REVISIONS` for heron, heron-onnx and
   TableFormer (docling's own downloader would fetch heron at `main`), and the build's `verify` step fails when a
   staged marker records anything else; `stage gliner` uses the pins unless `--revision` names another. The model
   stage copies only that module (standard library only, no runtime setting), so editing a runtime default in
   `ingest_models.py` never re-downloads the weights. The image grows by about 1.4 GB (about 670 MB + 755 MB). The CI
   smoke image builds with `UMRS_BUNDLE_INGEST_MODELS=0`. Nothing is downloaded at runtime: the offline rule stands.
2. **Defaults on (amends D-1 and D-3).** `UMRS_KNOWLEDGE_DOC_PARSER` defaults to `docling`.
   `UMRS_KNOWLEDGE_DOCLING_ARTIFACTS` and `UMRS_KNOWLEDGE_GLINER_MODEL` default to empty, which means the bundled
   directory. Explicit values turn them off: `UMRS_KNOWLEDGE_DOC_PARSER=pypdf` (also `off`, `none`, `false`, `0`;
   any other value reads as `docling`) and `UMRS_KNOWLEDGE_GLINER_MODEL=off` (also `none`, `false`, `0`). Every place that decides whether a model can run
   uses `is_docling_staged` / `is_gliner_staged`, not a bare directory check, so a missing or partly copied
   directory (or an image built with `=0`) falls back instead of failing the parse.
3. **A warm local service, `umrs-docmodels`.** The same image runs `python -m umrs.knowledge.docmodels_server`, one
   container per host, shared by every UMRS server in that compose file. It loads both models once and keeps them
   loaded. Its only network is internal (no egress). In the stand-alone stack it publishes no port and shares that
   network with the umrs container alone (`http://umrs-docmodels:8091`). On umrs-front the four servers run with
   `network_mode: host` and cannot join a compose network, so the service publishes one port on loopback only,
   `127.0.0.1:8091`, which is the servers' default URL there. It runs with a read-only root filesystem and a tmpfs,
   `cap_drop: ALL`, `no-new-privileges`, as uid 65534 (not root), no sockets, and memory and CPU limits (on
   umrs-front only as far as the rootless podman has the cgroup controllers). Its healthcheck is `GET /livez`
   (liveness), so a service whose models never load blocks neither a converge nor the stand-alone `up --wait`. No
   server depends on it being healthy. Endpoints: `GET /livez`; `GET /readyz` (200 once the loading has finished
   and at least one model loaded; 503 `loading` before that, `no_models` when neither loaded, `stuck` see below);
   `GET /proof`; `GET /info` (versions, revisions, staged and loaded flags, limits); `POST /parse` (a document in,
   text out) and `POST /tag` (texts and labels in, candidates out, the same shape as the `ingest_worker` tag op).
   It refuses a document over `UMRS_KNOWLEDGE_DOCLING_MAX_BYTES` (64 MiB, the body cap is that after base64) with
   413 `too_large` and a PDF over `UMRS_KNOWLEDGE_DOCLING_MAX_PAGES` (80 pages) with 422 `too_many_pages`; both are
   the same settings the servers read, fed from one `.env` value. A caller set to another model directory gets 409
   `model_mismatch`; a model the service does not hold gets 503 `docling_unavailable` / `gliner_unavailable`.
   Parse and tag have their own slots (`UMRS_DOCMODELS_CONCURRENCY`, 1 each). A request waits for a slot at most
   its budget (sent as `X-Docmodels-Budget`), capped at `UMRS_DOCMODELS_QUEUE_WAIT_S` (30 s), and runs at most
   that budget (capped at `UMRS_DOCMODELS_MAX_RUN_S`, 300 s); past `UMRS_DOCMODELS_MAX_REQUESTS` (4 per operation)
   in flight it is `busy` at once. Body decoding and the page count run in a worker thread inside the slot, not
   on the event loop. A thread cannot be stopped, so a watchdog that finds a request 5 s past its deadline frees
   its slot, turns `/livez` and `/readyz` to 503 `stuck` and ends the process (exit 75): the container's `restart`
   brings it back with the models loaded again, instead of one document holding the only slot while the
   healthcheck stays green. On umrs-front `/info`, `/parse` and `/tag` need a shared token (`UMRS_DOCMODELS_TOKEN`,
   generated once into `.env` by `start.sh`), and the client first asks `GET /proof` with a fresh nonce and checks
   the HMAC, so a process that takes the loopback port while the service is down or being recreated receives
   neither the token nor a document (`docmodels_unavailable:untrusted`). The stand-alone stack, where only umrs can
   reach the service, runs without a token.
4. **Order in the understand job.** The service first (`UMRS_KNOWLEDGE_DOCMODELS_URL`); else the D-1 local path
   (the ingest sandbox or the server process, where a bundled directory is already inside the image and is not
   mounted); else pypdf text and no hints. Each fallback is named in the job notes (`doc_parser_fallback:<reason>`,
   `pretag_unavailable:<reason>`, and `docmodels_unavailable:<code>` for a service that could not answer). A
   fallback never fails the job. Two limits on the local step when a service URL is set (review, 2026-11-13):
   - A refusal about the document itself (`too_large`, `too_many_pages`, `parse_failed`, `tag_failed`,
     `bad_request`, and `transport_error` / `timeout`, where the service may have died on it) is final: the same
     bytes are never parsed or tagged again in the server, which has host networking, credentials and no memory
     limit. The note is `doc_parser_fallback:<code>` or `pretag_unavailable:<code>`.
   - For any other failure (`busy`, `loading`, `connect_failed`, `untrusted`, `model_mismatch`, ...),
     `UMRS_KNOWLEDGE_DOCMODELS_LOCAL_FALLBACK` decides: `sandbox` (the default) runs only the one-shot ingest
     sandbox, which frees its memory; `inprocess` also the server process, where the models then stay loaded;
     `off` nothing. Refused, the note is `doc_parser_fallback:no_local_fallback` /
     `pretag_unavailable:no_local_fallback`. Without a service URL the local path always runs, as before.

   This narrows the session decision's "else the existing local path" where a service exists: routine `busy` and
   `loading` answers would otherwise put a 3 GB copy of the models into every front server on data0 (15 GB), and on
   stand-alone into the 6g umrs container next to e5.
5. **Own time budget.** `UMRS_KNOWLEDGE_DOCMODELS_TIMEOUT_S` (T, default 120 s) bounds parsing, and separately
   tagging, of one document; the service and the local attempt share that one budget. It sits outside the LLM
   extraction budget of D-2: the per-document window clock (`budget_s`) starts after parsing and tagging, so they
   no longer eat that clock. With a job deadline each call gets `min(T, deadline + T - now)` and is skipped at zero
   (`budget_exhausted`), so the two models can carry a job past its deadline by at most T in total. The job
   deadline itself still applies to the windows: when tagging ends past it (or within `LITE_FLOOR_S` of it), the
   document takes the lite rescue (its first window, outside the deadline, note `extract_job_budget_exhausted`)
   instead of a negative window budget and no graph (review fix, 2026-11-13). Before this amendment tagging had no
   deadline: in the sandbox it could run for the sandbox's full 600 s past the job budget.
6. **Visible.** connect-info carries a `knowledge_models` block, and `r4 doctor` / `r4 health full` print it as one
   line. connect-info is readable without signing in (it is the discovery call), so the block names no address and
   no path: it says whether a service URL is set, not the URL, and whether a directory is the bundled one, not where
   it is. The service's `/info` is asked at most once a minute, with a 2 s limit on each connect and each read (the
   token proof is one more read), and concurrent requests wait for one refill.

   ```json
   "knowledge_models": {
     "doc_parser": "docling",
     "docling": {"enabled": true, "source": "bundled", "staged": true, "version": "2.150.0"},
     "gliner": {"enabled": true, "source": "bundled", "staged": true,
                "repo": "urchade/gliner_medium-v2.1", "revision": "<pinned sha>"},
     "service": {"url_set": true, "ready": true, "local_fallback": "sandbox"}
   }
   ```

   `source` is `bundled`, `custom` or `off`. `service.ready` is `null` when no service URL is set, `false` when the
   service does not answer, is still loading or stuck, or has not loaded (staged is not enough) a model this front
   has switched on from this front's directory. `service.local_fallback` is the fallback in effect (`off` when it
   is `sandbox` and the server has no ingest sandbox lane).

### Why a local service and not ADR-106 `svc-*` roles

- Only rccs has a service tier. bdrt has two role aliases and no service blocks; ai4s, octopus and stand-alone have
  none. "On by default" through a role would be true on one front only.
- Docling and GLiNER cannot be served by vLLM. The one non-vLLM serving lane (the BentoML co-serve sidecar) has never
  served live: `svc-vision2` is still commented out because no aarch64 image is staged.
- ADR-106 D-8 requires every role adapter to answer `/v1/models` and sit behind nginx + LiteLLM with an API key. A
  parse or tag server does not fit that without an ADR-106 amendment first (AGENTS rule 10).
- The models are small and run on a CPU. A GPU `svc-docparse` endpoint stays a later option, as an accelerator in
  front of this default, once a non-vLLM role has served live.

### Cost on data0

The umrs-front host (data0) has no GPU: 8 cores and 15 GB of memory, shared by four UMRS servers, Postgres,
SeaweedFS and the CI runner. Nothing here has been measured on data0 yet. The Docling technical report
(arXiv 2408.09869, Table 1) gives 0.6–0.9 pages/s on 4 CPU threads and 0.9–1.6 pages/s on 16, with a peak of
2.4–6.2 GB depending on the PDF backend. An 18-page paper is therefore about 20–30 s once the models are loaded (the
service removes the 10–20 s load per document that the one-shot sandbox paid). The 120 s per-document cap and the
80-page cap keep long PDFs on the pypdf path. One service per host keeps the memory to one copy of each model
instead of one per front, as long as the servers do not load their own: with a service URL set they do so only
through the ingest sandbox (`LOCAL_FALLBACK=sandbox`), never in their own process unless set to `inprocess`.

Integration smoke (2026-11-13, a 32-core workstation in a venv whose torch has CUDA, so Docling most likely ran on the
workstation GPU; not a data0 figure): the service was
ready 4.8 s after start with both models loaded and held 3.4 GB resident; a 14-page arXiv PDF parsed in 3.5 s on
the first request and 0.9 s on a repeat, and tagging two sentences took 0.1 s. A service with no staged model
stayed at 503 `no_models`, and the client then read the PDF with pypdf and gave no hints, with notes
`docmodels_unavailable:docling_unavailable`, `doc_parser_fallback:artifacts_missing`,
`docmodels_unavailable:gliner_unavailable` and `pretag_unavailable:model_dir_missing`. (That was the client before
the review fixes, with a URL set it would now note `no_local_fallback` instead of trying the local path.)

Review-fix smoke (2026-11-13, same workstation, real models, service with a token): the PDF pipeline loaded during
the warm load, before `/readyz` turned 200, so the first parse of the same 14-page PDF took 0.75 s; `/parse` and
`/info` without the token answered 401; the client parsed and tagged through the token proof; a client set to
another GLiNER directory got `model_mismatch` and no hints; a client with the wrong token sent no document
(`docmodels_unavailable:untrusted`) and read the PDF with pypdf.

Built-image smoke (2026-11-13, the CPU figure): the umrs server image built from this change with
`UMRS_BUNDLE_INGEST_MODELS=1` (`podman build`, 9.4 GB with CUDA torch, of which the two models are about 1.5 GB;
the staging step and its `verify` passed), run the way the umrs-front compose runs `umrs-docmodels` (uid 65534,
read-only root, `cap_drop: ALL`, `no-new-privileges`, `--memory 6g --cpus 4`, offline, no GPU). Ready 9 s after
start at 1.8 GB; the compose healthcheck command answered inside the container. Through the umrs client with the
token: 14 pages in 7.4 s, 21 pages in 24 s, 26 pages (143 000 characters of Docling text) in 62 s, all inside the
120 s cap; tagging two sentences 0.16 s; peak 3.1 GB of the 6 GB limit. At about 0.4–2 pages/s, a PDF near the
80-page cap can exceed 120 s on data0 and is then read with pypdf (named in the notes).

### Quality, both ways (from the D-10 measurement)

- GLiNER hints raised entities by 26% at no extra LLM calls, but the share of `related_to` relations (the catch-all)
  rose from 18% to 36%.
- Docling recovered 50 tables and 306 headings on the 10 papers, but on 2408.09869 it kept 4 192 of pypdf's 7 322
  words: text inside figures and page furniture was dropped.

An operator whose users' documents keep their content in figures, or who prefers fewer generic relations, can turn
either model off with the two settings in item 2. The settings apply to a whole front, not to one project.

### Environment reference additions (2026-11-13)

| Variable | Default | Meaning |
|---|---|---|
| `UMRS_KNOWLEDGE_DOC_PARSER` | `docling` (was `pypdf`) | `pypdf`, `off`, `none`, `false` or `0` turn Docling off |
| `UMRS_KNOWLEDGE_DOCLING_ARTIFACTS` | empty: bundled `/opt/umrs/models/docling` (was `/models/docling`) | a custom staged Docling directory |
| `UMRS_KNOWLEDGE_GLINER_MODEL` | empty: bundled `/opt/umrs/models/gliner_medium-v2.1` (was off) | a custom staged directory; `off`, `none`, `false` or `0` turn GLiNER off |
| `UMRS_KNOWLEDGE_DOCMODELS_URL` | empty (umrs-front compose sets `http://127.0.0.1:8091`, stand-alone `http://umrs-docmodels:8091`) | the docmodels service; empty or `off` means run locally |
| `UMRS_KNOWLEDGE_DOCMODELS_TIMEOUT_S` | 120 | seconds per document for parsing, and for tagging, outside the extraction budget |
| `UMRS_KNOWLEDGE_DOCLING_MAX_PAGES` | 80 | longer PDFs are read with pypdf; the service reads the same setting |
| `UMRS_KNOWLEDGE_DOCLING_MAX_BYTES` | 64 MiB | larger documents are read with pypdf; the service reads the same setting |
| `UMRS_KNOWLEDGE_DOCMODELS_LOCAL_FALLBACK` | `sandbox` | with a service URL set, what runs locally when it cannot answer: `sandbox`, `inprocess` or `off` |
| `UMRS_KNOWLEDGE_DOCMODELS_TOKEN` | empty | client: the service's shared token (umrs-front maps `UMRS_DOCMODELS_TOKEN`) |
| `UMRS_DOCMODELS_HOST` | `0.0.0.0` | service only: listen address inside its container |
| `UMRS_DOCMODELS_PORT` | 8091 | service only: listen port |
| `UMRS_DOCMODELS_TOKEN` | empty | service only: shared token for `/info`, `/parse`, `/tag`; umrs-front `start.sh` generates it |
| `UMRS_DOCMODELS_CONCURRENCY` | 1 | service only: documents parsed, and texts tagged, at once |
| `UMRS_DOCMODELS_QUEUE_WAIT_S` | 30 | service only: the longest wait for a slot (never past the caller's budget) before 503 `busy` |
| `UMRS_DOCMODELS_MAX_RUN_S` | 300 | service only: the longest a request runs; past it plus 5 s the process exits and restarts |
| `UMRS_DOCMODELS_MAX_REQUESTS` | 4 | service only: requests per operation in flight before 503 `busy` at once |
| `UMRS_FRONT_ENABLE_DOCMODELS`, `UMRS_DOCMODELS_MEM_LIMIT`, `UMRS_DOCMODELS_CPUS` | `true`, `6g`, `4` | umrs-front `.env`, written by the converge (`0` = no limit when the cgroup controller is missing); with `false`, `start.sh` points every server without its own `DOCMODELS_URL` at `off` |
| `UMRS_DOCMODELS_DOC_PARSER`, `UMRS_DOCMODELS_DOCLING_ARTIFACTS`, `UMRS_DOCMODELS_GLINER_MODEL` | `docling`, empty, empty | umrs-front `.env`: the shared service's own models |
| `STANDALONE_DOCMODELS` | 1 | stand-alone: `0` leaves `umrs-docmodels` out and sets umrs's URL to `off` |
| `UMRS_BUNDLE_INGEST_MODELS` | 1 | image build argument; `0` builds without the weights |

### Owner legs

1. Rebuild the front images in the converge (about +1.4 GB each) and check the `umrs-docmodels` healthcheck.
2. Measure parse time and memory on data0 with a few real PDFs, and adjust the time and page caps if needed.
3. Stand-alone: dispatch the `standalone-image` workflow and bump the pinned image digest (`UMRS_IMAGE_DEFAULT` in
   `standalone.sh` and the `image:` lines in `deploy/standalone/compose.yaml`). Until then `./standalone.sh up`
   probes the pinned image for `umrs.knowledge.docmodels_server` and, finding none, starts the stack without
   `umrs-docmodels` (a warning names it; the URL is set to `off`, so umrs reads PDFs as before). *(2026-11-13: this
   probe replaced the earlier "bump in the same landing" requirement, so the merge does not break stand-alone.)*
   `STANDALONE_BUILD=1` gets the service from the checkout, and `STANDALONE_DOCMODELS=0` leaves it out. Since the healthcheck is liveness (`/livez`), an image without
   the weights (`UMRS_BUNDLE_INGEST_MODELS=0`) no longer blocks `up`.
4. Licences: GLiNER medium v2.1 is Apache-2.0, its DeBERTa-v3-base backbone MIT, Docling's layout weights
   (heron, heron-onnx) Apache-2.0, and TableFormer (`docling-project/docling-models` v2.3.0, the revision docling
   2.150.0 pins) CDLA-Permissive-2.0. `docker/server-image/NOTICE-ingest-models.txt` records each with its revision
   and carries the MIT text; the Apache-2.0 text ships in the image with the gliner dist-info, and CDLA-Permissive-2.0
   is given as a link, which that licence allows. Every repository is now staged at a pinned commit
   (`DOCLING_REVISIONS`), not at docling's `main`.
5. Stand-alone memory: `umrs-docmodels` adds about 3.4 GB resident (measured on a workstation) to the stack. The
   8 GB podman-machine floor for a native model host (`STANDALONE_MIN_VM_GB_NATIVE`) was not raised without a
   measurement: measure the warm stack in an 8 GB machine, then raise the floor or size `STANDALONE_DOCMODELS_MEM`.
   Until then manual 29 tells small machines to set `STANDALONE_DOCMODELS=0` or switch the stages off.

### Deployed 2026-11-13

The staged converge of `c075ea3c` (#868, #869, #870; umrs `610c5c2f`), run by the r4-private-demo session on the
maintainer's "deploy": nine runs, all green (bdrt check, apply and a second apply; rccs, ai4s and octopus check and
apply); all four fronts answer 200. Owner leg 1 is done: the front image carries the bundle, and `umrs-docmodels` on
data0 reports `Up (healthy)`, 1.99 GB of its 6.44 GB limit and 0.26% CPU at idle, with about 4.3 GB of the host's
15.7 GB still available. ADR-116 as a whole (#864) had gone live on bdrt, rccs and ai4s with `26bb212f` the same day;
octopus has it from this converge. Owner legs 2 (measure parses on data0), 3 (stand-alone image and digest) and 5
(stand-alone memory floor) remain.

## Related

- [ADR-081](081-typed-knowledge-spine.md), [ADR-084](084-growth-governance.md), [ADR-093](093-retrieval-for-small-local-models.md), [ADR-100](100-dependency-locks-cooldown-refresh.md), [ADR-106](106-service-model-tier.md)
- [docs/user-manual/27-knowledge-management.md](../user-manual/27-knowledge-management.md)
