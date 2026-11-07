# R4 CLI Command Taxonomy

## Namespace Organization

### Core (no namespace)

Direct user-facing commands requiring zero ceremony:

| Command | Purpose |
|---------|---------|
| `r4` | Launch companion TUI (default) |
| `r4 chat` | Quick chat message |
| `r4 health` | Platform health check |
| `r4 model` | Model management |
| `r4 endpoint` | Endpoint lifecycle |
| `r4 up` / `r4 down` | Quick provision/teardown |
| `r4 config` | Profile configuration |
| `r4 auth` | Authentication |
| `r4 rag` | RAG operations |
| `r4 mcp` | MCP server management |
| `r4 project` | Project management |
| `r4 job` | Job inspection |
| `r4 workflow` | Workflow operations |

### Conversational Surfaces

AI-powered interaction modes:

| Command | Purpose |
|---------|---------|
| `r4 agent` | AI workflow agent (chat, run, create, tools) |
| `r4 search` | Agentic multi-hop search |
| `r4 team` | Multi-agent HPC collaboration |

### Operator / Admin

Governance and operational commands grouped under `r4 admin`:

| Command | Purpose |
|---------|---------|
| `r4 admin governance` | Quotas, ownership, audit |
| `r4 admin trace` | Request audit trail |
| `r4 admin map` | Connection/security map |
| `r4 admin pool` | Roster-based endpoint ops |
| `r4 admin source` | Endpoint source management |

### Execution Tracking

| Command | Purpose |
|---------|---------|
| `r4 run` | A2L agent execution tracking |

## TUI Slash Commands

All CLI surfaces have TUI equivalents:

| Slash | CLI Equivalent |
|-------|---------------|
| `/agent` | `r4 agent` |
| `/search` | `r4 search` |
| `/team` | `r4 team` |
| `/run` | `r4 run` |
