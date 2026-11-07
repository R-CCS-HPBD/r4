# R4 MCP Servers

Model Context Protocol (MCP) servers for tool integration with the R4 + UMRS platform.

> **Design position (2026-09-22).** In the design
> ([../docs/R4-UMRS-DESIGN.md](../docs/R4-UMRS-DESIGN.md) §"Custom MCP servers
> via Hatch/Hatchling") a group's tools are wrapped once as MCP servers, built
> and published with Hatch, discovered and run with Hatchling, registered in
> UMRS with placement and trust class, and named by the Scientific Catalog.
> This package provides the platform's built-in servers, the default Hatch
> registry (`hatch-registry/`, six packages incl. the RIKYU and Fugaku stdio
> adapters) and the runtime bridge used by `r4 mcp hatch …`. Placement,
> trust classes, lifecycle accounting and catalog membership are gap rows
> D-19…D-24 in [../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md).
> "Gateway" below is today's HTTP edge in front of UMRS.

## Overview

MCP enables LLMs to interact with external tools and data sources through a standardized protocol.
These servers provide sandboxed access to:

- **Filesystem**: Read/write files within allowed directories
- **Database**: Execute SQL queries with read-only restrictions
- **Web Fetch**: Retrieve URLs from allowlisted domains
- **Code Execution**: Run Python/shell in isolated environments
- **Platform**: Query platform-facing Gateway resources such as jobs, workflows, RAG, models, and endpoints
  (job, workflow and RAG tools are generated from the UMRS agent manifest; see below)
- **RAG Proxy**: RAG search, document listing, and `rag_status` health/index diagnostics
- **Vision**: Vision-oriented MCP server implementation available in the built-in server set

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        LLM (vLLM/LiteLLM)                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │ Tool calls
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Platform Gateway                           │
│                      /gw/mcp/* routes                           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Filesystem MCP │  │   Database MCP  │  │  Web Fetch MCP  │
│   (sandboxed)   │  │   (read-only)   │  │  (allowlisted)  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## Installation

```bash
pip install r4-mcp
```

Or for development:

```bash
cd mcp-servers
pip install -e .
```

## Quick Start

### Running a Server

```bash
# Start filesystem server
r4-mcp filesystem --root /data/shared --readonly

# Start database server
r4-mcp database --dsn postgresql://user:pass@host/db

# Start web fetch server
r4-mcp webfetch --allowlist domains.txt

# Start platform operations server
r4-mcp platform --gateway-url http://gateway:8080 --api-key YOUR_API_KEY

# Prefer env vars for tokens so credentials do not appear in process args
R4_GATEWAY_TOKEN=YOUR_API_KEY r4-mcp platform --gateway-url http://gateway:8080
R4_GATEWAY_TOKEN=YOUR_API_KEY R4_PROJECT_ID=my-project \
  r4-mcp rag-proxy --gateway-url http://gateway:8080
```

### Using with Gateway

MCP servers register with the Gateway and are invoked automatically when the LLM
requests tool calls:

```python
from r4 import Client

client = Client()

# LLM can now use registered MCP tools
response = client.chat(
    messages=[{"role": "user", "content": "Read the contents of /data/report.txt"}],
    tools="auto",  # Enable all registered MCP tools
)
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_TRANSPORT` | Transport type (stdio, sse) | `stdio` |
| `MCP_LOG_LEVEL` | Logging level | `INFO` |
| `MCP_REGISTRY_URL` | Server registry URL | `http://gateway:8080/gw/mcp/registry` |

### Server-Specific Configuration

Built-in server implementations currently live under
`mcp-servers/src/r4_mcp/servers/`:

- `filesystem.py`
- `database.py`
- `webfetch.py`
- `code.py`
- `platform.py`
- `rag_proxy.py`
- `vision.py`

The `r4-mcp` CLI currently exposes `filesystem`, `database`, `webfetch`,
`code`, `platform`, and `rag-proxy` directly. The vision server is present in
the built-in server set but is not yet wired into the top-level CLI entry point
in this repository.

#### Platform tools from the UMRS agent manifest (ADR-105 D-2)

`servers/generated_platform.py` builds one tool per `mcp-eligible` row of
`contracts/umrs-http/v1/agent-tools.json` (mirrored byte-exact into
`src/r4_mcp/contracts/` by `python tools/umrs_agent_manifest.py --write`;
`--check` and the repo-root test fail on a stale mirror). The tool name is the
row's `operation_id`, the `inputSchema` is the operation's path and query
parameters plus its JSON body (`$ref` inlined), and the call goes to
`<gateway-url>/gw/<path without /umrs>` with the Bearer token. Identity inputs
(gateway headers, `caller_*` / `is_admin` query, MVP-only owner fields, the
client's session header) are never offered. `companion-only` rows (ADR-030,
e.g. `workflow_commit`) never become MCP tools; `rag_upload` (multipart) is
skipped. Workflow tools keep the local `R4_WORKFLOW_POLICY_FILE` veto
(`workflow_plan` counts as `describe`). The remaining platform tools
(`endpoint_*`, `model_*`, `platform_health`, `platform_guide`,
`workflow_upload`, `workflow_wait`, `workflow_trigger`) are hand-written until
their operations join the manifest.

## Security

All MCP servers implement strict sandboxing:

- **Filesystem**: Chroot-style path restrictions, optional read-only mode
- **Database**: Query validation, read-only connections, result size limits
- **Web Fetch**: Domain allowlisting, size limits, timeout enforcement
- **Code Execution**: Container isolation, resource limits, no network access
- **Platform**: Gateway-scoped access using the caller's configured URL and credentials
- **RAG Proxy**: Gateway-scoped, uses caller's project context; `rag_status` exposes index health without write access
- **Vision**: Subject to the same MCP server sandboxing and registration controls as other built-ins

## Development

### Creating a New MCP Server

```python
from r4_mcp import MCPServer, Tool, ToolResult


class MyServer(MCPServer):
    name = "my-server"
    version = "1.0.0"

    @Tool(
        name="my_tool",
        description="Does something useful",
        parameters={"input": {"type": "string", "description": "Input value"}},
    )
    async def my_tool(self, input: str):
        result = process(input)
        return ToolResult.success(result)


if __name__ == "__main__":
    MyServer().run()
```

### Running Tests

```bash
pytest tests/ -v
```

## License

Apache 2.0

### Vision Server

Run the SAM3 segmentation vision server (requires pre-staged model weights):

```bash
# Using CLI
r4-mcp vision --model-path /models/sam3.pt --device cuda

# Override max image size
r4-mcp vision --model-path /models/sam3.pt --max-image-size 104857600

# Using env vars (recommended for credentials)
UMRS_SAM3_MODEL_PATH=/models/sam3.pt r4-mcp vision --device cuda
```

Arguments:
| Flag | Default | Description |
|---|---|---|
| `--model-path` | `UMRS_SAM3_MODEL_PATH` or `VISION_MODEL_PATH` | Path to pre-staged SAM3 weights |
| `--device` | `cuda` | Inference device (`cuda` or `cpu`) |
| `--output-dir` | `/tmp/vision_outputs` | Directory for output files |
| `--max-image-size` | `52428800` (50 MB) | Maximum input image size in bytes |

## Remote proxy — registering standard remote MCP servers

The UMRS registry proxy speaks a fixed dialect per registered server
(`GET {url}/tools`, `POST {url}/tools/call` JSON-RPC — see
`umrs/src/umrs/api/mcp.py`). Standard remote MCP servers (context7, deepwiki,
microsoft-docs, Hugging Face, …) instead expose a single streamable-HTTP
endpoint with SSE replies, so they cannot be registered directly. The
`remote-proxy` runner bridges any number of them behind one port, one path
prefix per upstream, reusing a Copilot-style `mcpServers` config file. Both
provider classes are supported:

- **`type: "http"`** (public providers: context7, deepwiki, microsoft-docs,
  Hugging Face) — client handles streamable-HTTP with SSE replies,
  `MCP-Session-Id` re-handshake on 404, `${VAR}` expansion in headers (unset
  variables drop the header).
- **`type: "stdio"`** (local providers: playwright, repomix, mcp-server-fetch,
  …) — one long-lived child per upstream (NDJSON stdio handshake +
  `tools/*`), respawned automatically on death; `${VAR}` expansion in `env`.

**Per-entry `enabled` flag (2026-10-01):** any entry may carry
`"enabled": false`; the proxy skips it. The r4 TUI `/mcp` floating pane
toggles this flag live (bridge RPC `set_mcp_server_enabled`, atomic config
rewrite). The proxy reads the file at startup — restart it after toggles.

**Security boundary:** stdio entries execute arbitrary commands **on the host
that runs the proxy**. Only point `--config` at operator-controlled files;
the proxy host's config is the trust root for every local provider.

```bash
# bridge every http entry in the user config (e.g. ~/.config/r4/mcp-servers.json)
r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json --host 0.0.0.0 --port 50800

# or a single upstream without a config file
r4-mcp remote-proxy --name context7 --url https://mcp.context7.com/mcp --port 50800

# then register each upstream (one registration per name, http or stdio alike)
r4 mcp register context7 --url http://<host>:50800/context7 \
    -d "Up-to-date library docs (bridged)"
```

After registration the companion picks the tools up as first-class
`mcp.<server>.<tool>` entries at session start (or after the `mcp_refresh`
tool). Important placement rules:

- **The registered URL is resolved by the UMRS server, not by the client.**
  Run the proxy on the UMRS host (e.g. as a `systemd --user` unit like the
  default MCP set) or somewhere it can reach; `localhost` works only when the
  proxy runs on the same host. A hostname (not a literal IP) is required —
  the SSRF guard refuses literal private/loopback IPs on the registration
  route.
- Registration is project-scoped for non-admin identities; the default trust
  class (`local-safe`) applies. `trusted-host-only` remains admin-only and is
  call-time refused on every existing lane (gap D-19).
- Tools are cached per upstream for `--tools-ttl` seconds (default 300);
  upstream sessions are re-handshook automatically on a 404.

## Ping / Heartbeat

All built-in servers respond to the MCP `ping` method:
```json
{"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 1}
```
Response:
```json
{"jsonrpc": "2.0", "result": {}, "id": 1}
```
This is used by the Gateway health checker and the Hatchling bridge keepalive.

## Transport Modes

Built-in servers support:
- **stdio** — Default for local MCP host integration
- **SSE** — HTTP-based Server-Sent Events for containerized deployments

Set via CLI:
```bash
r4-mcp custom --transport sse --host 0.0.0.0 --port 8080
```

The Gateway proxies SSE connections at `/gw/mcp/sse/connect` and `/gw/mcp/sse/post`.
