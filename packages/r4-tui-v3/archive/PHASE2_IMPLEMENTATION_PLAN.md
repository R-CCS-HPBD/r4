> **ARCHIVED 2026-09-03 (superseded)** — M0–M17 milestone plan overtaken by wave plans (parity A–D+M shipped, sophistication plan active).

# Phase 2: Implementation Plan — Research Operations Console

**Date:** 2026-07-01  
**Goal:** Split the rewrite into small milestones. Each milestone must compile, pass tests, and preserve working functionality.

---

## Milestone Overview

| Milestone | Title | Priority | Estimated Effort |
|-----------|-------|----------|-----------------|
| M0 | Shared Chat Package | P0 (critical) | Medium |
| M1 | Event Bus + Store Refactor | P0 (critical) | Medium |
| M2 | Layout Shell Rewrite | P1 (high) | Small |
| M3 | Dashboard Route (Live) | P1 (high) | Small |
| M4 | Chat Route (Shared) | P0 (critical) | Small |
| M5 | Endpoints Route (Actions) | P1 (high) | Medium |
| M6 | Jobs Route (Actions) | P1 (high) | Medium |
| M7 | Knowledge Route (RAG) | P1 (high) | Medium |
| M8 | Workflows Route (DAG) | P2 (medium) | Large |
| M9 | Agents Route (Team) | P2 (medium) | Medium |
| M10 | Models/Tools/Settings Routes | P2 (medium) | Small |
| M11 | Command Palette + Slash Commands | P1 (high) | Medium |
| M12 | Context Panel (Selection-Driven) | P1 (high) | Medium |
| M13 | Activity Console (All Tabs) | P1 (high) | Medium |
| M14 | Knowledge Graph Visualization | P2 (medium) | Large |
| M15 | UMRS Workflow Graph (Live DAG) | P2 (medium) | Large |
| M16 | Remove All Placeholders | P1 (high) | Small |
| M17 | Tests + Smoke Verification | P1 (high) | Medium |

---

## M0: Shared Chat Package

**Goal:** Extract chat primitives into `packages/r4-tui-chat/`. Both `r4-tui` and `r4-tui-v3` depend on it.

**Tasks:**
1. Create `packages/r4-tui-chat/` package with `package.json`, `tsconfig.json`.
2. Extract `src/chat/markdown.ts` from `r4-tui` → `r4-tui-chat/src/markdown.ts`.
3. Extract `src/chat/streaming.ts` from `r4-tui` → `r4-tui-chat/src/streaming.ts`.
4. Extract conversation workspace components → `r4-tui-chat/src/conversation.tsx`, `message.tsx`, `input.tsx`.
5. Define shared types → `r4-tui-chat/src/types.ts` (`ChatMessage`, `Conversation`, `ChatBackend`).
6. Create `r4-tui-chat/src/index.ts` re-exporting all.
7. Update `r4-tui/package.json` to depend on `@r4/tui-chat`.
8. Update `r4-tui-v3/package.json` to depend on `@r4/tui-chat`.
9. Update imports in both packages.

**Acceptance:**
- `bun run typecheck` passes in all three packages.
- `bun run test` passes in `r4-tui` and `r4-tui-v3`.
- `r4-tui-v3` smoke test passes.
- No duplicated conversation rendering code remains.

---

## M1: Event Bus + Store Refactor

**Goal:** Create `EventBus`, `ServerStore`, `LocalStore`, `UIStore`. Wire `EventPoller` to emit events. Update views to subscribe to events.

**Tasks:**
1. Create `src/event-bus.ts` with typed `EventBus` class.
2. Define `ConsoleEvent` discriminated union (see Phase 1 §2).
3. Create `src/stores/server-store.ts` — ephemeral backend state.
4. Create `src/stores/local-store.ts` — persisted user preferences.
5. Create `src/stores/ui-store.ts` — transient UI state.
6. Create `src/stores/chat-store.ts` — conversation state (shared).
7. Create `src/event-poller.ts` — polls backend, diffs, emits events.
8. Wire `EventPoller` to `EventBus` → `ServerStore` updates.
9. Update `useBackend.ts` to use `EventPoller` instead of direct polling.
10. Update views to read from `ServerStore` instead of `workspace`.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Events are emitted on endpoint/job/model changes.
- Views update reactively from `ServerStore`.

---

## M2: Layout Shell Rewrite

**Goal:** Rewrite `Shell` with new layout. All components are placeholders initially.

**Tasks:**
1. Rewrite `src/layout/shell.tsx` with new layout:
   - Header (project, model, health)
   - Row: CommandCenter | Workspace | ContextPanel
   - ActivityConsole
   - StatusBar
2. Rewrite `src/layout/header.tsx` — project name, model badge, health indicators.
3. Rewrite `src/layout/status-bar.tsx` — space, model, counts, health.
4. Create placeholder routes for all workspaces.
5. Update `src/layout/workspace.tsx` to use `<Switch>`/`<Match>` routing.
6. Update `src/layout/command-center.tsx` — left nav tree with spaces.
7. Update `src/layout/context-panel.tsx` — right detail panel (placeholder).
8. Update `src/layout/activity-console.tsx` — bottom tabbed panel (placeholder).

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Smoke test passes.
- All layout zones render (even if empty).

---

## M3: Dashboard Route (Live)

**Goal:** Implement dashboard route with live endpoint/job/model counts, recent events, health summary.

**Tasks:**
1. Rewrite `src/routes/dashboard.tsx` with:
   - Live endpoint/job/model/skill counts (from `ServerStore`).
   - Recent events (last 10 from `ServerStore.events`).
   - Health summary (gateway, umrs, hpc status).
   - Active agents strip (from `ServerStore`).
2. Make dashboard badges clickable (navigate to respective routes).
3. Add "Backend Health" summary section.
4. Add recent notifications section.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Dashboard shows real counts from backend.
- Health badges update on connection changes.

---

## M4: Chat Route (Shared)

**Goal:** Integrate shared `ConversationWorkspace` as a route. Wire streaming, markdown, history, interrupt, model/endpoint attribution.

**Tasks:**
1. Create `src/routes/chat.tsx` route.
2. Import `ConversationWorkspace` from `@r4/tui-chat`.
3. Wire `ChatBackend` to `R4CliDataSource.chat()`.
4. Add model/endpoint attribution to every assistant message.
5. Add streaming support (if `chatStream` is available).
6. Add `Esc` interrupt for streaming.
7. Add `↑/↓` command history.
8. Add `/` slash command detection.
9. Add multi-line `<textarea>` input.
10. Add `<scrollbox stickyScroll stickyStart="bottom">` for scrollback.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Chat route renders shared `ConversationWorkspace`.
- Messages stream in real-time.
- Model/endpoint attribution is visible.
- `Esc` interrupts streaming.
- `↑/↓` cycles command history.
- `/` opens slash bar.

---

## M5: Endpoints Route (Actions)

**Goal:** Implement endpoints route with list, status, tier, model, actions (up/down/restart).

**Tasks:**
1. Create `src/routes/endpoints.tsx` route.
2. Render endpoint list from `ServerStore.endpoints`.
3. Show columns: status icon, model, tier, backend, host:port, created_at, ready_at.
4. Add actions:
   - `endpoint.up` — provision new endpoint (opens model picker dialog).
   - `endpoint.down` — tear down endpoint (with confirmation dialog).
   - `endpoint.restart` — restart endpoint (with confirmation dialog).
5. Wire actions to `R4CliAdapter` methods.
6. Add keyboard navigation (`↑/↓` to select, `Enter` to view details, `d` to tear down, `u` to provision).
7. Show endpoint details in Context Panel on selection.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Endpoints route shows real endpoints from backend.
- Actions (up/down/restart) execute backend commands.
- Confirmation dialogs appear for destructive actions.
- Context Panel shows endpoint details on selection.

---

## M6: Jobs Route (Actions)

**Goal:** Implement jobs route with list, status, type, duration, actions (cancel/retry/logs).

**Tasks:**
1. Create `src/routes/jobs.tsx` route.
2. Render job list from `ServerStore.jobs`.
3. Show columns: status icon, type, id, created_at, updated_at, duration.
4. Add actions:
   - `job.cancel` — cancel job (with confirmation dialog).
   - `job.retry` — retry failed job.
   - `job.logs` — view job logs (opens log viewer dialog).
5. Wire actions to `R4CliAdapter` methods.
6. Add keyboard navigation (`↑/↓` to select, `Enter` to view details, `c` to cancel, `r` to retry, `l` to view logs).
7. Show job details in Context Panel on selection.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Jobs route shows real jobs from backend.
- Actions (cancel/retry/logs) execute backend commands.
- Confirmation dialogs appear for destructive actions.
- Context Panel shows job details on selection.

---

## M7: Knowledge Route (RAG)

**Goal:** Implement knowledge route with RAG document list, search/query, upload, indexed status.

**Tasks:**
1. Create `src/routes/knowledge.tsx` route.
2. Render knowledge base list from `ServerStore.knowledge`.
3. Show columns: status icon, name, document_count, last_indexed.
4. Add search/query input.
5. Add actions:
   - `knowledge.upload` — upload document (opens file picker dialog).
   - `knowledge.query` — query documents (opens search results).
   - `knowledge.list` — list indexed documents.
6. Wire actions to `GatewayClient` methods.
7. Add keyboard navigation.
8. Show knowledge details in Context Panel on selection.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Knowledge route shows real knowledge bases from backend.
- Search/query returns real results.
- Upload opens file picker.
- Context Panel shows knowledge details on selection.

---

## M8: Workflows Route (DAG)

**Goal:** Implement workflows route with DAG visualization, node status, execution order.

**Tasks:**
1. Create `src/routes/workflows.tsx` route.
2. Render workflow list from `ServerStore.workflows`.
3. Create `src/components/workflow-graph.tsx` — DAG visualization component.
4. Show node states: pending (○), running (●), completed (✓), failed (✖), skipped (⊘), retrying (↻).
5. Show execution order with arrows.
6. Update live as node states change.
7. Selecting a node populates Context Panel.
8. Add actions:
   - `workflow.run` — trigger workflow.
   - `workflow.cancel` — cancel running workflow.
   - `workflow.pause` — pause workflow.
   - `workflow.resume` — resume paused workflow.
9. Wire actions to `GatewayClient` methods.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Workflows route shows real workflows from backend.
- DAG visualization renders with node states.
- DAG updates live as node states change.
- Selecting a node populates Context Panel.
- Actions (run/cancel/pause/resume) execute backend commands.

---

## M9: Agents Route (Team)

**Goal:** Implement agents route with list, status, current task, tools, actions (pause/resume/restart).

**Tasks:**
1. Create `src/routes/agents.tsx` route.
2. Render agent list from `ServerStore.agents`.
3. Show columns: status icon, name, role, current_task, tools.
4. Add actions:
   - `agent.restart` — restart agent.
   - `agent.logs` — view agent logs.
   - `agent.assign` — assign task to agent.
5. Wire actions to `R4CliAdapter` methods.
6. Add keyboard navigation.
7. Show agent details in Context Panel on selection.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Agents route shows real agents from backend (or "Not Yet Supported" if no agent backend).
- Actions (restart/logs/assign) execute backend commands.
- Context Panel shows agent details on selection.

---

## M10: Models/Tools/Settings Routes

**Goal:** Implement remaining routes (models, tools, settings).

**Tasks:**
1. Create `src/routes/models.tsx` route — model list with scope, type, status, actions (stage/download).
2. Create `src/routes/tools.tsx` route — MCP tool list with server, status, capabilities, actions (connect/call).
3. Rewrite `src/routes/settings.tsx` route — backend source, connection status, inventory counts, keybindings, theme.
4. Wire actions to `R4CliAdapter` / `GatewayClient` methods.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Models route shows real models from backend.
- Tools route shows real MCP tools from backend.
- Settings route shows real connection status and inventory counts.

---

## M11: Command Palette + Slash Commands

**Goal:** Implement command registry, command palette, slash bar. Wire to backend actions.

**Tasks:**
1. Create `src/commands/registry.ts` — command registry with namespaces.
2. Define commands for all namespaces (see Phase 1 §12.2).
3. Create `src/components/command-palette.tsx` — fuzzy-searchable command palette.
4. Create `src/components/slash-bar.tsx` — slash command input bar.
5. Wire commands to backend actions.
6. Add keyboard shortcut `Ctrl+P` to open command palette.
7. Add keyboard shortcut `Ctrl+/` to open slash bar.
8. Derive slash commands from command registry.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Command palette opens with `Ctrl+P`.
- Slash bar opens with `Ctrl+/`.
- Commands execute backend actions.
- No fake commands remain.

---

## M12: Context Panel (Selection-Driven)

**Goal:** Implement context panel with selection-driven content.

**Tasks:**
1. Rewrite `src/layout/context-panel.tsx` with selection-driven content.
2. Implement content for each selection type (endpoint, job, model, workflow, knowledge, agent, tool, conversation).
3. Add tabs: Overview, Provenance, Related, JSON.
4. Wire to `ServerStore` for real data.
5. Show "Not Yet Supported" for unsupported features.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Context Panel shows real details for each selection type.
- Context Panel is never empty (except when nothing is selected).
- Tabs (Overview, Provenance, Related, JSON) work.

---

## M13: Activity Console (All Tabs)

**Goal:** Implement all Activity Console tabs (conversation, events, agent logs, tool calls, terminal, notifications, metrics).

**Tasks:**
1. Implement `ConversationTab` — mirror chat messages.
2. Implement `EventsTab` — event log (timestamped).
3. Implement `AgentLogsTab` — agent execution logs.
4. Implement `ToolCallsTab` — MCP/tool call traces.
5. Implement `TerminalTab` — gateway/UMRS log tail.
6. Implement `NotificationsTab` — backend errors + approvals.
7. Implement `MetricsTab` — latency, token usage, queue depth.
8. Wire all tabs to `ServerStore` for real data.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- All 7 tabs render real data from backend.
- No placeholder tabs remain.

---

## M14: Knowledge Graph Visualization

**Goal:** Implement knowledge graph visualization with filter, search, provenance.

**Tasks:**
1. Create `src/components/knowledge-graph.tsx` — force-directed graph visualization.
2. Implement node types: paper, dataset, hypothesis, experiment, finding, figure, equation, memory.
3. Implement edge types: cites, produces, consumes, derives_from, supports, refutes.
4. Add filter: by type, by confidence.
5. Add search: find node by name/id.
6. Add highlight: selected node and its neighbors.
7. Show metadata, provenance, citations in Context Panel on node selection.
8. Update live as knowledge graph changes.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Knowledge graph renders with real nodes and edges from backend.
- Filter, search, highlight work.
- Selecting a node populates Context Panel.
- Graph updates live as knowledge changes.

---

## M15: UMRS Workflow Graph (Live DAG)

**Goal:** Implement workflow DAG visualization with node states, live updates.

**Tasks:**
1. Create `src/components/workflow-dag.tsx` — DAG visualization component.
2. Render nodes with states: pending, running, completed, failed, skipped, retrying.
3. Render edges with execution order.
4. Update live as node states change.
5. Selecting a node populates Context Panel.
6. Show execution metadata: agent, model, input, output, duration.
7. Add zoom/pan if terminal size allows.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- Workflow DAG renders with real workflow data from backend.
- Node states update live.
- Selecting a node populates Context Panel.
- Execution metadata is visible.

---

## M16: Remove All Placeholders

**Goal:** Remove all demo fixtures, fake agents, fake sparklines, placeholder tabs, no-op commands.

**Tasks:**
1. Remove `src/adapters/fixtures.ts` (or keep only for offline mode).
2. Remove fake agents from `OfflineDataSource`.
3. Remove fake load sparkline from infrastructure route.
4. Remove placeholder activity tabs (replace with real implementations from M13).
5. Remove no-op commands from command registry (replace with real implementations from M11).
6. Remove `generateLocalReply` fallback (or mark as "Offline mode" only).
7. Show "Not Yet Supported" for any feature that doesn't have a backend API.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes.
- No placeholder/demo data remains in the UI.
- Unsupported features show "Not Yet Supported".

---

## M17: Tests + Smoke Verification

**Goal:** Write tests for all components, run typecheck, tests, smoke.

**Tasks:**
1. Write unit tests for:
   - `EventBus` (emit, subscribe, unsubscribe).
   - `EventPoller` (diff, emit events).
   - `ServerStore` (updates, queries).
   - `ChatStore` (send message, stream, interrupt).
   - `R4CliAdapter` (poll, chat, actions).
   - `GatewayClient` (all endpoints).
2. Write integration tests for:
   - Dashboard route (renders live counts).
   - Chat route (renders shared conversation workspace).
   - Endpoints route (renders endpoint list, actions work).
   - Jobs route (renders job list, actions work).
   - Knowledge route (renders knowledge bases, search works).
   - Workflows route (renders DAG, node selection works).
   - Agents route (renders agent list, actions work).
   - Models/Tools/Settings routes.
   - Command palette (opens, searches, executes).
   - Context panel (shows details for each selection type).
   - Activity console (all tabs render).
3. Write smoke tests for:
   - `bun run smoke` (offline mode).
   - `R4_TUI_BACKEND=cli bun run smoke` (CLI mode).
4. Run `bun run typecheck`, `bun run test`, `bun run smoke`.
5. Fix any issues.

**Acceptance:**
- `bun run typecheck` passes.
- `bun run test` passes (all tests).
- `bun run smoke` passes (offline mode).
- `R4_TUI_BACKEND=cli bun run smoke` passes (CLI mode).
- All acceptance criteria from Phase 1 §14 are satisfied.

---

## Milestone Dependencies

```
M0 (Shared Chat) ──────► M4 (Chat Route) ──────► M13 (Activity Console)
                                  │
                                  ▼
M1 (Event Bus) ─────────► M2 (Layout Shell) ──► M3 (Dashboard)
                                  │
                                  ▼
                    M5 (Endpoints) ──► M12 (Context Panel)
                    M6 (Jobs)        ──► M12
                    M7 (Knowledge)   ──► M14 (Knowledge Graph)
                    M8 (Workflows)   ──► M15 (Workflow Graph)
                    M9 (Agents)
                    M10 (Models/Tools/Settings)
                    M11 (Command Palette)
                                  │
                                  ▼
                         M16 (Remove Placeholders)
                                  │
                                  ▼
                         M17 (Tests + Smoke)
```

---

## Implementation Order

1. **M0: Shared Chat Package** (critical path, highest priority)
2. **M1: Event Bus + Store Refactor** (critical path)
3. **M2: Layout Shell Rewrite** (depends on M1)
4. **M4: Chat Route** (depends on M0, M2)
5. **M3: Dashboard Route** (depends on M2)
6. **M5: Endpoints Route** (depends on M2)
7. **M6: Jobs Route** (depends on M2)
8. **M11: Command Palette** (depends on M2)
9. **M12: Context Panel** (depends on M5, M6)
10. **M13: Activity Console** (depends on M4)
11. **M7: Knowledge Route** (depends on M2)
12. **M8: Workflows Route** (depends on M2)
13. **M9: Agents Route** (depends on M2)
14. **M10: Models/Tools/Settings Routes** (depends on M2)
15. **M14: Knowledge Graph** (depends on M7)
16. **M15: Workflow Graph** (depends on M8)
17. **M16: Remove Placeholders** (depends on all)
18. **M17: Tests + Smoke** (depends on all)

---

## Next Steps

→ **Phase 3: Rewrite** — Implement incrementally, starting with M0 (Shared Chat Package).
