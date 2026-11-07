# 6. Endpoints

Endpoints are running model instances that serve inference requests. R4 supports both platform-managed endpoints (via UMRS/Slurm/K8s) and public cloud endpoints.

## Platform Endpoints

### Provisioning

```bash
# Basic endpoint
r4 endpoint up --model Qwen/Qwen2.5-7B-Instruct --wait

# With tier specification
r4 endpoint up --tier user --model Qwen/Qwen2.5-7B-Instruct

# With custom mounts
r4 endpoint up --model Qwen/Qwen2.5-7B-Instruct \
  --mount-mcp /home/user/my-mcp:/mcp-servers/my-mcp \
  --mount-data /projects/datasets:/project \
  --mount-rag /projects/rag-index:/rag-index

# With egress domains
r4 endpoint up --model Qwen/Qwen2.5-7B-Instruct \
  --egress-domains api.example.com

# With GPU/resource configuration
r4 endpoint up --model Qwen/Qwen2.5-32B-Instruct \
  --gpu-count 2 \
  --max-model-len 8192

# Vision endpoint (BentoML)
r4 endpoint up --model sam3 --backend bentoml --service-kind bentoml_vision
```

### Shorthand

```bash
r4 up --model my-qwen --wait     # Shorthand for r4 endpoint up
r4 up                            # Interactive model picker
r4 down <endpoint-id>            # Shorthand for r4 endpoint down
r4 down --all                    # Stop all active endpoints
r4 down --force <endpoint-id>    # Force stop without graceful shutdown
```

## Endpoint Pools

Provision or tear down multiple endpoints at once from a YAML roster file.

### Pool Definition File

```yaml
# pool.yaml
endpoints:
  - model: Qwen/Qwen2.5-7B-Instruct
    source: ai4s
    tier: user
    max_model_len: 4096
  - model: Qwen/Qwen2.5-32B-Instruct
    source: rccs-cloud
    tier: project
    gpu_count: 2
    max_model_len: 8192
```

### Pool Commands

```bash
# Provision all endpoints in a roster
r4 pool up pool.yaml
r4 pool up pool.yaml --dry-run          # Preview without provisioning
r4 pool up pool.yaml --sequential       # One-by-one instead of parallel
r4 pool up pool.yaml --continue-on-error

# Stop all endpoints tracked by a roster
r4 pool down pool.yaml

# Or use the shorthand via r4 up
r4 up --pool-file pool.yaml
r4 up --pool-file pool.yaml --dry-run
r4 down --pool-file pool.yaml
```

Pools are useful with task-based routing (`r4 chat --task ...`) to keep
multiple models warm for different workloads. The optional per-entry `source`
selects that entry's Gateway without changing the active source. This permits
one pool to provision independent HPC systems concurrently.

### Endpoint Lifecycle

```bash
# List all endpoints
r4 endpoint list
r4 endpoint list --all            # include stopped instances

# Inspect an endpoint
r4 endpoint describe <endpoint-id>
r4 endpoint status <endpoint-id>

# View logs
r4 endpoint logs <endpoint-id>
r4 endpoint logs <endpoint-id> --tail 100

# Stop an endpoint
r4 endpoint down <endpoint-id>
r4 endpoint down <endpoint-id> --force
```

### Endpoint Tiers

| Tier | Owner | Lifecycle | Egress |
|------|-------|-----------|--------|
| **system** | Admin | Warm pool (always-on) | None (offline) |
| **project** | Project lead | On-demand | EgressGuard controlled |
| **user** | Individual | On-demand | EgressGuard controlled |

The tier says who *owns* and pays for an endpoint. Who may *use* it is a
separate access list (2026-10-01):

| Scope | Who can route requests to it | Who may grant it |
|---|---|---|
| (owning project) | always — the default when nothing is set | — |
| `user:<id>` | that user, from any project | the owning project |
| `project:<id>` | callers working in that project | the owning project |
| `circle:<project>/<id>` | members of that circle (`/umrs/circles`) | the owning project |
| `system` | every authenticated user | admins only |

```bash
r4 endpoint up --model Bespoke-Nimble-9B --access circle:proj-a/lab --access user:bob
r4 endpoint access <endpoint-id>                    # show
r4 endpoint access <endpoint-id> --set system       # admin: open to everyone
r4 endpoint access <endpoint-id> --private          # owning project only
```

Requests always try your own project's endpoints first, then endpoints
shared with you; `/v1/models` and `r4 endpoint list` show both. A pool
recipe may carry `access:` (e.g. the always-on decision model is
`access: [system]`); it applies when the provisioning caller may grant it.

**Model limits in `/v1/models`** (2026-10-05). Each listed model includes
four extra fields. A field is `null` when the platform does not know the value:

| Field | Meaning | Source |
|---|---|---|
| `context_window` | Context length the endpoint serves | the served `max_model_len` (reported by vLLM), else the provisioned value |
| `max_output_tokens` | Default output-token budget for this model | the serving profile's `max_output_tokens`, else the model's `generation_config` `max_new_tokens` |
| `reasoning` | The model spends output tokens on reasoning | the registry's capability or model-family flag |
| `supports_reasoning_effort` | The endpoint honours `reasoning_effort` | the registry's `reasoning_effort_levels` |

All existing fields, including `max_model_len` when vLLM reports it, are
unchanged. The values are recorded when an endpoint is provisioned.
Endpoints provisioned earlier list them as `null` until they are provisioned
again. `max_output_tokens` is a new optional serving-profile field, set next
to `max_model_len`. Today it is published only in this listing: it is not
passed to the model server as a launch argument.

**The same access list governs every shareable family** (2026-10-03): knowledge graphs (`r4 knowledge graph-access`), scientific
catalogs (`r4 catalog access`), workflows (`r4 workflow access`), RAG
corpora (`r4 rag corpus-access`) and personal buckets (`r4 bucket access`)
carry the identical `user:/project:/circle:/system` list — multiple users
AND multiple circles per element, every circle membership of a caller counts,
`system` is admin-granted only, and the list governs *use* (reads and runs;
bucket objects are read+write) while lifecycle writes stay with the owner
or owning project. Per-project external storage bindings are
never shareable: they carry credentials and stay project-locked.

### Mount Types

| Flag | Purpose | Inside Container |
|------|---------|-----------------|
| `--mount-mcp SRC:DST` | Custom MCP server code | `/mcp-servers/` (read-only) |
| `--mount-data SRC:DST` | Project data directory | `/project/` (read-only) |
| `--mount-rag SRC:DST` | Pre-built RAG index | `/rag-index/` (read-only) |

All mounts are read-only except `/scratch/` (ephemeral workspace).

## Public Cloud Endpoints

Use any OpenAI-compatible cloud API without UMRS infrastructure.

### Supported Providers

| Provider | Command |
|----------|---------|
| OpenAI | `r4 up --public openai --api-key sk-xxxx --model gpt-4o` |
| Google Gemini | `r4 up --public gemini --api-key AIza-xxxx --model gemini-2.0-flash` |
| AWS Bedrock | `r4 up --public bedrock --model anthropic.claude-sonnet-4-6` |
| Groq | `r4 up --public groq --api-key gsk_xxxx --model llama-3.3-70b` |
| Together AI | `r4 up --public together --api-key xxxx --model meta-llama/Meta-Llama-3-70B` |
| DeepSeek | `r4 up --public deepseek --api-key xxxx --model deepseek-chat` |
| Mistral | `r4 up --public mistral --api-key xxxx --model mistral-large-latest` |
| OpenRouter | `r4 up --public openrouter --api-key xxxx --model anthropic/claude-sonnet-4-6` |

### Custom Endpoint URL

```bash
r4 up --public http://my-server:8080 --api-key xxxx --model my-model
```

### Remove Public Endpoint

```bash
r4 down --public
```

See [Public Endpoints](../r4/public-endpoints.md) for full provider details.

## Multi-Source Routing

R4 can connect to multiple backends simultaneously. See [Chapter 11 — Multi-Source Routing](11-sources.md).

## Python SDK

```python
from r4 import Client

client = Client()

# List endpoints
endpoints = client.endpoints.list()

# Provision
endpoint = client.endpoints.up(model="my-qwen", wait=True)

# Status
status = client.endpoints.status(endpoint.id)

# Teardown
client.endpoints.down(endpoint.id)
```

## Next Steps

- [Chapter 7 — RAG](07-rag.md)
- [Chapter 10 — Vision](10-vision.md)
- [Chapter 11 — Multi-Source Routing](11-sources.md)
