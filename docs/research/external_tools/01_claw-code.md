# 01 — claw-code

> Source: [tmp/claw-code/README.md](../../../tmp/claw-code/README.md),
> [tmp/claw-code/CLAW.md](../../../tmp/claw-code/CLAW.md),
> [tmp/claw-code/PARITY.md](../../../tmp/claw-code/PARITY.md),
> directory walk of `tmp/claw-code/src/` and `tmp/claw-code/rust/`.

## What it is

A community **clean-room re-port** of the leaked "Claw Code" coding-agent CLI.
Two parallel implementations live in the repo:

- `src/` — Python port (active surface)
- `rust/crates/` — Rust port (the "definitive" version, in progress)

The README explicitly frames the project as **harness engineering research**:
the value is the *shape of the agent harness* (tool wiring, runtime context,
plugin surface, slash commands), not the model itself.

## Philosophy / rules

- "Better Harness Tools, not merely storing the archive of the leak."
- Clean-room: capture *architectural patterns* without copying proprietary
  source.
- Ports must keep `src/` and `tests/` in sync; `CLAW.md` is the per-repo agent
  briefing file (analogous to `AGENTS.md` / `CLAUDE.md`).
- Settings split: shared defaults in `.claw.json`; machine-local overrides in
  `.claw/settings.local.json`.
- The bundled bootstrap files (e.g. `CLAW.md`) are intentional — they should be
  updated deliberately, not auto-overwritten.

## Architecture (Rust port crates)

| Crate | Responsibility |
|-------|----------------|
| `api-client` | Provider abstraction, OAuth, SSE streaming |
| `runtime` | Session state, conversation loop, MCP orchestration, prompt construction, compaction |
| `tools` | Tool manifest definitions and execution framework |
| `commands` | Slash commands, skills discovery, config inspection |
| `plugins` | Plugin model, hook pipeline, bundled plugins (mostly TS-only today) |
| `compat-harness` | Compatibility layer for upstream editor integration |
| `claw-cli` | Interactive REPL, markdown rendering, project bootstrap (`/init`) |
| `server` | HTTP/SSE server (axum) for headless / structured I/O |
| `lsp` | LSP client integration |

## Notable feature surface (from PARITY.md)

The TypeScript origin shipped a very wide tool set, of which the Rust port is
a deliberate MVP. The TS surface is the more interesting blueprint:

- **Tools**: `Bash`, `FileRead/Write/Edit`, `Glob`, `Grep`, `LSPTool`,
  `WebFetch`, `WebSearch`, `Notebook*`, `REPL`, `PowerShell`, `Skill`,
  `Agent` (sub-agent), `AskUserQuestion`, `TodoWrite`, `MCPTool` family
  (auth, list resources, read resource), `RemoteTrigger`, `ScheduleCron`,
  `Task*` (create/get/list/output/stop/update), `Team*` (create/delete),
  `ToolSearch`.
- **Plugin system**: bundled plugins, install/update/enable/disable lifecycle,
  hook pipeline (`PreToolUse`, `PostToolUse`, deny/rewrite/result hooks),
  marketplace style operations.
- **Skills**: bundled-skills registry, MCP skill builders, live discovery and
  reload, integration with session/team memory.
- **Slash commands**: `/agents`, `/hooks`, `/mcp`, `/plugin`, `/skills`,
  `/plan`, `/review`, `/tasks`, `/memory`, `/init`, `/permissions`,
  `/resume`, `/cost`, `/compact`, `/diff`, `/export`, `/session`, `/status`,
  `/model`, `/version`, `/clear`.
- **Transports / I/O**: `structuredIO` (JSON-mode), `remoteIO`, transport
  layer abstraction. Important: claw-code calls out that "tool-capable JSON
  output can leak human-readable lines before the final JSON object" — a
  contract bug worth designing around.
- **Services**: analytics, prompt-suggestion, session memory, plugin
  operations, settings sync, policy limits, team-memory sync, notifier,
  voice.
- **CLAW.md / project bootstrap**: stack auto-detection, per-repo agent
  briefing file.
- **Cost tracker / cost hooks**, usage accounting per turn.
- **Context compaction**.

## Standout ideas worth borrowing

1. **`PROJECT.md`-style per-repo agent briefing** with stack auto-detection
   and `/init` to scaffold it (we have nothing equivalent in r4).
2. **Two-file settings split**: shared (`.claw.json`) vs. machine-local
   (`.claw/settings.local.json`).
3. **PreToolUse / PostToolUse hooks** with deny/rewrite/result-mutation
   semantics — useful for our EgressGuard / audit story.
4. **Skill-as-MCP-builder** pipeline so a "skill" can register MCP tools
   on demand instead of pre-registering everything.
5. **Slash-command registry as a first-class crate** separate from the REPL —
   matches our `r4 ask <verb>` direction.
6. **Sub-agent / `Agent` tool** as a standard tool the model calls, with
   structured task graph (`Task*` family).
7. **`ToolSearch` tool** — agent explicitly searches the tool registry by
   description before binding to one. Reduces token cost of large MCP fleets.
8. **`AskUserQuestion` tool** as a structured (not free-text) way for the agent
   to demand input — useful for HPC long-running flows.
9. **`ScheduleCron` / `RemoteTrigger`** as user-callable tools, not just
   server-side cron.
10. **JSON-mode contract** with explicit acknowledgement that mixed
    human/JSON output is a known foot-gun (we should design our transport
    layer to forbid it from day one).
