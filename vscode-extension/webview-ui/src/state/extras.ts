/**
 * Per-session read-models of the bridge backend (ADR-103 VP-2b): the
 * queued messages, the session's changed files, its plan, and the passages
 * each turn cited. Pure; folded after the store's reducer chain.
 */

import type { ChangedFile, InboundEvent, PlanStepRow, SessionFilterState, ViewCitation } from '@/types/protocol';

export interface ExtrasSlice {
  queue: Record<string, string[]>;
  files: Record<string, ChangedFile[]>;
  plan: Record<string, PlanStepRow[]>;
  /** citations[sessionId][turnId] */
  citations: Record<string, Record<string, ViewCitation[]>>;
  /** The latest `@` candidates and the query they answer. */
  mentions: { query: string; items: string[] };
  /** `/changes` asked to open a dock panel; `seq` makes each ask distinct. */
  dockFocus: Record<string, { panel: string; seq: number }>;
  /** "bridge" once the bridge backend announced itself (it queues while streaming). */
  backend: string;
  /** VP-3a: the status-bar chips per session (`sbx:on`, the web-access label). */
  policy: Record<string, { sandboxChip: string; web: string }>;
  /** VP-4b: the armed-team chip per session ("" = not armed). */
  teamArmed: Record<string, string>;
  /** VP-2d: the live slash catalog (empty until the bridge sends it). */
  slashCatalog: { name: string; hint: string }[];
  /** VP-2c: the prompt each session is editing (its turn id). */
  rewind: Record<string, string>;
  /** VP-3b: the `remote:` chip per session and its state (ok/warn/error/info/dim). */
  venue: Record<string, { chip: string; state: string }>;
  /** VP-3a: the Sessions filters last sent to the server. */
  sessionFilter: SessionFilterState;
}

export const initialExtras: ExtrasSlice = {
  queue: {},
  files: {},
  plan: {},
  citations: {},
  mentions: { query: '', items: [] },
  dockFocus: {},
  backend: '',
  policy: {},
  venue: {},
  rewind: {},
  slashCatalog: [],
  teamArmed: {},
  sessionFilter: { state: '', venue: '', includeArchived: false },
};

type Handler = (extras: ExtrasSlice, event: any) => ExtrasSlice;

const HANDLERS: Record<string, Handler> = {
  queueState: (x, e) => ({ ...x, queue: { ...x.queue, [e.sessionId]: e.items } }),
  filesChanged: (x, e) => ({ ...x, files: { ...x.files, [e.sessionId]: e.files } }),
  planState: (x, e) => ({ ...x, plan: { ...x.plan, [e.sessionId]: e.steps } }),
  citations: (x, e) => {
    if (!e.turnId) return x;
    const bySession = { ...(x.citations[e.sessionId] ?? {}), [e.turnId]: e.citations };
    return { ...x, citations: { ...x.citations, [e.sessionId]: bySession } };
  },
  mentionCandidates: (x, e) => ({ ...x, mentions: { query: e.query, items: e.items } }),
  dockFocus: (x, e) => ({
    ...x,
    dockFocus: { ...x.dockFocus, [e.sessionId]: { panel: e.panel, seq: (x.dockFocus[e.sessionId]?.seq ?? 0) + 1 } },
  }),
  backendInfo: (x, e) => ({ ...x, backend: e.backend }),
  sessionFilterState: (x, e) => ({ ...x, sessionFilter: e.filter }),
  teamArmed: (x, e) => ({ ...x, teamArmed: { ...x.teamArmed, [e.sessionId]: e.label } }),
  slashCatalog: (x, e) => ({ ...x, slashCatalog: e.commands }),
  rewindPending: (x, e) => ({ ...x, rewind: { ...x.rewind, [e.sessionId]: e.turnId } }),
  truncateFrom: (x, e) => ({ ...x, rewind: { ...x.rewind, [e.sessionId]: '' } }),
  venueState: (x, e) => ({ ...x, venue: { ...x.venue, [e.sessionId]: { chip: e.chip, state: e.chipState } } }),
  policyState: (x, e) => ({ ...x, policy: { ...x.policy, [e.sessionId]: { sandboxChip: e.sandboxChip, web: e.web } } }),
};

/** The slice after one event; the same object when the event is not one of these. */
export function reduceExtras(extras: ExtrasSlice, event: InboundEvent | { type: string }): ExtrasSlice {
  const handler = HANDLERS[event.type];
  return handler ? handler(extras, event) : extras;
}

/** Plan glyphs, as the console draws todo status. */
export const PLAN_GLYPH: Record<string, string> = {
  completed: '✓',
  done: '✓',
  in_progress: '◐',
  running: '◐',
  pending: '○',
  todo: '○',
  failed: '✗',
  cancelled: '⊘',
  blocked: '⚠',
};

/** "3/5 done" for the plan header; null when there is no plan. */
export function planSummary(steps: PlanStepRow[] | undefined): string | null {
  if (!steps || steps.length === 0) return null;
  const done = steps.filter((s) => s.status === 'completed' || s.status === 'done').length;
  return `${done}/${steps.length} done`;
}
