# Phase 0: Literature Survey & Comparison Report

**Date:** 2026-07-01  
**Goal:** Build a production Research Operations Console, not a demo.

---

## 1. Current R4 TUI Landscape

### 1.1 `r4/src/r4/tui/` (Python helpers)

The Python `tui` package provides **backend support utilities** shared by the JS companion bridge:

- `commands.py` — slash command registry (`SlashCommand` dataclass, `SUBCOMMANDS` dict)
- `dashboard.py` — `collect_backend_snapshot()`, best-effort JSON-safe snapshot of R4 backend surfaces (endpoints, jobs, workflows, rag_documents, mcp_tools, skills)
- `companion_bridge.py` — skill/context wiring for the companion
- `mcp_config.py` — MCP server configuration loading (`MCPServerConfig`, `load_mcp_configs`)
- `mcp_manager.py` — MCP connection management (`MCPConnectionManager`)
- `modes.py` — `Mode` enum (chat, plan, autopilot)

**Key insight:** The Python TUI helpers already define the **command vocabulary** (`/endpoint up`, `/job cancel`, `/rag query`, `/workflow trigger`, `/skills on`, `/approve auto`) and the **dashboard snapshot** pattern. These are the backend contracts the new TUI must expose.

### 1.2 `packages/r4-tui/` (mature TypeScript TUI)

The original R4 TUI, built with OpenTUI + SolidJS. Contains mature chat implementation:

- **`src/chat/markdown.ts`** — `marked` → OpenTUI styled text. Handles headings, paragraphs, code blocks, inline code, bold, italic, links, lists, blockquotes, hr.
- **`src/chat/streaming.ts`** — SSE parser for OpenAI-compatible streaming (`parseSSEStream`, `ChatStreamChunk`).
- **`src/gateway-client.ts`** — Gateway HTTP client with `chatCompletion()` (non-streaming) and `chatCompletionStream()` (SSE streaming). Endpoints: `/gw/models`, `/gw/endpoints`, `/gw/jobs`, `/gw/workflows`, `/gw/llm/v1/chat/completions`.
- **`src/workspaces/conversation.tsx`** — full conversation workspace with scrollbox, sticky-bottom autoscroll, streaming messages, role badges, timestamps.
- **`src/stores.ts`** — SolidJS store with `sendMessage`, `sendChatStream`, `command_history`, `chat_streaming`, `chat_error`, `active_conversation_id`, `available_models`, `selected_model`, `selected_endpoint`.
- **`src/data-source.ts`** — `DataSource` interface with `chatCompletionStream(model, messages): AsyncGenerator<string>`.
- **`src/primitives/`** — reusable UI primitives (buttons, inputs, badges, etc.)
- **`src/keybindings.ts`** — declarative keybinding definitions.

**Architecture:** SolidJS reactive stores → OpenTUI renderer. Fine-grained updates, no full re-render.

### 1.3 `packages/r4-tui-v3/` (current prototype)

Built on the same OpenTUI + SolidJS stack. Has visual scaffolding but is disconnected from backend:

- **Layout:** Header → (CommandCenter | Workspace | ContextPanel) → AgentActivityStrip → ActivityConsole → StatusBar
- **Routes:** dashboard, tasks, knowledge, infrastructure, settings
- **Backend adapter:** `R4CliDataSource` polls `r4 endpoint/model/job/skill list --json`, chat via `r4.client.Client` Python call
- **Chat:** Custom single-line `<input>`, no streaming, no markdown, no scrollback. `src/components/chat.tsx`
- **Problems:** Demo agents, fake sparkline, placeholder tabs, empty context panel, no-op commands, local tasks/goals/knowledge.

---

## 2. OSS TUI Project Survey

### 2.1 OpenCode (`sst/opencode` → `anomalyco/opencode`)

| Aspect | Detail |
|--------|--------|
| **License** | MIT |
| **Stack** | TypeScript, `@opentui/core` + `@opentui/solid`, `@opentui/keymap`, `solid-js`, `effect` |
| **Stars** | 181k |

**Architecture:**
- Routes as discriminated unions (`Route = HomeRoute | SessionRoute | PluginRoute`)
- Layered SolidJS context stores (server-synced vs. local-persisted)
- Command-centric keybinding system (keys → command names → handlers)
- Mode stack for modal keybindings (`push(mode)` returns cleanup function)
- Dialog stack with focus save/restore
- Plugin slot system (`mode="replace"` or `mode="append"`)
- Event routing: Server events → `switch (event.type)` → store mutations with `produce()` + `reconcile()`
- Binary search for sorted arrays (sessions, messages)
- Message windowing (trim to 100 messages)
- 60 FPS render loop with dirty checking
- `ErrorBoundary` at top level
- Leader key with timeout

**Reusable patterns for R4 Console:**
1. Routes as discriminated unions — type-safe navigation
2. Server state vs. local state separation
3. Command-centric keybinding system
4. Mode stack for modal keybindings
5. Dialog stack with focus save/restore
6. Plugin slot system
7. Event routing with `produce()` + `reconcile()`
8. Binary search for sorted arrays
9. Message windowing
10. `ErrorBoundary` at top level

### 2.2 Lazygit (`jesseduffield/lazygit`)

| Aspect | Detail |
|--------|--------|
| **License** | MIT |
| **Stack** | Go, gocui |
| **Stars** | 79.9k |

**Architecture: Context-Controller Pattern**
- Views (gocui buffer) ↔ Contexts (per-view state) ↔ Controllers (keybindings + handlers)
- `window_arrangement_helper.go` centralizes UI layout
- Contexts own their state (selection index, filter text)
- `baseController` provides default no-op implementations
- `ListControllerTrait[T]` generic trait for list-based contexts
- Factory pattern: `SideWindowControllerFactory`, `FilterControllerFactory`, etc.
- Binding guard system: `GetDisabledReason` for conditional actions
- Tag-based binding classification: "global", "navigation", "panel"

**Reusable patterns:**
1. Context-Controller separation for multi-pane TUIs
2. Controller factory pattern for scaling keybinding registration
3. Binding guard system for conditional actions
4. Tag-based binding classification for help menu

### 2.3 K9s (`derailed/k9s`)

| Aspect | Detail |
|--------|--------|
| **License** | Apache-2.0 |
| **Stack** | Go, tview |
| **Stars** | 34k |

**Architecture: ResourceViewer Pattern**
- `ResourceViewer` interface: `Start()`, `Stop()`, `Init()`, `GVR()`, `App()`, `Name()`, `Hints()`
- Each K8s resource type has a viewer implementing this interface
- `PageStack` navigation with push/pop semantics for drill-down
- `Browser` struct as generic foundation for resource browsing
- `Table` component reused across all browser views
- Registrar pattern: `coreViewers()`, `rbacViewers()`, `batchViewers()` — register viewers by category
- `QueueUpdateDraw(func())` — thread-safe UI update queue
- YAML-driven extensibility: hotkeys, plugins, aliases, custom views, skins
- `refreshRate` config (default 2.0 seconds)
- Dangerous action flagging: `KeyAction.Dangerous`

**Reusable patterns:**
1. `ResourceViewer` uniform interface — applicable to endpoints, jobs, models, workflows
2. `PageStack` navigation with breadcrumbs
3. YAML-driven extensibility for hotkeys, plugins, aliases
4. `QueueUpdateDraw` pattern for thread-safe updates
5. Dangerous action flagging
6. Registrar pattern for categorized viewer registration

### 2.4 GitUI (`extrawurst/gitui`)

| Aspect | Detail |
|--------|--------|
| **License** | MIT |
| **Stack** | Rust, ratatui |
| **Stars** | 22.2k |

**Architecture: Async Elm-like with Modular Crates**
- Elm-like architecture: State → Update (message handling) → View
- `asyncgit` crate provides non-blocking git operations
- Tab-based UI: Inspect, Commit, Stash, Blame, Log
- Each functional area isolated in separate crates
- Async-first design: all I/O operations non-blocking
- RON configuration for key bindings
- Performance benchmarks: Fastest among git TUIs

**Reusable patterns:**
1. Crate-based modularity — each functional area isolated, testable independently
2. Async-first design — all I/O operations non-blocking
3. Context-based help — help text generated from current view context
4. RON configuration — type-safe configuration format for key bindings

### 2.5 Bubbles (`charmbracelet/bubbles`)

| Aspect | Detail |
|--------|--------|
| **License** | MIT |
| **Stack** | Go, Bubble Tea |
| **Stars** | 8.6k |

**Architecture: Elm Architecture Components**
- Each component follows Model-Init-Update-View
- `key.Binding` declarative keybindings with help text
- ID + tag message filtering for multi-instance components
- Functional options construction: `Component.New(WithWidth(80), WithHeight(24))`
- `StyleState` with Focused/Blurred sub-styles
- `tea.Batch` for concurrent commands

**Component inventory:**
- `viewport` — scrollable content area with vertical/horizontal scroll, mouse wheel, highlight search
- `list` — browsable item collection with pagination, fuzzy filtering, spinner, status messages
- `table` — tabular data display with columns, rows, cursor, focus/blur
- `textinput` — single-line text input with Unicode, pasting, suggestions, cursor blink
- `textarea` — multi-line text input with Unicode, pasting, vertical scroll, virtual cursor
- `spinner` — activity indicator with configurable frames
- `progress` — progress bar with gradient/solid fills
- `paginator` — pagination UI with dots/Arabic types
- `filepicker` — file system navigation with directory traversal, extension filtering
- `help` — auto-generated help view with single/multi-line modes
- `key` — keybinding management with `key.Binding`, `key.WithKeys()`, `key.WithHelp()`
- `timer` — countdown timer with configurable update frequency
- `stopwatch` — count-up timer

**Reusable patterns:**
1. Elm architecture as default — Model/Update/View triad
2. `key.Binding` declarative keybindings
3. ID + tag message filtering
4. Functional options construction
5. `StyleState` with Focused/Blurred
6. `tea.Batch` for concurrent commands
7. Component inventory: viewport, list, table, textinput, textarea, spinner, progress, paginator, filepicker, help, key, timer, stopwatch

### 2.6 Glow (`charmbracelet/glow`)

| Aspect | Detail |
|--------|--------|
| **License** | MIT |
| **Stack** | Go, Bubble Tea |
| **Stars** | 26.1k |

**Architecture: Two-Model State Machine**
- Top-level state machine: `stateShowStash` (file listing) ↔ `stateShowDocument` (document viewing)
- Sub-model delegation: `model.Update()` delegates to `stash.update()` or `pager.update()` based on current state
- Shared common model: `commonModel` (cfg, cwd, width, height) passed by pointer to all sub-models
- Nested state machines: `stashViewState` (ready/loading/error) and `filterState` (unfiltered/filtering/applied)
- Status message timeout: `time.NewTimer(statusMessageTimeout)` with cleanup
- Dynamic height calculation: `availableHeight := m.common.height - stashViewTopPadding - helpHeight - stashViewBottomPadding`

**Reusable patterns:**
1. Two-model state machine (stash ↔ pager) — applicable to dashboard ↔ detail views
2. Nested state machines for complex view states
3. Shared common model passed by pointer to all sub-models
4. Dynamic height calculation for responsive layouts
5. Status message timeout with cleanup

---

## 3. Cross-Project Comparison

### 3.1 Architecture Patterns

| Project | Architecture | State Management | Event Routing |
|---------|-------------|------------------|---------------|
| **OpenCode** | Reactive (SolidJS) | Layered context stores | Server events → store mutations |
| **Lazygit** | Context-Controller | Per-view state in contexts | Controller keybinding handlers |
| **K9s** | ResourceViewer | Model-Listener pattern | `QueueUpdateDraw` from background |
| **GitUI** | Async Elm-like | Message-based state updates | Async commands |
| **Bubbles** | Elm Architecture | Model-Init-Update-View | `tea.Cmd` side effects |
| **Glow** | Two-Model State Machine | Sub-model delegation | State-dependent update routing |
| **r4-tui** | Reactive (SolidJS) | Single SolidJS store | Backend polling → store updates |
| **r4-tui-v3** | Reactive (SolidJS) | Single SolidJS store | Backend polling → workspace state |

### 3.2 Reusable Patterns by Category

#### Navigation
- **Routes as discriminated unions** (OpenCode) — type-safe, exhaustive
- **PageStack with push/pop** (K9s) — drill-down navigation
- **Tab-based navigation** (GitUI) — functional area switching

#### State Management
- **Server state vs. local state separation** (OpenCode) — prevents accidental persistence
- **`produce()` + `reconcile()`** (OpenCode) — immutable updates, smart diffing
- **Binary search for sorted arrays** (OpenCode) — O(log n) updates
- **Message windowing** (OpenCode) — trim old messages, prevent unbounded memory

#### Keyboard
- **Command-centric keybinding** (OpenCode) — keys → command names → handlers
- **Mode stack** (OpenCode) — modal keybindings with cleanup functions
- **`key.Binding` with help text** (Bubbles) — declarative, self-documenting
- **Tag-based binding classification** (Lazygit) — "global", "navigation", "panel"

#### Layout
- **Flexbox in terminal** (OpenCode) — `flexGrow`, `flexShrink`, `minHeight={0}` for scrollable panes
- **Spacers as first-class** (OpenCode) — `<box flexGrow={1} minHeight={0} />`
- **Dynamic height calculation** (Glow) — responsive layout based on terminal size

#### Component Reusability
- **`ResourceViewer` uniform interface** (K9s) — applicable to endpoints, jobs, models, workflows
- **`ListControllerTrait[T]`** (Lazygit) — generic trait for list-based contexts
- **`StyleState` with Focused/Blurred** (Bubbles) — consistent focus-based styling
- **Plugin slot system** (OpenCode) — `mode="replace"` or `mode="append"`

#### Real-Time Data Updates
- **`QueueUpdateDraw` pattern** (K9s) — thread-safe UI update queue
- **`refreshRate` config** (K9s) — configurable polling interval
- **ID + tag message filtering** (Bubbles) — robust pattern for multi-instance components

#### Event Routing
- **`switch (event.type)` → store mutations** (OpenCode) — event-driven store updates
- **State-dependent update routing** (Glow) — route updates based on current state

#### Observability
- **Context-based help** (GitUI) — help text generated from current view context
- **Dangerous action flagging** (K9s) — `KeyAction.Dangerous` marks destructive operations
- **Status message timeout** (Glow) — transient status messages with cleanup

### 3.3 License Compatibility

| Project | License | Can Reuse Code? |
|---------|---------|-----------------|
| **OpenCode** | MIT | ✅ Yes, with attribution |
| **Lazygit** | MIT | ✅ Yes, with attribution |
| **K9s** | Apache-2.0 | ✅ Yes, with attribution + NOTICE |
| **GitUI** | MIT | ✅ Yes, with attribution |
| **Bubbles** | MIT | ✅ Yes, with attribution |
| **Glow** | MIT | ✅ Yes, with attribution |

All projects are MIT or Apache-2.0 licensed, meaning code can be legally reused with proper attribution.

### 3.4 Reusable Components from Bubbles

| Bubbles Component | R4 Console Equivalent |
|--------------------|-----------------------|
| `viewport` | Chat scrollback, log viewer |
| `list` | Endpoint list, job list, model list |
| `table` | Job queue, endpoint metrics |
| `textinput` | Command palette input, search |
| `textarea` | Chat input (multi-line) |
| `spinner` | Loading indicator, "agent thinking" |
| `progress` | Job progress bar, model loading |
| `paginator` | Large list pagination |
| `filepicker` | Knowledge upload |
| `help` | Auto-generated keybinding help |
| `key` | Declarative keybinding definitions |
| `timer` | Latency tracking |

**Note:** Bubbles is a Go library. The patterns are reusable in TypeScript, but the code itself cannot be directly imported. However, the same patterns can be implemented using OpenTUI primitives.

---

## 4. Recommendations for the New TUI

### 4.1 Adopt OpenCode's Reactive Architecture

The new R4 TUI should use the same stack as OpenCode:
- `@opentui/core` + `@opentui/solid` for rendering
- `@opentui/keymap` for keybinding management
- `solid-js` for fine-grained reactivity
- `effect` for lifecycle management (optional but recommended)

This is already the stack used by `r4-tui` and `r4-tui-v3`, so no migration is needed.

### 4.2 Adopt OpenCode's Command-Centric Keybinding System

- Keys bind to command names, not handlers
- Command handlers register separately
- Enables free command palette integration, user remapping, and slash command derivation
- Use `@opentui/keymap` which already provides this

### 4.3 Adopt K9s' ResourceViewer Pattern

Each backend resource type (endpoint, job, model, workflow, knowledge, agent, tool) should have a viewer implementing a uniform interface:

```typescript
interface ResourceViewer<T> {
  start(): void
  stop(): void
  refresh(): Promise<void>
  getItems(): T[]
  getKeybindings(): Keybinding[]
  render(width: number, height: number): JSX.Element
}
```

This enables:
- Uniform navigation across resource types
- Reusable list/table components
- Consistent keybinding patterns

### 4.4 Adopt OpenCode's Event Routing Pattern

- Server events → `switch (event.type)` → store mutations with `produce()` + `reconcile()`
- Binary search for sorted arrays
- Message windowing (trim old messages)
- Event-driven store updates

### 4.5 Adopt Lazygit's Context-Controller Pattern

- Views (OpenTUI components) ↔ Contexts (per-view state) ↔ Controllers (keybindings + handlers)
- `baseController` provides default no-op implementations
- Factory pattern for scaling keybinding registration
- Binding guard system for conditional actions

### 4.6 Adopt Bubbles' Component Inventory

Implement the following reusable components using OpenTUI primitives:
- `Viewport` — scrollable content area (chat scrollback, log viewer)
- `ListView` — browsable item collection (endpoint list, job list, model list)
- `TableView` — tabular data display (job queue, endpoint metrics)
- `TextInput` — single-line text input (command palette, search)
- `TextArea` — multi-line text input (chat input)
- `Spinner` — activity indicator (loading, "agent thinking")
- `ProgressBar` — progress bar (job progress, model loading)
- `HelpView` — auto-generated keybinding help
- `StatusBar` — bottom status bar
- `Dialog` — modal dialog (confirmations, selections)

### 4.7 Adopt K9s' Dangerous Action Flagging

- `KeyAction.Dangerous` marks destructive operations
- Confirmation dialogs for dangerous actions
- Color-coded warnings in the UI

### 4.8 Adopt GitUI's Async-First Design

- All I/O operations non-blocking
- Background polling for live data updates
- Async commands for backend operations

---

## 5. Summary: What to Reuse from Each Project

| Project | What to Reuse |
|---------|---------------|
| **OpenCode** | Routes as discriminated unions, layered context stores, command-centric keybindings, mode stack, dialog stack with focus save/restore, plugin slot system, event routing with `produce()` + `reconcile()`, binary search for sorted arrays, message windowing, `ErrorBoundary` at top level, 60 FPS render loop with dirty checking, leader key with timeout |
| **Lazygit** | Context-Controller separation, controller factory pattern, binding guard system, tag-based binding classification |
| **K9s** | `ResourceViewer` uniform interface, `PageStack` navigation with breadcrumbs, YAML-driven extensibility, `QueueUpdateDraw` pattern, dangerous action flagging, registrar pattern |
| **GitUI** | Crate-based modularity, async-first design, context-based help, RON configuration |
| **Bubbles** | Elm architecture as default, `key.Binding` declarative keybindings, ID + tag message filtering, functional options construction, `StyleState` with Focused/Blurred, `tea.Batch` for concurrent commands, component inventory: viewport, list, table, textinput, textarea, spinner, progress, paginator, filepicker, help, key, timer, stopwatch |
| **Glow** | Two-model state machine, nested state machines, shared common model, dynamic height calculation, status message timeout with cleanup |
| **r4-tui** | Chat implementation (markdown, streaming, history, interrupt, textarea, command history, model selection, endpoint selection, gateway client, SSE parser) |
| **r4-tui-v3** | Visual scaffolding (mascot, constellation, live cards, command center, activity console), backend adapter pattern |

---

## 6. License Summary

All studied projects are MIT or Apache-2.0 licensed. Code can be legally reused with proper attribution. The Bubbles library (Go) patterns can be reimplemented in TypeScript using OpenTUI primitives.

---

## Next Steps

→ **Phase 1: Design** — Architecture diagrams, event flow, store design, component hierarchy, navigation model, workspace model, backend contracts.
