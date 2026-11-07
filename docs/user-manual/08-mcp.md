# 8. MCP Tools

MCP (Model Context Protocol) tools extend LLM capabilities with external actions like file access, database queries, web fetching, and code execution.

## Overview

| Term | Meaning |
|------|---------|
| **MCP Server** | Backend tool provider |
| **MCP Tool** | Individual action exposed by a server |
| **MCP App** | Optional UI extension rendered in MCP-enabled hosts |

## Platform-Managed MCP Servers

Curated tools running inside the platform:

| Tool | Capability | Security |
|------|------------|----------|
| `filesystem` | Read files in project S3 bucket | Project-scoped, read-only |
| `database` | Query PostgreSQL (read-only) | Parameterized queries |
| `webfetch` | Fetch URLs | Domain allowlist enforced |
| `code` | Python sandbox execution | Isolated, no network, resource limits |

> MCP servers run INSIDE Mini-RiVault containers. All external access goes through EgressGuard.

## MCP Commands

```bash
# List registered MCP servers
r4 mcp list --servers

# List all available tools
r4 mcp list --tools

# List tools from a specific server
r4 mcp list --tools --server filesystem

# Check server health
r4 mcp health <server-id>

# Get server details
r4 mcp info <server-id>
```

## Registering Custom MCP Servers

### Remote Registration

```bash
r4 mcp register my-tool \
  --url https://my-lab-vm:8080/mcp \
  --description "Lab internal data tool"
```

### Mount-Based Registration

Mount a custom MCP server when provisioning an endpoint:

```bash
r4 endpoint up --model my-qwen \
  --mount-mcp /home/user/my-mcp-server:/mcp-servers/my-mcp
```

Mounted servers must include a `config.json` manifest:

```json
{
  "name": "my-custom-mcp",
  "version": "1.0.0",
  "entry_point": "python server.py",
  "capabilities": {
    "tools": ["search_database", "fetch_api"],
    "tool_capabilities": {
      "search_database": ["STANDARD_USER"],
      "fetch_api": ["WRITE_REMOTE"]
    },
    "egress": {
      "domains": ["api.example.com"],
      "protocols": ["https"]
    }
  },
  "sandbox": {
    "network": "egress_guard",
    "filesystem": "read_only",
    "max_memory_mb": 512,
    "max_runtime_sec": 30
  }
}
```

### Unregistering

```bash
r4 mcp unregister <server-id>
```

## Hatchling MCP Packages

R4 integrates with [Hatchling](https://github.com/CrackingShells/Hatchling) for streamlined MCP server development and sharing.

### Create a New MCP Package

```bash
r4 mcp hatch create my-tool --description "My custom tool"
```

This scaffolds a new MCP package with the `@hatch_mcp.tool()` decorator pattern.

### Install from Registry

```bash
r4 mcp hatch install chem-tools --from registry
```

### Share Packages

```bash
# Share within your project via UMRS storage
r4 mcp hatch share my-tool --project team-alpha
```

### Search, List, Connect, Disconnect

```bash
r4 mcp hatch search chemistry   # Search available packages
r4 mcp hatch registry list      # Inspect configured registries
r4 mcp hatch list
r4 mcp hatch connect my-tool
r4 mcp hatch disconnect my-tool
r4 mcp hatch uninstall my-tool
```

## Security Model

### Capability Declarations

Custom MCP servers must declare capabilities:

- `tools`: List of tool names provided
- `tool_capabilities`: Required permission flags per tool
- `egress.domains`: Allowlisted external domains
- `sandbox.network`: Must be `egress_guard`
- `sandbox.filesystem`: Must be `read_only`

### Restrictions

- Entry point may NOT invoke a shell (`sh`, `bash`, etc.)
- Entry point script path must stay within the mounted directory
- Zero/negative memory or runtime limits are rejected
- Missing `capabilities` or empty `tools` list causes load-time failure

## Python SDK

```python
from r4 import Client

client = Client()

# List servers
servers = client.mcp.list_servers()

# List tools
tools = client.mcp.list_tools()

# Invoke a tool
result = client.mcp.invoke("filesystem", "read_file", {"path": "/data/results.json"})

# Chat with MCP tools available
response = client.mcp.chat_with_tools(
    "Search the database for protein structures",
    tools=["database.query", "filesystem.read_file"],
)
```

## Next Steps

- [Chapter 9 — Workflows & Jobs](09-workflows-jobs.md)
- [Chapter 12 — Ask: Agent, Search & Teamup](12-ask.md)
