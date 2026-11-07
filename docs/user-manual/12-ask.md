# 12. Agent, Search & Team

Advanced AI interfaces beyond simple chat — including an agentic workflow
builder, multi-step search, and multi-agent collaboration. These are now
**top-level commands** (the old `r4 ask` umbrella has been removed).

> **Migration note:** `r4 ask agent`, `r4 ask search`, `r4 ask teamup`, and
> `r4 ask companion` are no longer available. Use `r4 agent`, `r4 search`,
> `r4 team`, and `r4` (no args) respectively.

## Agent (`r4 agent`)

The agentic workflow builder helps scientists describe goals in natural language
and automatically builds executable workflows.

### Agent Chat

Interactive agent that guides you through task planning:

```bash
r4 agent chat
> I want to analyze protein structure data from PDB files
```

### Agent Run

Plan and execute a workflow from a **natural-language objective**:

```bash
r4 agent run "triage the failing workflow"
r4 agent create "daily sync of the lab S3 bucket"   # build without running
```

### Agent Sessions, Tools & Audit

```bash
r4 agent sessions
r4 agent tools [--search rag]
r4 agent scan ./notes.md      # Scan content for sensitive data
r4 agent audit                # Your egress audit trail
```

> `r4 agent` is the legacy SciWorkflow Agent. New agentic work — and the
> `r4 acp` editor bridge — runs through the Research Companion instead.

### Features

- **Natural language interface** — Describe your goal, the agent builds the workflow
- **MCP tool discovery** — Agent finds and uses appropriate tools automatically
- **Egress surveillance** — All outbound data requests require user approval
- **Workflow persistence** — Save, share, and replay workflows

## Search (`r4 search`)

Multi-step reasoning search across code and documentation:

```bash
# Basic search
r4 search "find endpoint provisioning code"

# Control scope
r4 search "RAG implementation" --scope code_only

# Control depth
r4 search "authentication flow" --depth deep

# Limit steps
r4 search "error handling patterns" --max-steps 5
```

### Search Scopes

| Scope | Description |
|-------|-------------|
| `code_only` | Search code files only |
| `rag_only` | Search RAG documents only |
| `web_only` | Search the web only |
| `lab_only` | Lab data + RAG only (no web) |
| `all` | Lab + web + code (default) |

Additional flags: `--project/-p` (project for RAG), `--max-sources N`,
`--no-citations`, `--no-web|--no-rag|--no-code`, `--stream`, `--json`,
`--source NAME` (endpoint source for LLM steps).

### Search Depths

| Depth | Description |
|-------|-------------|
| `shallow` | Quick, single-pass search |
| `moderate` | Multi-pass with context expansion (default) |
| `deep` | Deep multi-step reasoning |

### Web and Code Providers

Web and code search resolve registered MCP tools through the Gateway. Recognized
tool names are `web_search`, `brave_search`, `code_search`, and `grep_repo`.
Missing capabilities are reported explicitly instead of silently returning no
results. Programmatic code searches may use a bounded local regex fallback only
when paths are supplied explicitly.

## Team (`r4 team`)

Multi-agent collaboration for complex tasks:

### Create a Team

```bash
r4 team create "code-review" \
  --code ./src/ \
  --goal "Review code quality and suggest improvements"
```

### List, Run, and Control

```bash
r4 team list
r4 team run <session-id> --iterations 5
r4 team status <session-id>
r4 team pause <session-id>
r4 team resume <session-id>
r4 team cancel <session-id>
r4 team logs <session-id>
r4 team report <session-id>
```

### Engines and Hardware

```bash
r4 team engines               # List installed team engines
r4 team hardware              # List hardware profiles
r4 team launch --spec spec.yaml   # Spec-driven run; supersedes create+run
```

## Summary

```
r4 agent              # Legacy agentic workflow builder
├── chat              # Interactive agent chat
├── run OBJECTIVE     # Plan+run workflow from natural language
├── create            # Create workflow from description
├── sessions          # List agent sessions
├── tools             # List available tools
├── scan              # Scan content for sensitive data
└── audit             # Egress audit trail

r4 search QUERY       # Multi-step reasoning search

r4 team               # Multi-agent collaboration
├── create / launch   # Create session / spec-driven launch
├── list / status     # List / inspect sessions
├── run               # Execute iterations
├── pause / resume / cancel
├── logs / report
├── crews             # Crews: hpc (a session) or the plan template each runs
└── engines / hardware
```

## Chat-driven demo preparation

The Companion (terminal `r4` or the webview chat) can prepare the platform's
configured demos from natural language:

> "Prepare the example demo" / "Set up the demo pipeline"

The agent runs `demo_plan` (checklist with resolved paths), confirms the
source/project and real-vs-synthetic data via `ask_user`, then executes
`demo_prepare` steps — `upload_bundle` → `stage` → `trigger` — and follows up
with `workflow_status` / `workflow_results`.

Demos are configured by a demo-spec JSON file named in `R4_DEMO_SPECS`.
Env knobs when demo assets live outside the repo checkout:
`UMRS_PRIVATE_DEMOS`, `R4_DEMO_REPO_ROOT`, and per demo
`<NAME>_BUNDLE_DIR` / `<NAME>_VIDEO_DIR` (the demo name upper-cased,
non-alphanumerics as `_`).

## Project status at a glance

Ask the Companion "show project status" (tool `project_status`) or type
`/overview` [PROJECT] in the TUI. One read-only snapshot: platform health,
endpoints, model registry, registered workflows + running jobs, MCP servers
and tool counts, knowledge graphs, research loops/sessions, RAG documents,
and the scientific catalog. Individual sections degrade independently when a
surface is unavailable (`/status` remains the live dashboard alias).

## Next Steps

- [Chapter 13 — CLI Reference](13-cli-reference.md)
- [Chapter 8 — MCP Tools](08-mcp.md)
