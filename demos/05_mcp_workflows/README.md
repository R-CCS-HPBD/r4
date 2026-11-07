# Demo 05: MCP & Workflows

Inspect MCP servers and tools, create and install Hatch packages (including
the default platform registry), validate mounted MCP manifests, and list
workflows.

## What It Shows

- `r4 mcp list --servers / --tools` — MCP server and tool discovery
- `r4 mcp hatch create / list` — Hatch package lifecycle
- `r4 mcp hatch search / install / connect` — default platform Hatch registry
  (`mcp-servers/hatch-registry`: platform-tools, rag-search, knowledge-tools)
- Custom MCP manifest validation
- `r4 workflow list` — workflow inventory

## Default Platform Registry

The platform ships a static Hatch registry at `mcp-servers/hatch-registry`:

```bash
export R4_HATCH_REGISTRIES=/path/to/r4/mcp-servers/hatch-registry
r4 mcp hatch search
r4 mcp hatch install knowledge-tools
r4 mcp hatch connect knowledge-tools   # requires MCP_URL_ALLOWLIST on UMRS
```

On `umrs-front` deployments the same three packages are launched and
registered automatically by the Ansible profile
(`umrs_front_default_mcp_enabled`), so `r4 mcp list --servers` shows
`default-platform-tools`, `default-rag-search`, and `default-knowledge-tools`
out of the box.

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. MCP surface
   ```bash
   r4 mcp list --servers
   r4 mcp list --tools
   ```
2. Hatch registry
   ```bash
   export R4_HATCH_REGISTRIES=/path/to/r4/mcp-servers/hatch-registry
   r4 mcp hatch search
   r4 mcp hatch list
   # Entries seen live: hatch-platform-tools, hatch-rag-search, hatch-knowledge-tools
   ```
3. Create a Hatch package in the scratch dir, list it, and validate a custom
   manifest (the demo maintains a template `.state` workspace).
4. Workflow inventory
   ```bash
   r4 workflow list
   # empty for YOUR identity — workflows register per-user (a fresh account lists no rows;
   # workflows owned by other users are not listed for your identity)
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No workflows registered` (empty) | listing is per-identity (user-owned workflows) | nothing wrong — register one (`r4 workflow upload …`) |
| `hatch search` returns nothing | registry path missing | set `R4_HATCH_REGISTRIES` |
