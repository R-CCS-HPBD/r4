/**
 * Outbound actions.
 *
 * Thin, typed wrappers over `post()`. They carry no state, so they are plain
 * module functions rather than hook results.
 */

import { post } from './acp-bridge';
import type { MgmtCardKind, OutboundMessage, SessionMode, ViewCitation } from '@/types/protocol';

type Send = Extract<OutboundMessage, { type: 'send' }>;

export const requestReady = (): void => post({ type: 'ready' });

/** Open a local file in the editor (from a tool-call chip). */
export const openFile = (path: string): void => post({ type: 'openFile', path });

/** Fetch the full tool result for an expanded bounded card (round-3 task 2). */
export const requestToolResult = (sessionId: string, toolCallId: string): void =>
  post({ type: 'toolResultRequest', sessionId, toolCallId });

// ── Agent edit review ───────────────────────────────────────────────────────

/** Accept a staged edit — writes it to disk. */
export const acceptEdit = (editId: string): void => post({ type: 'acceptEdit', editId });

/** Reject a staged edit — discards the proposal. */
export const rejectEdit = (editId: string): void => post({ type: 'rejectEdit', editId });

/** Re-open the native diff editor for a pending edit. */
export const reviewEdit = (editId: string): void => post({ type: 'reviewEdit', editId });

export const refresh = (): void => post({ type: 'refresh' });

export const restart = (): void => post({ type: 'restart' });

export const newSession = (model?: string): void => post({ type: 'newSession', model });

export const selectSession = (sessionId: string): void =>
  post({ type: 'selectSession', sessionId });

export const renameSession = (sessionId: string, name: string): void =>
  post({ type: 'renameSession', sessionId, name });

export const pinSession = (sessionId: string): void => post({ type: 'pinSession', sessionId });

export const togglePin = (sessionId: string, pinned: boolean): void =>
  pinned ? unpinSession(sessionId) : pinSession(sessionId);

/** C4: /compact — on-demand conversation compaction via the ACP verb. */
export const compactSession = (sessionId: string, keepRecent?: number): void =>
  post({ type: 'compactSession', sessionId, keepRecent });

export const unpinSession = (sessionId: string): void => post({ type: 'unpinSession', sessionId });

export const deleteSession = (sessionId: string): void =>
  post({ type: 'deleteSession', sessionId });

export const setModel = (sessionId: string, model?: string): void =>
  post({ type: 'setModel', sessionId, model });

export const cancel = (sessionId?: string): void => post({ type: 'cancel', sessionId });

/** W5c: cancel a live shell stream (extension host → ACP shell/cancel → pgid kill). */
export const cancelShellStream = (streamId: string, sessionId?: string): void =>
  post({ type: 'shellCancel', streamId, sessionId });

/** M5: send a management card query to the host — one immutable card row per
 * query, resolved by kind + arg, landing in the transcript via requestId. */
export const mgmtCardQuery = (
  requestId: string,
  sessionId: string,
  kind: MgmtCardKind,
  arg?: string,
): void => post({ type: 'mgmtCardQuery', requestId, sessionId, kind, arg });

export const sendMessage = (text: string, sessionId?: string, model?: string): void => {
  const message: Send = { type: 'send', text };
  if (sessionId) message.sessionId = sessionId;
  if (model) message.model = model;
  post(message);
};

// ── Mode selector (ask | plan | agent) ──────────────────────────────────────

export const setMode = (sessionId: string, mode: SessionMode): void =>
  post({ type: 'setMode', sessionId, mode });

// ── Studio tab (projects + UMRS workflow graph) ─────────────────────────────

export const refreshStudio = (): void => post({ type: 'refreshStudio' });

export const switchProject = (projectId: string): void =>
  post({ type: 'switchProject', projectId });

export const createProject = (
  projectId: string,
  opts?: { name?: string; gateway?: string; model?: string },
): void => post({ type: 'createProject', projectId, ...opts });

export const refreshKnowledge = (): void => post({ type: 'refreshKnowledge' });

/** Science catalogue search for the Knowledge panel card (\``catalog/search`\`). */
export const searchCatalog = (query = '', projectId?: string): void =>
  post({ type: 'catalogSearch', query, projectId });

export const setKnowledgeGraphConstraints = (
  constraints: { include: string[]; exclude: string[]; discovery: boolean },
): void => post({ type: 'setKnowledgeGraphConstraints', constraints });

export interface StartResearchLoopOptions {
  researchGoal: string;
  projectId?: string;
  maxIterations?: number;
  /** Drive iterations with the site LLM agent executor. */
  agentic?: boolean;
  /** Resource hints (gpu, cores, cluster…) routing cluster-class work to UMRS. */
  requires?: Record<string, unknown>;
  /** Venue override: umrs | local | hybrid (empty = resolver rules). */
  runtimeMode?: string;
}

export const startResearchLoop = (options: StartResearchLoopOptions): void =>
  post({ type: 'startResearchLoop', ...options });

export const stopResearchLoop = (loopId: string, projectId?: string): void =>
  post({ type: 'stopResearchLoop', loopId, projectId });

export const ingestKnowledge = (opts: {
  files: string[];
  sources: string[];
  projectId?: string;
}): void => post({ type: 'ingestKnowledge', ...opts });

export const pickKnowledgeFiles = (): void => post({ type: 'pickKnowledgeFiles' });

export interface RunWorkflowOptions {
  parameters?: Record<string, unknown>;
  projectId?: string;
  inputFiles?: string[];
  wait?: boolean;
}

export const runWorkflow = (workflowName: string, options: RunWorkflowOptions = {}): void =>
  post({ type: 'runWorkflow', workflowName, ...options });

/** Fetch steps + parameter schema for the planned-lane preview. */
export const describeWorkflow = (workflowName: string): void =>
  post({ type: 'describeWorkflow', workflowName });

// ── System tab (skills, MCP servers, command catalog) ──────────────────────

export const refreshSystem = (): void => post({ type: 'refreshSystem' });

/** Load/unload one skill on the bound companion session. */
export const setSkillActive = (sessionId: string, name: string, active: boolean): void =>
  post({ type: 'setSkillActive', sessionId, name, active });

/** Trigger a health check for one MCP server (refresh follows). */
export const checkMcpHealth = (serverId: string): void =>
  post({ type: 'checkMcpHealth', serverId });

export const cancelExecution = (executionId: string): void =>
  post({ type: 'cancelExecution', executionId });

export const approveGateStep = (
  executionId: string,
  stepName: string,
  approved: boolean,
): void => post({ type: 'approveGateStep', executionId, stepName, approved });

export const removeWatch = (executionId: string): void =>
  post({ type: 'removeWatch', executionId });

// ── VP-2b (bridge backend): message verbs, citations, changed files ─────────

export const forkSession = (turnId?: string): void => post({ type: 'forkSession', turnId });

export const saveTurn = (turnId: string, part: 'prompt' | 'answer'): void =>
  post({ type: 'saveTurn', turnId, part });

export const openCitation = (citation: ViewCitation): void => post({ type: 'openCitation', citation });

export const openChange = (path: string): void => post({ type: 'openChange', path });

export const revertChange = (path: string): void => post({ type: 'revertChange', path });
