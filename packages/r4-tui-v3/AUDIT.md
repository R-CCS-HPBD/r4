# R4 Console v3 — UX & Architecture Audit

**Date:** 2026-07-01  
**Scope:** `packages/r4-tui-v3`  
**Goal:** Move v3 from a visual mockup to a real Research Operations Console.

---

## 1. Executive Summary

v3 has the right visual scaffolding — mascot, constellation, live cards, command center, activity console — but most of the surface is still **presentation without backend contract**.

The biggest architectural debt is the **chat**: v3 implements its own minimal chat panel instead of reusing the mature streaming, markdown, history, and model-selection code already present in `packages/r4-tui`.

Three themes dominate the findings:

1. **Disconnected state.** Dashboard badges, agent strip, activity console, and context panel render local/demo data. Backend polling populates `workspace.endpoints`, `models`, `jobs`, `skills`, but many UI zones ignore them or keep parallel fixture state.
2. **No event spine.** Backend changes are diffed into `workspace.events`, yet the UI treats events as a log rather than the driver of updates. There is no central dispatcher that maps `EndpointStarted`, `JobCompleted`, etc. to panel refreshes, toasts, or notifications.
3. **Chat is isolated.** The v3 chat has single-line input, no streaming, no markdown, no scrollback, no endpoint/model visibility, and no shared code with `packages/r4-tui`. It is the highest-priority rewrite.

---

## 2. Current Architecture Inventory

### 2.1 Files & Responsibilities

| File | Responsibility | Backend Connected? |
|------|---------------|-------------------|
| `src/app.tsx` | Bootstraps renderer, keymap, data source, poller, chat backend | Partial (wires poller) |
| `src/stores/index.ts` | Global Solid store + actions | Partial (chat falls back, artifacts are local) |
| `src/adapters/r4-cli.ts` | Polls endpoints/models/jobs/skills via `r4` CLI; chat via `r4.client.Client` Python call | Yes (lists + chat) |
| `src/adapters/offline.ts` | Demo fixtures + canned replies | No |
| `src/hooks/useBackend.ts` | 8s poller, diff events, connection status | Yes |
| `src/layout/shell.tsx` | Layout composition | No state |
| `src/layout/header.tsx` | Mascot, project, connection status | Reads `connection` (updated by poller) ✅ |
| `src/layout/status-bar.tsx` | Space, agents, model, HPC | Reads local state; model updated by poller |
| `src/layout/agent-activity-strip.tsx` | Active agents strip | Reads local `agents` (demo only) ❌ |
| `src/layout/activity-console.tsx` | Bottom tabs: conversation, events, notifications | Events are real; agent-logs/tool-calls/terminal are placeholders |
| `src/layout/command-center.tsx` | Left nav tree | Static routes, no backend binding |
| `src/layout/context-panel.tsx` | Right detail panel | Empty placeholder tabs |
| `src/layout/workspace.tsx` | Route switcher | No state |
| `src/components/chat.tsx` | Chat panel (dashboard) | Custom, non-streaming, no shared code |
| `src/routes/dashboard.tsx` | Chat-first dashboard | Now shows backend counts; chat is custom |
| `src/routes/tasks.tsx` | Task list | Local tasks only |
| `src/routes/knowledge.tsx` | Constellation + knowledge bases | Local goals/tasks/knowledge |
| `src/routes/infrastructure.tsx` | Endpoints/jobs + fake load sparkline | Endpoints/jobs real; load sparkline fake |
| `src/routes/settings.tsx` | Settings + inventory | Real counts |
| `src/commands/index.ts` | Command registry | Most commands only emit events, no backend calls |
| `src/lib/chat-reply.ts` | Local fallback reply generator | Local only |

### 2.2 Data Flow Today

```
r4 CLI polls ──► R4CliDataSource ──► useBackend ──► workspace.{endpoints,models,jobs,skills}
                                      │
                                      ▼
                              workspace.events (diff log)
                                      │
                                      ▼
              UI reads workspace directly; no reactive event dispatcher
```

Polling is correct, but the **event layer is underused**. Events are displayed in a tab instead of being the source of truth for animations, notifications, and panel updates.

---

## 3. Backend Integration Audit (Per Screen)

### 3.1 Dashboard

| Element | Current Source | Real Backend Source | Missing Connection |
|---------|---------------|---------------------|-------------------|
| Project name | `demoProject` / loaded workspace | `r4 project current` or JWT profile | No project sync |
| Model badge | `workspace.currentModel` (auto-set from first ready endpoint) | Active endpoint / user selection | No model picker |
| Endpoints badge | Real count ✅ | `r4 endpoint list` | ✅ |
| Models badge | Real count ✅ | `r4 model list` | ✅ |
| Jobs badge | Real count ✅ | `r4 job list` | ✅ |
| Skills badge | Real count ✅ | `r4 skill list` | ✅ |
| Chat panel | Custom non-streaming panel | Shared R4 chat | Must replace |
| Activity strip | Local demo agents | Real agent execution | No agent backend |

### 3.2 Tasks

| Element | Current Source | Real Backend Source | Missing Connection |
|---------|---------------|---------------------|-------------------|
| Task list | `workspace.tasks` (local/demo) | UMRS jobs / workflow executions | No job→task mapping |
| Task status | Local state | Job status from `r4 job list` | No sync |
| Selection | Sets `selectedObjectId` | Could open job detail | Context panel empty |

### 3.3 Knowledge

| Element | Current Source | Real Backend Source | Missing Connection |
|---------|---------------|---------------------|-------------------|
| Constellation | Local goals/tasks/knowledge | RAG documents + entities | No RAG data |
| Knowledge bases | `workspace.knowledge` (local/demo) | `r4 rag list` or gateway `/gw/knowledge/objects` | No backend query |
| Document count | Local/demo | Real RAG stats | No backend query |
| Search/query input | None | RAG query endpoint | Not implemented |

> *Stale example, correction 2026-10-19:* the `r4 rag list` verb never existed — documents list via `r4 rag docs list` (`/gw/rag/documents`), and the pipeline/retriever-profile CRUDs this file elsewhere references are retired (H-10.1 waves 2026-10-18/19, wave-ledger (15)-(16)).

### 3.4 Infrastructure

| Element | Current Source | Real Backend Source | Missing Connection |
|---------|---------------|---------------------|-------------------|
| Endpoints list | Real ✅ | `r4 endpoint list` | ✅ |
| Jobs list | Real ✅ | `r4 job list` | ✅ |
| Load sparkline | Hardcoded `[2,3,4,6,5,7,8,7,9,8]` | Real queue/GPU metrics | Placeholder |
| Endpoint actions | None | `r4 endpoint up/down` | Not implemented |
| Job actions | None | `r4 job cancel/retry/logs` | Not implemented |

### 3.5 Settings

| Element | Current Source | Real Backend Source | Missing Connection |
|---------|---------------|---------------------|-------------------|
| Backend source label | Hardcoded string | Adapter `name` | Minor |
| Connection status | Real ✅ | Poller | ✅ |
| Inventory counts | Real ✅ | Poller | ✅ |

### 3.6 Activity Console

| Tab | Current State | Real Backend Source |
|-----|--------------|---------------------|
| Conversation | Truncated chat copy | Should mirror chat workspace |
| Agent Logs | Placeholder | Agent execution logs |
| Tool Calls | Placeholder | MCP/tool call traces |
| Events | Real event log ✅ | Diff events from poller |
| Terminal | Placeholder | Gateway/UMRS log tail |
| Notifications | Local notifications | Backend errors + approvals |

---

## 4. Chat Audit (Highest Priority)

### 4.1 Current v3 Chat (`src/components/chat.tsx`)

- Single-line `<input>`.
- No streaming; waits for full Python client response.
- No markdown rendering.
- No scrollback; slices last 12 messages.
- No syntax highlighting.
- No command history.
- No slash-command detection.
- No interrupt.
- No model or endpoint visibility in the message area.
- Replies are appended to a flat `chatMessages` array with role/user/assistant/system.

### 4.2 Existing r4-tui Chat (`packages/r4-tui`)

- `src/workspaces/conversation.tsx` — full conversation workspace.
- `src/chat/markdown.ts` — `marked` → OpenTUI styled text.
- `src/chat/streaming.ts` — SSE parser for OpenAI-compatible streaming.
- `src/gateway-client.ts` — `chatCompletionStream` via `/gw/llm/v1/chat/completions`.
- `src/stores.ts` — `sendMessage`, `sendChatStream`, `command_history`, `chat_streaming`, `chat_error`, `active_conversation_id`, `available_models`, `selected_model`, `selected_endpoint`.
- Supports multi-line `<textarea>`, `↑/↓` history, `Esc` interrupt, `/` slash detection, autoscroll with `stickyScroll`, role badges, timestamps.

### 4.3 Gap

v3 has **none** of the above. Maintaining two independent chat implementations violates the user's requirement for "ONE conversation implementation."

---

## 5. Placeholder Audit

| Placeholder | Location | Replacement |
|-------------|----------|-------------|
| `generateLocalReply` fallback used as primary chat path | `src/lib/chat-reply.ts`, `src/stores/index.ts` | Should only run when backend unavailable; should report which model/endpoint answered |
| Demo agents | `src/adapters/fixtures.ts` | Real agent status from backend or remove |
| Fake load sparkline | `src/routes/infrastructure.tsx` | Real queue depth / GPU utilization or remove |
| Placeholder activity tabs | `src/layout/activity-console.tsx` | Implement agent logs, tool calls, terminal, notifications |
| Empty context panel | `src/layout/context-panel.tsx` | Show selected endpoint/job/knowledge details |
| Local tasks/goals/knowledge | `src/routes/tasks.tsx`, `src/routes/knowledge.tsx` | Sync with UMRS jobs / RAG / workflow state |
| No-op commands | `src/commands/index.ts` | Implement backend actions or remove |
| `backendLoading` only shows spinner | `src/layout/header.tsx` | Add per-service health: gateway, UMRS, HPC |

---

## 6. Event-Driven Architecture Gaps

Currently the only event producer is `useBackend.ts`, which diffs polls into `ConsoleEvent` objects. There is no:

- **Event dispatcher** that routes events to the correct panel.
- **Toast / notification** creation on backend changes.
- **Mascot state machine** driven by events.
- **Activity console** as the central event sink.
- **Connection-state recovery** events.
- **Agent / tool / job lifecycle** events.

Needed event types:

- `EndpointStarted`, `EndpointReady`, `EndpointStopped`, `EndpointFailed`
- `JobSubmitted`, `JobRunning`, `JobCompleted`, `JobFailed`, `JobCancelled`
- `AgentStarted`, `AgentThinking`, `AgentToolCall`, `AgentFinished`, `AgentError`
- `ToolStarted`, `ToolOutput`, `ToolFinished`
- `KnowledgeUploaded`, `KnowledgeIndexed`
- `ConversationUpdated`, `StreamingStarted`, `StreamingFinished`
- `ConnectionLost`, `ConnectionRecovered`
- `NotificationCreated`

---

## 7. Observability Gaps

Users cannot see:

- Which endpoint handled a chat request.
- Which model generated a response.
- Latency per request.
- Token usage.
- Streaming progress.
- Job queue length.
- Per-endpoint health.
- Why a fallback reply occurred.
- Which tool an agent called and what it returned.

---

## 8. Recommended Implementation Plan

### Phase 1 — Unify Chat (Highest Priority)

1. Extract reusable chat primitives from `packages/r4-tui`:
   - `chat/markdown.ts` → shared package or copy into `r4-tui-v3/src/chat`
   - `chat/streaming.ts` → shared
   - `GatewayClient.chatCompletionStream` → v3 data source
2. Replace `src/components/chat.tsx` with a conversation workspace using:
   - `<scrollbox stickyScroll stickyStart="bottom">`
   - `<markdown>` rendering
   - `<textarea>` multi-line input
   - `↑/↓` command history
   - `Esc` interrupt
   - `/` slash detection
3. Update `DataSource` to expose `chatCompletionStream(model, messages): AsyncGenerator<string>`.
4. Keep `generateLocalReply` only as an offline fallback, and surface a small notice when fallback is active.
5. Add model/endpoint attribution to every assistant message.

### Phase 2 — Live Dashboard

1. Replace static agent data with real agent status or hide the strip until agents are wired.
2. Add a "Backend Health" summary in the header with per-service status and last-poll time.
3. Make dashboard badges clickable (e.g., Endpoints → Infrastructure space).
4. Add recent events and notifications directly on the dashboard.

### Phase 3 — Activity Console Becomes the Hub

1. Implement `Agent Logs`, `Tool Calls`, `Terminal`, and `Notifications` tabs.
2. Route every backend event through the Activity Console.
3. Add a persistent `System Logs` tab that tails gateway/UMRS logs when possible.

### Phase 4 — Backend Operations

1. Add actions in `R4CliDataSource`:
   - `provisionEndpoint(model, tier?)`
   - `stopEndpoint(id)`
   - `submitJob(spec)`
   - `cancelJob(id)`
   - `retryJob(id)`
   - `getJobLogs(id)`
   - `uploadKnowledge(path)`
   - `queryKnowledge(query)`
2. Wire commands in `src/commands/index.ts` to real backend calls.
3. Add confirmation dialogs for destructive actions.

### Phase 5 — Context Panel & Details

1. When an endpoint/job/knowledge item is selected, show real details in the context panel.
2. Add JSON/raw view for debugging.

### Phase 6 — Event Spine & Observability

1. Create a central `EventBus` or dispatcher.
2. Map backend diffs to typed events.
3. Auto-update mascot, notifications, status bar, and activity console from events.
4. Add per-request latency and token usage display.
5. Show fallback / error reasons inline.

### Phase 7 — Remove Placeholders

1. Remove hardcoded load sparkline or replace with real data.
2. Remove demo fixtures from `R4CliDataSource`.
3. Delete no-op commands or implement them.
4. Convert local fallback replies into explicit offline mode.

---

## 9. Reuse Plan from `packages/r4-tui`

| Asset in `packages/r4-tui` | How to Reuse in v3 |
|---------------------------|-------------------|
| `src/chat/markdown.ts` | Copy or extract to shared `@r4/tui-chat` package; import in v3 |
| `src/chat/streaming.ts` | Same as above |
| `src/gateway-client.ts` | Refactor into shared HTTP client; v3 data source consumes it |
| `src/workspaces/conversation.tsx` | Adapt layout to v3 dashboard / activity console |
| `src/stores.ts` chat actions | Merge `sendMessage`, `sendChatStream`, `command_history`, `chat_streaming` into v3 stores |
| `src/types.ts` `Conversation`, `ConversationMessage` | Align v3 `ChatMessage` with r4-tui message model |
| `src/data-source.ts` `chatCompletionStream` | Add to v3 `DataSource` interface and implement in `R4CliDataSource` |

Recommended structural move:

```
packages/
  r4-tui-v3/
  r4-tui-chat/      # NEW shared chat primitives
    src/
      markdown.ts
      streaming.ts
      conversation.tsx
      types.ts
```

Both `r4-tui` and `r4-tui-v3` depend on `r4-tui-chat`. This avoids duplicated conversation code.

---

## 10. Acceptance-Criteria Mapping

| Acceptance Criterion | How It Will Be Satisfied |
|---------------------|-------------------------|
| Users never wonder whether backend is connected | Header health summary + status bar + connection events |
| Every backend operation has visible progress | Activity console + mascot state + progress toasts |
| Backend state updates automatically | Event dispatcher drives panels from polls |
| Chat uses shared R4 conversation implementation | Reuse r4-tui chat primitives / extract shared package |
| No duplicated conversation rendering code | Delete custom v3 chat panel; use shared conversation workspace |
| Dashboard reflects live backend state | Real badges + recent events + health |
| Every important event appears in Activity Console | Central event bus + all tabs wired |
| Users know which endpoint/model generated a response | Attribution on every assistant message |
| Long-running operations provide continuous feedback | Streaming + job status events + terminal tab |
| Feels like operating a live distributed AI platform | Real actions + live state + event-driven UI |

---

## 11. Immediate Next Steps

1. Decide whether to extract a shared `r4-tui-chat` package or copy-and-share-later.
2. Replace `src/components/chat.tsx` with the shared conversation implementation.
3. Add `chatCompletionStream` to `DataSource` and implement it in `R4CliDataSource`.
4. Wire streaming interrupt, command history, markdown, and model attribution.
5. After chat is unified, proceed to live dashboard + Activity Console tabs.
