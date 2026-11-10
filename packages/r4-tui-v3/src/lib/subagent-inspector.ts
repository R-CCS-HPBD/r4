/**
 * D-70 S4 read-only child-session inspector.
 *
 * One bounded EventLog page is resident at a time. Polls are sequential and
 * only run while the owning tab still has this inspector open on a live local
 * child. There is deliberately no load_session or CompanionSession path here.
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc, type RpcRail } from "../bridge/rpc";
import type { EventLogRow, InspectSubagentResult, SubagentSessionRecord } from "../bridge/rpc-types.gen";

export interface InspectorTarget {
  sessionId: string;
  agentId: string;
  parentSessionId: string;
  parentAgentId: string;
  venue: string;
  ownerTabId: string;
}

export interface InspectorState {
  snapshot: InspectSubagentResult | null;
  loading: boolean;
  error: string;
  afterId: number;
  canGoBack: boolean;
}

export interface SubagentInspector {
  state(): InspectorState;
  start(): Promise<void>;
  refresh(): Promise<void>;
  nextPage(): Promise<void>;
  previousPage(): Promise<void>;
  stop(): void;
}

interface InspectorOptions {
  target: InspectorTarget;
  rail?: RpcRail;
  isActive: () => boolean;
  onChange?: (state: InspectorState) => void;
  pollMs?: number;
  schedule?: (fn: () => void | Promise<void>, ms: number) => unknown;
  cancelSchedule?: (handle: unknown) => void;
}

export const SUBAGENT_EVENT_PAGE_LIMIT = 100;
const REQUEST_TIMEOUT_MS = 15_000;
const DEFAULT_POLL_MS = 1_500;
const MAX_PAGE_HISTORY = 32;
const DETAIL_CHAR_CAP = 12_000;
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

export function subagentTerminal(status: string): boolean {
  return TERMINAL_STATES.has(status);
}

export function remoteSubagentVenue(venue: string): boolean {
  return venue !== "" && venue !== "local";
}

function productionSchedule(fn: () => void | Promise<void>, ms: number): unknown {
  const timer = setTimeout(fn, ms);
  if (typeof timer === "object" && "unref" in timer) timer.unref();
  return timer;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function createSubagentInspector(options: InspectorOptions): SubagentInspector {
  const schedule = options.schedule ?? productionSchedule;
  const cancelSchedule = options.cancelSchedule ?? ((handle: unknown) => clearTimeout(handle as never));
  const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
  const cursors: number[] = [];
  let snapshot: InspectSubagentResult | null = null;
  let loading = false;
  let error = "";
  let afterId = 0;
  let pollHandle: unknown;
  let generation = 0;
  let stopped = false;

  const state = (): InspectorState => ({
    snapshot,
    loading,
    error,
    afterId,
    canGoBack: cursors.length > 0,
  });
  const emit = (): void => options.onChange?.(state());
  const active = (): boolean => !stopped && options.isActive();
  const cancelPoll = (): void => {
    if (pollHandle === undefined) return;
    cancelSchedule(pollHandle);
    pollHandle = undefined;
  };
  const stop = (): void => {
    stopped = true;
    generation += 1;
    loading = false;
    cancelPoll();
    emit();
  };
  const schedulePoll = (): void => {
    if (!active() || (snapshot && subagentTerminal(snapshot.session.status))) return;
    pollHandle = schedule(() => {
      pollHandle = undefined;
      if (!active()) {
        stop();
        return;
      }
      return load(afterId);
    }, pollMs);
  };
  const unavailable = (venue: string): void => {
    error = `local-only inspector unavailable for remote child venue '${venue}'; S5 remote inspection is not implemented`;
    loading = false;
    emit();
  };
  const currentRequest = (requestGeneration: number): boolean => requestGeneration === generation && !stopped;
  const finishRequest = (requestGeneration: number): void => {
    if (!currentRequest(requestGeneration)) return;
    loading = false;
    emit();
    schedulePoll();
  };
  const readPage = async (cursor: number): Promise<void> => {
    const requestGeneration = ++generation;
    loading = true;
    error = "";
    emit();
    try {
      const result = await requestPage(options.target, options.rail, cursor);
      if (!currentRequest(requestGeneration)) return;
      assertInspectionLineage(options.target, result);
      snapshot = result;
      afterId = cursor;
    } catch (cause) {
      if (currentRequest(requestGeneration)) error = errorText(cause);
    } finally {
      finishRequest(requestGeneration);
    }
  };
  const load = async (cursor: number): Promise<void> => {
    if (!active()) {
      stop();
      return;
    }
    cancelPoll();
    if (remoteSubagentVenue(options.target.venue)) {
      unavailable(options.target.venue);
      return;
    }
    await readPage(cursor);
  };
  const nextPage = async (): Promise<void> => {
    if (!snapshot?.has_more) return;
    cursors.push(afterId);
    if (cursors.length > MAX_PAGE_HISTORY) cursors.shift();
    await load(snapshot.next_after_id);
  };
  const previousPage = async (): Promise<void> => {
    const cursor = cursors.pop();
    if (cursor === undefined) return;
    await load(cursor);
  };

  return {
    state,
    start: () => load(afterId),
    refresh: () => load(afterId),
    nextPage,
    previousPage,
    stop,
  };
}

async function requestPage(target: InspectorTarget, rail: RpcRail | undefined, afterId: number): Promise<InspectSubagentResult> {
  return rpc(rail ?? getCompanionBridge(), "inspect_subagent", {
    session_id: target.sessionId,
    after_id: afterId,
    limit: SUBAGENT_EVENT_PAGE_LIMIT,
  }, REQUEST_TIMEOUT_MS);
}

function assertInspectionLineage(target: InspectorTarget, result: InspectSubagentResult): void {
  if (result.read_only !== true) throw new Error("inspector refused a non-read-only bridge response");
  const child = result.session;
  if (result.session_id !== target.sessionId || child.id !== target.sessionId) {
    throw new Error("subagent lineage mismatch: bridge returned another session");
  }
  const expected = [target.agentId, target.parentSessionId, target.parentAgentId];
  const actual = [child.agent_id, child.parent_session_id, child.parent_agent_id];
  if (child.session_kind !== "subagent" || expected.some((value, index) => value !== actual[index])) {
    throw new Error("subagent lineage mismatch: agent or immediate parent differs");
  }
  if (remoteSubagentVenue(child.venue)) {
    throw new Error(`local-only inspector unavailable for remote child venue '${child.venue}'; S5 remote inspection is not implemented`);
  }
}

function textRows(label: string, text: string): string[] {
  const lines = text.split("\n");
  return [label, ...lines.map((line) => `  ${line}`)];
}

function sessionHeader(child: SubagentSessionRecord): string[] {
  return [
    `status · ${child.status || "unknown"} · model · ${child.model || "unknown"} · ${child.execution_kind}`,
    `child · ${child.id} · agent · ${child.agent_id}`,
    `parent · ${child.parent_session_id} · agent · ${child.parent_agent_id}`,
    `venue · ${child.venue || "unknown"} · follow-ups · ${child.follow_up_count}`,
  ];
}

function messageTurn(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object") return "";
  const turn = (metadata as Record<string, unknown>).subagent_turn;
  return typeof turn === "number" ? ` · turn ${turn}` : "";
}

function conversationRows(child: SubagentSessionRecord): string[] {
  const rows = ["", "conversation — stored child messages"];
  child.messages.forEach((message, index) => {
    const turn = messageTurn(message.metadata);
    const role = typeof message.role === "string" ? message.role : "message";
    rows.push(...textRows(`${index + 1}. ${role}${turn}`, typeof message.content === "string" ? message.content : ""));
  });
  return rows;
}

function oneLine(text: string): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length <= 180 ? compact : `${compact.slice(0, 179)}…`;
}

function boundedDetail(text: string): string {
  if (text.length <= DETAIL_CHAR_CAP) return text;
  return `${text.slice(0, DETAIL_CHAR_CAP)}\n… inspector display cap; ${text.length - DETAIL_CHAR_CAP} more chars`;
}

function eventRows(event: EventLogRow, expanded: boolean, selected: boolean): string[] {
  const name = event.tool_name ? ` ${event.tool_name}` : "";
  const summary = oneLine(event.message || JSON.stringify(event.data));
  const rows = [`${selected ? "▸" : " "} #${event.id} ${event.event_type}${name} · ${summary}`];
  if (!expanded) return rows;
  rows.push(...textRows("  message", boundedDetail(event.message)));
  rows.push(...textRows("  data", boundedDetail(JSON.stringify(event.data, null, 2))));
  return rows;
}

export function inspectorRows(
  snapshot: InspectSubagentResult,
  expandedEventId: number | null = null,
  selectedEventId: number | null = null,
): string[] {
  const child = snapshot.session;
  const events = [...snapshot.events].sort((left, right) => left.id - right.id);
  const rows = [...sessionHeader(child), ...textRows("task", child.task), ...textRows("system prompt", child.system_prompt)];
  if (child.terminal_error) rows.push(...textRows("terminal error", child.terminal_error));
  if (child.event_log_error) rows.push(...textRows("event recording error", child.event_log_error));
  rows.push(...conversationRows(child), "", "events — chronological; Enter expands the selected row");
  events.forEach((event) => rows.push(...eventRows(event, event.id === expandedEventId, event.id === selectedEventId)));
  if (events.length === 0) rows.push("  (no persisted events in this page)");
  rows.push("", `event payloads are redacted/bounded by the bridge (${snapshot.event_payload_cap} chars/string; may be abridged)`);
  return rows;
}
