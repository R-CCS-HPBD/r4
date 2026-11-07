# R4 Library Group Structure

R4 is a platform made from a small group of libraries, services, and runtime assets. The goal is to keep user experience simple while avoiding a monolith behind the scenes.

Use this document when deciding where a new model, command, DTO, validator, service, or deployment script should live.

## Short Version

| Layer | Home | Owns | Does Not Own |
|---|---|---|---|
| User experience | `r4/` | CLI, SDK, notebook helpers, companion/chat UX | Durable state, workflow execution, endpoint lifecycle |
| Shared contracts | `r4-contracts/` | DTOs, public schemas, enum-like values, local validation helpers | Persistence, orchestration, heavy dependencies |
| API edge | `gateway/` | Auth, routing, rate limits, audit, policy edge | UMRS state machines or RiVault lifecycle |
| Durable platform | `umrs/` | Jobs, attempts, files, endpoints, models, workflows, RAG, context | R4 client UX or Gateway edge concerns |
| Tool surface | `mcp-servers/` | Platform-managed MCP tools | Endpoint or job orchestration |
| Interactive app surface | `mcp-apps/` | Optional UI components for MCP-capable hosts | Core control-plane state |
| Runtime infrastructure | Mini-RiVault assets under `umrs/` and deployment dirs | vLLM/LiteLLM launch templates, runtime env, site profiles | User CLI behavior |

The two most important rules are:

- `r4` may depend on `r4-contracts`, but it should not import UMRS.
- UMRS must remain independently workable without `r4`.

## Runtime Flow

```text
User / notebook / automation
        |
        v
r4 CLI and SDK
        |
        v
Platform Gateway
  - authentication
  - project scoping
  - audit and rate limits
        |
        v
UMRS
  - files and object storage
  - durable jobs and attempts
  - model registry
  - endpoint registry and orchestration
  - workflow and RAG services
        |
        v
Mini-RiVault runtime
  - LiteLLM API facade
  - vLLM model server
  - optional RAG and MCP tools inside the runtime boundary
```

## Why `r4-contracts` Is Separate

`r4-contracts` exists because both clients and platform services need to speak the same public language without making the client import the server.

In scope:

- recipe definition schemas and validation
- schedule and cron validators
- skill manifest schemas
- pre-flight security scanners
- MCP server and tool DTOs
- lightweight API DTO helpers

Out of scope:

- durable jobs and retry semantics
- endpoint provisioning and registry state
- workflow execution
- RAG indexing and retrieval
- connector implementations
- database stores and background daemons

The package is intentionally stdlib-only. That keeps client installation small and makes it safe to use in docs tooling, local validation, tests, or future thin clients.

## Import Rules

| From | May Import | Must Avoid |
|---|---|---|
| `r4/` | `r4_contracts`, stdlib, optional UX integrations behind extras | `umrs`, Gateway internals, server stores |
| `r4-contracts/` | Python stdlib only | `r4`, `umrs`, `httpx`, Pydantic, FastAPI, ML libraries |
| `gateway/` | Gateway-local policy code, UMRS HTTP APIs, shared DTOs if useful | UMRS internal stores or orchestrator classes |
| `umrs/` | UMRS-owned modules, backend SDKs, runtime adapters | `r4` client package |
| `mcp-servers/` | Tool-local helpers and stable platform contracts | UMRS durable internals unless explicitly mediated by APIs |

If a client-visible shape changes, update the server-side authoritative validation and the shared contract package together. The server remains the final gate; client validation is a fast pre-flight check.

## UMRS Separation Strategy

UMRS should keep ownership of the durable platform, but it can still become more modular. A useful future split is by dependency and deployment weight rather than by product branding.

| Candidate Module | Good Direction | Notes |
|---|---|---|
| `umrs-core` | Durable jobs, stores, workflows, models, files, RAG contracts | Still the authoritative platform owner |
| `umrs-server` | FastAPI/server entrypoints, admin CLI, migrations | Depends on `umrs-core` |
| `umrs-orchestrators` | Slurm, Kubernetes, Compose targets and wrappers | Backends can be optional extras by site |
| `umrs-connectors` | GitHub, Slack, Google Drive, Notion, S3, custom source connectors | Natural plugin boundary; keep emitted document contracts stable |
| `umrs-inference-adapters` | HMAE, SAM3, sentence-transformers, OpenAI-compatible adapters | Heavy ML dependencies stay optional |
| `rivault-runtime` | Mini-RiVault image/build assets and launch templates | UMRS still provisions it; runtime packaging can be separate |

Avoid extracting a package if doing so would hide an important invariant. Durable job attempts, endpoint registry state, workflow execution, model registry policy, and RAG indexing semantics should remain UMRS-owned even if their implementation is split into smaller UMRS packages.

## Deployment Install Sets

### End-User Client

Users need only `r4`. In normal packaging, `r4-contracts` is installed as a dependency.

```bash
./scripts/setup_r4_client.sh
export PATH="$HOME/.local/bin:$PATH"
r4 auth login --gateway-url https://<gateway-host> --project <project-id> --oidc
```

### Developer Workspace

The repository uses a workspace layout. For local development, install the relevant workspace packages together.

```bash
uv sync --package r4

# Or with pip/editable installs:
python -m pip install -e ./r4-contracts -e ./r4
```

### Shared Server Deployment

Server-side virtual environments commonly include `r4` for operator smoke tests, but UMRS must not depend on `r4` at runtime.

```bash
python -m pip install \
  -e ./r4-contracts \
  -e ./r4 \
  -e ./umrs/src
```

For RCCS-cloud and similar Ansible deployments, the editable refresh step should run every deploy, even when the venv already exists. This prevents a successful code update from leaving new packages such as `r4_contracts` unavailable at runtime.

### Offline Or Air-Gapped Deployments

Build or mirror wheels for matching versions of:

- `r4-contracts`
- `r4`
- `umrs`
- Gateway sidecar/package artifacts
- MCP server artifacts
- any selected UMRS optional extras, such as orchestrator or inference adapter dependencies

Do not rely on runtime downloads inside Mini-RiVault inference containers. Models must be pre-staged or downloaded through controlled UMRS jobs.

## Design Checklist

Before adding code, ask:

1. Is this a public data shape or local pre-flight validation? Put it in `r4-contracts` and revalidate authoritatively in UMRS.
2. Is this durable state, retry behavior, endpoint lifecycle, workflow execution, model registry policy, or RAG semantics? Keep it in UMRS.
3. Is this user ergonomics around an existing API? Put it in `r4`.
4. Is this authentication, request routing, audit, rate limiting, or external boundary policy? Put it in Gateway.
5. Is this a source connector, inference adapter, or runtime backend with heavy dependencies? Prefer a UMRS-owned optional module or plugin boundary.
6. Would importing the package surprise a user by pulling in server, ML, or cluster dependencies? Split the contract from the implementation.

## Related Docs

- [R4 docs index](r4/index.md)
- [Server-side and client-side deployment](SERVER_CLIENT_DEPLOYMENT.md)
- [Integration policy](INTEGRATION_POLICY.md)
- [Architecture](../ARCHITECTURE.md)
