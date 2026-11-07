# 15. TUI Reference

The R4 companion is a JS (Ink) terminal UI for conversational AI with tool use.

## Launch

Running `r4` or `r4 companion` launches the JS companion TUI.

**Requirements:** Node.js ≥ 18; JS bundle at `r4/companion-tui-js/dist/`.

```bash
r4
r4 companion
r4 companion --resume SESSION_ID
r4 -p "Summarize this project"    # one-shot, no interactive loop
```

## Interaction Modes

The companion supports three interaction modes, switchable at any time:

| Mode | Description | Behavior |
|------|-------------|----------|
| `chat` | Conversational Q&A | Standard chat with current tool-approval policy |
| `plan` | Planning and reasoning | No tools first; picker to auto-approve, manual-approve, or keep planning |
| `autopilot` | Auto-execute tool calls | Every tool runs; it asks only before credential access or a force-push, for a knowledge decision, at your own ask rules, and for a delete it could not back up first (deleted files are copied to `~/.local/share/r4/trash`) |

```bash
# Launch in a specific mode
r4 --mode chat
r4 --mode plan
r4 --mode autopilot

# Switch during session
/mode plan
/mode autopilot
/mode chat
```

## Slash Commands

| Command | Aliases | Description |
|---------|---------|-------------|
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
| `/mode <mode>` | | Set the mode: `chat` (read-only tools run, others ask), `plan`, `autopilot` (Shift+Tab cycles) |
| `/trust` | `/yolo` | Same as `/mode autopilot` (2026-10-31, ADR-104: `/approve` and `/allow-all` are retired) |
| `/models` | `/model` | Show/switch provisioned model (picker on bare `/models`) |
| `/skills` | `/skill` | Manage skill bundles |
| `/decision-model on [model]\|off\|status` | `/decision` | The decision model (`decision_evaluate`: fast choice / score / yes-no judgments; `Bespoke-Nimble-9B`). **On by default since 2026-10-03** wherever the platform serves it (rccs, and every front that routes to it); `off` is saved in your profile and applies from the next message. While on, the session also uses it to read unclear replies to a plan, to ask before an auto-approved risky tool call that looks off-task, and to drop false lessons. It only ever adds caution (2026-10-02) |
| `/usage` | | Billing usage view: calls, tokens, node-hours |
| `/context` | | Context footprint: turns, messages, token totals |
| `/export [path.md]` | | Preview export or save transcript markdown |
| `/save path.md` | | Save transcript markdown to file |
| `/sessions` | `/resume` | Session management helpers |
| `/progress` | | Show harness continuity/progress orientation |
| `/probe` | | Re-scan harness environment |
| `/lessons` | | Show steering lessons (`/lessons clear` to reset) |
| `/template` | `/templates` | List or apply a harness workflow template |
| `/health` | | Check connectivity |
| `/quit` | `/exit`, `/q` | Save session and exit |

Research, governance, and utility commands (shared registry):

| Command | Aliases | Description |
|---------|---------|-------------|
| `/research` | | Autonomous research loop controls |
| `/project` | | Project context and actions |
| `/knowledge` | `/kb` | Knowledge query, ingest, graphs, bundle export |
| `/search` | | Agentic multi-hop search |
| `/team` | | Multi-agent (teamup) sessions |
| `/artifact` | | A2L artifact registry operations |
| `/run` | | A2L agent executions |
| `/governance` | `/gov` | Quotas, ownership, shares |
| `/audit` | | Audit trail view |
| `/trace` | `/traces` | Step-level execution traces |
| `/checkpoint` | | Save/restore loop checkpoints |
| `/recovery` | | Crash-recovery inspection |
| `/replay` | | Replay a recorded execution |
| `/outcome` | | Outcome review for runs |
| `/lineage` | | Artifact/trace lineage graph |
| `/adaptive` | | Adaptive routing settings |
| `/vectors` | | Embedding/vector diagnostics |
| `/visibility` | | Visibility scope controls |
| `/memory` | `/mem` | Retired 2026-10-17: the reflective-memory routes have no gateway location; the command prints a notice |
| `/secret` | | Retired 2026-10-17: the secret-broker routes have no gateway location; the command prints a notice |
| `/pipeline` | | Pipeline composition |
| `/code` | | Code search/actions |
| `/companion` | | Companion session controls |
| `/agent` | | Agent session controls |
| `/compact` | | Compact the conversation context |
| `/history` | | Conversation history |
| `/cost` | | Token/cost breakdown |
| `/share` | | Share session/tooling |
| `/system` | | System-prompt management |
| `/config` | | Configuration helpers |
| `/new` | | Start a fresh session |
| `/clear` | | Clear the screen |
| `/worktree` | `[list]` · `<name>` · `exit [keep\|remove] [--discard]` · `mode [off\|auto]` | Work in an isolated git worktree of this repository (`.r4/worktrees/<name>`, branch `r4/<name>`; status chip `wt:<name>`). The tab's agent, `!` shell, git changes and @-mentions follow it. Refused while an answer streams. A changed worktree is kept on exit unless `--discard` (2026-11-03). `mode auto` (or `r4 config worktree auto`): the agent moves into a worktree by itself before its first write, and the chip and a notice follow. When the session ends, an unchanged worktree is removed and a changed one kept (2026-11-04) |

Deferred slash commands (planned, not implemented in companion slash dispatcher yet):

- `/milestone` (milestones are seeded when you apply a template via `/template`)

`/retry` exists, but transcript presentation semantics are still under review.

## Root-Level Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-p / --prompt` | — | One-shot prompt and exit |
| `-m / --model` | profile default | Override model |
| `--mode` | `chat` | Interaction mode |
| `--allow-all-tools / --yolo` | off | Auto-approve tool calls |
| `--fs-root` | — | Enable local filesystem tools |
| `--secure` | off | Pre-send content scan and per-file consent |
| `--source NAME` | active source | Route to a specific endpoint source |
| `--worktree NAME / -w` | — | Start in the isolated git worktree NAME (made if new) |

`--resume SESSION_ID` and `--skill NAME` are **companion flags**, not root
flags: `r4 companion --resume <session-id>`, `r4 companion --skill <name>`
(equivalently `r4 chat --companion --resume …`).

## Session Persistence

Sessions are automatically saved and can be resumed:

```bash
# Resume a previous companion session
r4 companion --resume <session-id>

# List sessions
r4 session list

# Show session details
r4 session show <session-id>
```

## Filesystem Tools

Enable the companion to read local files:

```bash
r4 --fs-root .                  # Current directory
r4 --fs-root /path/to/project   # Specific directory
```

## Secure Mode

Pre-send content scanning and per-file attachment consent:

```bash
r4 --secure
```

In secure mode, the companion:
1. Scans outgoing content for PII patterns
2. Asks permission before attaching each file
3. Logs all data transfers

## JS Companion

The Node.js companion provides an Ink-based React TUI with live panels.
It communicates with the Python backend via a JSON-lines RPC bridge.

See [TUI Reference](../r4/TUI_REFERENCE.md) for slash commands and flags.
