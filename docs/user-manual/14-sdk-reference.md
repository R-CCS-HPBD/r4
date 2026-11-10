# 14. SDK Reference

Complete Python API reference for the `r4` SDK.

## Installation

```bash
pip install r4
```

## Quick Start

```python
from r4 import Client

client = Client()
response = client.chat("Hello, world!")
print(response.content)
```

## Client

### Constructor

```python
from r4 import Client

client = Client(
    gateway_url="https://gateway.example.com",  # Or R4_GATEWAY_URL env
    api_key="sk-xxxx",                           # Or R4_API_KEY env
    project_id="mylab",                          # Or R4_PROJECT_ID env
)

Defaults (model via R4_MODEL, timeout via R4_TIMEOUT) come from the active
profile / environment; tune per-call params (model, temperature, max_tokens,
retries) on the individual methods.
```

### Sub-Clients

| Property | Type | Purpose |
|----------|------|---------|
| `client.rag` | `RAGClient` | RAG operations |
| `client.mcp` | `MCPClient` | MCP tool integration |
| `client.jobs` | `JobClient` | Durable job tracking |
| `client.data` | `DataClient` | File upload/download |
| `client.workflows` | `WorkflowClient` | Workflow management |
| `client.vision` | `VisionClient` | Vision model inference |
| `client.knowledge` | `KnowledgeClient` | Knowledge graph/query, ingest, readiness, research loops, `.kb` export/restore |
| `client.artifacts` | `ArtifactsClient` | A2L artifact + execution lineage (ch. 24) |
| `client.dashboard` | `DashboardClient` | Ops board data (jobs, models, health, connectors) |
| `client.data_catalog` | `DataCatalogClient` | Catalog objects and intake |

> No `client.models` sub-client — use `client.list_models()` directly.

## Chat

### `client.chat()`

Simple chat interface.

```python
def chat(
    message: str,
    *,
    model: str = None,
    system: str = None,
    stream: bool = False,
    temperature: float = 0.7,
    max_tokens: int = None,
    task: str = None,        # X-R4-Task header for routing
    agent: str = None,       # X-R4-Agent header for routing
) -> Union[ChatResponse, Iterator[str]]
```

**Examples:**

```python
# Simple chat
response = client.chat("What is Python?")
print(response.content)

# With options
response = client.chat(
    "Write a haiku",
    model="rivault-gpt-oss-static",
    temperature=0.9,
    max_tokens=100,
)

# Streaming
for chunk in client.chat("Tell a story", stream=True):
    print(chunk, end="", flush=True)
```

### `client.chat_completion()`

Full OpenAI-compatible chat completion with message history.

```python
def chat_completion(
    messages: List[dict],
    *,
    model: str = None,
    stream: bool = False,
    temperature: float = 0.7,
    max_tokens: int = None,
    tools: List[dict] = None,
    tool_choice: Union[str, dict] = None,
    task: str = None,          # X-R4-Task routing header
    agent: str = None,         # X-R4-Agent routing header
) -> ChatCompletionResponse
```

**Example:**

```python
response = client.chat_completion([
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is quantum computing?"},
    {"role": "assistant", "content": "Quantum computing uses qubits..."},
    {"role": "user", "content": "How does it differ from classical?"},
])
print(response.choices[0].message.content)
```

## RAG

*Updated 2026-10-10:* `client.rag.chat_with_context()` and
`client.rag.inject_context()` were removed. `retrieve()` and `fetch_passage()`
are new. `query()` no longer sends a similarity threshold unless you pass one.

### `client.rag.upload()`

```python
client.rag.upload("paper.pdf", project_id="mylab")
# Directory upload lives on the data client (also usable for RAG corpora):
client.data.upload_directory("./papers/", project_id="mylab")
```

### `client.rag.retrieve()`

Ranked passages and UMRS's verdict on them, over `GET /gw/rag/retrieve`. The
call makes no chat-model call.

```python
result = client.rag.retrieve(
    "What are the key findings?",      # or a list of 1-3 variants, fused server-side
    project_id="mylab",                # default: the client's project
    k_max=6,                           # upper bound, 1-12; omit for the server's default
)
print(result.sufficiency.status)       # sufficient | partial | insufficient | unknown
for p in result.passages:              # r4_contracts.retrieval.RetrievedPassage, best first
    print(p.rank, p.source_name, p.section, p.scores, p.lanes)
    print(p.text[:200])
```

The return type is `r4_contracts.retrieval.RetrieveResult`. Its fields are
`passages`, `sufficiency` (`status`, `basis`, `value`, `threshold`, `detail`),
`searched` (candidate rows in scope; `0` means nothing to search), `space`
(the embedding model), and `lanes` (per-stage status; `lanes["profile"]` names
the profile that sized the cut).

Optional keyword arguments; any left unset is not sent, so the server's
default applies:

| Argument | Meaning |
|----------|---------|
| `graphs`, `exclude_graphs` | Graph ids or names to search only, or to leave out |
| `k_max`, `pool`, `per_artifact` | Passage bound (1-12), candidate pool (10-200), passages per document (1-4) |
| `lexical`, `rerank`, `merge` | Stage switches: `auto`, `on` or `off` |
| `cutoff` | `adaptive` (default) or `fixed` |
| `threshold` | A float switches to the legacy absolute similarity cut |
| `profile` | `auto`, `small`, `medium` or `large`: sizes the server's defaults for the reader |
| `reader_model` | The model that will read the passages; the server picks the profile from it |
| `trace` | `True` asks for the stage trace |
| `timeout` | Seconds (default `R4_RAG_RETRIEVE_TIMEOUT`, 10; never above `R4_TIMEOUT`) |

UMRS decides relevance, the cut and sufficiency. Read
`result.sufficiency.status` and never re-cut `result.passages` by score.
`r4.rag.passage_score(p)` gives the one score to display. An older server
without the verb gets one fallback call to `/gw/rag/query`
(`lanes["fallback"]`, status `unknown`, or `insufficient` when nothing
passed). With a knowledge bundle loaded (`client.rag.load_bundle(path)`, or
`R4_KNOWLEDGE_BUNDLE=path`, which `r4 chat --knowledge` sets), the bundle is
searched offline with BM25: no cut, and status `unknown`.
`lanes["local_bundle"]["unapplied"]` lists the options the bundle search
could not apply.

### `client.rag.fetch_passage()`

One passage by its handle (`RetrievedPassage.source_id`), over
`GET /gw/rag/passages/{node_id}`, with the same access rule as `retrieve()`.

```python
if result.passages:
    passage = client.rag.fetch_passage(
        result.passages[0].source_id,
        project_id="mylab",            # the project the handle was retrieved in
        window=1,                      # neighbouring chunks on each side, 0-2
        max_chars=4000,
    )
    print(passage["source_name"], passage["span"], passage["text"])
```

Returns `{source_id, artifact_id, source_name, text, span, window,
text_source}`. A passage you cannot see raises `APIError` (HTTP 404).

### Answering with retrieved context (replaces `chat_with_context()`)

```python
from r4_contracts.context_pack import render_pack

question = "Explain the methodology"
result = client.rag.retrieve(question, project_id="mylab")
if result.passages and result.sufficiency.status != "insufficient":
    status = "sufficient" if result.sufficiency.status == "sufficient" else "partial"
    prompt, labels = render_pack(question, result.passages, status=status)
    print(client.chat(prompt).content)  # cites passages as [1], [2]; labels maps them to source_id
else:
    print("The project's documents do not answer this.")
```

`render_pack` is the same renderer the platform's answer path and the R4
console use. The module helper `r4.rag.inject_context_into_messages(messages,
sources)` remains; it adds `RAGSource` objects (for example
`client.rag.query(...).sources`) to a message list's system message.

### `client.rag.query()`

An answer composed on the platform, with its sources, over `POST /gw/rag/query`.

```python
response = client.rag.query(
    "What are the key findings?",
    project_id="mylab",
    top_k=5,
    # threshold=0.7,  # optional legacy absolute cut; default None = the server decides
)
print(response.answer)
for src in response.sources:            # response.documents is a read-only alias
    print(f"{src.source_name}: {src.score:.3f} — {src.content[:100]}")
```

`RAGSource` has `document_id`, `content`, `score` and `metadata`, the
properties `source_name` and `section`, and `to_dict()`. In
`RAGQuery.threshold`, `None` (the default) means the field is not sent.

### `client.rag.list_documents()`

```python
docs = client.rag.list_documents(project_id="mylab")   # list of dicts from the server
for doc in docs:
    print(doc.get("document_id"), doc.get("filename"))
```

### `client.rag.delete_document()`

```python
result = client.rag.delete_document(doc_id, project_id="mylab")
print(result.artifact_id, result.counts)
```

Resolves the document id to the knowledge artifact it names and deletes it
like `client.knowledge.delete_artifact()`. Returns an
`ArtifactDeleteResult`; raises `APIError` (404) when the id names no artifact
of the project, and nothing was deleted.

### `client.knowledge.delete_artifact()`

```python
result = client.knowledge.delete_artifact("art-0123abcd", project_id="mylab")
print(result.counts)          # {"artifacts": 1, "nodes": …, "edges": …, "embeddings": …,
                              #  "ir": …, "findings": …, "events_detached": …}
for obj in result.objects:    # stored bytes: deleted | kept_shared | kept | failed | not_attempted
    print(obj["key"], obj["outcome"])
```

One server-side transaction over the artifact and everything derived from it;
the stored bytes follow, best-effort (`result.failed_objects` names any that
failed; the rows stay deleted). 404 for an unknown, foreign or already-deleted
id. A user verb: a client in an agent context is refused.

## Models

### `client.list_models()`

```python
models = client.list_models()
for model in models:
    print(f"{model.name} ({model.scope}, {model.status})")
```

### Model scale

*Added 2026-10-10.* R4 sizes retrieval for the model that
reads the passages. It detects that model's size class on its source, the
same way `r4 model scale` does
([Chapter 13](13-cli-reference.md#model-scale)):

```python
from r4.model_scale import detect_model_scale

scale = detect_model_scale("llama3.2:3b", source="ollama")   # a registered source; never raises
print(scale.size_class, scale.profile, scale.basis)         # e.g. "small small ollama"
print(scale.params_b, scale.active_params_b, scale.context)
```

The first decisive basis wins:

1. a pin: `R4_MODEL_SCALE`, then `r4 model scale set`;
2. a probe of the source: ollama `/api/show`, the platform model registry,
   an OpenAI-compatible `/v1/models`, or the hosted families (bedrock and
   anthropic are large);
3. the name rule.

Otherwise the class is `unknown`, and an unknown model reads with the
`medium` profile. Small is at most 4B parameters, large is over 14B, and
mixture-of-experts models use sqrt(total × active). A probe is bounded at
2 s. Its results are cached for 7 days in `<config>/cache/model_scale.json`.

## MCP

### `client.mcp.list_servers()`

```python
servers = client.mcp.list_servers()
```

### `client.mcp.list_tools()`

```python
tools = client.mcp.list_tools()
```

### `client.mcp.invoke()`

```python
result = client.mcp.invoke(
    "filesystem",
    "read_file",
    {"path": "/data/results.json"},
)
```

### `client.mcp.chat_with_tools()`

```python
response = client.mcp.chat_with_tools(
    "Search the database for protein structures",
    tools=["database.query"],
)
```

## Jobs

### `client.jobs.list()`

```python
jobs = client.jobs.list(status="running", project="mylab")
```

### `client.jobs.get()`

```python
job = client.jobs.get(job_id)
print(f"Status: {job.status}, Type: {job.type}")
```

### `client.jobs.cancel()` / `client.jobs.retry()`

```python
client.jobs.cancel(job_id)
client.jobs.retry(job_id)
```

## Workflows

### `client.workflows.list_workflows()`

```python
workflows = client.workflows.list_workflows()
```

Other workflow verbs: `upload`, `describe`, `get_results`, `cancel`,
`wait_for_completion`, `approve_gate`, and `lint_file` for offline validation.

Versions (ADR-113, 2026-11-08): each row carries `version` (the default),
`versions` (every registered one) and `source_sha256`;
`list_workflows(all_versions=True)` returns one row per version. Every verb
that names a workflow (`trigger`, `plan`, `describe`, `definition_package`,
`export`, `delete`, `save_run_definition`) takes `version=` or the
`"name@1.2.0"` shorthand; `r4.workflows.parse_workflow_ref("name@1.2.0")`
returns `("name", "1.2.0")`. `adopt(from_name, name, version)` and
`set_trigger_version(name, version | None)` cover migration and the trigger
pin; run status and results carry `workflow_version` and `source_sha256`.

### `client.workflows.export()`

```python
path = client.workflows.export("my-pipeline", "exports/", overwrite=False)
```

Downloads a workflow you can see (own, `system`, or shared) as a zip and
returns its `Path`: `./NAME.zip` by default, `DIR/NAME.zip` for an existing
directory or a destination ending in `/` (created when missing). The file is verified against its `EXPORT.json` checksums before
it is renamed into place; a mismatch raises `R4Error` and leaves no file.
`r4.workflows.extract_export(path, dest_dir)` unpacks it, refusing paths
that escape `dest_dir`. Container images are not included.

### `client.workflows.trigger()`

```python
# 2026-11-08: keywords corrected to the SDK's (`parameters=`, `project_id=`).
execution = client.workflows.trigger(
    "my-pipeline",
    version="1.2.0",          # optional; default: the highest registered
    parameters={"key": "value"},
    project_id="mylab",
)
```

### `client.workflows.status()`

```python
status = client.workflows.status(execution_id)
```

## Data

### `client.data.upload()` / `client.data.download()`

```python
client.data.upload("data.csv", project="mylab")
client.data.download("results/output.csv", local_path="./output.csv")
client.data.list_files(project="mylab")
```

## Vision

### `client.vision.submit()` (async task flow)

```python
task = client.vision.submit(
    "cell.png",
    model="sam3",
    adapter="sam3",
)
status = client.vision.status(task.task_id, model="sam3")
result = client.vision.get(task.task_id, model="sam3")

# Low-level helper: upload a file for vision input
file_ref = client.vision.upload_file("cell.png")
```

## Health

```python
health = client.health()
readiness = client.readiness()
```

## Environment Variables

| Variable | Client Parameter | Purpose |
|----------|-----------------|---------|
| `R4_GATEWAY_URL` | `gateway_url` | Gateway endpoint |
| `R4_API_KEY` | `api_key` | Authentication token |
| `R4_PROJECT_ID` | `project_id` | Default project |
| `R4_MODEL` | `model` | Default model |
| `R4_TIMEOUT` | `timeout` | Request timeout |
| `R4_RAG_RETRIEVE_TIMEOUT` | `rag.retrieve(timeout=...)` | Retrieve and passage-fetch timeout, default 10 s (added 2026-10-10) |
| `R4_KNOWLEDGE_BUNDLE` | `rag.load_bundle(path)` | Bundle that `client.rag` searches offline (added 2026-10-10) |

The retrieval router, profile and citation-check variables (`R4_RAG_*`,
`R4_MODEL_SCALE`) are listed in
[Appendix B](appendix-b-env-vars.md#r4-retrieval).

## Code Examples

See the [examples directory](../r4/examples/README.md) for runnable scripts:

- [simple_chat.py](../r4/examples/simple_chat.py) — Basic chat
- [streaming_chat.py](../r4/examples/streaming_chat.py) — Streaming responses
- [multi_turn_conversation.py](../r4/examples/multi_turn_conversation.py) — Multi-turn dialogue
- [rag_workflow.py](../r4/examples/rag_workflow.py) — RAG pipeline
- [mcp_tools.py](../r4/examples/mcp_tools.py) — MCP tool usage
- [batch_processing.py](../r4/examples/batch_processing.py) — Batch operations
- [error_handling.py](../r4/examples/error_handling.py) — Error handling patterns
