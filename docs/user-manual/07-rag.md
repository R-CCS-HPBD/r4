# 7. RAG (Retrieval-Augmented Generation)

R4's RAG system lets you upload documents, build embeddings, and query your data using semantic search with LLM-augmented answers.

## Quick Start

```bash
# Upload a document
r4 rag upload paper.pdf --project mylab

# Query with semantic search
r4 rag query "What are the key findings?" --project mylab

# List uploaded documents
r4 rag docs list --project mylab
```

## Document Upload

```bash
# Upload a single file (one file per call)
r4 rag upload paper.pdf --project mylab

# Upload every supported file in a directory
r4 rag bulk-upload ./research-papers/ --project mylab

# Corpus statistics
r4 rag stats --project mylab
```

Supported file types include PDF, TXT, Markdown, HTML, CSV, JSON, and other text formats.
The same corpus verbs are also available under `r4 knowledge upload|bulk-upload|docs|stats`

## Sharing a corpus (2026-10-03)

By default only your project reads its corpus (plus the platform `system`
corpus, readable by everyone). A project member can open read access to
named users, circles, projects or everyone — multiple entries of each kind;
every circle membership of a caller counts:

```bash
r4 rag corpus-access mylab --set circle:proj-a/lab --set user:bob   # share reads
r4 rag corpus-access mylab                                     # show the list
r4 rag corpus-access mylab --private                           # project only again
```

The list governs **reads** (`query`, `docs`, `stats`, search); ingest and
delete stay with the owning project. `system` entries are admin-granted
only. A colleague reads your shared corpus by passing `--project mylab`.
(see [27. Knowledge Management](27-knowledge-management.md)).

## Querying

```bash
# Basic query
r4 rag query "What causes MPI_Init failures?" --project mylab

# Control result count
r4 rag query "UCX shmget error" --project mylab --top-k 10

# Force the legacy absolute cut (omitted by default since 2026-10-10: the server's adaptive cut decides)
r4 rag query "quantum computing" --project mylab --threshold 0.7

# Query without source citations
r4 rag query "summarize findings" --project mylab --no-sources
```

## Document Management

```bash
# List all documents
r4 rag docs list --project mylab

# Delete a specific document
r4 rag docs delete <doc-id> --project mylab --yes
```

## Chunking and Embedding

Chunking and embedding are site settings, not per-project definitions: UMRS
chunks with `UMRS_RAG_CHUNK_MAX_TOKENS` and embeds with
`UMRS_RAG_EMBEDDING_MODEL`. Ask your site operator to change them. List the
components the site offers with:

```bash
r4 rag retriever components chunkers
r4 rag retriever components embedders
r4 rag retriever components rerankers
```

> **Retired 2026-10-18:** `r4 rag ingest list|show|create|update|delete`.
> Saved ingest pipelines lived in server memory, vanished at every restart,
> and no ingest or search ever read them. The command now prints this
> pointer and sends nothing.

### Chunker Options

| Chunker | Description |
|---------|-------------|
| `token` | Fixed token-count chunks |
| `sentence` | SpaCy sentence-boundary chunks |

### Embedding Models

| Model | Description |
|-------|-------------|
| `sentence-transformers/all-MiniLM-L6-v2` | Default, fast, general purpose |
| Custom model path | User-staged embedding model |

## Retriever Components

> *Updated 2026-10-18:* the retriever-profile CRUD is retired and its routes
> are gone. Profiles were kept in server memory, lost at every restart, and
> never read at ingest or search time (410 since 2026-08-22; deleted with
> `r4 rag retriever list|show|create|update|delete` in the hardening H-10.1
> rag-profiles wave). Retrieval tuning lives in site configuration
> (`UMRS_RAG_CHUNK_MAX_TOKENS`, `UMRS_RAG_EMBEDDING_MODEL`). The component
> catalogs stay:

```bash
# List available retriever components
r4 rag retriever components chunkers
r4 rag retriever components embedders
r4 rag retriever components rerankers
```

### Search Methods

| Method | Description |
|--------|-------------|
| `vector` | Cosine similarity via pgvector |
| `hybrid` | BM25 + vector with Reciprocal Rank Fusion (RRF) |

### Reranker Options

| Reranker | Description |
|----------|-------------|
| `none` | No reranking |
| `cross-encoder` | Cross-encoder/ms-marco-MiniLM-L-6-v2 |

## Python SDK

*Updated 2026-10-10:* `chat_with_context()` and `inject_context()`
were removed. `retrieve()` and `fetch_passage()` are new, and `query()` no
longer sends a similarity threshold unless you pass one.

```python
from r4 import Client

client = Client()

# Upload
client.rag.upload("paper.pdf", project_id="mylab")

# Passages, best first, with UMRS's sufficiency verdict (no model call)
result = client.rag.retrieve("What are the key findings?", project_id="mylab")
print(result.sufficiency.status)   # sufficient | partial | insufficient | unknown
for p in result.passages:
    print(f"[{p.rank}] {p.source_name} › {p.section}: {p.text[:100]}")

# More text around one passage (window = neighbouring chunks on each side, 0-2)
if result.passages:
    more = client.rag.fetch_passage(result.passages[0].source_id, project_id="mylab", window=1)
    print(more["text"])

# An answer composed on the platform, with its sources
response = client.rag.query("What are the key findings?", project_id="mylab", top_k=5)
print(response.answer)
for src in response.sources:
    print(f"{src.source_name}: {src.content[:100]}")

# List documents (dicts from the server)
for doc in client.rag.list_documents(project_id="mylab"):
    print(doc.get("document_id"), doc.get("filename"))

# Delete document: the knowledge artifact it names, with everything derived
# 404 when the id names no artifact of the project
result = client.rag.delete_document(doc_id, project_id="mylab")
print(result.counts)   # rows deleted per kind; result.objects: stored bytes
```

UMRS decides which passages are relevant and whether they answer the question
(`sufficient`, `partial` or `insufficient`; `unknown` from an offline bundle or
an older server). Do not cut the passages again by score. For the parameters,
and for answering with your own model, see
[Chapter 14](14-sdk-reference.md#rag).

In the console, the agent's `search_docs` tool uses the same verb. To have R4
search before the model's first round, and to check the citations in the
answer, see the `R4_RAG_*` variables in
[Appendix B](appendix-b-env-vars.md#r4-retrieval-adr-093).

## How RAG Works Internally

1. **Upload:** Files are uploaded to UMRS (S3/MinIO).
2. **Chunking:** Documents are split into chunks (token or sentence-based).
3. **Embedding:** Chunks are embedded using the configured embedding model.
4. **Storage:** Embeddings are stored in PostgreSQL with pgvector.
5. **Query:** User query is embedded and compared against stored chunks.
6. **Retrieval:** Top-k relevant chunks are retrieved (vector or hybrid search).
7. **Reranking:** Optional cross-encoder reranking for precision.
8. **Response:** Retrieved context is passed to the LLM for a contextualized answer.

> **Note:** RAG indexing and embedding run INSIDE mini-RiVault containers, never in the Gateway. *(Amended 2026-10-10: ranking for the retrieve verb runs in UMRS, as described below.)*

*Updated 2026-10-10:* the retrieve verb (`GET /gw/rag/retrieve`,
used by `client.rag.retrieve()` and the console's `search_docs`) is one scored
path in the UMRS retrieval core
(`umrs/src/umrs/knowledge/retrieval/pipeline.py`). It asks the RAG service to
embed the query. It then runs dense and lexical search, fuses the lanes,
optionally reranks, and applies an adaptive cut with a sufficiency verdict. No
chat model is called. The passage count and pool are sized for the model that
will read the passages. Passing `threshold` keeps the legacy path instead: the
top `k` dense hits above that absolute similarity.

## Next Steps

- [Chapter 8 — MCP Tools](08-mcp.md)
- [Chapter 9 — Workflows & Jobs](09-workflows-jobs.md)
