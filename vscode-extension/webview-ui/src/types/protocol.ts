/**
 * Protocol types — the message contract between webview and extension host.
 *
 * These mirror ViewMessage and notification types in agent-view.ts exactly.
 * No changes needed to the extension host; types are a parallel reflection.
 */

// ── Outbound (webview → extension host) ────────────────────────────────────

export type SessionMode = 'ask' | 'plan' | 'agent';

export type OutboundMessage =
  // ADR-103 VP-2 (bridge backend): answers to the inline cards.
  | { type: 'approvalDecision'; id: string | number; decision: ApprovalDecision }
  | { type: 'askUserAnswer'; id: string | number; answer: string | null }
  | { type: 'confirmPlan'; action: PlanAction }
  // VP-2b: message verbs, citations, changed files.
  | { type: 'forkSession'; turnId?: string }
  | { type: 'saveTurn'; turnId: string; part: 'prompt' | 'answer' }
  | { type: 'openCitation'; citation: ViewCitation }
  | { type: 'openChange'; path: string }
  | { type: 'revertChange'; path: string }
  // VP-2b part 2: `@` file mentions.
  | { type: 'mentionQuery'; query: string }
  // VP-3a: the Sessions filters (server-side) and export.
  | { type: 'sessionFilter'; state?: string; venue?: string; includeArchived?: boolean }
  | { type: 'exportSession'; sessionId: string }
  // VP-3b: a session in its own chat tab.
  | { type: 'openInTab'; sessionId: string }
  /** VP-3b: stop a session's runtime on the server (a Sessions row). */
  | { type: 'venueStop'; sessionId: string }
  /** VP-3b: a Sessions row's fork and turn index. */
  | { type: 'forkRow'; sessionId: string }
  /** VP-2c: a message's ✎ — the next send replaces the conversation from this turn. */
  | { type: 'rewindTo'; turnId: string; text: string }
  | { type: 'sessionTurns'; sessionId: string }
  | { type: 'ready' }
  | { type: 'refresh' }
  | { type: 'login' }
  | { type: 'newSession'; model?: string }
  | { type: 'selectSession'; sessionId: string }
  | { type: 'send'; text: string; sessionId?: string; model?: string }
  | { type: 'cancel'; sessionId?: string }
  | { type: 'shellCancel'; streamId: string; sessionId?: string }
  | { type: 'restart' }
  | { type: 'renameSession'; sessionId: string; name: string }
  | { type: 'deleteSession'; sessionId: string }
  | { type: 'pinSession'; sessionId: string }
  | { type: 'unpinSession'; sessionId: string }
  | { type: 'compactSession'; sessionId: string; keepRecent?: number }
  | { type: 'setModel'; sessionId: string; model?: string }
  | { type: 'setMode'; sessionId: string; mode: SessionMode }
  | { type: 'openFile'; path: string }
  // ── Agent edit review (accept / reject / reopen native diff) ──
  | { type: 'acceptEdit'; editId: string }
  | { type: 'rejectEdit'; editId: string }
  | { type: 'reviewEdit'; editId: string }
  // ── Studio tab ──
  | { type: 'refreshStudio' }
  | { type: 'refreshKnowledge' }
  | { type: 'switchProject'; projectId: string }
  | { type: 'createProject'; projectId: string; name?: string; gateway?: string; model?: string }
  | { type: 'runWorkflow'; workflowName: string; parameters?: Record<string, unknown>; projectId?: string; inputFiles?: string[]; wait?: boolean }
  | { type: 'describeWorkflow'; workflowName: string }
  | { type: 'startResearchLoop'; researchGoal: string; projectId?: string; maxIterations?: number; agentic?: boolean; requires?: Record<string, unknown>; runtimeMode?: string }
  | { type: 'stopResearchLoop'; loopId: string; projectId?: string }
  | { type: 'ingestKnowledge'; files: string[]; sources: string[]; projectId?: string }
  | { type: 'pickKnowledgeFiles' }
  | { type: 'catalogSearch'; query?: string; projectId?: string }
  | { type: 'setKnowledgeGraphConstraints'; constraints: KnowledgeGraphConstraints }
  | { type: 'cancelExecution'; executionId: string }
  | { type: 'approveGateStep'; executionId: string; stepName: string; approved: boolean }
  | { type: 'removeWatch'; executionId: string }
  // ── System tab (skills, MCP servers, command help) ──
  | { type: 'refreshSystem' }
  | { type: 'setSkillActive'; sessionId: string; name: string; active: boolean }
  | { type: 'checkMcpHealth'; serverId: string }
  // ── Management cards (M5): chat-row query cards driven by bridge verbs ──
  | { type: 'mgmtCardQuery'; requestId: string; sessionId: string; kind: MgmtCardKind; arg?: string }
  // Tool card expand: fetch the FULL result for a bounded (16 KiB) preview
  // from the session's server-side result journal (round-3 task 2).
  | { type: 'toolResultRequest'; sessionId: string; toolCallId: string };

// ── Inbound (extension host → webview) ─────────────────────────────────────

/** Canonical budget read-model payload (T1 control-schema budget_state). */
export interface BudgetStatePayload {
  llm_calls_used: number;
  llm_calls_limit: number | null;
  tool_rounds_used: number;
  tool_rounds_limit: number;
  context_used_tokens: number;
  context_limit_tokens: number;
  session_id: string;
}

/** C3: per-turn token/cost/context chip payload (session/cost event). */
export interface CostStatePayload {
  session_id: string;
  model: string;
  turn_tokens: number;
  total_tokens: number;
  cost_usd_turn: number;
  cost_usd_total: number;
  /** false when the provider is not priced (local/platform) — chip shows "free". */
  price_known: boolean;
  context_used_tokens: number;
  context_limit_tokens: number;
}

/** Canonical thinking_delta payload (T1 transparency envelope). */
export interface ThinkingDeltaPayload {
  session_id: string;
  seq: number;
  text: string;
  partial: boolean;
}

/** Canonical thinking_done payload (T1 transparency envelope). */
export interface ThinkingDonePayload {
  session_id: string;
  summary_hint: string;
}

/** ADR-037 tool-selection record (one per model call, from r4 core). */
export interface ToolSelectionPayload {
  available_tools?: number;
  candidate_tools?: number;
  strategy?: string;
  selected_skills?: string[];
  /** Measured removals by reason (user-denied, plan-read, availability gate). */
  filtered_out?: Record<string, { count?: number; sample?: string[] }>;
  [key: string]: unknown;
}

export type InboundEvent =
  // Status
  | { type: 'status'; status: string }
  // ACP child source tree drifted after process start (round-3 task 7)
  | { type: 'sourceStale'; text: string }
  // Sessions
  | {
      type: 'sessions';
      sessions: SessionSummary[];
      running: string[];
    }
  // Inventory
  | { type: 'inventory'; endpoints: EndpointOption[]; connected: boolean }
  // Session selection
  | {
      type: 'sessionSelected';
      session: SessionSummary;
      fresh: boolean;
      messages: AgentSessionMessage[];
    }
  // Session state (running/idle/error)
  | { type: 'sessionState'; sessionId: string; state: 'running' | 'idle' | 'error' }
  // User message echo
  | { type: 'userMessage'; sessionId: string; text: string }
  // Assistant streaming delta
  | { type: 'assistantDelta'; sessionId: string; text: string }
  // Session thinking/reasoning. partial=true marks a growing snapshot of
  // the still-open block — the store replaces the live entry in place and
  // renders it expanded until the block closes (partial=false).
  | { type: 'sessionThinking'; sessionId: string; content: string; partial?: boolean }
  // Tool call (structured) — C6
  | {
      type: 'toolCall';
      sessionId: string;
      toolCallId: string;
      name: string;
      arguments?: Record<string, unknown>;
      /** Sanitized one-line summary from the server (canonical T1). */
      argsSummary?: string;
      status: 'pending' | 'completed' | 'error';
      resultPreview?: string;
      durationMs?: number;
      /** Wire preview was bounded and a full result exists in the journal. */
      resultTruncated?: boolean;
    }
  // Full tool result delivered after toolResultRequest (journal fetch)
  | {
      type: 'toolResult';
      sessionId: string;
      toolCallId: string;
      name?: string;
      content?: string;
      found: boolean;
    }
  // Token usage
  | { type: 'usage'; sessionId: string; promptTokens: number; completionTokens: number; totalTokens: number }
  // Budget read-model (wave T2 — canonical control-schema budget_state payload)
  | { type: 'budgetState'; sessionId: string; payload: BudgetStatePayload }
  // C3: cost/token/context chip read-model
  | { type: 'costState'; sessionId: string; payload: CostStatePayload }
  // Canonical T1 transparency thinking events (wave T3-ui S3)
  | { type: 'thinkingDelta'; sessionId: string; payload: ThinkingDeltaPayload }
  | { type: 'thinkingDone'; sessionId: string; payload: ThinkingDonePayload }
  // Assistant complete (final text)
  | { type: 'assistantComplete'; sessionId: string; text: string }
  // Tool state changes
  | { type: 'toolState'; sessionId: string; tool: string; status: string }
  // Agent edit lifecycle: staged → accepted | rejected
  | {
      type: 'editState';
      sessionId: string;
      editId: string;
      agentCallId?: string;
      path: string;
      relPath: string;
      added: number;
      removed: number;
      status: 'staged' | 'accepted' | 'rejected';
    }
  // Agent event (planning, tool, progress, error)
  | { type: 'agentEvent'; sessionId: string; event: Record<string, unknown> }
  // ADR-037 tool-selection telemetry: one record per model call. The store
  // collapses the sequence into a single updatable roll-up line per
  // session (never raw "event: …" transcript rows).
  | { type: 'toolSelection'; sessionId: string; payload: ToolSelectionPayload }
  // Model change confirmation
  | { type: 'modelChanged'; sessionId: string; model: string }
  // Session deleted
  | { type: 'sessionDeleted'; sessionId: string }
  // Cancelled
  | { type: 'cancelled'; sessionId: string }
  // B2b-full: typed plan stream (from planning_start / planning_complete)
  | { type: 'planEvent'; sessionId: string; plan: PlanEventPayload }
  // B2b-full: typed diff stream (tool-produced file edit preview)
  | { type: 'diffEvent'; sessionId: string; diff: DiffEventPayload }
  // B2b-full: typed citation stream (RAG knowledge citations)
  | { type: 'citationEvent'; sessionId: string; citations: CitationPayload[] }
  // Interaction mode change (ask | plan | agent)
  | { type: 'modeChanged'; sessionId: string; mode: string }
  // Shell streaming — per-line output chunks from a shell_exec running the
  // streaming executor (W5c). flow via shell_streaming → acp → agent-view.
  | { type: 'shellStream'; sessionId: string; streamId: string; chunk: string }
  // Ack from `shell/cancel` (or stream lifecycle end): transitions the panel
  // to terminal states; chunks arriving after this state are still appended
  // iff the status isn't already a terminal marker.
  | {
      type: 'shellStreamStatus';
      sessionId?: string;
      streamId: string;
      status: 'cancelled' | 'done' | 'timed_out' | 'error' | 'not-found' | 'disconnected';
      detail?: string;
    }
  // Studio snapshot (projects + workflows + watched executions)
  | { type: 'studioState'; projects: ProjectEntry[]; currentProjectId: string | null; workflows: WorkflowEntry[]; executions: ExecutionSnapshot[] }
  // Studio side-snapshots: project knowledge graph + research loops + verification
  | { type: 'knowledgeState'; knowledgeGraph: KnowledgeGraphPayload | null; researchLoops: ResearchLoopPayload; verification: KnowledgeVerificationPayload | null; gatewayOnline: boolean; projectId: string | null; knowledgeGraphs?: KnowledgeGraphMeta[] | null; knowledgeGraphConstraints?: KnowledgeGraphConstraints | null; loopRuntime?: LoopRuntimeInfo | null; selectedGraphContents?: GraphContentPayload[]; reconcile?: KnowledgeReconcilePayload | null }
  // Workflow describe (planned-lane preview for the Executions DAG)
  | { type: 'workflowDescribe'; workflowName: string; describe?: WorkflowDescribe; error?: string }
  // Science catalogue search results for the Knowledge panel card
  | { type: 'catalogState'; query: string; payload?: { project_id?: string; query?: string; results?: CatalogSearchHit[]; total?: number }; error?: string }
  // System tab snapshot (skills + MCP + slash catalog); skillsOnly = partial update
  | { type: 'systemState'; skills: SkillEntry[]; sessionBound?: boolean; skillsSessionId?: string; commands?: CommandEntry[]; mcpServers?: MCPServerInfo[]; mcpTools?: MCPToolInfo[]; errors?: Record<string, string>; skillsOnly?: boolean }
  // Ingest telemetry after a knowledge/import call
  | { type: 'knowledgeIngested'; result: Record<string, unknown> }
  // File chips returned by the extension host's file-picker
  | { type: 'knowledgeFilesPicked'; files: string[] }
  // Management card responses (M5) — the row lands in the Transcript,
  // keyed by sessionId + requestId; identical semantics to the v3 mgmt-card
  // chat message. status='pending' until the bridge request returns.
  | {
      type: 'mgmtCard';
      requestId: string;
      sessionId: string;
      kind: MgmtCardKind;
      payload: unknown;
      status: 'pending' | 'ok' | 'error';
      error?: string;
      at: string;
    }
  // A3: shared prompt history tape (oldest → newest), pushed at init and
  // after every accepted send.
  | { type: 'historyState'; history: string[] }
  // D5: editor-context chip from the active VS Code editor
  // (`file.ts#3-9`, `file.ts#3`, `file.ts` for cursor-only, null when none).
  | { type: 'editorContext'; chip: string | null }
  // C4: /compact answer (from sessions/compact via the host)
  | {
      type: 'compactResult';
      sessionId: string;
      changed: boolean;
      from: number;
      to: number;
      keepRecent: number;
      reason?: string;
      error?: string;
    }
  // Error
  // ADR-103 VP-2 (bridge backend): the console's inline cards.
  | ({ type: 'approvalRequest'; sessionId: string } & ApprovalAsk)
  | { type: 'approvalResolved'; sessionId: string; id: string | number; decision: string }
  | { type: 'askUserRequest'; sessionId: string; id: string | number; question: string; options: string[] }
  | { type: 'askUserResolved'; sessionId: string; id: string | number }
  | { type: 'planConfirm'; sessionId: string; request: string }
  /** A turn refused before it ran (session_conflict, stage before_turn):
   *  its text goes back into the composer. */
  | { type: 'restoreDraft'; sessionId: string; text: string; force?: boolean }
  // VP-2b (bridge backend): queue, !cmd cells, turn ids, citations, changed files, plan.
  | { type: 'queueState'; sessionId: string; items: string[] }
  | { type: 'shellStarted'; sessionId: string; id: string; echo: string }
  | { type: 'shellResult'; sessionId: string; id: string; text: string; exitCode: number }
  | { type: 'turnOpened'; sessionId: string; turnId: string }
  | { type: 'citations'; sessionId: string; turnId: string; citations: ViewCitation[] }
  | { type: 'filesChanged'; sessionId: string; files: ChangedFile[] }
  | { type: 'planState'; sessionId: string; steps: PlanStepRow[] }
  /** A `key` updates the same row in place (a team's or a plan run's live row, VP-4b). */
  | { type: 'notice'; sessionId: string; text: string; key?: string }
  /** VP-4b: `/teamup on` armed the next message (`team:on · …`; "" = disarmed). */
  | { type: 'teamArmed'; sessionId: string; label: string }
  // VP-2b part 2: slash output card, dock focus (`/changes`), `@` candidates.
  | { type: 'slashOutput'; sessionId: string; command: string; lines: OutputLine[] }
  /** Turns a slash command cut from the session (`/retry`): their rows go too. */
  | { type: 'dropTurns'; sessionId: string; turnIds: string[] }
  | { type: 'dockFocus'; sessionId: string; panel: 'files' | 'plan' }
  | { type: 'mentionCandidates'; sessionId: string; query: string; items: string[] }
  /** VP-3a: the session's sandbox (ADR-101) and web access (ADR-099).
   *  `sandboxNotice` is the console's session-open row ("" = none). */
  | { type: 'policyState'; sessionId: string; sandboxNotice: string; sandboxChip: string; web: string }
  /** VP-3b: the `remote:` chip (`remote:running · lease 47m`); empty = local. */
  | { type: 'venueState'; sessionId: string; chip: string; chipState: string }
  /** VP-2c: the prompt being edited ("" = none); the history was cut from this turn. */
  | { type: 'rewindPending'; sessionId: string; turnId: string }
  /** VP-2d: the bridge's slash catalog plus the host's verbs, for the `/` popup. */
  | { type: 'slashCatalog'; commands: { name: string; hint: string }[] }
  | { type: 'truncateFrom'; sessionId: string; turnId: string }
  /** Local echo of the Sessions filters (kept in the store so the controls
   *  survive a remount when a filter empties the list). */
  | { type: 'sessionFilterState'; filter: SessionFilterState }
  /** The agent view's backend (the bridge queues messages typed during a turn). */
  | { type: 'backendInfo'; backend: string }
  | { type: 'error'; message: string; sessionId?: string };

/** The Sessions filters sent to the server (VP-3a). */
export interface SessionFilterState {
  state: string;
  venue: string;
  includeArchived: boolean;
}

/** One line a slash command printed (the bridge's OutputLine). */
export interface OutputLine {
  kind: string;
  text: string;
}

/** A passage a reply cited (K1.4); `openCitation` sends it back. */
export interface ViewCitation {
  sid: string;
  title: string;
  artifactId: string;
  sourceId: string;
  projectId: string;
  preview: string;
}

/** One file the session changed (the console's /changes). */
export interface ChangedFile {
  path: string;
  inserted: number;
  deleted: number;
  created: boolean;
  reverted: boolean;
}

/** One session-plan step; `depth` nests it under its parent. */
export interface PlanStepRow {
  id: string;
  title: string;
  status: string;
  depth: number;
}

/** ADR-104 approval answers (amendment 1: no project-wide answer); a card
 * offers the subset the ask allows. */
export type ApprovalDecision = 'once' | 'always' | 'reject';

export type PlanAction = 'execute_auto' | 'execute_ask' | 'keep_planning';

/** One tool approval the bridge is waiting on. */
export interface ApprovalAsk {
  id: string | number;
  tool: string;
  description: string;
  /** The plan agent or team role that asks ("" / absent = the lead). */
  agent?: string;
  /** file_write / file_edit: the server-computed diff of the pending change. */
  path?: string;
  diff?: string;
  /** A content fence raised this ask: no "always", the yes covers this call only. */
  fenced: boolean;
  fenceReason?: string;
  options: ApprovalDecision[];
}

// ── B2b-full: typed event payloads ─────────────────────────────────────────

export interface PlanEventPayload {
  phase: 'start' | 'complete';
  steps?: PlanStep[];
  summary?: string;
}

export interface PlanStep {
  id: string;
  description: string;
  status: 'pending' | 'running' | 'done' | 'failed';
}

export interface DiffEventPayload {
  path: string;
  action: 'create' | 'modify' | 'delete';
  hunks?: DiffHunk[];
  summary?: string;
}

export interface DiffHunk {
  header: string;
  lines: { kind: 'add' | 'del' | 'context'; text: string }[];
}

export interface CitationPayload {
  source: string;
  title?: string;
  excerpt?: string;
  score?: number;
  uri?: string;
}

// ── Tool call (structured) — C6 ────────────────────────────────────────────

export interface ToolCallEvent {
  type: 'tool_call_start' | 'tool_call_done' | 'usage';
  name?: string;
  arguments?: Record<string, unknown>;
  tool_call_id?: string;
  status?: 'pending' | 'completed' | 'error';
  result_preview?: string;
  duration_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

// ── Management cards (M5) ────────────────────────────────────────────────

export type MgmtCardKind =
  | 'wf-list'
  | 'wf-show'
  | 'wf-run'
  | 'wf-results'
  | 'loop-list'
  | 'loop-status'
  | 'session-view'
  | 'knowledge-status'
  | 'knowledge-detail'
  | 'knowledge-graph'
  | 'request-op';

export interface MgmtCardMessage {
  requestId: string;
  kind: MgmtCardKind;
  payload: unknown;
  status: 'pending' | 'ok' | 'error';
  error?: string;
  at: string;
}

/** Descriptor for the query → (method, params, card) mapping that the
 * host side resolves against the bridge. Kept in shared types so the
 * webview knows exactly which verbs exist. */
export interface MgmtCardQuerySpec {
  kind: MgmtCardKind;
  bridgeMethod: string;
  params: Record<string, unknown>;
}

// ── mgmt-card.v1 payload shapes (bridge responses) ─────────────────────────

export interface MgmtRow {
  key: string;
  value: string;
}

export interface WfListResponse {
  workflows?: Array<{
    workflow_name?: string;
    name?: string;
    description?: string;
    tags?: string[];
  }>;
  count?: number;
}

export interface WfRunStep {
  name?: string;
  step_name?: string;
  id?: string;
  status?: string;
  state?: string;
  duration_ms?: number;
  [key: string]: unknown;
}

export interface WfRunDetail {
  execution_id?: string;
  workflow_name?: string;
  status?: string;
  progress?: number;
  error?: string;
  steps?: WfRunStep[];
  step_results?: Record<string, WfRunStep> | WfRunStep[];
  attempts?: WfRunStep[];
  [key: string]: unknown;
}

export interface LoopListResponse {
  loops?: Array<{
    loop_id?: string;
    status?: string;
    current_iteration?: number;
    max_iterations?: number;
    research_goal?: string;
    [key: string]: unknown;
  }>;
  active_count?: number;
  runtime?: string | null;
  project_id?: string;
}

export interface LoopIteration {
  iteration?: number;
  status?: string;
  hypothesis?: string;
  result_summary?: string;
  started_at?: string;
  finished_at?: string;
}

export interface LoopStatusResponse {
  loop_id?: string;
  status?: string;
  current_iteration?: number;
  max_iterations?: number;
  phase?: string;
  project_id?: string;
  runtime?: string;
  created_at?: string;
  error_message?: string;
  recent_iterations?: LoopIteration[];
  [key: string]: unknown;
}

export interface SessionViewResponse {
  session_id?: string;
  project_id?: string;
  authority_note?: string | null;
  loops?: Array<Record<string, unknown>>;
  knowledge_digest?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface KnowledgeStatus {
  nodes_by_label?: Record<string, number>;
  edges?: number;
  last_ingest_summary?: string;
  growth_since_last?: number;
  runtime?: string;
  [key: string]: unknown;
}

export interface KnowledgeInventory {
  nodes?: number;
  edges?: number;
  patterns?: number;
  lessons?: number;
  backend?: string | null;
}

export interface KnowledgeGraphEntry {
  graph_id?: string;
  name?: string;
  kind?: string;
  owner?: string;
  description?: string;
  [key: string]: unknown;
}

// ── Data types ──────────────────────────────────────────────────────────────

export interface SessionSummary {
  id: string;
  name: string;
  model?: string;
  updated_at?: string;
  message_count?: number;
  /** Research project the chat belongs to (empty/absent = untagged). */
  project_id?: string;
  /** C2: pinned (rides the sessions.pinned_at column; pinned-first sort). */
  pinned?: boolean;
  /** VP-3a (bridge backend): where it runs (local | frontend | cluster), its
   * state, how the copies agree (local-only | remote-only | mirrored | degraded). */
  venue?: string;
  state?: string;
  sync?: string;
  archived?: boolean;
}

export interface AgentSessionMessage {
  role: string;
  content: string;
  /** ADR-057 turn id (bridge backend): fork and save address turns by it. */
  turnId?: string;
}

export interface EndpointOption {
  id: string;
  label: string;
  status: string;
}

// ── Studio surface types ─────────────────────────────────────────────────────

export interface ProjectEntry {
  project_id: string;
  name?: string | null;
  active?: boolean;
  gateway_url?: string | null;
  model?: string | null;
}

export interface KnowledgeNode {
  id: string;
  type?: string;
  name?: string;
  label?: string;
  status?: string;
  confidence?: number;
  [key: string]: unknown;
}

export interface KnowledgeEdge {
  from: string;
  to: string;
  label?: string;
  type?: string;
  [key: string]: unknown;
}

/** Raw /gw/knowledge/graph payload (project-scoped). */
export interface KnowledgeGraphPayload {
  nodes?: KnowledgeNode[];
  edges?: KnowledgeEdge[];
  [key: string]: unknown;
}

/** Registered knowledge graph metadata (0069 registry, /gw/knowledge/graphs). */
export interface KnowledgeGraphMeta {
  graph_id: string;
  name: string;
  description?: string;
  kind?: string;
  project_id?: string | null;
  status: string;
  owner_id?: string;
  [key: string]: unknown;
}

/** User's multi-graph selection constraints (Studio selector state). */
export interface KnowledgeGraphConstraints {
  /** Graph ids/names always included. */
  include: string[];
  /** Graph ids/names always excluded (wins over include). */
  exclude: string[];
  /** Whether the agent may add relevance-ranked graphs automatically. */
  discovery: boolean;
}

/** ADR-031 reconciliation payload (checker state, /gw/knowledge/reconcile). */
export interface KnowledgeReconcilePayload {
  project_id: string;
  catalogue_available?: boolean;
  counts?: Record<string, number>;
  assets_sampled?: number;
  assets?: Array<{
    document_id: string;
    state: string;
    reasons?: string[];
    chunk_nodes?: number;
    has_catalogue?: boolean;
    has_embeddings?: boolean;
  }>;
  orphaned_vector_rows?: number;
  orphaned_chunk_nodes?: number;
}

/** ADR-031 plane links hydrated server-side per catalogue row. */
export interface CatalogLinks {
  document_id: string;
  link_method: 's3_object' | 'filename' | string;
  chunk_nodes: number;
  vector_rows: Array<{ model: string; dimension: number; rows: number }>;
}

/** One science catalogue row (/gw/catalog/search entry). */
export interface CatalogSearchHit {
  s3_key: string;
  state: string;
  summary?: string;
  deterministic_summary?: string;
  perception_summary?: string | null;
  perception_model?: string | null;
  detected_modality?: string;
  tags?: string[];
  filename_signals?: string[];
  relevance_score?: number;
  links?: CatalogLinks | null;
  [key: string]: unknown;
}

export interface ResearchLoop {
  loop_id: string;
  status?: string;
  phase?: string;
  research_goal?: string;
  current_iteration?: number;
  max_iterations?: number;
  iterations?: Array<Record<string, unknown>>;
  detail?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** Raw /gw/knowledge/loop payload (list + optional detail). */
export interface ResearchLoopPayload {
  loops?: ResearchLoop[];
  detail?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** Raw /gw/knowledge/verify/status payload (open shape across gateway versions). */
export interface KnowledgeVerificationPayload {
  status?: string;
  verified?: boolean;
  confidence?: number;
  total_checks?: number;
  passed_checks?: number;
  failed_checks?: number;
  blocking_findings?: number;
  report_id?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface WorkflowEntry {
  workflow_name: string;
  name?: string | null;
  description?: string | null;
  tags?: string[];
}

/** Workflow describe payload (/gw/workflows/{name}) — preview before run. */
export interface WorkflowDescribe {
  workflow_name?: string;
  version?: string;
  description?: string;
  scope?: string;
  parameter_schema?: Record<string, unknown>;
  /** Declared steps (workflow.json) — strings or {name, …} objects. */
  steps?: Array<string | Record<string, unknown>>;
  requirements?: Array<string | Record<string, unknown>>;
  triggers?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** Loop runtime capabilities (venue the research loops resolve to). */
export interface LoopRuntimeInfo {
  runtime: string | null;
  durable: boolean;
  distributed: boolean;
  backends: string[];
}

/** Bounded nodes+edges of one selected knowledge graph (0069 content route). */
export interface GraphContentPayload {
  graph_id: string;
  name?: string;
  kind?: string;
  project_id?: string | null;
  nodes?: KnowledgeNode[];
  edges?: KnowledgeEdge[];
  truncated?: boolean;
}

// ── System tab types ────────────────────────────────────────────────────────

/** One companion skill with its loaded (active) state. */
export interface SkillEntry {
  name: string;
  description?: string;
  source?: string;
  active: boolean;
  tools?: string[];
  keywords?: string[];
}

/** One slash-command catalog entry (companion parity). */
export interface CommandEntry {
  name: string;
  help: string;
  category: string;
}

/** Registered MCP server (gateway /gw/mcp/servers). */
export interface MCPServerInfo {
  name: string;
  server_id?: string;
  id?: string;
  url?: string;
  status?: string;
  description?: string;
  version?: string;
  [key: string]: unknown;
}

/** One MCP tool (grouped under its server in the UI). */
export interface MCPToolInfo {
  name: string;
  description?: string;
  server?: string;
  input_schema?: Record<string, unknown>;
}

export interface WorkflowStepSnapshot {
  name?: string;
  status?: string;
  [key: string]: unknown;
}

export interface ExecutionSnapshot {
  execution_id: string;
  workflow_name?: string | null;
  status?: string;
  progress?: number | null;
  error?: string | null;
  steps?: WorkflowStepSnapshot[];
  [key: string]: unknown;
}
