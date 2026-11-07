---
name: mcp_tools
description: "Discovers, invokes, and registers MCP (Model Context Protocol) servers and tools. Use for mcp, tool server, function calling, mcp_invoke tasks."
allowed-tools: "mcp_list_servers mcp_list_tools mcp_invoke mcp_register_server mcp_unregister_server mcp_refresh"
metadata:
  x-r4-keywords: "mcp, tool server, invoke tool, mcp server, tool calling, function calling"
---

You work with MCP servers registered on the platform.

## Invocation workflow (always in this order)

1. `mcp_list_servers` — discover registered servers and pick the right one.
2. `mcp_list_tools` — read the tool names, descriptions, and input schemas
   BEFORE calling anything; arguments must be valid JSON matching the
   tool's inputSchema.
3. `mcp_invoke` — call with the exact argument names from the schema.

Never guess tool names or argument shapes; a wrong-name call is a wasted
turn. If mcp_list_tools shows the needed tool missing, register the server
first instead of improvising around it.

## Registration

- URL-based servers: `mcp_register_server` with the server URL, then VERIFY
  with `mcp_list_tools` — a silent registration failure must not surface as
  a failed invoke later.
- Remove with `mcp_unregister_server`.
- Custom mounted servers (inside mini-RiVault) need a config.json declaring
  capabilities + sandbox: filesystem=read_only, network=egress_guard, no
  shell entry points, entry point inside the mounted directory.

## Rules

- MCP tool calls do not bypass EgressGuard; external network access works
  only for domains on the endpoint's egress allowlist.
- On invoke errors, surface the raw JSON error verbatim — do not paraphrase
  protocol errors into friendly guesses.
