# R4 VS Code Extension — Implementation Map

Audience: small coding agents. Everything below was verified against source on
2026-07-26. Read this before editing anything under `vscode-extension/src/`.

---

## Direction since 2026-10-31 (ADR-103)

*Dated note; the sections below still describe the tree as verified on
2026-07-26, except where this block corrects them.*

- The extension is a second host of the **console's** contract
  ([ADR-103](../docs/adr/103-vscode-console-parity.md),
  plan). New
  agent work goes over the companion JSONL bridge
  (`python -m r4.companion.ui.bridge_server`), not `r4 acp`, which is frozen.
- `src/bridge/companion-bridge.ts` is the bridge client (Node port of the
  console's `jsonl-client.ts`); `src/bridge/resolve-python.ts` finds the
  interpreter (`r4.pythonPath` first); `src/bridge/rpc.ts` re-exports the
  console's typed `rpc()`. *(2026-10-31, VP-2a:)* with
  `r4.agent.backend=bridge`, `agent-view.ts` `dispatchMessage` routes the chat
  core (`bridge/bridge-view-router.ts`) to `bridge/bridge-agent-host.ts`, which
  answers with the existing webview events plus `approvalRequest`,
  `askUserRequest`, `planConfirm` and `restoreDraft` (cards:
  `webview-ui/src/components/AskCards.tsx`, state `webview-ui/src/state/asks.ts`).
  Everything else still goes through `handleMessage` and the ACP rail.
  *(2026-11-01, VP-2b part 1:)* the host also queues while streaming
  (`lib/queue-rail.ts`), runs `!cmd` (`bridge/shell-runner.ts`, framing from
  `lib/shell-relay.ts`), tags turns, forks, saves, opens citations and diffs
  through editor hooks (`bridge/editor-hooks.ts`), and posts changed files
  and the plan (`bridge/turn-extras.ts`); the webview keeps them in
  `state/extras.ts` and shows them in `components/SessionDock.tsx`.
  *(VP-2b part 2:)* the composer's `@` mentions and paste chips live in
  `components/composer-assist.ts` on console modules re-exported by
  `webview-ui/src/lib/console-shared.ts`; slash output renders as an `output`
  entry; the host answers typed `/changes` `/fork` `/save` (`LOCAL_SLASH`).
  *(VP-3a:)* `/sandbox` and `/web` join `LOCAL_SLASH`; each snapshot posts
  `policyState` (sandbox notice and chip, web label); the web-access question
  is `bridge/web-access-picker.ts`; Sessions filters and export ride
  `sessionFilter` / `exportSession` (`components/session-badges.tsx`).
  *(VP-3b:)* chat tabs are `bridge/chat-panels.ts`: one `WebviewPanel`, bridge
  and host per tab, built with the same `bridge/host-hooks.ts` and
  `webview-html.ts` as the sidebar view; `/tab` reaches them through the
  hidden `r4.tab.command`.
  *(VP-3b part 2:)* a server session is `bridge/venue-host.ts` (`/venue`, the
  `remote:` chip and its pollers, the source picker hook
  `bridge/source-picker.ts`), `bridge/remote-turn.ts` (the `session.venue_sync`
  watch) and `bridge/remote-asks.ts` (`remote:` approval and question cards).
  The host composes them; their clock is injectable for tests.
- Shared console modules are imported from `../packages/r4-tui-v3/src/`
  (`bridge/wire.ts`, `lib/terminal-safe.ts`, `lib/turn-reply.ts`, `bridge/rpc.ts`). That is why
  `tsconfig.json` has `rootDir: ".."` and `moduleResolution: "Bundler"`.
  `test/shared-modules.test.mjs` keeps them free of Solid, OpenTUI and Bun.
- Parity is measured: `parity/console-parity.json` →
  `node scripts/parity-matrix.mjs --write` → `PARITY.md`;
  `test/parity-matrix.test.mjs` fails on unclassified console rows or a
  rising `missing` count.
- `R4: Open Console in Editor` (`r4.openConsole`, `src/console-launch.ts`)
  runs `r4 console` in an editor-area terminal.
- §4 is stale on numbers: the version is 0.2.x and `npm run test:unit` runs
  29 files (250+ tests on 2026-11-01, after VP-2b part 2).

## 0. The one thing that confuses everyone

The string **"R4 Platform"** appears in *four unrelated places* in
[vscode-extension/package.json](package.json):

| Where | Meaning |
|---|---|
| `viewsContainers.activitybar[0].title` | The **activity-bar container** (the sidebar icon labelled `R4 PLATFORM`). It is a *container*, not a chat implementation. |
| `chatParticipants[0].fullName` | Display name of the `@r4` chat participant. |
| `languageModelChatProviders[0].displayName` | Display name of the model vendor in Copilot's model picker. |
| `mcpServerDefinitionProviders[0].label` | Display name of the MCP provider. |

**"R4 PLATFORM" in the sidebar is not a chat window.** It is the container that
holds one webview (`r4.agent`) plus six tree views. Any doc claiming there is a
separate "original R4 chat window implementation" is wrong.

---

## 1. Registered surfaces (ground truth)

`package.json → contributes` declares exactly four extension points, all wired in
[activate()](src/extension.ts).

### Surface A — `@r4` chat participant
- **id**: `r4.chat` (`chatParticipants`)
- **Entry**: [src/chat-participant.ts](src/chat-participant.ts) → `R4ChatParticipant`
- **Loop owner**: **client-side, TypeScript** — [src/agent-engine.ts](src/agent-engine.ts) `AgentEngine.runTurn()`
- **Transport**: `GatewayClient` → HTTP `POST /gw/llm/v1/chat/completions` (SSE)
- **History**: ephemeral, owned by VS Code (`ChatContext.history`)
- **Slash commands**: `/models /endpoints /jobs /tools /workflows /companion /stats`
  plus two undeclared-but-handled ones: `/model` (QuickPick) and `/history` (search)
- **Session state**: in-memory field `sessionState` on the participant instance
  (plan mode, auto-approve, selected model). **Not persisted.**

### Surface B — Agent webview (sidebar)
- **id**: `r4.agent`, `"type": "webview"` inside container `r4-platform`
- **Entry**: [src/agent-view.ts](src/agent-view.ts) → `R4AgentViewProvider`
- **Loop owner**: **server-side, Python** — the extension is only a client
- **Transport**: [src/acp-process.ts](src/acp-process.ts) `AcpProcessSupervisor`
  spawns `r4 acp` as a child process → JSON-RPC over stdio via
  [src/acp-client.ts](src/acp-client.ts) `AcpClient`
- **Python side**: [r4/src/r4/_cli/acp.py](../r4/src/r4/_cli/acp.py) →
  [r4/src/r4/acp.py](../r4/src/r4/acp.py) `ACPServer` → [r4/src/r4/agent/session.py](../r4/src/r4/agent/session.py) `AgentSession`
- **History**: disk-persisted, resumable by session id (managed by Python)
- **Key RPC methods**: `initialize`, `sessions/*`, `chat/send`, `editor/tools/register`,
  `editor/toolCall`; notifications `session/text`, `session/toolState`, `editor/toolCancel`
- **Webview security**: every message from the webview must carry a nonce
  (`messageToken`); mismatched messages are dropped.

### Surface C — Language model provider (Copilot model picker)
- **vendor**: `r4` (`languageModelChatProviders`), management command `r4.login`
- **Entry**: [src/lm-provider.ts](src/lm-provider.ts) → `R4LanguageModelProvider`
- **Loop owner**: **GitHub Copilot** — R4 supplies only the model
- **Transport**: `GatewayClient` → `/gw/llm/v1/*`; aggregates models from *all*
  platform sources in `sources.json`
- Handles: message/tool conversion ([src/lm-convert.ts](src/lm-convert.ts)),
  token estimation ([src/lm-tokens.ts](src/lm-tokens.ts)),
  reasoning cache/replay, thinking UI, and vision fallback
  ([src/vision-proxy.ts](src/vision-proxy.ts)).

### Surface D — MCP server definition provider
- **id**: `r4` (`mcpServerDefinitionProviders`)
- **Entry**: [src/mcp-provider.ts](src/mcp-provider.ts) → `R4McpDefinitionProvider`
- Exposes R4 MCP servers to Copilot agent mode / Agent Window. Tokens are attached
  only at `resolve` time.

### Plus: six tree views (read/act UI, no chat)
`r4.endpoints`, `r4.jobs`, `r4.models`, `r4.mcp`, `r4.workflows`, `r4.companion`
→ [src/views/](src/views).
`r4.companion` is **a launcher, not a chat surface**: it supplies a capability
system prompt that is fed into Surface A via `/companion`.

---

## 2. One-line map

| Surface | Entry file | Class | Loop runs in | Transport | History |
|---|---|---|---|---|---|
| A `@r4` chat | `chat-participant.ts` | `R4ChatParticipant` | Extension (TS) | HTTP → Gateway | VS Code, ephemeral |
| B Sidebar webview | `agent-view.ts` | `R4AgentViewProvider` | Python `r4 acp` | JSON-RPC/stdio | Disk, resumable |
| C Model picker | `lm-provider.ts` | `R4LanguageModelProvider` | Copilot | HTTP → Gateway | Copilot |
| D MCP provider | `mcp-provider.ts` | `R4McpDefinitionProvider` | Copilot | MCP | n/a |

Surfaces A and B are **independent agent stacks**. They share only
`AgentToolBridge` for workspace tool execution. Neither subsumes the other.

---

## 3. File responsibilities (`src/`)

| File | Responsibility |
|---|---|
| `extension.ts` | `activate()`; registers all four surfaces, six tree views, ~30 commands; TLS config; `resolveCliPath()`; provisioning reconciliation |
| `auth.ts` | `AuthManager` — Gateway URL + JWT from settings / CLI `sources.json` / SecretStorage. Live CLI api_key wins over the SecretStorage snapshot |
| `gateway-client.ts` | `GatewayClient` — **the only egress point**. Node `http`/`https` (not fetch). `GatewayError{status, body}`. `chatCompletionStream()`, `callMCPTool()`, `listEndpoints/Models/Jobs/Workflows` |
| `chat-participant.ts` | Surface A. Command routing, plan-mode state machine (`resolvePlanState`), single-render of engine output |
| `agent-engine.ts` | Surface A loop: stream → accumulate `tool_calls` → execute → feed back, max 10 rounds. Owns B8 trimming, B9 dedup, B10 tool-rejection fallback, B11 failure recovery, B13 usage, B14 follow-ups |
| `agent-view.ts` | Surface B webview host, session list, per-session concurrency guard, nonce gate |
| `acp-process.ts` / `acp-client.ts` | Spawn/supervise `r4 acp`; JSON-RPC framing, timeouts, cancellation |
| `agent-tools.ts` | `AgentToolRegistry`, `AgentToolBridge` (policy gate → approval → path containment → JSONL audit), `createCoreToolRegistry()` (`workspace.readFile/writeFile`) |
| `vscode-agent-tools.ts` | Builds the bridge with VS Code-specific tools: `workspace.diagnostics`, `terminal.run`; loads `resources/agent-tool-policy.json`; approval via modal dialog |
| `lm-provider.ts` | Surface C |
| `lm-convert.ts` | VS Code ↔ OpenAI message/tool conversion; strips leaked `$mid`/`cache_control` metadata |
| `lm-tokens.ts` | Adaptive chars-per-token estimation |
| `reasoning-cache.ts` / `reasoning-replay.ts` | Persist and re-inject `reasoning_content` across multi-turn tool calls |
| `think-filter.ts` | `ThinkTagFilter` — strips `<think>…</think>` (and orphan closers) from streamed `content` |
| `thinking.ts` / `think-gate.ts` | Route reasoning to VS Code's collapsible thinking UI; stop updating it once answer text starts |
| `vision-proxy.ts` | Describe image attachments with a vision model when the selected model is text-only |
| `mcp-provider.ts` | Surface D |
| `companion-session.ts` | Companion mode/state types mirroring the Python companion |
| `shared-constants.ts` | `KNOWN_R4_TOOLS`, `buildR4SystemPrompt()` — shared grounding between Python companion and extension |
| `status-bar.ts` | Gateway connection indicator + polling |
| `chat-model.ts` | Point Copilot's model picker at an R4 model when a sidebar item is selected |
| `views/*.ts` | Tree data providers over Gateway routes |

---

## 4. Build / test / install

```bash
cd vscode-extension
npm run check-types      # tsc --noEmit
npm run package          # check-types + esbuild --production
npm run test:unit        # node --test, 10 suites (currently 77 tests)
npm run lint

# From repo root — build, package, install locally:
./scripts/publish-vscode-extension.sh --install
# then in VS Code: Developer: Reload Window
```

Extension code changes **do not take effect** until repackage + reinstall +
window reload. Current version `0.1.28`, extension id `mnagaso.r4-platform`.

Unit tests import esbuild-produced `test/*.bundle.cjs` fixtures and a
`vscode-stub` — there is no real VS Code API at unit-test time.

---

## 5. Invariants — do not break

1. **Single render.** `AgentEngine.runTurn()` must never call `stream.markdown()`.
   It buffers everything and returns `result.content`; `R4ChatParticipant` renders
   it exactly once. `stream.progress()` is allowed (transient).
2. **Gateway-only egress.** No direct UMRS / LiteLLM / MinIO calls from the
   extension. Everything goes through `GatewayClient`.
3. **Tools are always advertised.** `KNOWN_R4_TOOLS` stays in the schema list even
   when `/gw/mcp/tools` fails, otherwise the model invents shell commands.
4. **Auto-approve skips the prompt, never the policy.** Path containment, policy
   check, and JSONL audit always run in `AgentToolBridge`.
5. **History is thinking-free.** Replayed assistant turns must be stripped of
   `<think>` blocks before re-sending.
6. **Webview messages are nonce-gated.** Do not remove `messageToken` checks in
   `agent-view.ts`.

---

## 6. Known defects (verified in source, not yet fixed)

These are real and will bite anyone continuing development.

**D1 — Surface A has no tools wired.**
In `chat-participant.ts → handleChat()` and `runCompanionChat()` the engine is
built as:
```ts
new AgentEngine(client, this.toolBridge, [], [], …)
```
`this.toolBridge` is declared but **never assigned** (always `undefined`), and
both the MCP-tool array and workspace-tool array are literal `[]`. Consequences:
- `workspace.readFile/writeFile/diagnostics/terminal.run` are never advertised and
  can never execute on Surface A.
- Every tool call falls through to `client.callMCPTool()`.
- `buildR4SystemPrompt(mcpTools, workspaceTools)` receives empty lists, so the
  advertised tool list is `KNOWN_R4_TOOLS` only.
Fix direction: call `createVscodeAgentToolBridge(context)` once at activation,
pass it into `R4ChatParticipant`, and fetch `client.listMCPTools()` per turn.

**D2 — Trailing `system` messages break some backends and suppress the R4 prompt.**
Three places append a `role: "system"` message to the **end** of the array:
- `handleChat()` open-tab context (`collectOpenTabContexts()`)
- `handleChat()` plan override
- `AgentEngine.runTurn()` plan-mode instructions

Two consequences:
- `AgentEngine` only prepends `buildR4SystemPrompt()` when
  `!config.messages.some(m => m.role === "system")`. So **whenever an editor tab is
  open, or plan mode is on, the R4 system prompt is silently skipped.**
- LiteLLM/vLLM backends such as `Qwen3.6-35B-A3B-NVFP4` reject mid-conversation
  system messages with HTTP 400 `System message must be at the beginning`.
Fix direction: merge all system content into a single leading system message.

**D3 — Plan mode adds two plan prompts.** `chat-participant.ts` pushes
`planModeSystemPrompt()` and `agent-engine.ts` pushes its own plan-mode text when
`config.mode === "plan"`. They are redundant and can conflict.

**D4 — No session persistence on Surface A.** `sessionState` lives on the
participant instance; mode/auto-approve/selected model are lost on reload.
`companion-session.ts` exists for this but is not used by `handleChat`.

**D5 — Surface B has no tool-calling loop.** Despite running "server-side", the
Python path is not a Copilot-class agent:
- `AgentSession.chat()` routes on a **keyword heuristic**
  (`create|build|run|analyze|workflow|help me|can you`).
- Non-matching turns go to `_conversational_response()` — **one LLM call, no
  `tools` parameter, no loop**. Most questions therefore get a hallucinated answer.
- Matching turns go to `SciWorkflowAgent.run()`, which is **plan-then-execute**
  (one JSON `WorkflowDefinition`, then typed steps), not observe→act→observe.
- `editor/tools/register` accepts only `{name, description}` — **no JSON parameter
  schema**, so the planner cannot emit valid tool arguments.
- `models/list` and `tools/list` ACP methods are stubs returning `[]`.

Upgrade plan: see Part II (C1–C12) in
`docs/development/agent-protocol/plan-vscode-extension-agent-upgrade.md`
(the maintainers' development records, not in the public repository).

---

## 7. "Where do I change X?"

| Goal | Touch |
|---|---|
| Add a `/slash` command to `@r4` | `package.json → chatParticipants[0].commands` + `handleRequest()` switch in `chat-participant.ts` |
| Change the agent loop (rounds, dedup, trimming, retries) | `agent-engine.ts` only |
| Add a workspace tool | `vscode-agent-tools.ts` (`registry.register`) + `resources/agent-tool-policy.json` |
| Add a Gateway route call | `gateway-client.ts` — nowhere else |
| Change how R4 models appear in Copilot's picker | `lm-provider.ts` (+ `lm-convert.ts` for wire mapping) |
| Change sidebar chat behaviour | **Python** `r4/src/r4/acp.py` / `r4/src/r4/agent/session.py`; `agent-view.ts` is only the client |
| Add a sidebar tree view | `package.json → contributes.views` + new file in `src/views/` + register in `extension.ts` |
| Change thinking/reasoning display | `think-filter.ts`, `thinking.ts`, `think-gate.ts` |
| Change the grounding system prompt or tool list | `shared-constants.ts` |

---

## 8. Do not assume

- There is **no** `/gw/agent/chat` or `/gw/companion/chat` route. Surface B's
  server-side loop is reached via the `r4 acp` **subprocess**, not HTTP.
- The sidebar webview does **not** use `AgentEngine`.
- The `Research Companion` tree view does **not** open its own chat.
- `packages/r4-tui-v3/` is a **separate product** (Bun + OpenTUI terminal UI)
  that shells out to Python `r4.client.Client.chat_stream()`. It shares no code
  with this extension and has no agent loop.
