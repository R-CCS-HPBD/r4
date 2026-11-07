# SDK Reference

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

---

## Client

The main entry point for the SDK.

### `class Client`

```python
from r4 import Client

client = Client(
    gateway_url: str = None,        # Gateway URL (from env if not set)
    api_key: str = None,            # API key (from env if not set)
    project_id: str = None,         # Default project ID
    model: str = None,              # Default model name
    timeout: int = 300,             # Request timeout in seconds
    max_retries: int = 3,           # Max retry attempts
    verify_ssl: bool = True,        # Verify SSL certificates
)
```

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `config` | `Config` | Configuration object |
| `rag` | `RAGClient` | RAG operations |
| `mcp` | `MCPClient` | MCP operations |
| `job` | `JobClient` | Job management |
| `models` | `ModelClient` | Model registry |

#### Methods

##### `chat()`

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
    task: str = None,
    agent: str = None,
) -> Union[ChatResponse, Iterator[str]]
```

**Parameters:**
- `message`: User message to send
- `model`: Model to use (optional, uses default)
- `system`: System prompt (optional)
- `stream`: Stream response chunks
- `temperature`: Sampling temperature (0.0-2.0)
- `max_tokens`: Maximum tokens to generate
- `task`: MM-4-MA task hint (e.g. `"code"`, `"reason"`) — sets `X-R4-Task` header for gateway routing
- `agent`: MM-4-MA agent hint (e.g. `"coder"`, `"reviewer"`) — sets `X-R4-Agent` header

**Returns:**
- `ChatResponse` if `stream=False`
- `Iterator[str]` if `stream=True`

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

##### `chat_completion()`

Full chat completion with message history.

```python
def chat_completion(
    messages: List[ChatMessage],
    *,
    model: str = None,
    stream: bool = False,
    temperature: float = 0.7,
    max_tokens: int = None,
    top_p: float = 1.0,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    stop: List[str] = None,
) -> Union[ChatResponse, Iterator[str]]
```

**Parameters:**
- `messages`: List of `ChatMessage` objects
- `model`: Model to use
- `stream`: Stream response
- `temperature`: Sampling temperature
- `max_tokens`: Maximum tokens
- `top_p`: Nucleus sampling
- `frequency_penalty`: Reduce repetition
- `presence_penalty`: Encourage diversity
- `stop`: Stop sequences

**Example:**

```python
from r4 import Client, ChatMessage

client = Client()

messages = [
    ChatMessage.system("You are a helpful assistant."),
    ChatMessage.user("What is 2+2?"),
]

response = client.chat_completion(
    messages,
    temperature=0.3,
    max_tokens=100,
)

print(response.content)
print(f"Tokens: {response.usage.total_tokens}")
```

##### `list_models()`

List available models.

```python
def list_models() -> List[str]
```

**Returns:** List of model names.

##### `health()`

Check API health.

```python
def health() -> bool
```

**Returns:** `True` if healthy.

---

## ChatMessage

Represents a message in a conversation.

### `class ChatMessage`

```python
from r4 import ChatMessage

message = ChatMessage(
    role: str,              # "user", "assistant", "system"
    content: str,           # Message content
    name: str = None,       # Optional name
)
```

#### Class Methods

```python
# Create user message
ChatMessage.user("Hello!")

# Create assistant message
ChatMessage.assistant("Hi there!")

# Create system message
ChatMessage.system("You are helpful.")
```

#### Methods

```python
# Convert to dict
message.to_dict() -> dict
```

---

## ChatResponse

Response from chat completion.

### `class ChatResponse`

```python
@dataclass
class ChatResponse:
    content: str            # Response text
    model: str              # Model used
    finish_reason: str      # "stop", "length", "tool_calls"
    usage: Usage            # Token usage
    raw_response: dict      # Full API response
```

---

## Usage

Token usage information.

### `class Usage`

```python
@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
```

---

## RAGClient

RAG (Retrieval-Augmented Generation) operations.

### Access

```python
client = Client()
rag = client.rag  # RAGClient instance
```

### Methods

*Updated 2026-10-10 (ADR-093):* `retrieve()` and `fetch_passage()` were
added. `chat_with_context()` and `inject_context()` were removed (see
[below](#removed-chat_with_context-and-inject_context)). The `threshold` of
`query()` now defaults to `None`.

##### `retrieve()`

The passages UMRS keeps for a question, best first, with its sufficiency
verdict, over `GET /gw/rag/retrieve`. No chat model is called.

```python
def retrieve(
    query: str | Sequence[str],        # the question, or 1-3 variants fused server-side
    *,
    project_id: str = None,            # default: the client's project
    graphs: Sequence[str] = None,      # graph ids/names to search only
    exclude_graphs: Sequence[str] = None,
    k_max: int = None,                 # passage bound, clamped to 1-12
    pool: int = None,                  # candidate pool, 10-200
    per_artifact: int = None,          # passages per document, 1-4
    lexical: str = None,               # "auto" | "on" | "off"
    rerank: str = None,                # "auto" | "on" | "off"
    merge: str = None,                 # "auto" | "on" | "off" (neighbour merge)
    cutoff: str = None,                # "adaptive" | "fixed"
    threshold: float = None,           # a float selects the legacy absolute cut
    trace: bool = False,               # ask for the stage trace
    timeout: float = None,             # default R4_RAG_RETRIEVE_TIMEOUT (10 s), <= R4_TIMEOUT
    profile: str = None,               # "auto" | "small" | "medium" | "large" (D-14)
    reader_model: str = None,          # the model that will read the passages
) -> RetrieveResult
```

An option left as `None` is not sent, so the server's default applies (the
adaptive cut, with `k_max` and pool sized by the profile). `profile` and
`reader_model` size those defaults for the reading model; an explicit option
still wins.

UMRS decides relevance, the cut and sufficiency (CONSTITUTION §1.3). Read
`result.sufficiency.status` and do not re-cut `result.passages`. To display
one score per passage, use `r4.rag.passage_score(passage)`: the rerank
probability, else the dense cosine, else the fused or lexical score.

Fallbacks:

- An older server without the verb (HTTP 404/405) gets one call to
  `/gw/rag/query`. The result then has `lanes["fallback"]` and status
  `unknown`, or `insufficient` when nothing passed the legacy cut.
- A loaded bundle (`load_bundle()`, or `R4_KNOWLEDGE_BUNDLE`) is searched
  offline: BM25 per variant, fused by reciprocal rank fusion, with no cut.
  The status is `unknown` with basis `offline`.
  `lanes["local_bundle"]["unapplied"]` lists the options the bundle search
  could not apply.

**Example:**

```python
result = client.rag.retrieve("What are the key findings?", project_id="my-project")

print(result.sufficiency.status, result.sufficiency.basis)
for p in result.passages:
    print(f"[{p.rank}] {p.source_name} › {p.section} {p.scores} {p.lanes}")
    print(p.text[:200])
```

##### `fetch_passage()`

One passage by its handle (`RetrievedPassage.source_id`), over
`GET /gw/rag/passages/{node_id}`, with the same access rule as `retrieve()`.

```python
def fetch_passage(
    node_id: str,
    *,
    project_id: str = None,   # the project the handle was retrieved in (default: the client's)
    window: int = 0,          # neighbouring chunks on each side, 0-2
    max_chars: int = 4000,
    timeout: float = None,    # as for retrieve()
) -> dict                     # {source_id, artifact_id, source_name, text, span, window, text_source}
```

A passage you cannot see raises `APIError` (HTTP 404).

```python
if result.passages:
    passage = client.rag.fetch_passage(result.passages[0].source_id, window=1)
    print(passage["text"])
```

##### `query()`

An answer composed on the platform, with its sources, over `POST /gw/rag/query`.

```python
def query(
    query: str,
    *,
    project_id: str = None,
    collection: str = None,         # alias of project_id
    top_k: int = 5,
    threshold: float = None,        # legacy absolute cut; None = not sent, the server decides
    include_sources: bool = True,
    filters: dict = None,
    query_embedding: list = None,   # local bundle only: a query vector in the bundle's space
) -> RAGResponse
```

**Parameters:**
- `query`: Search query
- `project_id`: Project to query
- `top_k`: Number of sources to retrieve
- `threshold`: Legacy absolute similarity cut (0.0-1.0). With `None`, the
  default, it is left out of the request and the server decides relevance
  (changed 2026-10-10; it was 0.7)
- `include_sources`: Include source documents
- `filters`: Metadata filters

**Example:**

```python
response = client.rag.query(
    "What are the key findings?",
    project_id="my-project",
    top_k=10,
)

print(response.answer)
for source in response.sources:
    print(f"- {source.source_name}: {source.score:.2f}")
```

##### `upload()`

Upload a document for indexing.

```python
def upload(
    file_path: str,
    *,
    project_id: str = None,
    metadata: dict = None,
) -> UploadResult
```

**Parameters:**
- `file_path`: Path to file
- `project_id`: Target project
- `metadata`: Optional metadata

**Returns:** `UploadResult` with document ID and chunk count.

**Example:**

```python
result = client.rag.upload(
    "paper.pdf",
    project_id="my-project",
    metadata={"source": "arxiv", "year": 2024},
)
print(f"Uploaded {result.document_id}, {result.chunks} chunks")
```

##### `list_documents()`

List documents in a project.

```python
def list_documents(
    project_id: str = None,
) -> List[dict]          # the server's document rows, e.g. doc["document_id"], doc["filename"]
```

##### `delete_document()`

Delete a document: the knowledge artifact its id names, with everything
derived from it (ADR-093 MD-16). Raises `APIError` (404) when the id names no
artifact of the project.

```python
def delete_document(
    document_id: str,
    project_id: str = None,
) -> ArtifactDeleteResult   # r4.knowledge: counts per row kind, object outcomes
```

##### Removed: `chat_with_context()` and `inject_context()`

*Removed 2026-10-10 (ADR-093; they had no caller, CONSTITUTION §6).* To answer
with your own model, render the `retrieve()` passages with the shared context
pack and send that to your chat call:

```python
from r4_contracts.context_pack import render_pack

question = "Explain the methodology"
result = client.rag.retrieve(question, project_id="my-project")
if result.passages and result.sufficiency.status != "insufficient":
    status = "sufficient" if result.sufficiency.status == "sufficient" else "partial"
    prompt, labels = render_pack(question, result.passages, status=status)  # labels: {"[1]": source_id}
    print(client.chat(prompt).content)
```

The module function `r4.rag.inject_context_into_messages(messages, sources,
*, max_context_tokens=2000)` remains. It appends `RAGSource` objects, such as
`query().sources`, to the first system message.

---

## RAGResponse

Response from RAG query.

### `class RAGResponse`

```python
@dataclass
class RAGResponse:
    answer: str                    # Generated answer
    sources: List[RAGSource]       # Source documents
    usage: dict                    # Token usage
    raw_response: dict             # Full response

    documents: List[RAGSource]     # read-only alias of sources (property, 2026-10-10)
```

---

## RAGSource

A source document from retrieval.

### `class RAGSource`

```python
@dataclass
class RAGSource:
    document_id: str       # Document identifier
    content: str           # Retrieved content
    score: float           # Similarity score
    metadata: dict         # Document metadata

    source_name: str       # property: the artifact's name from metadata, else document_id
    section: str           # property: metadata["section"] or ""
    def to_dict(self) -> dict   # the fields above, source_name and section included
```

---

## RetrieveResult

What `retrieve()` returns (`r4_contracts.retrieval`, added 2026-10-10 with
ADR-093). These are frozen dataclasses with `to_dict()` / `from_dict()`.

```python
@dataclass(frozen=True)
class RetrieveResult:
    query: str
    passages: tuple[RetrievedPassage, ...]   # best first
    sufficiency: RetrievalSufficiency
    searched: int          # candidate rows in scope (0 = nothing to search)
    space: str             # the embedding space the dense lane used
    lanes: dict            # lane -> {status, reason, n}; "profile", "fallback", "local_bundle"
    timing_ms: dict
    trace: dict            # only with trace=True

@dataclass(frozen=True)
class RetrievedPassage:
    rank: int              # 1-based
    source_id: str         # the fetch_passage() handle
    artifact_id: str
    source_name: str       # trusted artifact metadata, never passage text
    text: str
    section: str
    chunk_index: int
    chunk_count: int
    span: tuple[int, int] | None   # character offsets in the artifact
    text_source: str       # search_text | artifact | node | excerpt
    scores: dict           # dense, lexical, fused, rerank_*
    lanes: tuple[str, ...] # the lanes that found it ("graph" = a graph bridge)

@dataclass(frozen=True)
class RetrievalSufficiency:
    status: str            # sufficient | partial | insufficient | unknown
    basis: str             # z_null, lexical, rerank_p, threshold, no_vectors, offline
    value: float | None    # what the rule measured
    threshold: float | None  # what it was held to
    detail: str
```

---

## UploadResult

Result of document upload.

### `class UploadResult`

```python
@dataclass
class UploadResult:
    document_id: str       # Assigned document ID
    filename: str          # Original filename
    status: str            # "success" or "error"
    chunks: int            # Number of chunks created
    error: str             # Error message (if any)
```

---

## DataClient

File and directory transfer operations. Uses ZeroMQ for high-throughput
bulk transfers with automatic fallback to HTTP multipart when ZeroMQ is
unavailable.

### Access

```python
client = Client()
data = client.data  # DataClient instance
```

### Methods

##### `upload(local_path, remote_path=None, *, project=None)`

Upload a file to the platform.

```python
result = data.upload("data.csv", project="my-project")
# UploadResult(filename='data.csv', status='success', chunks=1, error=None)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `local_path` | `str \| Path` | Required | Path to local file |
| `remote_path` | `str \| None` | `None` | Remote destination path |
| `project` | `str \| None` | `None` | Project ID |

**Returns:** `UploadResult`

##### `download(remote_path, local_path=None, *, project=None)`

Download a file from the platform.

```python
local = data.download("results/output.csv", project="my-project")
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `remote_path` | `str` | Required | Remote file path |
| `local_path` | `str \| Path \| None` | `None` | Local destination (defaults to filename) |
| `project` | `str \| None` | `None` | Project ID |

**Returns:** `Path` — path to the downloaded file

##### `upload_directory(local_dir, remote_dir=None, *, project=None, pattern="**/*")`

Upload an entire directory.

```python
results = data.upload_directory("./datasets", project="my-project")
for r in results:
    print(f"{r.filename}: {r.status}")
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `local_dir` | `str \| Path` | Required | Local directory path |
| `remote_dir` | `str \| None` | `None` | Remote destination directory |
| `project` | `str \| None` | `None` | Project ID |
| `pattern` | `str` | `"**/*"` | Glob pattern for file matching |

**Returns:** `list[UploadResult]`

### Transport Notes

- **ZeroMQ** (preferred): Used when the `umrs` extra is installed
  (`pip install r4[umrs]`). Provides chunked streaming for large files.
- **HTTP multipart**: Automatic fallback when ZeroMQ is not available.
  Suitable for files up to the gateway upload limit.

---

## MCPClient

MCP (Model Context Protocol) operations.

### Access

```python
client = Client()
mcp = client.mcp  # MCPClient instance
```

### Methods

##### `list_tools()`

List available MCP tools.

```python
def list_tools(
    server: str = None,
    refresh: bool = False,
) -> List[MCPTool]
```

**Example:**

```python
tools = client.mcp.list_tools()
for tool in tools:
    print(f"{tool.name}: {tool.description}")
```

##### `list_servers()`

List MCP servers.

```python
def list_servers() -> List[Dict[str, Any]]
```

**Example:**

```python
servers = client.mcp.list_servers()
for server in servers:
    print(server["name"], server["url"])
```

##### `invoke()`

Invoke an MCP tool.

```python
def invoke(
    tool_name: str,
    **kwargs,
) -> MCPToolResult
```

**Example:**

```python
result = client.mcp.invoke(
    "read_file",
    path="/path/to/file.txt",
)

if result.success:
    print(result.result)
else:
    print(f"Error: {result.error}")
```

##### `register_server()`

Register a custom MCP server.

```python
def register_server(
    name: str,
    url: str,
    tools: List[dict] = None,
    auth_type: str = "none",
    auth_config: dict = None,
    description: str = "",
    metadata: dict = None,
) -> dict
```

**Example:**

```python
result = client.mcp.register_server(
    name="my-server",
    url="http://localhost:9000",
    auth_type="bearer",
    auth_config={"token": "secret"},
    description="My custom server",
)
print(f"Server ID: {result['server_id']}")
```

##### `unregister_server()`

Remove an MCP server.

```python
def unregister_server(server_id: str) -> bool
```

##### `get_server_info()`

Get server details.

```python
def get_server_info(server_id: str) -> dict
```

##### `check_server_health()`

Check server health.

```python
def check_server_health(server_id: str) -> dict
```

##### `chat_with_tools()`

Chat with tool calling enabled.

```python
def chat_with_tools(
    message: str,
    tools: List[str] = None,
    max_iterations: int = 5,
) -> ChatResponse
```

**Example:**

```python
response = client.mcp.chat_with_tools(
    "Read config.json and summarize it",
    tools=["read_file"],
    max_iterations=3,
)
print(response.content)
```

##### `agent_run()`

Run a multi-step agent loop.

```python
def agent_run(
    task: str,
    tools: List[str] = None,
    max_iterations: int = 10,
) -> AgentResult
```

---

## MCPTool

An MCP tool definition.

### `class MCPTool`

```python
@dataclass
class MCPTool:
    name: str                  # Tool name
    description: str           # Description
    parameters: dict           # JSON Schema parameters
    server: str                # Server name
```

---

## MCPToolResult

Result from tool invocation.

### `class MCPToolResult`

```python
@dataclass
class MCPToolResult:
    tool_name: str             # Tool that was called
    result: Any                # Result value
    success: bool              # Whether call succeeded
    error: str                 # Error message (if failed)
    call_id: str               # Call identifier
```

---

## JobClient

Job management operations.

### Access

```python
client = Client()
jobs = client.job  # JobClient instance
```

### Methods

##### `list()`

List jobs.

```python
def list(
    project_id: str = None,
    status: str = None,
    job_type: str = None,
    limit: int = 20,
) -> List[Job]
```

**Example:**

```python
jobs = client.job.list(status="running")
for job in jobs:
    print(f"{job.job_id}: {job.status}")
```

##### `describe()`

Get job details.

```python
def describe(job_id: str) -> Job
```

**Example:**

```python
job = client.job.describe("job-12345")
print(f"Status: {job.status}")
print(f"Attempts: {len(job.attempts)}")
```

##### `cancel()`

Cancel a job.

```python
def cancel(job_id: str, force: bool = False) -> bool
```

##### `retry()`

Retry a failed job.

```python
def retry(job_id: str) -> str
```

**Returns:** New job ID.

---

## Job

Job information.

### `class Job`

```python
@dataclass
class Job:
    job_id: str                    # Job identifier
    project_id: str                # Project
    user_id: str                   # Owner
    job_type: str                  # Type (endpoint_provision, etc.)
    spec: dict                     # Job specification
    status: str                    # Current status
    priority: int                  # Priority level
    max_attempts: int              # Max retry attempts
    restart_policy: str            # Restart policy
    created_at: datetime           # Creation time
    updated_at: datetime           # Last update
    active_attempt_id: str         # Current attempt
    commit_attempt_id: str         # Successful attempt
    attempts: List[JobAttempt]     # All attempts
```

---

## JobAttempt

Information about a job attempt.

### `class JobAttempt`

```python
@dataclass
class JobAttempt:
    attempt_id: str                # Attempt identifier
    job_id: str                    # Parent job
    attempt_no: int                # Attempt number
    backend: str                   # Backend (slurm, k8s, compose)
    backend_ref: str               # Backend job ID
    status: str                    # Status
    started_at: datetime           # Start time
    ended_at: datetime             # End time
    exit_reason: str               # Exit/error reason
    log_uri: str                   # Log location
    status_detail: str             # Status details
    metrics: dict                  # Metrics data
```

---

## ModelClient

Model registry operations.

### Access

```python
client = Client()
models = client.models  # ModelClient instance
```

### Methods

##### `list()`

List models.

```python
def list(
    scope: str = None,         # system, project, user
    model_type: str = None,    # llm, embedding
    project_id: str = None,
    status: str = None,
    limit: int = 50,
) -> List[ModelInfo]
```

##### `get()`

Get model details.

```python
def get(name: str) -> ModelInfo
```

##### `stage()`

Download model from HuggingFace.

```python
def stage(
    source_id: str,            # HuggingFace ID
    name: str = None,          # Local name
    scope: str = "user",
    project_id: str = None,
    model_type: str = "llm",
    description: str = "",
    tags: List[str] = None,
) -> str  # Returns job_id
```

##### `register()`

Register existing model.

```python
def register(
    name: str,
    path: str,
    scope: str = "user",
    project_id: str = None,
    model_type: str = "llm",
    source_uri: str = None,
    huggingface_id: str = None,
    description: str = "",
    tags: List[str] = None,
) -> ModelInfo
```

##### `provision()`

Provision model endpoint.

```python
def provision(
    name: str,
    prefer_system: bool = False,
    wait: bool = False,
    timeout: int = 600,
) -> ProvisionResult
```

##### `publish()`

Publish to project scope.

```python
def publish(name: str, project_id: str) -> bool
```

##### `delete()`

Delete a model.

```python
def delete(name: str) -> bool
```

### Model scale

*Added 2026-10-10 (ADR-093 D-14).* This is a module function, not a
`ModelClient` method. It detects the size class that sizes retrieval for a
reading model, as `r4 model scale` does.

```python
from r4.model_scale import detect_model_scale

def detect_model_scale(
    model: str,
    *,
    source: Source | str = None,   # a registered source; default: the model's route
    client: Client = None,         # supplies config and platform credentials
    refresh: bool = False,         # skip the 7-day cache
) -> ModelScale                    # never raises; on failure the name rule answers
```

`ModelScale` fields:

- `model`, `source`;
- `size_class`: `small`, `medium`, `large` or `unknown`;
- `params_b`, `active_params_b`, `effective_params_b`: billions of parameters;
- `context`;
- `basis`: `env`, `override`, `ollama`, `ollama-moe-estimate`, `registry`,
  `endpoint`, `family`, `name`, `hosted` or `unknown`;
- `detail`, `detected_at`, `provisional`;
- the property `profile`: the retrieval profile, where `unknown` reads with
  `medium`.

The first decisive basis wins:

1. a pin: `R4_MODEL_SCALE`, then `<config>/model_scales.json`, written by
   `r4 model scale set`;
2. a probe of the source, bounded at 2 s: ollama `/api/show`, the platform
   registry, or an OpenAI-compatible `/v1/models`; bedrock and anthropic are
   large;
3. the name rule.

Small is at most 4B parameters and large is over 14B, using sqrt(total ×
active) for mixture-of-experts models.

---

## ModelInfo

Model registry entry.

### `class ModelInfo`

```python
@dataclass
class ModelInfo:
    model_id: str
    name: str
    scope: ModelScope           # SYSTEM, PROJECT, USER
    model_type: ModelType       # LLM, EMBEDDING
    storage_path: str
    status: ModelStatus         # STAGING, AVAILABLE, DEPRECATED
    source_uri: str
    huggingface_id: str
    owner_id: str
    project_id: str
    description: str
    tags: List[str]
    serving_profile: ServingProfile
    created_at: datetime
    updated_at: datetime
```

---

## Config

Configuration class.

### `class Config`

```python
@dataclass
class Config:
    gateway_url: str
    api_key: str
    project_id: str
    model: str
    timeout: int
    max_retries: int
    slurm_job_id: str          # Auto-detected
    direct_endpoint: str
    verify_ssl: bool
    proxy: str
```

#### Class Methods

```python
# Load from environment
config = Config.from_env()
```

#### Properties

```python
config.is_hpc              # True if running on HPC
config.effective_endpoint  # Actual endpoint URL
```

---

## Errors

Exception classes.

### `class R4Error`

Base exception for all r4 errors.

```python
from r4 import R4Error

try:
    client.chat("Hello")
except R4Error as e:
    print(f"Error: {e}")
```

### `class AuthenticationError`

Authentication failed (401).

```python
from r4 import AuthenticationError

try:
    client.chat("Hello")
except AuthenticationError:
    print("Check your API key")
```

### `class RateLimitError`

Rate limit exceeded (429).

```python
from r4 import RateLimitError

try:
    client.chat("Hello")
except RateLimitError as e:
    print(f"Retry after {e.retry_after} seconds")
```

### `class APIError`

General API error.

```python
from r4 import APIError

try:
    client.chat("Hello")
except APIError as e:
    print(f"Status: {e.status_code}")
    print(f"Message: {e.message}")
    print(f"Body: {e.response_body}")
```

### `class R4TimeoutError`

Request timed out.

```python
from r4 import R4TimeoutError

try:
    client.chat("Hello", timeout=5)
except R4TimeoutError:
    print("Request timed out")
```

---

## Jupyter Integration

### Load Extension

```python
%load_ext r4.jupyter
```

### Magic Commands

#### `%r4_config`

```python
%r4_config                          # Show config (same as --show)
%r4_config model=NAME               # Set model
%r4_config temperature=0.8 top_k=5  # Set several, as key=value pairs
%r4_config collection=NAME          # Set RAG collection
%r4_config --reset                  # Back to defaults
```

#### `%%r4`

```python
%%r4
Your prompt here
```

```python
%%r4 --no-stream
Your prompt here
```

#### `%rag`

```python
%rag query text

%rag --collection NAME --top-k 10 query text
%rag --with-llm query text          # Also synthesise an answer
%rag -o hits query text             # Store the results in `hits`
```

*2026-10-10:* there is no `%%rag_chat` cell magic. The extension defines
`%r4`, `%%r4`, `%rag` and `%r4_config` only (`r4/src/r4/jupyter/magics.py`).
To answer from retrieved passages with your own model, use the
`retrieve()` + `render_pack` recipe under
[Removed: `chat_with_context()`](#removed-chat_with_context-and-inject_context).

### Widgets

#### `get_chat_widget()`

```python
from r4 import get_chat_widget

chat = get_chat_widget(
    model="rivault-gpt-oss-static",
    system_prompt="You are helpful.",
)

chat.display()
response = chat.chat("Hello")
chat.history()
chat.clear()
```

#### `get_rag_widget()`

```python
from r4 import get_rag_widget

rag = get_rag_widget(
    collection="my-docs",
    top_k=5,
)

rag.display()
results = rag.query("search query")
```

---

## Async Support

### `class AsyncClient`

```python
import asyncio
from r4 import AsyncClient

async def main():
    async with AsyncClient() as client:
        # Regular chat
        response = await client.chat("Hello")
        print(response.content)

        # Streaming
        async for chunk in client.chat("Story", stream=True):
            print(chunk, end="")

        # RAG
        rag_response = await client.rag.query(
            "Question",
            project_id="project",
        )

asyncio.run(main())
```

---

## Multi-Agent Teamup

The teamup module enables VibeCodeHPC-style multi-agent collaboration for HPC code optimization:
the HPC preset (the `hpc` crew, `AgentRole` PM/SE/PG/CD). Every other crew (research, code,
paper-writing, kakenhi-report, peer-review) is a team plan template since ADR-092 slice 4; see
`r4.teams.crews` and `r4 team plan`.

### Session Management

```python
from r4 import Client
from r4.teamup import TeamupSession, TeamupSessionConfig, AgentRole

# Create configuration
config = TeamupSessionConfig(
    name="matrix-optimization",
    roles=[AgentRole.PM, AgentRole.SE, AgentRole.PG],
    optimization_goal="Optimize matrix multiplication for GPU",
    max_iterations=10,
    hardware_target="fugaku",
)

# Create and run session
client = Client()
session = TeamupSession.create(config=config, client=client)

# Run iteratively
for iteration, outputs in session.run_iteratively():
    print(f"Iteration {iteration}")
    for agent, output in outputs.items():
        print(f"[{agent.value}]: {output[:200]}...")

# Get summary
print(session.get_summary())
```

The teamup module's dormant goal parser, evolutionary optimiser, SSH/Slurm
connection and report generator were deleted on 2026-10-11 (ADR-092 slice 4
cleanup, gap row D-37): nothing imported them, and the connection reached a
scheduler directly. Submit HPC work through UMRS durable jobs (`hpc_submit`);
`TeamupSession.generate_report()` writes a session's report.

## Agentic Search

Multi-hop agentic search with RAG, web, and code integration.

### Basic Usage

```python
from r4.search import AgenticSearch, SearchConfig

# Configure search
config = SearchConfig(
    max_hops=5,
    tools=["rag", "web", "code"],
    confidence_threshold=0.8,
)

# Run search
search = AgenticSearch(config=config, client=client)
result = await search.search("What are the best practices for GPU memory optimization?")

print(result.answer)
print(f"Confidence: {result.confidence}")
for citation in result.citations:
    print(f"Source: {citation.url}")
```

### Search Pipeline

```python
from r4.search import AgenticSearchPipeline

pipeline = AgenticSearchPipeline(
    planner=SearchPlanner(...),
    executor=ToolExecutor(...),
    synthesizer=ResultSynthesizer(...),
)

result = await pipeline.execute("Complex research query...")
```

### Iteration Control

```python
from r4.search import IterationController, IterationConfig

# Control iterative refinement
controller = IterationController(
    config=IterationConfig(
        max_iterations=5,
        min_confidence=0.7,
        gap_threshold=0.3,
    )
)

# Check if more iterations needed
decision = controller.should_continue(current_result)
if decision.should_continue:
    print(f"Continuing: {decision.reason}")
    for query in decision.follow_up_queries:
        # Execute follow-up queries
        ...
```

### Gap Detection

```python
from r4.search import GapDetector, GapType

detector = GapDetector()
gaps = detector.detect_gaps(query="...", result="...", sources=[...])

for gap in gaps:
    print(f"Gap: {gap.description}")
    print(f"Type: {gap.gap_type}")  # MISSING_INFO, CONTRADICTION, etc.
    print(f"Importance: {gap.importance}")
```

### Reasoning Trace

```python
from r4.search import create_tracer, TraceFormatter

# Create tracer for transparency
tracer = create_tracer(session_id="search-001", query="My query")

# Record events during search
tracer.event(TraceEventType.QUERY_DECOMPOSED, "Split into 3 sub-questions")
tracer.tool_call_start("web_search", {"query": "..."})
tracer.decision("route_selection", ["rag", "web"], "web", "No local docs match")

# Complete and format
trace = tracer.complete(success=True, confidence=0.85)

# Output formats
print(TraceFormatter.to_markdown(trace))
print(TraceFormatter.to_html(trace))
```

---

## Observability

Integration with Prometheus, Jaeger, and Loki for metrics, tracing, and logging.

### Metrics

```python
from r4 import metrics, timed, counted

# Counter metric
metrics.counter("api_requests_total", labels={"endpoint": "/chat"})

# Gauge metric
metrics.gauge("active_connections", 42)

# Histogram metric
metrics.histogram("request_duration_seconds", 0.245, labels={"method": "POST"})

# Decorated timing
@timed("function_duration_seconds", {"function": "process"})
def process_data():
    ...

# Decorated counting
@counted("api_calls_total", {"endpoint": "/chat"})
def handle_chat():
    ...

# Export Prometheus format
print(metrics.to_prometheus_format())
```

### Distributed Tracing

```python
from r4 import tracer, traced

# Context manager for spans
with tracer.span("my_operation") as span:
    span.set_attribute("user_id", "user123")
    span.add_event("processing_started")
    
    result = do_work()
    
    span.set_attribute("result_size", len(result))

# Decorated tracing
@traced("process_data")
def process_data():
    with tracer.span("sub_operation"):
        ...

# Access active span
if tracer.active_span:
    tracer.active_span.set_attribute("key", "value")
```

### Structured Logging

```python
from r4 import setup_logging, LogContext

# Setup JSON structured logging (Loki-compatible)
setup_logging(service_name="my-service", level=logging.INFO, structured=True)

# Add context to logs
with LogContext(user_id="123", request_id="abc"):
    logger.info("Processing request")
    # Output: {"timestamp": "...", "level": "INFO", "message": "Processing request", "user_id": "123", "request_id": "abc"}
```

### FastAPI Integration

```python
from fastapi import FastAPI
from r4 import instrument_fastapi, get_health_metrics

app = FastAPI()

# Add automatic instrumentation
instrument_fastapi(app)

# Health endpoint with metrics
@app.get("/health")
def health():
    return get_health_metrics()
```

---

## Full Example

```python
from r4 import Client, ChatMessage

# Initialize client
client = Client()

# Check health
if not client.health():
    print("API not available")
    exit(1)

# List models
models = client.list_models()
print(f"Available models: {models}")

# Simple chat
response = client.chat("What is Python?")
print(f"Answer: {response.content}")

# Multi-turn conversation
messages = [
    ChatMessage.system("You are a Python expert."),
    ChatMessage.user("What are decorators?"),
]

response = client.chat_completion(messages, temperature=0.3)
messages.append(ChatMessage.assistant(response.content))

messages.append(ChatMessage.user("Give me an example"))
response = client.chat_completion(messages)
print(response.content)

# RAG workflow
result = client.rag.upload("documentation.pdf", project_id="my-docs")
print(f"Uploaded {result.chunks} chunks")

rag_response = client.rag.query(
    "How do I install the package?",
    project_id="my-docs",
)
print(f"Answer: {rag_response.answer}")
for src in rag_response.sources:
    print(f"Source: {src.document_id}")

# MCP tools
tools = client.mcp.list_tools()
result = client.mcp.invoke("read_file", path="/etc/hosts")
if result.success:
    print(result.result)

# Job tracking
jobs = client.job.list(status="running")
for job in jobs:
    details = client.job.describe(job.job_id)
    print(f"{job.job_id}: {len(details.attempts)} attempts")
```
