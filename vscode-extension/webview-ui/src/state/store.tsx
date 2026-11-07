/**
 * Application store.
 *
 * All webview state lives here, in a single reducer fed by one subscription to
 * the extension host. Components read it through `useApp()`.
 *
 * This is deliberately not a per-component hook: every consumer must observe
 * the same sessions, transcripts and streaming flags, and the host must see
 * exactly one subscriber.
 */

import {
  createContext,
  useContext,
  useEffect,
  useReducer,
  type ReactNode,
} from 'react';
import { subscribe } from '@/lib/acp-bridge';
import { stripThinking } from '@/lib/text';
import { requestReady } from '@/lib/actions';
import { createStreamDeltaDispatcher } from './delta-batcher';
import { initialAsks, reduceAsks, type AsksSlice } from './asks';
import { initialExtras, reduceExtras, type ExtrasSlice } from './extras';
import type {
  AgentSessionMessage,
  EndpointOption,
  BudgetStatePayload,
  CatalogSearchHit,
  CostStatePayload,
  CommandEntry,
  ExecutionSnapshot,
  GraphContentPayload,
  InboundEvent,
  KnowledgeGraphConstraints,
  KnowledgeGraphMeta,
  KnowledgeGraphPayload,
  KnowledgeReconcilePayload,
  KnowledgeVerificationPayload,
  LoopRuntimeInfo,
  MCPServerInfo,
  MCPToolInfo,
  MgmtCardKind,
  MgmtCardMessage,
  ProjectEntry,
  ResearchLoop,
  SessionSummary,
  SkillEntry,
  ToolSelectionPayload,
  WorkflowDescribe,
  WorkflowEntry,
} from '@/types/protocol';

// ── State shape ────────────────────────────────────────────────────────────

export type EntryKind = 'msg' | 'thinking' | 'event' | 'shell' | 'output';
export type RunState = 'running' | 'idle' | 'error';

export interface TranscriptEntry {
  /** Stable, monotonically increasing id — used as the React key so
   *  entries inserted mid-list (e.g. thinking blocks) don't remount and
   *  replay entrance animations. */
  id: number;
  kind: EntryKind;
  /** Present for `msg` entries only. */
  role?: 'You' | 'R4 Agent';
  text: string;
  error?: boolean;
  /** Thinking entries only: true while the block is still being written.
   *  Live entries render expanded and auto-collapse once the block closes
   *  (a non-partial `sessionThinking` lands, or the assistant completes). */
  live?: boolean;
  /** Reducer marker for upsert-style entries (toolSelection roll-up). */
  tag?: string;
  /** User messages (bridge backend): the ADR-057 turn the message opened. */
  turnId?: string;
}

/** Top-level view tab: chat framing vs the Studio graph surface vs System. */
export type ViewTab = 'chat' | 'studio' | 'system';

/** Studio sub-panel: execution DAG vs the knowledge/loops constellation. */
export type StudioPanel = 'executions' | 'knowledge';

/** Normalized interaction mode per chat session. */
export type SessionMode = 'ask' | 'plan' | 'agent';

/** Snapshot of the Studio surface (projects + workflows + live executions). */
export interface StudioSlice {
  projects: ProjectEntry[];
  currentProjectId: string | null;
  workflows: WorkflowEntry[];
  executions: ExecutionSnapshot[];
  /** Project knowledge graph from the /gw/knowledge/graph route. */
  knowledgeGraph: KnowledgeGraphPayload | null;
  /** Research loops (console Research DAG page). */
  researchLoops: ResearchLoop[];
  /** Detail of the current/latest research loop (iterations for the DAG). */
  researchLoopDetail: Record<string, unknown> | null;
  /** Latest knowledge verification state (verify/status; null when gateway lacks it). */
  knowledgeVerification: KnowledgeVerificationPayload | null;
  /** ADR-031 plane reconciliation state (§12 preflight; null when gateway lacks it). */
  knowledgeReconcile: KnowledgeReconcilePayload | null;
  /** Live refresh reachability for the Studio knowledge accessors. */
  gatewayOnline: boolean;
  /** Registered knowledge graphs (0069 registry) for the graph selector. */
  knowledgeGraphs: KnowledgeGraphMeta[];
  /** User's multi-graph selection constraints (include/exclude/discovery). */
  knowledgeGraphConstraints: KnowledgeGraphConstraints;
  /** Last ingest telemetry from knowledge/import. */
  knowledgeIngested: Record<string, unknown> | null;
  /** File chips pending from an extension-host file picker. */
  pendingKnowledgeFiles: string[];
  /** execution_id of the node focused in the graph inspector. */
  selectedExecutionId?: string;
  /** step name focused inside the selected execution, if any. */
  selectedStepName?: string;
  /** Whether an initial studioState has been received. */
  loadedOnce: boolean;
  /** Workflow catalog selection (drives the planned preview lane). */
  selectedWorkflowName?: string;
  /** Describe cache per workflow name (steps + schema; or the load error). */
  workflowDescribes: Record<string, WorkflowDescribe | { error: string }>;
  /** Research-loop venue capabilities from the research/loops payload. */
  loopRuntime: LoopRuntimeInfo | null;
  /** Bounded content per selected knowledge graph (selector include list). */
  selectedGraphContents: GraphContentPayload[];
  /** Science catalogue rows for the current project (last catalog/search). */
  catalogHits: CatalogSearchHit[];
  /** query of the last catalogue search (row context), or error message. */
  catalogQuery: string;
  catalogError: string | null;
}

/** System tab snapshot (skills, MCP servers, slash-command catalog). */
export interface SystemSlice {
  skills: SkillEntry[];
  sessionBound: boolean;
  skillsSessionId?: string;
  commands: CommandEntry[];
  mcpServers: MCPServerInfo[];
  mcpTools: MCPToolInfo[];
  /** Per-slice load errors (skills | commands | mcpServers | mcpTools). */
  errors: Record<string, string>;
  loadedOnce: boolean;
}

export interface ShellStreamState {
  streamId: string;
  sessionId?: string;
  /** Joined chunk text, truncated to STREAM_PANEL_MAX_BYTES. */
  body: string;
  lines: number;
  startedAt: number;
  status: 'running' | 'done' | 'cancelled' | 'timed_out' | 'error' | 'not-found' | 'disconnected';
  detail?: string;
}

/** Max bytes kept per stream in the panel — past this the store truncates
 * the front (keep the visible tail, no silent unbounded growth). */
export const STREAM_PANEL_MAX_BYTES = 256 * 1024;

const STREAM_STATUS_TO_TERMINAL: Readonly<Record<string, ShellStreamState['status']>> = {
  done: 'done',
  cancelled: 'cancelled',
  timed_out: 'timed_out',
  error: 'error',
  'not-found': 'not-found',
  disconnected: 'disconnected',
};

/** Bound retained terminal chips — long sessions otherwise accumulate one
 *  card per shell call forever (the "cannot remove" bug). Oldest terminal
 *  chips are dropped past this count; running streams are never pruned. */
export const MAX_TERMINAL_STREAMS = 10;

function pruneTerminalStreams(streams: Record<string, ShellStreamState>): Record<string, ShellStreamState> {
  const terminals = Object.values(streams)
    .filter((s) => s.status !== 'running')
    .sort((a, b) => a.startedAt - b.startedAt);
  if (terminals.length <= MAX_TERMINAL_STREAMS) return streams;
  const next = { ...streams };
  for (const old of terminals.slice(0, terminals.length - MAX_TERMINAL_STREAMS)) {
    delete next[old.streamId];
  }
  return next;
}

export interface AppState {
  sessions: SessionSummary[];
  currentId?: string;
  runStates: Record<string, RunState>;
  endpoints: EndpointOption[];
  /** Live/running shell streams keyed by streamId (companion W5c streaming). */
  shellStreams: Record<string, ShellStreamState>;
  /** Tool activity happened in the current turn per session (tool-call card
   *  seen since the last user message). Lets the dead-window guard skip
   *  tool-only turns instead of claiming 'no content' (H.5). */
  toolActivityThisTurn: Record<string, boolean>;
  connected: boolean;
  status: string;
  lastError?: string;
  transcripts: Record<string, TranscriptEntry[]>;
  /** Sessions with an assistant reply currently streaming in. */
  streaming: Record<string, boolean>;
  /** A3: shared prompt history tape (oldest → newest), one file across r4
   *  surfaces (~/.config/r4/prompt-history.jsonl). */
  history: string[];
  /** D5: editor-context chip (`file.ts#3-9`) — follows the VS Code cursor. */
  editorContext: string | null;
  /** In-flight tool calls keyed by (sessionId → list of cards). */
  toolCalls: Record<string, ToolCallCard[]>;
  /** Management cards (M5) — answer cards from the slash-verb bridge, keyed
   *  per-session like toolCalls; the pending row flips to ok|error when the
   *  host's mgmtCard event lands for the matching requestId. */
  cards: Record<string, MgmtCardMessage[]>;
  /** Token usage per session (C6). */
  usage: Record<string, { prompt: number; completion: number; total: number }>;
  /** One-shot: stale ACP source warning already rendered (task 7). */
  sourceStaleWarned: boolean;
  /** Budget read-model per session (wave T2). */
  budgets: Record<string, BudgetStatePayload>;
  /** C3: cost/token/context chip read-model per session. */
  costs: Record<string, CostStatePayload>;
  /** Thinking trace per session (wave T3-ui S3). */
  thinking: Record<string, ThinkingTrace>;
  /** Per-session interaction mode (ask | plan | agent). */
  modes: Record<string, SessionMode>;
  /** Which top-level tab is on screen. */
  tab: ViewTab;
  /** Which Studio sub-panel is focused (deep-link target from chat). */
  studioPanel: StudioPanel;
  /** Studio surface state. */
  studio: StudioSlice;
  /** System tab (skills, MCP servers, command help) state. */
  system: SystemSlice;
  /** Agent-proposed file edits per session (drives the edit cards). */
  edits: Record<string, SessionEdit[]>;
  /** Bridge backend inline cards: approvals, questions, plan confirm (VP-2). */
  asks: AsksSlice;
  /** Bridge backend read-models: queue, changed files, plan, citations (VP-2b). */
  extras: ExtrasSlice;
}

/** One agent-proposed file edit awaiting (or past) review. */
export interface SessionEdit {
  editId: string;
  agentCallId?: string;
  path: string;
  relPath: string;
  added: number;
  removed: number;
  status: 'pending_review' | 'accepted' | 'rejected';
}

/** A tool call card rendered in the transcript. */
export interface ToolCallCard {
  /** Wire preview was bounded; full result fetchable via toolResultRequest. */
  truncated?: boolean;
  /** Full result text fetched on expand (journal). */
  fullResult?: string;
  id: string;
  name: string;
  status: 'pending' | 'completed' | 'error';
  arguments?: Record<string, unknown>;
  /** Sanitized one-line args summary (canonical T1 emission). */
  argsSummary?: string;
  resultPreview?: string;
  durationMs?: number;
  /** Linked edit-session entry for applyEdit calls. */
  edit?: { editId: string; status: SessionEdit['status'] };
}

const initialState: AppState = {
  sessions: [],
  runStates: {},
  endpoints: [],
  shellStreams: {},
  toolActivityThisTurn: {},
  sourceStaleWarned: false,
  connected: false,
  status: 'connecting',
  transcripts: {},
  streaming: {},
  toolCalls: {},
  cards: {},
  usage: {},
  budgets: {},
  costs: {},
  thinking: {},
  modes: {},
  edits: {},
  asks: initialAsks,
  extras: initialExtras,
  history: [],
  editorContext: null,
  tab: 'chat',
  studioPanel: 'executions',
  studio: {
    projects: [],
    currentProjectId: null,
    workflows: [],
    executions: [],
    knowledgeGraph: null,
    knowledgeVerification: null,
    knowledgeReconcile: null,
    researchLoops: [],
    researchLoopDetail: null,
    gatewayOnline: true,
    knowledgeGraphs: [],
    knowledgeGraphConstraints: { include: [], exclude: [], discovery: false },
    knowledgeIngested: null,
    pendingKnowledgeFiles: [],
    selectedExecutionId: undefined,
    selectedStepName: undefined,
    loadedOnce: false,
    selectedWorkflowName: undefined,
    workflowDescribes: {},
    loopRuntime: null,
    selectedGraphContents: [],
    catalogHits: [],
    catalogQuery: '',
    catalogError: null,
  },
  system: {
    skills: [],
    sessionBound: false,
    skillsSessionId: undefined,
    commands: [],
    mcpServers: [],
    mcpTools: [],
    errors: {},
    loadedOnce: false,
  },
};

// ── Helpers ────────────────────────────────────────────────────────────────

function omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const next = { ...record };
  delete next[key];
  return next;
}

/** Apply a pure transform to one session's transcript. */
function withEntries(
  state: AppState,
  sessionId: string,
  transform: (entries: TranscriptEntry[]) => TranscriptEntry[],
): AppState {
  const current = state.transcripts[sessionId] ?? [];
  return {
    ...state,
    transcripts: { ...state.transcripts, [sessionId]: transform(current) },
  };
}

function isStreamingAssistant(entries: TranscriptEntry[]): boolean {
  const last = entries[entries.length - 1];
  return Boolean(last && last.kind === 'msg' && last.role === 'R4 Agent');
}

/** Module-level counter for stable entry ids (see TranscriptEntry.id). */
let nextEntryId = 0;

/** Create a transcript entry with a fresh stable id. */
function mkEntry(entry: Omit<TranscriptEntry, 'id'>): TranscriptEntry {
  return { ...entry, id: ++nextEntryId };
}

function toEntries(messages: AgentSessionMessage[]): TranscriptEntry[] {
  return messages.map((message) =>
    mkEntry({
      kind: 'msg',
      role: message.role === 'user' ? 'You' : 'R4 Agent',
      text: message.content,
      ...(message.turnId ? { turnId: message.turnId } : {}),
    }),
  );
}

/** Map any server-side mode alias onto the UI's three-state vocabulary. */
function normalizeMode(mode: string): SessionMode {
  const value = (mode || '').toLowerCase();
  if (value === 'autopilot' || value === 'agent' || value === 'auto') return 'agent';
  if (value === 'plan') return 'plan';
  return 'ask';
}

function describeEvent(event: Record<string, unknown>): string {
  const kind = typeof event.type === 'string' ? event.type : 'event';
  const message =
    typeof event.message === 'string'
      ? event.message
      : typeof event.detail === 'string'
        ? event.detail
        : '';
  return message ? `${kind}: ${message}` : kind;
}

// ── Reducer ────────────────────────────────────────────────────────────────

/** Local UI events that never cross the wire (tab switch, graph focus). */
export type UiEvent =
  | { type: 'ui/tab'; tab: ViewTab }
  | { type: 'ui/mode'; sessionId: string; mode: SessionMode }
  | { type: 'ui/studioPanel'; panel: StudioPanel }
  | { type: 'ui/focusExecution'; executionId?: string; stepName?: string }
  | { type: 'ui/selectWorkflow'; workflowName?: string }
  /** M5: webview dispatched a management verb — pending row lands
   *  immediately and flips when the host's mgmtCard event arrives. */
  | { type: 'ui/mgmtCardQueued'; requestId: string; sessionId: string; kind: MgmtCardKind; at: string }
  /** C4: /undo — drop transcripts from index on (client-side prompt restore). */
  | { type: 'ui/discardFrom'; sessionId: string; index: number }
  /** Remove a shell-stream chip from the panel (terminal streams only get
   *  a dismiss affordance; removal is client-local — the server stream is
   *  already dead by then). */
  | { type: 'ui/dismissShellStream'; streamId: string };

// ── Reducer groups ─────────────────────────────────────────────────────────
//
// The polyglot complexity gate caps one function at CCN 9, so the reducer is
// split into small event-group reducers. Each group returns `state` untouched
// when the event is not theirs; the dispatcher takes the first transformed
// result. Order matters only where two groups could claim the same type —
// groups here are disjoint by construction.

function reduceUiEvents(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'ui/tab') {
    return { ...state, tab: event.tab };
  }
  if (event.type === 'ui/mode') {
    return { ...state, modes: { ...state.modes, [event.sessionId]: event.mode } };
  }
  if (event.type === 'ui/studioPanel') {
    return { ...state, studioPanel: event.panel };
  }
  if (event.type === 'ui/focusExecution') {
    return {
      ...state,
      studio: {
        ...state.studio,
        selectedExecutionId: event.executionId,
        selectedStepName: event.stepName,
      },
    };
  }
  if (event.type === 'ui/selectWorkflow') {
    return {
      ...state,
      studio: { ...state.studio, selectedWorkflowName: event.workflowName },
    };
  }
  return state;
}

function reduceStudioGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'modeChanged') {
    const normalized = normalizeMode(event.mode);
    return {
      ...state,
      modes: { ...state.modes, [event.sessionId]: normalized },
    };
  }
  if (event.type === 'knowledgeState') {
    return applyKnowledgeSnapshot(state, event);
  }
  if (event.type === 'knowledgeIngested' || event.type === 'knowledgeFilesPicked') {
    return applyKnowledgeSideSignal(state, event);
  }
  if (event.type === 'workflowDescribe') {
    return applyWorkflowDescribe(state, event);
  }
  if (event.type === 'catalogState') {
    return applyCatalogState(state, event);
  }
  if (event.type !== 'studioState') return state;
  return { ...state, studio: applyStudioState(state, event) };
}

/** workflowDescribe payload → describe cache (or load error) for the preview lane. */
function applyWorkflowDescribe(
  state: AppState,
  event: Extract<InboundEvent, { type: 'workflowDescribe' }>,
): AppState {
  const entry: WorkflowDescribe | { error: string } =
    typeof event.error === 'string'
      ? { error: event.error }
      : (event.describe ?? { error: 'no describe payload' });
  return {
    ...state,
    studio: {
      ...state.studio,
      workflowDescribes: {
        ...state.studio.workflowDescribes,
        [event.workflowName]: entry,
      },
    },
  };
}

/** catalogState event → catalogue rows (project data card in Knowledge panel). */
export function applyCatalogState(
  state: AppState,
  event: Extract<InboundEvent, { type: 'catalogState' }>,
): AppState {
  /* Stale-answer guard: a slow response for a previous project must never
     land on the new project's card; only responses for the CURRENT project
     update rows. Errors also clear rows (an wedged error for the current
     query would otherwise leave stale hits rendering). */
  const payloadProject =
    typeof event.payload?.project_id === 'string' ? event.payload.project_id : null;
  const answersCurrentProject =
    payloadProject === null || payloadProject === state.studio.currentProjectId;
  if (!answersCurrentProject) {
    return state;
  }
  const hadError = typeof event.error === 'string';
  return {
    ...state,
    studio: {
      ...state.studio,
      catalogHits: hadError ? [] : (event.payload?.results ?? state.studio.catalogHits),
      catalogQuery: event.query,
      catalogError: hadError ? event.error! : null,
    },
  };
}

/** Shared skills/session fields of a systemState payload. */
function skillsSliceFields(
  system: SystemSlice,
  event: Extract<InboundEvent, { type: 'systemState' }>,
): Partial<SystemSlice> {
  return {
    skills: event.skills,
    sessionBound: event.sessionBound === true,
    skillsSessionId: event.skillsSessionId ?? system.skillsSessionId,
  };
}

/** Full (non-skillsOnly) systemState payload → complete System tab slice. */
function fullSystemSlice(
  system: SystemSlice,
  event: Extract<InboundEvent, { type: 'systemState' }>,
): SystemSlice {
  return {
    ...system,
    ...skillsSliceFields(system, event),
    commands: event.commands ?? system.commands,
    mcpServers: event.mcpServers ?? [],
    mcpTools: event.mcpTools ?? [],
    errors: event.errors ?? {},
    loadedOnce: true,
  };
}

/** systemState payload → System tab slice (skillsOnly = partial skills update). */
function reduceSystemGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'systemState') return state;
  const slice = event.skillsOnly
    ? { ...state.system, ...skillsSliceFields(state.system, event) }
    : fullSystemSlice(state.system, event);
  return { ...state, system: slice };
}

/** knowledgeState payload fallbacks — graph payload + connectivity. */
function knowledgeCoreFields(
  event: Extract<InboundEvent, { type: 'knowledgeState' }>,
): Pick<StudioSlice, 'knowledgeGraph' | 'knowledgeVerification' | 'gatewayOnline' | 'knowledgeReconcile'> {
  return {
    knowledgeGraph: event.knowledgeGraph ?? null,
    knowledgeVerification: event.verification ?? null,
    knowledgeReconcile: event.reconcile ?? null,
    gatewayOnline: event.gatewayOnline !== false,
  };
}

/** knowledgeState research-loop payload fallbacks. */
function knowledgeLoopFields(
  event: Extract<InboundEvent, { type: 'knowledgeState' }>,
): Pick<StudioSlice, 'researchLoops' | 'researchLoopDetail'> {
  return {
    researchLoops: event.researchLoops?.loops ?? [],
    researchLoopDetail: event.researchLoops?.detail ?? null,
  };
}

/** knowledgeState snapshot → studio slice (loops + graphs + verification). */
export function applyKnowledgeSnapshot(
  state: AppState,
  event: Extract<InboundEvent, { type: 'knowledgeState' }>,
): AppState {
  const studio = state.studio;
  /* Stale-response guard (audit B5): a snapshot that attests a different
     project than the current selection is a lost race (an in-flight
     refreshKnowledge(A) landing after the switch to B). Dropping it keeps
     project A's constellation from overwriting project B's state. A null
     attestation is unverifiable — apply it only when there is no selection
     to contradict. */
  const attested = event.projectId ?? null;
  if (attested !== null && studio.currentProjectId !== null && attested !== studio.currentProjectId) {
    return state;
  }
  return {
    ...state,
    studio: {
      ...studio,
      ...knowledgeCoreFields(event),
      ...knowledgeLoopFields(event),
      ...mergeKnowledgeRegistry(studio, event),
      loopRuntime: event.loopRuntime ?? studio.loopRuntime,
      selectedGraphContents:
        event.selectedGraphContents ?? studio.selectedGraphContents,
    },
  };
}

/** 0069 registry fields: fall back to the previous selection when absent. */
function mergeKnowledgeRegistry(
  studio: StudioSlice,
  event: Extract<InboundEvent, { type: 'knowledgeState' }>,
): Pick<StudioSlice, 'knowledgeGraphs' | 'knowledgeGraphConstraints'> {
  return {
    knowledgeGraphs: event.knowledgeGraphs ?? studio.knowledgeGraphs,
    knowledgeGraphConstraints:
      event.knowledgeGraphConstraints ?? studio.knowledgeGraphConstraints,
  };
}

/** Apply post-query / post-ingest / file-chip Flash Studio side-signals. */
function applyKnowledgeSideSignal(
  state: AppState,
  event: Extract<InboundEvent, { type: 'knowledgeIngested' | 'knowledgeFilesPicked' }>,
): AppState {
  const nextStudio = { ...state.studio };
  if (event.type === 'knowledgeIngested') {
    nextStudio.knowledgeIngested = event.result;
  } else if (event.type === 'knowledgeFilesPicked') {
    nextStudio.pendingKnowledgeFiles = event.files;
  }
  return { ...state, studio: nextStudio };
}

/** Merge a studioState snapshot into the studio slice (selection preserved when tracked). */
export function applyStudioState(state: AppState, event: Extract<InboundEvent, { type: 'studioState' }>): StudioSlice {
  const { projects = [], currentProjectId = null, workflows = [], executions = [] } = event;
  const keepSelection = studioSelectionTracked(state, executions);
  const selectedExecutionId = keepSelection ? state.studio.selectedExecutionId : undefined;
  const selectedStepName = keepSelection ? state.studio.selectedStepName : undefined;
  /* Project-switch invalidation (F3 + audit B4): the catalogue AND the
     whole knowledge surface belong to the previous project — clear them so
     nothing of project A renders while project B's refetch is in flight.
     Otherwise rows, graphs, loops, and selected-graph contents of the old
     project survive the switch for the entire async gap. */
  const projectChanged = state.studio.currentProjectId !== currentProjectId;
  return {
    ...state.studio,
    projects,
    currentProjectId,
    workflows,
    executions,
    selectedExecutionId,
    selectedStepName,
    catalogHits: projectChanged ? [] : state.studio.catalogHits,
    catalogError: projectChanged ? null : state.studio.catalogError,
    knowledgeGraph: projectChanged ? null : state.studio.knowledgeGraph,
    knowledgeVerification: projectChanged ? null : state.studio.knowledgeVerification,
    knowledgeReconcile: projectChanged ? null : state.studio.knowledgeReconcile,
    researchLoops: projectChanged ? [] : state.studio.researchLoops,
    researchLoopDetail: projectChanged ? null : state.studio.researchLoopDetail,
    knowledgeGraphs: projectChanged ? [] : state.studio.knowledgeGraphs,
    knowledgeGraphConstraints: projectChanged
      ? { include: [], exclude: [], discovery: false }
      : state.studio.knowledgeGraphConstraints,
    selectedGraphContents: projectChanged ? [] : state.studio.selectedGraphContents,
    loopRuntime: projectChanged ? null : state.studio.loopRuntime,
    loadedOnce: true,
  };
}

/** True when the current graph focus still points at a tracked execution. */
function studioSelectionTracked(state: AppState, executions: ExecutionSnapshot[]): boolean {
  const selectedId = state.studio.selectedExecutionId;
  if (!selectedId) return false;
  return executions.some((execution) => execution.execution_id === selectedId);
}

/** Session list lifecycle (connect, list, select, state). */
function reduceSessionGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'status') {
    return { ...state, status: event.status };
  }
  if (event.type === 'sessions') {
    const runningSet = new Set(event.running);
    const runStates: Record<string, RunState> = { ...state.runStates };
    for (const id of event.running) runStates[id] = 'running';
    // Demote any session still marked "running" that the host no longer
    // reports as active. The host's running list is the source of truth
    // (activeRequests), so a stale "running" here means a turn ended
    // without a matching sessionState event (e.g. the sessions snapshot
    // raced past the idle/error post). Preserve explicit "error" states
    // so a failed turn stays visible instead of being silently buried.
    for (const s of event.sessions) {
      if (!runningSet.has(s.id) && runStates[s.id] === 'running') {
        runStates[s.id] = 'idle';
      }
    }
    return { ...state, sessions: event.sessions, runStates };
  }
  if (event.type === 'inventory') {
    return { ...state, endpoints: event.endpoints, connected: event.connected };
  }
  return state;
}

/** Session selection + per-session run-state events. */
function reduceSessionSelectionGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'sessionSelected') {
    const id = event.session.id;
    const known = state.sessions.some((s) => s.id === id);
    return {
      ...state,
      currentId: id,
      lastError: undefined,
      sessions: known
        ? state.sessions.map((s) => (s.id === id ? event.session : s))
        : [event.session, ...state.sessions],
      transcripts: {
        ...state.transcripts,
        [id]: event.fresh ? [] : toEntries(event.messages),
      },
      streaming: omitKey(state.streaming, id),
    };
  }
  if (event.type === 'sessionState') {
    return {
      ...state,
      runStates: { ...state.runStates, [event.sessionId]: event.state },
    };
  }
  return state;
}

/** Partial thinking snapshot: grow or create the live thinking card in place. */
function upsertLiveThinking(
  entries: TranscriptEntry[],
  content: string,
  streamingAssistant: boolean | undefined,
): TranscriptEntry[] {
  const last = entries[entries.length - 1];
  if (last?.kind === 'thinking' && last.live) {
    return [...entries.slice(0, -1), { ...last, text: content }];
  }
  const live = { ...mkEntry({ kind: 'thinking', text: content }), live: true };
  if (streamingAssistant) {
    return [...entries.slice(0, -1), live, entries[entries.length - 1]];
  }
  return [...entries, live];
}

/** Closed thinking block: freeze the live card, or insert above the answer. */
function finalizeThinking(
  entries: TranscriptEntry[],
  content: string,
  streamingAssistant: boolean | undefined,
): TranscriptEntry[] {
  const last = entries[entries.length - 1];
  if (last?.kind === 'thinking' && last.live) {
    return [...entries.slice(0, -1), { ...last, text: content, live: false }];
  }
  const entry = mkEntry({ kind: 'thinking', text: content });
  if (streamingAssistant) {
    return [...entries.slice(0, -1), entry, entries[entries.length - 1]];
  }
  return [...entries, entry];
}

/** Transcript stream events: user echo, deltas, thinking, completion. */
function reduceStreamGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'userMessage') {
    const cleared = {
      ...state,
      streaming: omitKey(state.streaming, event.sessionId),
      toolActivityThisTurn: omitKey(state.toolActivityThisTurn, event.sessionId),
      // A new turn starts a new reasoning trace (it used to keep every turn's,
      // and the next turn's seq 0 overwrote the previous turn's).
      thinking: omitKey(state.thinking, event.sessionId),
    };
    return withEntries(cleared, event.sessionId, (entries) => [
      ...entries,
      mkEntry({ kind: 'msg', role: 'You', text: event.text }),
    ]);
  }
  if (event.type === 'assistantDelta') {
    const chunk = stripThinking(event.text);
    if (!chunk) return state;
    const id = event.sessionId;
    const appending = state.streaming[id];
    const next = withEntries(state, id, (entries) => {
      if (appending && isStreamingAssistant(entries)) {
        const last = entries[entries.length - 1];
        return [...entries.slice(0, -1), { ...last, text: last.text + chunk }];
      }
      return [...entries, mkEntry({ kind: 'msg', role: 'R4 Agent', text: chunk })];
    });
    return { ...next, streaming: { ...next.streaming, [id]: true } };
  }
  if (event.type === 'sessionThinking') {
    const content = event.content.trim();
    if (!content) return state;
    const streaming = state.streaming[event.sessionId];
    return withEntries(state, event.sessionId, (entries) => {
      const streamingAssistant = streaming && isStreamingAssistant(entries);
      // Partial snapshot of the still-open block: update the live entry in
      // place so the reasoning card grows in place; a closed block freezes
      // the live entry (or inserts above the answer being written).
      return event.partial
        ? upsertLiveThinking(entries, content, streamingAssistant)
        : finalizeThinking(entries, content, streamingAssistant);
    });
  }
  if (event.type === 'assistantComplete') {
    const id = event.sessionId;
    const text = stripThinking(event.text);
    const wasStreaming = state.streaming[id];
    const next = withEntries(state, id, (entries) => {
      // Collapse any still-open thinking card when the answer lands.
      const closed = entries.map((entry) =>
        entry.kind === 'thinking' && entry.live ? { ...entry, live: false } : entry,
      );
      // Replace the streamed draft with the authoritative final text.
      return assistantCompletionEntries(
        closed,
        text,
        wasStreaming,
        Boolean(state.toolActivityThisTurn[id]),
      );
    });
    return { ...next, streaming: omitKey(next.streaming, id) };
  }
  return state;
}

/**
 * applyEdit tool results carry the staged proposal (edit_id + line stats) in
 * their result preview. Parse it into the session edit slice and link the
 * originating tool card by toolCallId so the badge stays in sync.
 */
/** basename of a previewed path ('' when absent). */
function previewRelPath(preview: EditPreview): string {
  return preview.path?.split('/').pop() ?? '';
}

interface EditPreview {
  edit_id?: string;
  path?: string;
  added?: number;
  removed?: number;
  status?: string;
}

/** Shared path/stats fields from an edit preview payload. */
function editPreviewFields(
  preview: EditPreview,
): Pick<SessionEdit, 'path' | 'relPath' | 'added' | 'removed'> {
  return {
    path: preview.path ?? '',
    relPath: previewRelPath(preview),
    added: preview.added ?? 0,
    removed: preview.removed ?? 0,
  };
}

/** writeFile result → inline accepted row (no review pending). */
function writeFileEditEntry(
  event: Extract<InboundEvent, { type: 'toolCall' }>,
  preview: EditPreview,
  rowIndex: number,
): SessionEdit {
  return {
    editId: `writefile-${String(rowIndex)}`,
    agentCallId: event.toolCallId,
    ...editPreviewFields(preview),
    status: 'accepted' as const,
  };
}

/** applyEdit result → pending-review row linked to the tool call. */
function pendingReviewEntry(
  event: Extract<InboundEvent, { type: 'toolCall' }>,
  preview: EditPreview,
  editId: string,
): SessionEdit {
  return {
    editId,
    agentCallId: event.toolCallId,
    ...editPreviewFields(preview),
    status: 'pending_review',
  };
}

/** Parse the edit preview only for edit-capable tools (undefined otherwise). */
function previewForEditTool(
  event: Extract<InboundEvent, { type: 'toolCall' }>,
): EditPreview | undefined {
  const isEditTool = event.name === 'workspace.applyEdit' || event.name === 'workspace.writeFile';
  if (!isEditTool) return undefined;
  return parseEditPreview(event.resultPreview);
}

/** applyEdit result → replace/append the pending-review row. */
function mergePendingReviewEdit(
  edits: AppState['edits'],
  sessionId: string,
  event: Extract<InboundEvent, { type: 'toolCall' }>,
  preview: EditPreview,
  list: SessionEdit[],
): AppState['edits'] {
  const next = list.filter((e) => e.editId !== preview.edit_id);
  next.push(pendingReviewEntry(event, preview, preview.edit_id as string));
  return { ...edits, [sessionId]: next };
}

/** writeFile result → append the inline accepted row (no review pending). */
function writeFileEditState(
  edits: AppState['edits'],
  sessionId: string,
  event: Extract<InboundEvent, { type: 'toolCall' }>,
  preview: EditPreview,
  list: SessionEdit[],
): AppState['edits'] {
  const row = writeFileEditEntry(event, preview, list.length);
  return { ...edits, [sessionId]: [...list, row] };
}

function applyToolCallEditResult(
  edits: AppState['edits'],
  sessionId: string,
  event: Extract<InboundEvent, { type: 'toolCall' }>,
): AppState['edits'] {
  if (event.status === 'pending') return edits;
  const preview = previewForEditTool(event);
  if (!preview) return edits;
  const list = edits[sessionId] ?? [];
  if (!preview.edit_id) {
    // writeFile: stats only, no pending review — record the outcome inline.
    return writeFileEditState(edits, sessionId, event, preview, list);
  }
  return mergePendingReviewEdit(edits, sessionId, event, preview, list);
}

function optString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function optNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' ? value : fallback;
}

function parseEditPreview(resultPreview: string | undefined): EditPreview | undefined {
  if (!resultPreview) return undefined;
  try {
    const parsed: unknown = JSON.parse(resultPreview);
    if (typeof parsed !== 'object' || parsed === null) return undefined;
    const record = parsed as Record<string, unknown>;
    if (typeof record.added !== 'number') return undefined;
    return {
      edit_id: optString(record.edit_id),
      path: optString(record.path),
      added: optNumber(record.added, 0),
      removed: optNumber(record.removed, 0),
      status: optString(record.status),
    };
  } catch {
    return undefined;
  }
}

type EditStateEvent = Extract<InboundEvent, { type: 'editState' }>;

function editStatusOf(event: EditStateEvent): SessionEdit['status'] {
  if (event.status === 'accepted') return 'accepted';
  if (event.status === 'rejected') return 'rejected';
  return 'pending_review';
}

function editPathOf(existing: SessionEdit | undefined, event: EditStateEvent): string {
  return event.path || existing?.path || '';
}

function editRelPathOf(existing: SessionEdit | undefined, event: EditStateEvent): string {
  return event.relPath || existing?.relPath || (event.path.split('/').pop() ?? '');
}

/** Event counter with legacy-row fallback. */
function editCountOf(value: number | undefined, existing: number | undefined): number {
  return value ?? existing ?? 0;
}

/** Merge an editState notification over any existing row for the edit. */
function mergeSessionEdit(list: SessionEdit[], event: EditStateEvent): SessionEdit {
  const existing = list.find((e) => e.editId === event.editId);
  return {
    editId: event.editId,
    agentCallId: event.agentCallId ?? existing?.agentCallId,
    path: editPathOf(existing, event),
    relPath: editRelPathOf(existing, event),
    added: editCountOf(event.added, existing?.added),
    removed: editCountOf(event.removed, existing?.removed),
    status: editStatusOf(event),
  };
}

/** Keep the originating tool card's edit badge in sync with the session edit. */
function syncLinkedToolCard(
  cards: ToolCallCard[] | undefined,
  editId: string,
  status: SessionEdit['status'],
): ToolCallCard[] | undefined {
  return cards?.map((card) =>
    card.edit?.editId === editId ? { ...card, edit: { editId, status } } : card,
  );
}

/** Edit-session lifecycle notifications from the extension host. */
function reduceEditState(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'editState') return state;
  const sessionId = event.sessionId;
  const list = state.edits[sessionId] ?? [];
  const merged = mergeSessionEdit(list, event);
  const next = [...list.filter((e) => e.editId !== event.editId), merged];
  const updatedCalls = syncLinkedToolCard(state.toolCalls[sessionId], event.editId, merged.status);
  return {
    ...state,
    edits: { ...state.edits, [sessionId]: next },
    ...(updatedCalls ? { toolCalls: { ...state.toolCalls, [sessionId]: updatedCalls } } : {}),
  };
}

/** Pending tool card for a tool_call_start. */
function pendingToolCard(event: Extract<InboundEvent, { type: 'toolCall' }>): ToolCallCard {
  return {
    id: event.toolCallId,
    name: event.name,
    arguments: event.arguments,
    argsSummary: event.argsSummary,
    status: 'pending',
    resultPreview: event.resultPreview,
    durationMs: event.durationMs,
  };
}

/** Completed/error update for the matching card (edit badge linked for applyEdit). */
function completedToolCard(
  card: ToolCallCard,
  event: Extract<InboundEvent, { type: 'toolCall' }>,
): ToolCallCard {
  if (card.id !== event.toolCallId) return card;
  const preview = parseEditPreview(event.resultPreview);
  const editId = event.name === 'workspace.applyEdit' ? preview?.edit_id : undefined;
  return {
    ...card,
    status: event.status,
    resultPreview: event.resultPreview,
    durationMs: event.durationMs,
    ...(event.resultTruncated ? { truncated: true } : {}),
    ...(editId ? { edit: { editId, status: 'pending_review' as const } } : {}),
  };
}

/** Tool invocation events: state rows, structured cards, token usage. */
function reduceToolGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'toolState') {
    return withEntries(state, event.sessionId, (entries) => [
      ...entries,
      mkEntry({ kind: 'event', text: `${event.tool} — ${event.status}` }),
    ]);
  }
  if (event.type === 'toolCall') {
    const id = event.sessionId;
    // Any tool card in this window counts as turn activity for the guard.
    state = {
      ...state,
      toolActivityThisTurn: { ...state.toolActivityThisTurn, [id]: true },
    };
    // C6: tool_call_start → add a card; tool_call_done → update it
    const existing = state.toolCalls[id] ?? [];
    if (event.status === 'pending') {
      // Add new card if not already present
      const updated = existing.filter((t) => t.id !== event.toolCallId);
      updated.push(pendingToolCard(event));
      return { ...state, toolCalls: { ...state.toolCalls, [id]: updated } };
    }
    // completed / error — replace the matching card
    return {
      ...state,
      edits: applyToolCallEditResult(state.edits, id, event),
      toolCalls: {
        ...state.toolCalls,
        [id]: existing.map((t) => completedToolCard(t, event)),
      },
    };
  }
  if (event.type === 'toolResult') {
    const existing = state.toolCalls[event.sessionId];
    if (!existing) return state;
    const updated = existing.map((card) =>
      card.id === event.toolCallId
        ? { ...card, fullResult: event.found ? event.content ?? '' : undefined, truncated: false }
        : card,
    );
    return { ...state, toolCalls: { ...state.toolCalls, [event.sessionId]: updated } };
  }
  if (event.type === 'budgetState') {
    return {
      ...state,
      budgets: { ...state.budgets, [event.sessionId]: event.payload },
    };
  }
  if (event.type !== 'usage') return state;
  return {
    ...state,
    usage: {
      ...state.usage,
      [event.sessionId]: {
        prompt: event.promptTokens,
        completion: event.completionTokens,
        total: event.totalTokens,
      },
    },
  };
}

/** One thinking delta inside a per-turn trace. `seq` restarts at 0 in every
 * reasoning block (the bridge's `_emit_thinking`), so a chunk is keyed by
 * (block, seq). */
export interface ThinkingChunk {
  block: number;
  seq: number;
  text: string;
  partial: boolean;
}

/** Ordered thinking trace for a session's current turn. `thinkingDone` closes
 * the open block; a delta after it opens the next one. */
export interface ThinkingTrace {
  chunks: ThinkingChunk[];
  done: boolean;
  block: number;
}

const EMPTY_TRACE: ThinkingTrace = { chunks: [], done: false, block: 0 };

/** The trace as text: a block's deltas are pieces of one stream and join as
 * they came (the console appends them); blocks are separated by a blank line. */
export function thinkingText(trace: ThinkingTrace): string {
  const blocks = new Map<number, string>();
  for (const chunk of trace.chunks) blocks.set(chunk.block, (blocks.get(chunk.block) ?? '') + chunk.text);
  return [...blocks.values()].map((text) => text.trim()).filter(Boolean).join('\n\n');
}

/** Merge one thinking delta into the per-session trace (dedupe + reorder). */
function applyThinkingDelta(
  state: AppState,
  event: Extract<InboundEvent, { type: 'thinkingDelta' }>,
): AppState {
  const open = state.thinking[event.sessionId] ?? EMPTY_TRACE;
  // A delta after thinkingDone starts the next reasoning block of the turn.
  const trace = open.done ? { ...open, done: false, block: open.block + 1 } : open;
  const entry = {
    block: trace.block,
    seq: event.payload.seq,
    text: event.payload.text,
    partial: event.payload.partial,
  };
  // Perf guard (S5): identical seq+text duplicate → no-op (identity-same
  // state, no downstream render); deltas stream monotonically in seq so
  // single-push fast-path is the real shape in practice.
  const same = (c: ThinkingChunk) => c.block === entry.block && c.seq === entry.seq;
  const existing = trace.chunks.find(same);
  if (existing && existing.text === entry.text && existing.partial === entry.partial && trace === open) return state;
  const chunks = [...trace.chunks.filter((c) => !same(c)), entry].sort((a, b) => a.block - b.block || a.seq - b.seq);
  return {
    ...state,
    thinking: { ...state.thinking, [event.sessionId]: { ...trace, chunks } },
  };
}

/** Mark the per-session thinking trace as complete. */
function applyThinkingDone(
  state: AppState,
  event: Extract<InboundEvent, { type: 'thinkingDone' }>,
): AppState {
  const trace = state.thinking[event.sessionId] ?? EMPTY_TRACE;
  return {
    ...state,
    thinking: { ...state.thinking, [event.sessionId]: { ...trace, done: true } },
  };
}

/** Thinking transparency events → per-session ordered trace (wave T3-ui S3). */
function reduceThinkingGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'thinkingDelta') return applyThinkingDelta(state, event);
  if (event.type === 'thinkingDone') return applyThinkingDone(state, event);
  return state;
}

/** Free-form agent events (one-shot progress notices). */
export function applyShellStreamState(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'ui/dismissShellStream') {
    if (!state.shellStreams[event.streamId]) return state;
    const next = { ...state.shellStreams };
    delete next[event.streamId];
    return { ...state, shellStreams: next };
  }
  if (event.type === 'shellStream') {
    const existing = state.shellStreams[event.streamId];
    const base: ShellStreamState = existing ?? {
      streamId: event.streamId,
      sessionId: event.sessionId,
      body: '',
      lines: 0,
      startedAt: Date.now(),
      status: 'running',
    };
    // Never re-transform a terminal stream back to running; chunks for the
    // CLOSE of a stream set status before the last bytes arrive anyway.
    const status = existing && existing.status !== 'running' ? existing.status : 'running';
    let body = base.body + event.chunk;
    if (body.length > STREAM_PANEL_MAX_BYTES) {
      body = body.slice(body.length - STREAM_PANEL_MAX_BYTES + 32_768);
    }
    const lines = body === '' ? 0 : body.split('\n').length - (body.endsWith('\n') ? 1 : 0);
    return {
      ...state,
      shellStreams: pruneTerminalStreams({
        ...state.shellStreams,
        [event.streamId]: { ...base, sessionId: event.sessionId, body, lines, status },
      }),
    };
  }
  if (event.type === 'shellStreamStatus') {
    const existing = state.shellStreams[event.streamId];
    const nextStatus = STREAM_STATUS_TO_TERMINAL[event.status] ?? 'error';
    if (!existing) {
      return {
        ...state,
        shellStreams: pruneTerminalStreams({
          ...state.shellStreams,
          [event.streamId]: {
            streamId: event.streamId,
            sessionId: event.sessionId,
            body: '',
            lines: 0,
            startedAt: Date.now(),
            status: nextStatus,
            detail: event.detail,
          },
        }),
      };
    }
    return {
      ...state,
      shellStreams: pruneTerminalStreams({
        ...state.shellStreams,
        [event.streamId]: { ...existing, status: nextStatus, detail: event.detail ?? existing.detail },
      }),
    };
  }
  return state;
}

function reduceAgentEventGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'agentEvent') return state;
  // Skip free-form events already surfaced through dedicated channels
  // (streamed deltas, thinking rows, tool cards, errors). Rendering them
  // again as "event" rows duplicates the assistant reply in the
  // transcript (each turn showed ~3 copies before this filter).
  const kind = String((event.event as Record<string, unknown> | undefined)?.event_type ?? '');
  // reasoning_delta / reasoning_done: a model's reasoning stream, one event
  // per token — an older r4 core forwarded them here and every token became
  // its own "event:" row (2026-11-01); r4 now sends them as thinking deltas.
  if (/^(thinking|reasoning_delta|reasoning_done|response_start|response_chunk|response_done|error|tool_selection)$/.test(kind)) {
    return state;
  }
  return withEntries(state, event.sessionId, (entries) => [
    ...entries,
    mkEntry({ kind: 'event', text: describeEvent(event.event) }),
  ]);
}

/** Planning (plan start/update with step list). */
function reducePlanEventGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'planEvent') return state;
  const headline = event.plan.phase === 'start' ? 'Plan started' : 'Plan update';
  const steps = (event.plan.steps ?? [])
    .map((s) => `  ${({ pending: '·', running: '▶', done: '✓', failed: '✗' } as const)[s.status]} ${s.description || s.id}`)
    .join('\n');
  return withEntries(state, event.sessionId, (entries) => [
    ...entries,
    mkEntry({
      kind: 'event',
      text: `${headline}${event.plan.summary ? ` — ${event.plan.summary}` : ''}${steps ? `\n${steps}` : ''}`,
    }),
  ]);
}

/** File-edit artifacts (create/modify/delete preview). */
function reduceDiffEventGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'diffEvent') return state;
  const actionGlyph = { create: '➕', modify: '✏️', delete: '🗑' }[event.diff.action] ?? '✏️';
  const hunkCount = event.diff.hunks?.length ?? 0;
  return withEntries(state, event.sessionId, (entries) => [
    ...entries,
    mkEntry({
      kind: 'event',
      text: `${actionGlyph} ${event.diff.action} ${event.diff.path}${hunkCount ? ` (${hunkCount} hunk${hunkCount === 1 ? '' : 's'})` : ''}${event.diff.summary ? ` — ${event.diff.summary}` : ''}`,
    }),
  ]);
}

/** RAG citation artifacts. */
function reduceCitationEventGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'citationEvent') return state;
  const lines = event.citations
    .map((c) => `  ${c.title ?? c.source}${c.uri ? ` — ${c.uri}` : ''}`)
    .join('\n');
  return withEntries(state, event.sessionId, (entries) => [
    ...entries,
    mkEntry({ kind: 'event', text: `Citations (${event.citations.length}):\n${lines}` }),
  ]);
}

/** Session lifecycle end states: model change, delete, cancel, error. */
/** M5: management card lifecycle — pending rows appear on query dispatch
 *  (ui/mgmtCardQueued) and flip when the host's mgmtCard event lands; kind
 *  matches mgmt-card.v1 contract shared with v3 and tui-js. */
function reduceMgmtCardGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'ui/mgmtCardQueued') {
    const row: MgmtCardMessage = {
      requestId: event.requestId,
      kind: event.kind,
      payload: undefined,
      status: 'pending',
      at: event.at,
    };
    const existing = state.cards[event.sessionId] ?? [];
    return {
      ...state,
      cards: { ...state.cards, [event.sessionId]: [...existing, row] },
    };
  }
  if (event.type !== 'mgmtCard') return state;
  const existing = state.cards[event.sessionId] ?? [];
  const idx = existing.findIndex((row) => row.requestId === event.requestId);
  const row: MgmtCardMessage = {
    requestId: event.requestId,
    kind: event.kind,
    payload: event.payload,
    status: event.status,
    error: event.error,
    at: event.at,
  };
  const next = idx >= 0
    ? [...existing.slice(0, idx), { ...existing[idx], ...row }, ...existing.slice(idx + 1)]
    : [...existing, row];
  return { ...state, cards: { ...state.cards, [event.sessionId]: next } };
}

function reduceLifecycleGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'sourceStale') {
    // One line, once: the ACP child's source tree drifted after process start.
    if (state.sourceStaleWarned) return state;
    const id = state.currentId;
    const warned = { ...state, sourceStaleWarned: true };
    if (!id) {
      // No session on screen yet — still surface it, in the status slot.
      return { ...warned, lastError: event.text };
    }
    return {
      ...withEntries({ ...state, sourceStaleWarned: true }, id, (entries) => [
        ...entries,
        mkEntry({ kind: 'event', text: `\u26a0\ufe0f \u26a1 ${event.text}` }),
      ]),
    };
  }
  if (event.type === 'modelChanged') {
    return {
      ...state,
      sessions: state.sessions.map((s) =>
        s.id === event.sessionId ? { ...s, model: event.model || undefined } : s,
      ),
    };
  }
  if (event.type === 'sessionDeleted') {
    return {
      ...state,
      sessions: state.sessions.filter((s) => s.id !== event.sessionId),
      currentId: state.currentId === event.sessionId ? undefined : state.currentId,
      transcripts: omitKey(state.transcripts, event.sessionId),
      streaming: omitKey(state.streaming, event.sessionId),
      runStates: omitKey(state.runStates, event.sessionId),
    };
  }
  if (event.type === 'cancelled') {
    return {
      ...state,
      streaming: omitKey(state.streaming, event.sessionId),
      runStates: { ...state.runStates, [event.sessionId]: 'idle' },
    };
  }
  if (event.type === 'historyState') {
    return { ...state, history: event.history };
  }
  if (event.type === 'editorContext') {
    return { ...state, editorContext: event.chip };
  }
  if (event.type === 'costState') {
    return {
      ...state,
      costs: { ...state.costs, [event.sessionId]: event.payload },
    };
  }
  if (event.type === 'ui/discardFrom') {
    return withEntries(state, event.sessionId, (entries) =>
      entries.slice(0, Math.max(0, event.index)),
    );
  }
  if (event.type === 'compactResult') {
    const text = event.error
      ? `compact failed: ${event.error}`
      : event.changed
        ? `compacted history: ${event.from} → ${event.to} messages (kept ${event.keepRecent} recent)`
        : `nothing to compact (${event.to} messages)`;
    return withEntries(state, event.sessionId, (entries) => [
      ...entries,
      mkEntry({ kind: 'event', text }),
    ]);
  }
  if (event.type !== 'error') return state;
  const withMessage = { ...state, lastError: event.message };
  if (!event.sessionId) return withMessage;
  return withEntries(withMessage, event.sessionId, (entries) => [
    ...entries,
    mkEntry({ kind: 'event', text: event.message, error: true }),
  ]);
}

/** ADR-037 tool-selection roll-up: honest one-liner from the record alone
 *  (mirrors the TUI bridge format — no "optimized" language). */
export function toolSelectionLine(payload: ToolSelectionPayload): string {
  const available = Number(payload.available_tools ?? 0);
  const candidate = Number(payload.candidate_tools ?? 0);
  const strategy = String(payload.strategy ?? 'passthrough');
  const skills = Array.isArray(payload.selected_skills) ? payload.selected_skills.length : 0;
  const skillSuffix = skills > 0 ? ` · ${skills} skill${skills === 1 ? '' : 's'}` : '';
  // Round-2 finding D: the availability gate's removals must be visible.
  const unavailable = (payload.filtered_out as Record<string, { count?: number }> | undefined)
    ?.unavailable?.count ?? 0;
  const unavailableSuffix = unavailable > 0 ? ` · ${unavailable} unavailable filtered` : '';
  if (strategy === 'passthrough' && candidate === available) {
    return `⚙ all ${available} tools offered (no selection)${skillSuffix}${unavailableSuffix}`;
  }
  return `⚙ ${candidate} of ${available} tools offered (${strategy})${skillSuffix}${unavailableSuffix}`;
}

/** ADR-037: collapse a selection record into the single updatable
 *  roll-up entry — replaces any previous roll-up line, never stacks. */
export function nextToolSelectionEntries(
  entries: TranscriptEntry[],
  payload: ToolSelectionPayload,
): TranscriptEntry[] {
  const kept = entries.filter((entry) => entry.tag !== 'toolSelection');
  return [
    ...kept,
    mkEntry({ kind: 'event', text: toolSelectionLine(payload), tag: 'toolSelection' }),
  ];
}

/** Final assistant entry/entries for a completed turn. Exported for the
 *  webview test bundle (dead-window guard lives here). */
export function assistantCompletionEntries(
  closed: TranscriptEntry[],
  text: string,
  wasStreaming: boolean,
  hadToolActivity = false,
): TranscriptEntry[] {
  const last = closed[closed.length - 1];
  if (wasStreaming && isStreamingAssistant(closed)) {
    return [...closed.slice(0, -1), { ...last!, text: text || last!.text }];
  }
  if (text) return [...closed, mkEntry({ kind: 'msg', role: 'R4 Agent', text })];
  if (hadToolActivity) {
    // Tool-only turn (H.5): cards are the visible evidence — emitting
    // 'no content' here was a false positive.
    return closed;
  }
  // Dead-window guard: a turn that completes with no text and no streamed
  // draft still leaves a visible trace — never a silent gap after the
  // last tool event ("one event line, no reply").
  return [...closed, mkEntry({ kind: 'event', text: 'The agent returned no content for this turn.' })];
}

/** ADR-037: one updatable roll-up line per session — each model call's
 *  record replaces the previous line instead of stacking raw event rows. */
function reduceToolSelectionGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type !== 'toolSelection') return state;
  return withEntries(state, event.sessionId, (entries) =>
    nextToolSelectionEntries(entries, event.payload),
  );
}

/** The latest user message without a turn id takes the turn it opened. */
function tagLatestUserTurn(entries: TranscriptEntry[], turnId: string): TranscriptEntry[] {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry.kind !== 'msg' || entry.role !== 'You') continue;
    if (entry.turnId) return entries;
    return [...entries.slice(0, i), { ...entry, turnId }, ...entries.slice(i + 1)];
  }
  return entries;
}

/** Without the rows of `turnIds`: each such user message and what follows it
 * up to the next user message (the console's dropTranscriptTurns). */
function dropTurnRows(entries: TranscriptEntry[], turnIds: Set<string>): TranscriptEntry[] {
  let dropping = false;
  return entries.filter((entry) => {
    if (entry.kind === 'msg' && entry.role === 'You') dropping = Boolean(entry.turnId && turnIds.has(entry.turnId));
    return !dropping;
  });
}

/** `session.truncate` cut the history at this turn: its user row and everything after go. */
function truncateFromTurn(entries: TranscriptEntry[], turnId: string): TranscriptEntry[] {
  const at = entries.findIndex((entry) => entry.kind === 'msg' && entry.role === 'You' && entry.turnId === turnId);
  return at < 0 ? entries : entries.slice(0, at);
}

/** A slash command's printed lines as one output card (the console's notices). */
function slashOutputEntry(lines: { kind: string; text: string }[]): TranscriptEntry {
  const text = lines.map((line) => line.text).join('\n') || '(done)';
  return mkEntry({ kind: 'output', text, error: lines.some((line) => line.kind === 'error') });
}

/** Replace the `!cmd` cell started under `tag`, or add it. */
/** A notice row; a keyed one replaces its earlier row in place (VP-4b live rows). */
function upsertNotice(entries: TranscriptEntry[], text: string, key: string | undefined): TranscriptEntry[] {
  const tag = key ? `notice:${key}` : undefined;
  const at = tag ? entries.findIndex((e) => e.tag === tag) : -1;
  if (at < 0) return [...entries, mkEntry({ kind: 'event', text, ...(tag ? { tag } : {}) })];
  return [...entries.slice(0, at), { ...entries[at], text }, ...entries.slice(at + 1)];
}

function upsertShell(entries: TranscriptEntry[], tag: string, text: string, error: boolean): TranscriptEntry[] {
  const at = entries.findIndex((e) => e.tag === tag);
  if (at < 0) return [...entries, mkEntry({ kind: 'shell', text, tag, error })];
  return [...entries.slice(0, at), { ...entries[at], text, error }, ...entries.slice(at + 1)];
}

/** VP-2b transcript events: turn ids, `!cmd` cells, notices. */
function reduceBridgeTranscriptGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  switch (event.type) {
    case 'turnOpened':
      return withEntries(state, event.sessionId, (entries) => tagLatestUserTurn(entries, event.turnId));
    case 'shellStarted':
      return withEntries(state, event.sessionId, (entries) =>
        upsertShell(entries, `shell:${event.id}`, `${event.echo}\n(running…)`, false),
      );
    case 'shellResult':
      return withEntries(state, event.sessionId, (entries) =>
        upsertShell(entries, `shell:${event.id}`, event.text, event.exitCode !== 0),
      );
    case 'notice':
      return withEntries(state, event.sessionId, (entries) => upsertNotice(entries, event.text, event.key));
    case 'slashOutput':
      return withEntries(state, event.sessionId, (entries) => [...entries, slashOutputEntry(event.lines)]);
    case 'policyState':
      // ADR-101 D-1: the console's one-row sandbox notice when a session opens.
      if (!event.sandboxNotice) return state;
      return withEntries(state, event.sessionId, (entries) => [...entries, mkEntry({ kind: 'event', text: event.sandboxNotice })]);
    default:
      return state;
  }
}

/** Rows the session no longer has: /retry's dropped turns, a rewind's cut (VP-2c). */
function reduceBridgeCutGroup(state: AppState, event: InboundEvent | UiEvent): AppState {
  if (event.type === 'dropTurns') {
    return withEntries(state, event.sessionId, (entries) => dropTurnRows(entries, new Set(event.turnIds)));
  }
  if (event.type === 'truncateFrom') {
    return withEntries(state, event.sessionId, (entries) => truncateFromTurn(entries, event.turnId));
  }
  return state;
}

const REDUCER_GROUPS: ReadonlyArray<
  (state: AppState, event: InboundEvent | UiEvent) => AppState
> = [
  reduceBridgeTranscriptGroup,
  reduceBridgeCutGroup,
  reduceUiEvents,
  reduceStudioGroup,
  reduceSystemGroup,
  reduceSessionGroup,
  reduceSessionSelectionGroup,
  reduceStreamGroup,
  reduceEditState,
  reduceToolGroup,
  reduceToolSelectionGroup,
  reduceMgmtCardGroup,
  reduceThinkingGroup,
  applyShellStreamState,
  reduceAgentEventGroup,
  reducePlanEventGroup,
  reduceDiffEventGroup,
  reduceCitationEventGroup,
  reduceLifecycleGroup,
];

function reduceGroups(state: AppState, event: InboundEvent | UiEvent): AppState {
  for (const group of REDUCER_GROUPS) {
    const next = group(state, event);
    if (next !== state) return next;
  }
  return state;
}

/** The groups claim an event first-come; the inline cards (ADR-103 VP-2)
 * also react to events a group already claimed (a new message closes the
 * plan question), so they fold after the chain, on every event. */
function reducer(state: AppState, event: InboundEvent | UiEvent): AppState {
  const next = reduceGroups(state, event);
  const asks = reduceAsks(next.asks, event);
  const extras = reduceExtras(next.extras, event);
  if (asks === next.asks && extras === next.extras) return next;
  return { ...next, asks, extras };
}

// ── Provider ───────────────────────────────────────────────────────────────

// ── Provider ───────────────────────────────────────────────────────────────

interface StoreRoot {
  state: AppState;
  dispatch: (event: InboundEvent | UiEvent) => void;
}

const AppContext = createContext<StoreRoot | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    // OpenHands delta batcher: coalesce assistantDelta bursts per session so
    // token storms re-render once per frame-window, not once per token.
    const batchedDispatch = createStreamDeltaDispatcher(dispatch);
    const unsubscribe = subscribe(batchedDispatch);
    requestReady();
    return () => {
      unsubscribe();
    };
  }, []);

  return (
    <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>
  );
}

export function useAppDispatch(): (event: InboundEvent | UiEvent) => void {
  const root = useContext(AppContext);
  if (!root) throw new Error('useAppDispatch must be used inside <AppProvider>');
  return root.dispatch;
}

export function useApp(): AppState {
  const root = useContext(AppContext);
  if (!root) throw new Error('useApp must be used inside <AppProvider>');
  return root.state;
}

/** Convenience selector for the session the user is currently looking at. */
export function useCurrentSession(): SessionSummary | undefined {
  const { sessions, currentId } = useApp();
  return currentId ? sessions.find((s) => s.id === currentId) : undefined;
}

/** Exported for the webview test bundle (store-stream-boundary.test.mjs):
 *  drive the real reducer chain over an initial state. */
export function applyEventForTest(state: AppState, event: InboundEvent | UiEvent): AppState {
  return reducer(state, event);
}

/** Exported for the webview test bundle — fresh state seed. */
export function initialStateForTest(): AppState {
  return initialState;
}
