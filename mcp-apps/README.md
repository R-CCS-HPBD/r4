# RiVault MCP Apps

Interactive UI Applications for the Model Context Protocol (MCP).

> **Brand convention.** The R4 Platform uses three
> intentionally distinct prefixes:
>
> | Layer | Prefix | Examples |
> |---|---|---|
> | User-facing CLI / SDK | `r4` | `r4 chat`, `r4-mcp` (PyPI), `r4-companion` |
> | Service role / config | `rivault` | npm scope `@rivault/*`, k8s `rivault-llm`, role docs |
> | Deployment artifact | `genai` | docker image tag `genai-mini-rivault.sif` |
>
> The npm scope `@rivault/mcp-apps` is therefore correct: this package
> *is* a service-role artifact. Do not rename it to `@r4/...`.
> Any remaining `aisci-*` strings have been migrated to the names above.

## Overview

This package provides rich, interactive UI applications that can be served via MCP's `ext-apps` SDK and displayed in Claude desktop and other MCP hosts. Each app tool returns both data and a UI resource URI for enhanced user experience.

## Apps

### RAG Document Explorer (MA-101 ~ MA-105)
Search and explore documents in your RAG index.
- **Tool**: `search-documents`
- **Features**:
  - Search with relevance scoring
  - View document chunks with context expansion
  - Source document preview
  - Navigate between related chunks

### Job Monitor Dashboard (MA-201 ~ MA-205)
Real-time dashboard for monitoring inference jobs.
- **Tool**: `monitor-jobs`
- **Features**:
  - Active job listing with status
  - GPU/memory utilization charts (planned)
  - Log streaming panel
  - Job lifecycle controls (abort/restart)

### Model Configuration Wizard (MA-301 ~ MA-305)
Configure model parameters with an interactive wizard.
- **Tool**: `configure-model`
- **Features**:
  - Model selection from registry
  - Parameter sliders (temperature, max_tokens, etc.)
  - GPU memory estimation
  - One-click endpoint provisioning

### Embedding Visualization (MA-401 ~ MA-405)
Visualize document embeddings in 2D/3D space.
- **Tool**: `visualize-embeddings`
- **Features**:
  - D3.js scatter plot
  - Cluster coloring
  - Point selection and details
  - t-SNE/UMAP/PCA method selection

## Development

### Prerequisites

- Node.js >= 18
- pnpm (recommended) or npm

### Setup

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Run tests
npm test
```

### Project Structure

```
mcp-apps/
├── apps/                    # App HTML entry points
│   ├── rag-explorer/
│   ├── job-monitor/
│   ├── model-wizard/
│   └── embedding-viz/
├── src/
│   ├── apps/               # App React components
│   │   ├── rag-explorer/
│   │   ├── job-monitor/
│   │   ├── model-wizard/
│   │   └── embedding-viz/
│   ├── components/         # Shared UI components
│   │   └── ui.tsx
│   ├── lib/                # Shared utilities
│   │   ├── hooks.ts
│   │   └── umrs.ts
│   ├── server/             # MCP server entry
│   │   └── index.ts
│   └── styles/             # Global styles
│       └── global.css
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## MCP Server

The MCP server exposes tools with UI capabilities:

```bash
# Run MCP server (stdio transport)
npx tsx src/server/index.ts
```

### Tool Registration

Each tool returns both structured data and a UI resource:

```typescript
{
  content: [
    { type: 'text', text: JSON.stringify(data) },
    {
      type: 'resource',
      resource: {
        uri: 'ui://rivault-mcp-apps/rag-explorer?query=...',
        mimeType: 'text/html',
      },
    },
  ],
}
```

### Resources

UI resources are served from the built `/dist` directory:

- `ui://rivault-mcp-apps/rag-explorer`
- `ui://rivault-mcp-apps/job-monitor`
- `ui://rivault-mcp-apps/model-wizard`
- `ui://rivault-mcp-apps/embedding-viz`

## Docker

Build and run with Docker:

```bash
# Build image
docker build -t rivault-mcp-apps .

# Run container
docker run -p 5173:5173 rivault-mcp-apps
```

## Integration with UMRS

Apps communicate with UMRS via the Platform Gateway:

```typescript
// Proxy configuration in vite.config.ts
proxy: {
  '/umrs': { target: 'http://localhost:8000' },
  '/gw': { target: 'http://localhost:8080' },
}
```

Set environment variables for production:

```bash
VITE_UMRS_URL=http://umrs:8000
VITE_GATEWAY_URL=http://gateway:8080
```

## License

Apache-2.0

## Transport Modes

The MCP Apps server supports two transport modes, selected via the `MCP_TRANSPORT` environment variable:

### stdio (default)
For direct invocation by MCP hosts (Claude Desktop, etc.):
```bash
npm run build
npx tsx src/server/index.ts
```

### SSE
For containerized deployment behind the Platform Gateway:
```bash
export MCP_TRANSPORT=sse
export MCP_PORT=8085
export SSE_ENDPOINT=/mcp
npx tsx src/server/index.ts
```
Gateway routes:
- `GET /gw/mcp/sse/connect` — Establish SSE connection (proxied to mcp-apps)
- `POST /gw/mcp/sse/post` — Forward MCP JSON-RPC message

## Deployment (umrs-front)

The `mcp-apps` container is deployed as part of the umrs-front stack when `UMRS_FRONT_ENABLE_MCP=true`:

```bash
# Ansible
cd deploy/ansible
./bootstrap.sh --profile umrs-front
```

(2026-11-07: `deploy/ansible/` is the R-CCS site converge and exists only in
the development repository. Elsewhere, run the container with your own site
tooling and set the variables below.)

Environment variables injected by Ansible:
| Variable | Default | Description |
|---|---|---|
| `UMRS_FRONT_ENABLE_MCP` | `false` | Enable mcp-apps container |
| `MCP_TRANSPORT` | `stdio` | Transport mode (`stdio` or `sse`) |
| `MCP_APP_PORT` | `8085` | SSE listen port |
| `R4_GATEWAY_TOKEN` | — | Bearer token for Gateway API calls |

## Authentication

All Gateway calls include:
```
Authorization: Bearer ${R4_GATEWAY_TOKEN}
```

When `R4_GATEWAY_TOKEN` is unset, the server runs in dev mode with no auth.
