> **ARCHIVED 2026-09-03 (superseded)** — Phase-1 (2026-07) design; console is now at sophistication-plan era. Active design docs: `docs/development/` (root repo).

# Phase 1: Design — Research Operations Console

**Date:** 2026-07-01  
**Goal:** Design a production R4 TUI driven by backend state and events.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        R4 Backend                              │
│  (UMRS, Gateway, HPC/Slurm, MCP, RAG, vLLM, LiteLLM)         │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                    Backend Service Layer                       │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ GatewayClient│  │ R4CliAdapter │  │ EventPoller        │  │
│  │ (HTTP/SSE)  │  │ (r4 CLI)     │  │ (poll → diff → evt)│  │
│  └─────────────┘  └──────────────┘  └────────────────────┘  │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                      Event Bus                                 │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  EventBus.emit(TypedEvent) → subscriber callbacks      │ │
│  │  Event types: EndpointReady, JobCompleted, ToolCalled, │ │
│  │  ConversationStreaming, ConnectionLost, etc.           │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                    SolidJS Store                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌─────────┐│
│  │ ServerStore│  │ LocalStore │  │ UIStore    │  │ChatStore││
│  │ (ephemeral)│  │ (persisted)│  │ (selections)│  │(shared) ││
│  └────────────┘  └────────────┘  └────────────┘  └─────────┘│
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                      View Layer                                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Header (project, model, health)                        │ │
│  │  ┌─────────┬──────────────────────┬─────────────────┐  │ │
│  │  │ Command │   Main Workspace      │  Context Panel   │  │ │
│  │  │ Center  │   (route-specific)    │  (selection det) │  │ │
│  │  └─────────┴──────────────────────┴─────────────────┘  │ │
│  │  Activity Console (tabs: conv, events, logs, tools)    │ │
│  │  Status Bar (space, model, endpoints, jobs, health)    │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Event Bus as spine**: All backend state changes flow through a typed `EventBus`. Views subscribe to events, not polls. The poller emits diff events.

2. **Store separation**: 
   - `ServerStore` — ephemeral backend state (endpoints, jobs, models, workflows, events, logs)
   - `LocalStore` — persisted user preferences (selected model, theme, keybindings)
   - `UIStore` — transient UI state (selections, panel toggles, focus)
   - `ChatStore` — conversation state (shared between TUIs)

3. **Routes as discriminated unions**: Type-safe navigation. Each route maps to a workspace.

4. **Shared chat package**: Extract `packages/r4-tui-chat/` with markdown renderer, SSE parser, conversation model, streaming hook, and message types. Both `r4-tui` and `r4-tui-v3` depend on it.

5. **Command-centric keybindings**: Keys bind to command names. Handlers register separately. Enables command palette and slash command derivation.

6. **ResourceViewer pattern**: Each backend resource type (endpoint, job, model, workflow, knowledge, agent, tool) has a uniform viewer interface.

---

## 2. Event Flow

```
Backend (UMRS/Gateway/HPC)
    │
    ▼
EventPoller (every 5s)
    │  - Polls r4 endpoint/model/job/skill list --json
    │  - Diffs against previous snapshot
    │  - Emits typed events for changes
    │
    ▼
EventBus
    │  - Routes events to subscribers
    │  - Updates ServerStore
    │  - Drives mascot state machine
    │  - Triggers toast notifications
    │  - Appends to Activity Console
    │
    ▼
Views (subscribed to EventBus)
    │  - Header updates health badges
    │  - Dashboard updates endpoint/job/model counts
    │  - Activity Console appends events
    │  - Status Bar updates connection status
    │  - Context Panel updates selection details
```

### Event Types

```typescript
type ConsoleEvent =
  // Endpoint lifecycle
  | { type: "EndpointStarted"; endpointId: string; model: string }
  | { type: "EndpointReady"; endpointId: string; model: string; url: string }
  | { type: "EndpointStopped"; endpointId: string; model: string }
  | { type: "EndpointFailed"; endpointId: string; model: string; error: string }
  // Job lifecycle
  | { type: "JobSubmitted"; jobId: string; jobType: string }
  | { type: "JobRunning"; jobId: string; jobType: string }
  | { type: "JobCompleted"; jobId: string; jobType: string; durationMs: number }
  | { type: "JobFailed"; jobId: string; jobType: string; error: string }
  // Workflow lifecycle
  | { type: "WorkflowStarted"; workflowId: string }
  | { type: "WorkflowNodeRunning"; workflowId: string; nodeId: string }
  | { type: "WorkflowNodeCompleted"; workflowId: string; nodeId: string }
  | { type: "WorkflowFinished"; workflowId: string; status: string }
  // Knowledge/RAG
  | { type: "KnowledgeIndexed"; knowledgeId: string; documentCount: number }
  | { type: "KnowledgeUploaded"; knowledgeId: string; filename: string }
  // Agent/Tool
  | { type: "AgentStarted"; agentId: string; task: string }
  | { type: "AgentFinished"; agentId: string; task: string; durationMs: number }
  | { type: "ToolCalled"; toolName: string; args: Record<string, unknown> }
  | { type: "ToolFinished"; toolName: string; result: unknown; durationMs: number }
  // Chat/Streaming
  | { type: "ConversationStreaming"; conversationId: string; delta: string }
  | { type: "ConversationCompleted"; conversationId: string; model: string; latencyMs: number; tokens: number }
  // Connection
  | { type: "ConnectionLost"; service: "gateway" | "umrs" | "hpc" }
  | { type: "ConnectionRecovered"; service: "gateway" | "umrs" | "hpc" }
  // System
  | { type: "PollFailed"; error: string }
  | { type: "NotificationCreated"; notification: Notification };
```

---

## 3. Store Design

### 3.1 ServerStore (ephemeral, backend-owned)

```typescript
interface ServerStore {
  // Backend inventory
  endpoints: UiEndpoint[];
  models: UiModel[];
  jobs: UiJob[];
  skills: UiSkill[];
  
  // Live state
  events: ConsoleEvent[];
  logs: LogEntry[];
  toolCalls: ToolCall[];
  agentActivity: AgentActivity[];
  
  // Connection health
  connection: {
    gateway: ConnectionState;
    umrs: ConnectionState;
    hpc: ConnectionState;
    lastPollAt: string | null;
    lastPollError: string | null;
  };
  
  // Polling metadata
  pollCount: number;
  pollIntervalMs: number;
}
```

### 3.2 LocalStore (persisted, user preferences)

```typescript
interface LocalStore {
  // User selections
  selectedModel: string | null;
  selectedEndpoint: string | null;
  selectedSpace: Space;
  
  // UI preferences
  theme: "dark" | "light";
  activityConsoleOpen: boolean;
  contextPanelOpen: boolean;
  activityTab: ActivityTab;
  
  // Chat history (persisted per conversation)
  conversations: Conversation[];
  activeConversationId: string | null;
  
  // Command history
  commandHistory: string[];
}
```

### 3.3 UIStore (transient, per-session)

```typescript
interface UIStore {
  // Selections (transient)
  selectedNodeId: string | null;
  selectedObjectId: string | null;
  contextPanelTab: string;
  
  // Overlays
  commandPaletteOpen: boolean;
  slashBarOpen: boolean;
  helpOpen: boolean;
  notificationCenterOpen: boolean;
  modelSelectorOpen: boolean;
  endpointSelectorOpen: boolean;
  quitDialogOpen: boolean;
  quitPending: boolean;
  
  // Chat state (shared)
  chatMessages: ChatMessage[];
  isTyping: boolean;
  streamingContent: string;
  chatError: string | null;
  chatBackend: ChatBackend | null;
  
  // Mascot state machine
  mascotState: "idle" | "listening" | "thinking" | "running" | "success" | "error";
  
  // Activity console
  activityOpen: boolean;
}
```

### 3.4 EventBus

```typescript
class EventBus {
  private subscribers: Map<ConsoleEvent["type"], Set<(event: ConsoleEvent) => void>> = new Map();
  
  on(eventType: ConsoleEvent["type"], handler: (event: ConsoleEvent) => void): () => void {
    // Returns unsubscribe function
  }
  
  emit(event: ConsoleEvent): void {
    // Route to subscribers
    // Update ServerStore
    // Drive mascot state machine
    // Trigger toast notifications
    // Append to Activity Console
  }
}
```

---

## 4. Component Hierarchy

```
<App>
  <ErrorBoundary>
    <EventBusProvider>
      <ServerStoreProvider>
        <LocalStoreProvider>
          <UIStoreProvider>
            <ChatStoreProvider>
              <KeymapProvider>
                <Shell>
                  <Header />
                  <box flexDirection="row">
                    <CommandCenter />
                    <Workspace />
                    <ContextPanel />
                  </box>
                  <ActivityConsole />
                  <StatusBar />
                </Shell>
                <CommandPaletteOverlay />
                <HelpOverlay />
                <QuitDialog />
                <ModelSelectorDialog />
                <EndpointSelectorDialog />
              </KeymapProvider>
            </ChatStoreProvider>
          </UIStoreProvider>
        </LocalStoreProvider>
      </ServerStoreProvider>
    </EventBusProvider>
  </ErrorBoundary>
</App>
```

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `Header` | Project name, model badge, health indicators, user |
| `CommandCenter` | Left nav tree, space switcher, resource browser |
| `Workspace` | Route switcher, main content area |
| `ContextPanel` | Right detail panel, selection-driven content |
| `ActivityConsole` | Bottom tabbed panel, event log, agent logs, tool calls |
| `StatusBar` | Bottom status bar, space, model, counts, health |
| `CommandPaletteOverlay` | Ctrl+P fuzzy command search |
| `HelpOverlay` | Keyboard shortcut reference |
| `QuitDialog` | Ctrl+Q confirmation |

### Routes (Workspaces)

| Route | Workspace | Primary View |
|-------|-----------|-------------|
| `dashboard` | Overview | Live endpoint/job/model counts, recent events, health summary |
| `chat` | Conversation | Shared chat implementation (streaming, markdown, history) |
| `endpoints` | Infrastructure | Endpoint list with status, tier, model, actions (up/down/restart) |
| `jobs` | Task Queue | Job list with status, type, duration, actions (cancel/retry/logs) |
| `knowledge` | Knowledge Base | RAG document list, search/query, upload, indexed status |
| `agents` | Agent Team | Agent list with status, current task, tools, actions (pause/resume/restart) |
| `workflows` | UMRS Workflows | Workflow DAG visualization, node status, execution order |
| `models` | Model Registry | Model list with scope, type, status, actions (stage/download) |
| `tools` | MCP Tools | Tool list with server, status, capabilities, actions (connect/call) |
| `settings` | Configuration | Backend source, connection status, inventory counts, keybindings, theme |

---

## 5. Navigation Model

### 5.1 Route as Discriminated Union

```typescript
type Route =
  | { type: "dashboard" }
  | { type: "chat" }
  | { type: "endpoints" }
  | { type: "jobs" }
  | { type: "knowledge" }
  | { type: "agents" }
  | { type: "workflows" }
  | { type: "models" }
  | { type: "tools" }
  | { type: "settings" };
```

### 5.2 Navigation Actions

```typescript
interface NavigationActions {
  navigate(route: Route): void;
  goBack(): void;
  goForward(): void;
  refresh(): void;
}
```

### 5.3 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+1` | Dashboard |
| `Ctrl+2` | Chat |
| `Ctrl+3` | Endpoints |
| `Ctrl+4` | Jobs |
| `Ctrl+5` | Knowledge |
| `Ctrl+6` | Agents |
| `Ctrl+7` | Workflows |
| `Ctrl+8` | Models |
| `Ctrl+9` | Tools |
| `Ctrl+0` | Settings |
| `Ctrl+P` | Command palette |
| `Ctrl+I` | Toggle context panel |
| `Ctrl+Shift+C` | Toggle activity console |
| `Ctrl+E` | Event timeline |
| `Ctrl+N` | Notifications |
| `Ctrl+H` / `?` | Help |
| `Ctrl+Q` | Quit |
| `Tab` / `Shift+Tab` | Cycle panes |
| `Esc` | Close overlay |

---

## 6. Shared Chat Package Design

### 6.1 Package Structure

```
packages/
  r4-tui-chat/      # NEW shared chat primitives
    src/
      markdown.ts    # marked → OpenTUI styled text
      streaming.ts   # SSE parser for OpenAI-compatible streaming
      conversation.tsx # ConversationWorkspace component
      message.tsx    # MessageBlock with role badges, timestamps, markdown
      input.tsx      # Multi-line textarea with ↑/↓ history, / slash detection, Esc interrupt
      types.ts       # ChatMessage, Conversation, ChatBackend interfaces
      index.ts       # Re-exports
    package.json
    tsconfig.json
```

### 6.2 Shared Interfaces

```typescript
// types.ts
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  model?: string;
  endpoint?: string;
  latencyMs?: number;
  tokens?: number;
}

export interface ChatBackend {
  chat(message: string, history?: ChatMessage[], model?: string): Promise<string>;
  chatStream?(model: string, messages: ChatMessage[]): AsyncGenerator<string>;
}

export interface Conversation {
  id: string;
  messages: ChatMessage[];
  model: string;
  endpoint: string;
}
```

### 6.3 ConversationWorkspace Component

```tsx
// conversation.tsx
export function ConversationWorkspace(props: { backend: ChatBackend }) {
  return (
    <box flexDirection="column" width="100%" height="100%">
      <ConversationHeader />
      <ConversationViewport />
      <ConversationInput />
    </box>
  );
}
```

### 6.4 Integration Plan

1. Extract `chat/markdown.ts` and `chat/streaming.ts` from `r4-tui` into `r4-tui-chat`.
2. Extract conversation workspace components into `r4-tui-chat`.
3. Update `r4-tui` to import from `r4-tui-chat`.
4. Update `r4-tui-v3` to import from `r4-tui-chat`.
5. Both TUIs now share the same conversation implementation.

---

## 7. Backend Contracts

### 7.1 GatewayClient (HTTP)

| Method | Endpoint | Returns |
|--------|----------|---------|
| `listModels()` | `GET /gw/models` | `GatewayModel[]` |
| `listEndpoints()` | `GET /gw/endpoints` | `GatewayEndpoint[]` |
| `listJobs()` | `GET /gw/jobs` | `GatewayJob[]` |
| `listWorkflows()` | `GET /gw/workflows` | `GatewayWorkflow[]` |
| `listWorkflowExecutions()` | `GET /gw/workflow-executions` | `GatewayWorkflowExecution[]` |
| `chatCompletion(model, messages)` | `POST /gw/llm/v1/chat/completions` | `string` |
| `chatCompletionStream(model, messages)` | `POST /gw/llm/v1/chat/completions` (SSE) | `AsyncGenerator<string>` |
| `getKnowledgeDashboard()` | `GET /gw/knowledge/dashboard` | `KnowledgeSummary` |
| `listKnowledgeObjects(type?)` | `GET /gw/knowledge/objects` | `ObjectSummary[]` |
| `getKnowledgeObject(id)` | `GET /gw/knowledge/objects/{id}` | `ObjectDetail` |
| `getGraphNeighbors(id)` | `GET /gw/knowledge/graph/{id}/neighbors` | `RelatedObjectRef[]` |
| `askVerification(question, anchor?)` | `POST /gw/verification/chat` | `VerificationAnswer` |
| `submitCorrection(...)` | `POST /gw/knowledge/correction` | `CorrectionRecord` |
| `getReadinessReport()` | `GET /gw/knowledge/readiness` | `ReadinessReport` |
| `submitApproval(approved, by)` | `POST /gw/knowledge/approval` | `ApprovalRecord` |

### 7.2 R4CliAdapter (CLI)

| Method | CLI Command | Returns |
|--------|-------------|---------|
| `pollEndpoints()` | `r4 endpoint list --json --all` | `UiEndpoint[]` |
| `pollModels()` | `r4 model list --json` | `UiModel[]` |
| `pollJobs()` | `r4 job list --json --limit 20` | `UiJob[]` |
| `pollSkills()` | `r4 skill list --json` | `UiSkill[]` |
| `chat(message, history, model)` | Python `r4.client.Client.chat()` | `string` |
| `provisionEndpoint(model, tier)` | `r4 endpoint up --model {model} --tier {tier}` | `string` |
| `stopEndpoint(id)` | `r4 endpoint down {id}` | `string` |
| `submitJob(spec)` | `r4 job submit {spec}` | `string` |
| `cancelJob(id)` | `r4 job cancel {id}` | `string` |
| `retryJob(id)` | `r4 job retry {id}` | `string` |
| `getJobLogs(id)` | `r4 job logs {id}` | `string` |
| `uploadKnowledge(path)` | `r4 rag upload {path}` | `string` |
| `queryKnowledge(query)` | `r4 rag query {query}` | `string` |

### 7.3 EventPoller

```typescript
class EventPoller {
  constructor(
    private adapter: R4CliAdapter,
    private eventBus: EventBus,
    private intervalMs: number = 5000,
  ) {}
  
  start(): void {
    // Poll every intervalMs
    // Diff against previous snapshot
    // Emit events for changes
  }
  
  stop(): void {
    // Clear interval
  }
}
```

---

## 8. UMRS Workflow Graph Design

### 8.1 Workflow DAG Visualization

```
┌─────────────────────────────────────────────────────────────────┐
│  UMRS Workflow: data-pipeline-v2                                 │
│  Status: running │ Started: 14:32 │ Nodes: 3/5 complete         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│    ┌──────────┐     ┌──────────┐     ┌──────────┐              │
│    │ ✓ Fetch  │────▶│ ✓ Parse  │────▶│ ● Index  │              │
│    │   Data   │     │   JSON   │     │   RAG    │              │
│    └──────────┘     └──────────┘     └──────────┘              │
│                                              │                   │
│                                              ▼                   │
│    ┌──────────┐                       ┌──────────┐              │
│    │ ○ Publish│◀──────────────────────│ ○ Verify │              │
│    │   Results│                       │   Quality│              │
│    └──────────┘                       └──────────┘              │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  Selected: Index RAG                                             │
│  Status: running │ Agent: indexer-01 │ Model: GLM-5.2-NVFP4    │
│  Started: 14:35 │ Duration: 2m 15s │ Input: parsed-json.json   │
│  Output: (streaming...) rag-index-v2.bin                         │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Workflow Node States

| State | Icon | Color |
|-------|------|-------|
| `pending` | ○ | fgDim |
| `running` | ● | statusInfo |
| `completed` | ✓ | statusOk |
| `failed` | ✖ | statusErr |
| `skipped` | ⊘ | fgDim |
| `retrying` | ↻ | statusWarn |

### 8.3 Workflow Graph Component

```tsx
interface WorkflowGraphProps {
  workflow: Workflow;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

export function WorkflowGraph(props: WorkflowGraphProps) {
  // Render DAG as ASCII art with node states
  // Highlight selected node
  // Show execution order with arrows
  // Update live as node states change
}
```

---

## 9. Knowledge Graph Design

### 9.1 Knowledge Graph Visualization

```
┌─────────────────────────────────────────────────────────────────┐
│  Knowledge Graph — 42 nodes, 87 edges                           │
│  Filter: [All Types ▾] [Confidence ≥ 0.7 ▾]                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│         ┌─────────────┐                                         │
│         │  Paper: GLM  │──┐                                     │
│         │  (cited 12x) │  │                                     │
│         └─────────────┘  │                                     │
│                │           ▼                                     │
│                │     ┌─────────────┐                            │
│                │     │ Dataset:     │                            │
│                │     │ GLM-5.2-Eval │                            │
│                ▼     └─────────────┘                            │
│         ┌─────────────┐                                         │
│         │ Hypothesis:  │                                         │
│         │ GLM-5.2 > GPT│                                         │
│         └─────────────┘                                         │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  Selected: Paper: GLM-5.2 Technical Report                       │
│  Type: paper │ Citations: 12 │ References: 8                    │
│  Provenance: arxiv:2024.xxxxx │ Indexed: 2026-06-15             │
│  Related: Dataset: GLM-5.2-Eval, Hypothesis: GLM-5.2 > GPT     │
└─────────────────────────────────────────────────────────────────┘
```

### 9.2 Knowledge Graph Component

```tsx
interface KnowledgeGraphProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  selectedNodeId: string | null;
  filter: KnowledgeGraphFilter;
  onSelectNode: (nodeId: string) => void;
}

export function KnowledgeGraph(props: KnowledgeGraphProps) {
  // Force-directed layout (simplified for terminal)
  // Filter nodes by type and confidence
  // Highlight selected node and its neighbors
  // Show metadata, provenance, citations in Context Panel
}
```

---

## 10. Activity Console Design

### 10.1 Tabs

| Tab | Content | Source |
|-----|---------|--------|
| Conversation | Chat messages (mirror) | ChatStore |
| Events | Event log (timestamped) | ServerStore.events |
| Agent Logs | Agent execution logs | ServerStore.logs (filtered) |
| Tool Calls | MCP/tool call traces | ServerStore.toolCalls |
| Terminal | Gateway/UMRS log tail | ServerStore.logs (filtered) |
| Notifications | Backend errors + approvals | ServerStore.notifications |
| Metrics | Latency, token usage, queue depth | ServerStore.metrics |

### 10.2 Activity Console Component

```tsx
export function ActivityConsole() {
  return (
    <box width="100%" height={12} borderStyle="single" title="Activity Console">
      <TabBar tabs={TABS} activeTab={workspace.activityTab} onSelect={actions.setActivityTab} />
      <box flexGrow={1} flexDirection="column" overflow="hidden">
        <Switch>
          <Match when={workspace.activityTab === "conversation"}><ConversationTab /></Match>
          <Match when={workspace.activityTab === "events"}><EventsTab /></Match>
          <Match when={workspace.activityTab === "agent-logs"}><AgentLogsTab /></Match>
          <Match when={workspace.activityTab === "tool-calls"}><ToolCallsTab /></Match>
          <Match when={workspace.activityTab === "terminal"}><TerminalTab /></Match>
          <Match when={workspace.activityTab === "notifications"}><NotificationsTab /></Match>
          <Match when={workspace.activityTab === "metrics"}><MetricsTab /></Match>
        </Switch>
      </box>
    </box>
  );
}
```

---

## 11. Context Panel Design

### 11.1 Context Panel Content (Selection-Driven)

| Selection | Content |
|-----------|---------|
| Endpoint | ID, model, status, tier, URL, host:port, created_at, ready_at, expires_at, error, message |
| Job | ID, type, status, project_id, created_at, updated_at, duration, logs (tail) |
| Model | ID, name, scope, type, status, served_model_name, provider |
| Workflow | ID, name, status, steps (DAG), started_at, ended_at, nodes (with states) |
| Knowledge | ID, name, status, document_count, last_indexed, related_objects, provenance |
| Agent | ID, name, role, status, current_task, tools, memory, reasoning |
| Tool | Name, server, status, capabilities, description |
| Conversation | ID, message_count, model, endpoint, tokens, latency, created_at |
| None | "Select an object to see context." |

### 11.2 Context Panel Component

```tsx
export function ContextPanel() {
  const selectedId = () => workspace.selectedObjectId;
  return (
    <box width={28} borderStyle="single" title="Context">
      <Show when={selectedId()} fallback={<text fg={palette.fgDim}>Select an object to see context.</text>}>
        <ContextContent id={selectedId()!} />
      </Show>
    </box>
  );
}
```

---

## 12. Command Palette Design

### 12.1 Command Registry

```typescript
interface Command {
  id: string;
  namespace: string;
  title: string;
  description?: string;
  category?: string;
  shortcut?: string;
  slashName?: string;
  slashAliases?: string[];
  hidden?: boolean;
  suggested?: boolean;
  run: () => void;
}
```

### 12.2 Command Namespaces

| Namespace | Commands |
|-----------|---------|
| `project` | `project.open`, `project.create`, `project.switch` |
| `knowledge` | `knowledge.upload`, `knowledge.query`, `knowledge.list` |
| `workflow` | `workflow.run`, `workflow.pause`, `workflow.resume`, `workflow.cancel` |
| `agent` | `agent.restart`, `agent.logs`, `agent.assign` |
| `endpoint` | `endpoint.up`, `endpoint.down`, `endpoint.restart`, `endpoint.list` |
| `job` | `job.submit`, `job.cancel`, `job.retry`, `job.logs`, `job.list` |
| `model` | `model.list`, `model.select`, `model.stage`, `model.download` |
| `rag` | `rag.index`, `rag.search`, `rag.documents` |
| `storage` | `storage.mount`, `storage.list`, `storage.usage` |
| `settings` | `settings.theme`, `settings.keybindings`, `settings.profile` |
| `ui` | `ui.command-palette`, `ui.slash-bar`, `ui.toggle-context-panel`, `ui.toggle-activity-console`, `ui.toggle-notifications`, `ui.toggle-help`, `ui.close-overlay`, `ui.quit`, `ui.goto-{space}` |

### 12.3 Slash Commands

Slash commands are derived from the same command registry:

```typescript
// Commands with slashName can be invoked via /slashName
// Commands with slashAliases can be invoked via any alias

// Examples:
// /endpoint up → endpoint.up command
// /models → model.list command (slashName: "models")
// /resume → session.list command (slashAliases: ["resume", "continue"])
```

---

## 13. Layout Design

### 13.1 Main Layout

```
+----------------------------------------------------------------------------+
| Header: R4 · Project · Model · [gw:● umrs:● hpc:●] · user              |
+------------+--------------------------------------+-------------------------+
|            |                                      |                         |
| Command    |          Main Workspace              |   Context Panel         |
| Center     |                                      |                         |
|            |   (dashboard, chat, endpoints,       |   (selection-driven     |
| (nav tree, |    jobs, knowledge, agents,          |    detail view)         |
|  spaces,   |    workflows, models, tools,        |                         |
|  resources)|    settings)                        |                         |
|            |                                      |                         |
+------------+--------------------------------------+-------------------------+
| Activity Console (Conversation | Events | Logs | Tools | Metrics | ...)    |
+----------------------------------------------------------------------------+
| Status Bar: space · model · endpoints · jobs · health · notifications      |
+----------------------------------------------------------------------------+
```

### 13.2 Responsive Behavior

- **Wide terminal (>120 cols)**: All three columns visible.
- **Medium terminal (80-120 cols)**: Context panel hidden by default, toggle with `Ctrl+I`.
- **Narrow terminal (<80 cols)**: Command center hidden, only workspace + activity console visible.

---

## 14. Acceptance Criteria Mapping

| Criterion | How Satisfied |
|-----------|---------------|
| Users never wonder whether backend is connected | Header health badges + status bar + connection events |
| Every backend operation has visible progress | Activity console + progress bars + streaming |
| Backend state updates automatically | Event poller → event bus → store → views |
| Chat uses shared R4 conversation implementation | `packages/r4-tui-chat/` shared by both TUIs |
| No duplicated conversation rendering code | Single `ConversationWorkspace` component |
| Dashboard reflects live backend state | Real badges + recent events + health summary |
| Every important event appears in Activity Console | Event bus → activity console tabs |
| Users know which endpoint/model generated a response | Attribution on every assistant message |
| Long-running operations provide continuous feedback | Streaming + job status events + progress bars |
| Feels like operating a live distributed AI platform | Real actions + live state + event-driven UI |
| UMRS workflow graph is interactive and live | DAG visualization with node states, live updates |
| Knowledge graph is visual, explorable, connected | Force-directed graph with filter, search, provenance |
| Activity Console becomes central observability hub | 7 tabs: conversation, events, agent logs, tool calls, terminal, notifications, metrics |
| Event Bus is single source of truth for UI updates | All state changes flow through event bus |
| Placeholder/demo data eliminated | "Not Yet Supported" for unsupported features |
| Architecture modular enough for plugins/future | Route-based workspaces, command registry, plugin slots |

---

## Next Steps

→ **Phase 2: Implementation Plan** — Split into small milestones, each must compile and preserve working functionality.
