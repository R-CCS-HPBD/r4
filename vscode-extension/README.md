# R4 Platform VS Code Extension

VS Code extension for the R4 / RiVault AI Platform. Provides native editor
integration with Gateway-authenticated LLM endpoints, endpoint management,
job monitoring, model browsing — and an AI research Studio
(project knowledge + research loops + UMRS workflows), right in the sidebar.

## Parity with the R4 console (2026-10-31)

The extension is catching up with the terminal console (`r4 console`), function
by function ([ADR-103](../docs/adr/103-vscode-console-parity.md),
plan).
[PARITY.md](PARITY.md) shows where each console function stands.

- **R4: Open Console in Editor** runs `r4 console` in an editor-area terminal in
  your workspace folder, so every console function is available today. VS Code
  keeps some chords for itself (Ctrl+P, for example); set
  `terminal.integrated.sendKeybindingsToShell` if you want them to reach the console.
- The agent view is moving to the console's backend, the companion bridge.
  `r4.agent.backend` (`acp` today, `bridge` from wave VP-3) chooses it, and
  `r4.pythonPath` names the Python that runs `r4` if it is not found on its own.
- With `"r4.agent.backend": "bridge"` (opt-in since 2026-10-31) the chat runs
  on the same session, approvals and slash commands as the console:
  - Tool approvals are inline cards: `y`/Enter allow once, `a` always this
    session, `p` always for this project (when the platform offers it), `n`
    reject. File edits show their diff on the card.
  - Agent questions and the plan card (`a` run with auto-approval, `m` run
    asking for each tool, `n` keep planning) are inline too.
  - Any `/command` the console's registry knows runs as typed.
  - Type while the agent answers: your message waits in the queue (shown
    above the composer) and goes when the turn ends; Stop gives it back.
  - `!command` runs in your shell in the workspace; the output shows in the
    chat and the agent gets it (keys on the line are masked).
  - `@` opens a file list from the workspace (↑↓, Tab/Enter inserts the path;
    `#L10-L20` ranges work). A large paste becomes a `[Pasted text #N]` chip
    that is sent in full; one Backspace removes a chip.
  - `/changes`, `/fork` and `/save` work as in the console. The first save
    in a window asks before it publishes to the project's knowledge graph.
  - The status bar shows the local tool sandbox (`sbx:on` / `sbx:off`) and the
    agent's web access; `/sandbox` and `/web` change them. A session with no
    web-access choice asks before its first message.
  - Chat tabs: R4: New Chat Tab (or `+` on the agent view, ⧉ on a chat row)
    opens a session in an editor tab with its own bridge, so turns run side by
    side (up to 8). `/tab` lists, switches, closes and reopens them; R4: Reopen
    Closed Chat Tab brings a closed one back.
  - Sessions on the server: `/venue start` runs this session on the server
    (you choose its web access and a pushed source; a source not pushed yet is
    pushed after you confirm). The status bar shows `remote:running · lease
    47m`; replies stream from the server, and its approvals and questions open
    as cards. `/venue stop` or ⏹ on a chat row stops it; `/venue sources`
    pushes or unpushes a source.
  - ✎ on a message (or `/rewind`) puts that prompt back in the composer;
    sending replaces the conversation from there (`/rewind cancel` keeps it).
    `/btw <question>` asks on the side, even while a reply streams; `/bg`
    lists background `!` commands; `/explain <error.id>` explains an error.
  - The `/` popup and `/help` list every command the platform knows; Esc
    stops a reply, Esc Esc on an empty composer rewinds; an error that the
    platform recognises says how to fix it (`/explain <id>` for more).
    `/search-server up` starts a local search server for web search.
  - New chat asks where the session runs (Local, or Server when the platform
    answers); a chat row can also fork (⑂) or list its stored turns (≡).
  - The chat list shows where each session runs (L local, F server), its
    state and sync, filters by state / venue / archives, and exports a
    transcript to an editor tab.
  - On a message: ↻ send again, ✎ edit, ⑂ fork the session from that turn,
    ⬒ keep it in project knowledge. Cited answers show "cited N"; a source
    opens in an editor tab.
  - The files the session changed are listed above the composer: a name
    opens the diff editor, Revert (click twice) puts the file back. The
    agent's todos show there too.
  - Limits until wave VP-3: one session at a time (switching while a turn
    runs is refused), and New chat restarts the backend. Studio, Knowledge
    and management cards still use the old backend.

## What's new in 0.2.x

- **Research Studio panel** (Projects & Loops + Executions + Knowledge tabs)
  — chat now has a real follow-through: kick off knowledge ingestion,
  start/stop research loops, and track them visually from the sidecar.
- **Research sessions (ADR-025)** — durable investigation identity on the
  gateway. Sessions bind a topic + title + the loops under them — start one
  from the Companion ("research session…") or `/research start <goal> --session`
  on any surface that carries loops.
- **Agentic research loops (Wave 9)** — loops can advance with a bounded
  one-turn LLM decision per step on the UMRS venue (`--agentic`), mirroring
  the local research runtime's agentic path.
- **Iteration-level venue hints** — iterations may declare cluster-class
  requirements (`gpu`, `cores`, `cluster`, …) for future venue routing.
- **Gateway-linked surfaces** — CLI, Companion, and the extension share the
  same sessions/loops payload your project produces.
- **Native Model Provider** upgrades — per-model "thinking" effort picker
  when a model advertises `reasoning_effort_levels`, and automatic
  `reasoning_content` replay across agent tool loops.

## Features

- **@r4 Chat Participant** — Chat with R4 platform LLM models directly in
  VS Code Chat. Supports streaming responses and conversation history.
- **Research Studio** — Project knowledge graph, loop list/live pulse,
  Studio workflow graph, and offline-preloading project cache, all in one
  sidebar webview.
- **Endpoint Management** — Sidebar tree view to list, provision, and
  teardown inference endpoints.
- **Job Monitoring** — Sidebar tree view to list jobs, view logs, and cancel
  running/pending jobs.
- **Model Browser** — Browse the UMRS model registry with scope-aware
  filtering (system/project/user).
- **Status Bar** — Connection indicator showing Gateway health.
- **Agent Window MCP Tools** — Exposes R4 Platform and RAG MCP servers to
  Copilot agent mode and the VS Code Agents window.
- **Native Model Provider** — Registers R4 Platform LLM endpoints as native
  language models, selectable in the Copilot chat/edit/agent model picker.
  Supports streaming and tool calling.

## License

MIT — see [LICENSE](./LICENSE).

## Chat Commands

| Command | Description |
|---------|-------------|
| `@r4` | Chat with the platform LLM |
| `@r4 /models` | List available models |
| `@r4 /endpoints` | List active endpoints |
| `@r4 /jobs` | List recent jobs |

## Setup

1. Install the extension
2. Run **R4: Login to Gateway** from the Command Palette
3. Enter your Gateway URL and API token
4. Ensure `r4-mcp` is available on your PATH, or set `r4.mcp.command`
   to the executable path

Alternatively, set `r4.gatewayUrl` in VS Code settings and the
`R4_GATEWAY_URL` environment variable.

After login, VS Code discovers two MCP servers from this extension:

- `R4 Platform` — endpoint, job, workflow, model, and platform health tools
- `R4 RAG` — `search_docs`, `list_documents`, and `rag_status`

These servers appear in Copilot agent mode and the Agents window
customizations panel. The extension passes Gateway credentials to the spawned
MCP process through environment variables, not command-line arguments.

## Using R4 Models in Copilot

There are two ways to drive Copilot chat / edit / agent on R4 Platform models
(e.g. `Kimi-K2.6`, `GLM-5.1`, `DeepSeek-V4-Pro`). Both require a model with a
**ready endpoint** — the model picker only lists models that currently have a
warm/active endpoint.

### Option A — Native provider (this extension)

1. Run **R4: Login to Gateway**.
2. Open the Copilot **model picker** → models appear under the **R4 Platform**
   vendor. Pick one for chat, edit, or agent mode.

On **VS Code 1.125+** (stock VS Code only), open **Chat → Manage Language Models**
(gear icon in the model picker) → **Install Model Providers** to discover
provider extensions. In **Cursor** (VS Code API ~1.105), install the `.vsix`
or marketplace build directly — the Install Model Providers button may not
be present. After installing, use the Language Models editor to **pin**,
**hide**, or filter models (`@provider:"R4 Platform"`).

Thinking models (Kimi, GLM, DeepSeek-V4, llm-jp-4, etc.) expose a
**Thinking** effort control in the model picker when the model catalog
advertises `reasoning_effort_levels` (per-model; not a fixed four-level list).
Multi-turn agent tool loops replay `reasoning_content` automatically
via in-memory cache and replay markers.

### Option B — Copilot BYOK via Custom Endpoint (no extension required)

This uses VS Code's built-in **Custom Endpoint** provider (which replaced the
deprecated *OpenAI Compatible* provider). You do not create
`chatLanguageModels.json` by hand — VS Code opens it for you during the flow.

1. Issue a long-lived JWT (admin): `... admin user add ... --issue-token
   --token-days 365 --gateway-url <gateway> --profile ai4s`.
2. In VS Code, run **Chat: Manage Language Models** (Command Palette), or open
   the Chat model picker → **Manage Language Models** (gear icon).
3. Select **Add Models** → **Custom Endpoint**.
4. Enter a group name (e.g. `R4 AI4S`), a display name, and paste the JWT as the
   **API key**. Select API type **Chat Completions**.
5. VS Code opens `chatLanguageModels.json`. Paste the config below and save. The
   `url` must be the **full** chat-completions URL
   (`<gateway>/gw/llm/v1/chat/completions`), and each `id` must match the model
   id returned by `GET <gateway>/gw/llm/v1/models`.

```json
[
  {
    "name": "R4 AI4S",
    "vendor": "customendpoint",
    "apiKey": "YOUR_R4_JWT",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "Kimi-K2.6",
        "name": "Kimi-K2.6 (R4)",
        "url": "https://data0.ai.r-ccs.riken.jp/ai4s/gw/llm/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 253952,
        "maxOutputTokens": 8192
      },
      {
        "id": "DeepSeek-V4-Pro",
        "name": "DeepSeek-V4-Pro (R4)",
        "url": "https://data0.ai.r-ccs.riken.jp/ai4s/gw/llm/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 1040000,
        "maxOutputTokens": 8192
      },
      {
        "id": "GLM-5.1",
        "name": "GLM-5.1 (R4)",
        "url": "https://data0.ai.r-ccs.riken.jp/ai4s/gw/llm/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 194560,
        "maxOutputTokens": 8192
      }
    ]
  }
]
```

6. Pick the model from the chat model picker. If it does not appear, restart
   VS Code.

> **Config notes:**
> - `toolCalling: true` is required — models without it are hidden from the
>   agent-mode picker.
> - `maxInputTokens` should track each model's served context window (query
>   `GET <gateway>/gw/llm/v1/models` and read `max_model_len`). Leave headroom
>   for output. Current ai4s ceilings: **DeepSeek-V4-Pro 1,048,576 (1M)**,
>   Kimi-K2.6 262,144, GLM-5.1 202,752. Only DeepSeek serves a 1M window; the
>   other two are capped at their trained context and cannot be raised to 1M.
> - With **Option A (native provider)** the context window is derived from
>   `max_model_len` automatically — no per-model token config needed.
> - `apiKey` is sent as `Authorization: Bearer <JWT>`, which the Gateway
>   expects.

> **Note:** Both paths drive Copilot **chat, edit, and agent (tool calling)**
> only. Neither drives inline ghost-text completions, which stay on Copilot's
> own model.

### Utility models (no Copilot plan)

R4 defaults `chat.byokUtilityModelDefault` to `mainAgent`, so VS Code utility
flows automatically use the selected R4 model without a Copilot subscription.
You can override it with `none` or `copilot`, or choose dedicated models with:

- `chat.utilityModel` — titles, summaries, settings search
- `chat.utilitySmallModel` — commit messages, rename suggestions, intent detection

Use a fast/cheap R4 model for `chat.utilitySmallModel`.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `r4.gatewayUrl` | `""` | Gateway URL. Falls back to `R4_GATEWAY_URL` env. |
| `r4.defaultModel` | `""` | Default model for chat. Uses first available if empty. |
| `r4.autoRefreshInterval` | `30` | Polling interval in seconds (0 to disable). |
| `r4.mcp.enabled` | `true` | Expose R4 MCP servers to Copilot agent mode and Agents window. |
| `r4.mcp.command` | `"r4-mcp"` | Command used to launch R4 MCP servers. |
| `r4.mcp.projectId` | `""` | Default RAG project ID. Falls back to `R4_PROJECT_ID`. |
| `r4.mcp.timeout` | `30` | Gateway request timeout for R4 MCP tools, in seconds. |
| `r4.debugStream` | `false` | Log raw LM stream deltas to the **R4 LM Stream** output channel. |

## Architecture

The extension is a **thin client** over Gateway HTTP APIs:

```
VS Code Extension → Gateway HTTP (/gw/*) → UMRS
```

- No direct UMRS or LiteLLM access
- API token stored in VS Code SecretStorage (never plaintext)
- Graceful offline degradation when Gateway is unreachable
- All MCP tool calls pass through Gateway policy and audit
- Agent Window integration uses VS Code's MCP provider API; it does not bypass
  Gateway or call UMRS/LiteLLM directly.

## Development

```bash
cd vscode-extension
npm install
npm run compile        # Type-check + build
npm run watch          # Watch mode (esbuild)
```

Press **F5** in VS Code to launch the Extension Development Host.

## Packaging & Installing the `.vsix`

To build a distributable `.vsix` and install it into VS Code:

```bash
cd vscode-extension
npm install                                          # first time only

# Build the production bundle and package the .vsix.
# (uses @vscode/vsce; npx fetches it on demand — no global install needed)
npx --yes @vscode/vsce package --allow-missing-repository --skip-license
# → produces r4-platform-<version>.vsix in this folder
```

Install the packaged extension:

```bash
# From the command line
code --install-extension r4-platform-<version>.vsix

# To remove a previously installed copy first
code --uninstall-extension mnagaso.r4-platform
```

Or install from the VS Code UI: **Extensions** view → `...` menu →
**Install from VSIX...** → select the `.vsix` file.

After installing, **reload the window** (Developer: Reload Window) and run
**R4: Login to Gateway** to authenticate.

> **Notes:**
> - The `.vsix` is a build artifact and is git-ignored; rebuild it from source
>   rather than committing it.
> - `npm run package` runs the same production build that `vsce package`
>   invokes via `vscode:prepublish`, so type errors fail the package step.

## Project Structure

```
src/
  extension.ts          — Entry point, wires all components
  gateway-client.ts     — HTTP client for all Gateway communication
  auth.ts               — SecretStorage-based auth management
  status-bar.ts         — Connection status indicator
  chat-participant.ts   — @r4 Chat Participant
  agent-view.ts         — Studio sidebar (projects, loops, knowledge, workflows)
  views/
    endpoints.ts        — Endpoint tree view
    jobs.ts             — Job tree view
    models.ts           — Model tree view
webview-ui/             — Studio UI (projects, loops, knowledge, workflows),
                          offline cache, offline snapshots
resources/
  r4-icon.svg           — Activity bar icon
```
