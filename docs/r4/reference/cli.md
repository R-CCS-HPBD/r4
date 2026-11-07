# R4 CLI Reference

This page is a stable map of the command groups and ownership boundaries. For exact flags, use the help shipped with the installed command:

```bash
r4 --help
r4 <group> --help
```

## Command Ownership

| CLI | Audience | Boundary |
|---|---|---|
| `r4` | End users, notebook users, workflow authors | Gateway-mediated user workflows |
| `umrs` | Operators and developers | Direct UMRS diagnostics and migration helpers |
| `umrs-server admin` | Site admins on trusted hosts | Offline user, token, Slurm account, and daemon-local administration |

Start with `r4` unless you are operating or debugging the UMRS service itself.

## Interactive TUI (no subcommand)

Running `r4` with no subcommand launches the JS (Ink) companion TUI.

```bash
r4                         # launch companion TUI
r4 companion               # same
r4 -p "hello"              # one-shot prompt and exit
r4 -p "task" --yolo        # one-shot with auto-approved tool calls
r4 --mode plan             # launch in planning mode
```

**Requirements:** Node.js ≥ 18; JS bundle at `r4/companion-tui-js/dist/`.

### Root-Level Flags

| Flag | Default | Description |
|---|---|---|
| `-p / --prompt TEXT` | — | Run a single prompt and exit (programmatic mode) |
| `-m / --model NAME` | profile default | Override the model for this session |
| `--mode chat\|plan\|autopilot` | `chat` | TUI interaction mode |
| `--allow-all-tools / --yolo` | off | Auto-approve all tool calls without prompting |
| `--fs-root [PATH]` | — | Enable local filesystem tools (default `.` when flag present) |
| `--secure` | off | Pre-send content scan and per-file attach consent |

### Companion Slash Commands

Inside any companion UI, type `/` to see available commands:

| Command | Aliases | Description |
|---|---|---|
| `/help` | `/h`, `/?` | Show all slash commands |
| `/dashboard` | `/status`, `/refresh` | Backend overview: Gateway, endpoints, jobs, MCP, models |
| `/endpoint list` | | List active endpoints |
| `/endpoint up` | `/up` | Provision or reuse a model endpoint |
| `/endpoint down` | `/down` | Stop an endpoint |
| `/mcp list` | | List registered MCP servers |
| `/mcp tools` | | List all available MCP tools |
| `/rag query` | | Query indexed documents |
| `/rag upload` | | Upload a document for RAG |
| `/job list` | | List durable jobs |
| `/workflow list` | | List registered workflows |
| `/mode chat\|plan\|autopilot` | | Cycle the interaction mode |
| `/models` | `/model` | Show/switch provisioned model (picker on bare `/models`) |
| `/skills` | `/skill` | Manage skill bundles |
| `/usage` | | Billing usage view: calls, tokens, node-hours |
| `/context` | | Context footprint: turns, messages, token totals |
| `/export [path.md]` | | Preview export or save transcript markdown |
| `/save path.md` | | Save transcript markdown to file |
| `/sessions` | `/resume` | Session management helpers |
| `/progress` | | Show harness continuity/progress orientation |
| `/probe` | | Re-scan harness environment |
| `/lessons` | | Show steering lessons (`/lessons clear` to reset) |
| `/template` | `/templates` | List or apply a harness workflow template |
| `/health` | | Check Gateway and endpoint connectivity |
| `/quit` | `/exit`, `/q` | Save session and exit |

Planned (not yet implemented in companion slash dispatcher): `/remember`,
`/memories`, `/forget`, `/milestone`.

`/retry` exists, but transcript presentation semantics are still under review.

For the full TUI reference including mode cycling, session persistence, and JS companion features, see [TUI Reference](TUI_REFERENCE.md).

## Common `r4` Groups

| Group | Purpose |
|---|---|
| `r4 config` | Profiles, gateway URL, project context |
| `r4 auth` | Login, logout, token status |
| `r4 health` | Fast and full readiness checks (config, auth, gateway, umrs, umrs_profile) |
| `r4 chat` | One-shot and streaming chat |
| `r4 project` | Project selection and project-scoped workflows |
| `r4 model` | Model listing, staging, registration, download status; size class for retrieval (`r4 model scale [MODEL]`, `scale set/unset/list`; added 2026-10-10, ADR-093 D-14; see [User Manual ch. 13](../../user-manual/13-cli-reference.md#model-scale)) |
| `r4 endpoint` | Provision, list, inspect, and stop model endpoints |
| `r4 rag` | Upload, inspect, search, and query project data |
| `r4 job` | Durable job inspection and control |
| `r4 mcp` | MCP tool discovery, registration, and Hatchling package management |
| `r4 skill` | Skill registry: search, install, uninstall, list, info |
| `r4 workflow` | UMRS-backed workflow operations |
| `r4 session` | Local and platform session helpers |
| `r4 agent` | Agentic workflow builder |
| `r4 search` | Multi-step reasoning search |
| `r4 team` | Multi-agent collaboration |
| `r4 run` | Agent execution tracking (A2L) |
| `r4 admin` | Admin subcommands: source, governance, trace, map, pool |
| `r4 recipe` | Pipeline recipe management |
| `r4 schedule` | Cron-based workflow scheduling |
| `r4 artifact` | Artifact inspection and download |

## Renamed Commands

The following commands were renamed. The old forms have been **removed**:

| Old form | Current form |
|---|---|
| `r4 init` | `r4 login` or `r4 auth login` |
| `r4 config init` | `r4 login` or `r4 auth login` (deprecated alias) |
| `r4 login` | `r4 login` (top-level alias for `r4 auth login`) |
| `r4 logout` | `r4 auth logout` |
| `r4 sessions` | `r4 session` |
| `r4 ask search` | `r4 search` |
| `r4 ask agent` | `r4 agent` |
| `r4 ask teamup` | `r4 team` |
| `r4 ask companion` | `r4` (no args) |
| `r4 governance` | `r4 governance` (top-level) |
| `r4 trace` | `r4 admin trace` |
| `r4 map` | `r4 map` (top-level) |
| `r4 pool` | `r4 admin pool` |
| `r4 source` | `r4 source` (top-level alias for `r4 admin source`) |
| `r4 agent-exec` | `r4 run` |

Do not document the old names in new user-facing material except when explaining migrations.

## Useful Smoke Sequence

```bash
r4 config show
r4 auth status
r4 health account
r4 model list --limit 10
r4 endpoint list
r4 chat "hello"
```

If `r4 health` passes but authenticated commands fail, run `r4 health account` and refresh the token with `r4 auth login --api-key <token>`.

## `r4 auth login` — Auto-Discovery

During login, `r4 auth login` queries `GET /gw/ops/connect-info` to
auto-discover ZMQ transport parameters (control port, auth type, tunnel mode,
data port range). These are written to the connection profile and used for all
subsequent ZMQ operations. Falls back to `GET /health` `site_capabilities` for
older servers.

## `r4 model list` — Transport & Output

`r4 model list` uses ZMQ by default and falls back to HTTP (`GET /gw/models`)
when ZMQ is unreachable. Terminal output uses ANSI color styling (colored
headers, status indicators); color is suppressed when output is piped or
redirected.
