/**
 * Fan Companion bridge events into agentRuntime + workspace activity for Research monitor.
 */

import { getCompanionBridge } from "./jsonl-client";
import { eventTargetTab } from "../tabs/registry";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import { announceSandboxFromSnapshot } from "./sandbox-bridge";
import { actions, workspace, activeTab, eventScopedTab } from "../stores";
import { agentActions } from "../stores/agent-runtime";
import { ulid } from "ulid";
import { wireProjectJobEventsOnce } from "../commands/project-job-events";
import { iconizeToolLabel, isDeniedText } from "../lib/tool-glyph";
import {
  formatToolActivityEntry,
  humanizeToolInProgress,
  type ActivityLine,
} from "../lib/tool-activity";
import type { ToolArgs } from "../lib/tool-args";
import { parseToolArgs } from "../lib/tool-args";
import { isTodoToolName, parseTodoPayload } from "../lib/session-todos";
import type { HostResources } from "../lib/resource-monitor";
import type { KnowledgeBaseState, ScienceLoopState } from "../lib/companion-panels";
import type { ToolSelectionRecord } from "../types";

const COMPANION_AGENT_ROLE = "companion";

/** Keyed identity for compaction notices (keeps them update-in-place; B6). */
/** One keyed "context compacted" line: the bridge event and /compact share it. */
export const COMPACTION_NOTICE_KEY = "context-compaction";

let wired = false;

/**
 * A new turn starts with an empty round accumulator: a cancelled turn whose
 * turn_stopped never came (or came late) left its lines here, and the next
 * turn's first flush carried them (TUI audit r2 #33).
 */
export function resetTurnLines(): void {
  acc().turnLines = [];
  acc().pendingToolCalls.clear();
  acc().pendingPinSlices = [];
}

/**
 * Chat tabs: the four per-turn accumulators are keyed PER TAB at use time
 * (H-7 stage 4a) — a background event under `withEventTarget` accumulates
 * on its own tab, so rounds can never merge across tabs and no swap is
 * needed at switches. The ACTIVE tab's set is the one the live pane uses.
 */
export interface TurnAccumulators {
  companionAgentId: string | null;
  pendingToolCalls: Map<string, { name: string; args: ToolArgs }>;
  turnLines: ActivityLine[];
  turnToolSelection: ToolSelectionRecord[];
  /** Stage 4 (#33): lengths of the slices flushTurnSummary appended to
   *  turnToolActivity but no pin consumed yet, FIFO. Each roundBreak pin
   *  shifts one slice and pins exactly that many lines OFF THE HEAD, so a
   *  backgrounded tab that flushed two rounds before its pump resumed pins
   *  each round's own lines, in arrival order. */
  pendingPinSlices: number[];
}
const accByTab = new Map<string, TurnAccumulators>();

/** The accumulator set for the tab the current write belongs to. */
export function acc(): TurnAccumulators {
  const id = eventTargetTab() ?? workspace.activeChatTabId ?? "";
  let owned = accByTab.get(id);
  if (!owned) {
    owned = {
      companionAgentId: null,
      pendingToolCalls: new Map(),
      turnLines: [],
      turnToolSelection: [],
      pendingPinSlices: [],
    };
    accByTab.set(id, owned);
  }
  return owned;
}

/** Opencode-style workspace greeting — one upserted notice at bridge init. */
const WORKSPACE_GREETING_KEY = "workspace-greeting";

/**
 * Close the current round's tool-activity slice: mirror the accumulated
 * lines into the turn slice (the chat stream's roundBreak chunk or the
 * message finalizer pins them under the matching assistant segment) and
 * reset the accumulator so the next round starts empty.
 * Exported so companion-chat can flush on normal (non-interrupted) completion.
 */
export function flushTurnSummary(): void {
  if (acc().turnLines.length > 0) {
    actions.setTurnToolActivity([...acc().turnLines]);
    acc().pendingPinSlices.push(acc().turnLines.length);
  }
  flushToolSelectionRollup();
  acc().turnLines = [];
}

/** Length of the oldest unpinned flushed slice (0 when none is pending). */
export function shiftTurnPinSlice(): number {
  const next = acc().pendingPinSlices.shift();
  return next ?? 0;
}

/** Drop every pending slice marker (the drain pin consumed them all). */
export function clearTurnPinSlices(): void {
  acc().pendingPinSlices = [];
}

/** Build the honest roll-up line from the record alone (no extra claims). */
export function toolSelectionRollupLine(record: ToolSelectionRecord): string {
  const skills = record.selected_skills?.length ?? 0;
  const skillSuffix = skills > 0 ? ` · ${skills} skill${skills === 1 ? "" : "s"}` : "";
  if (record.strategy === "passthrough" && record.candidate_tools === record.available_tools) {
    return `⚙ all ${record.available_tools} tools offered (no selection)${skillSuffix}`;
  }
  return `⚙ ${record.candidate_tools} of ${record.available_tools} tools offered (${record.strategy})${skillSuffix}`;
}

/** Detail lines behind the roll-up row's ↵ expand (B1 pattern). */
function toolSelectionDetailLines(record: ToolSelectionRecord): string[] {
  const lines: string[] = [];
  for (const [source, value] of Object.entries(record.discovered ?? {})) {
    lines.push(`discovered ${source}: ${typeof value === "number" ? value : "true"}`);
  }
  for (const [reason, bucket] of Object.entries(record.filtered_out ?? {})) {
    if (!bucket || typeof bucket !== "object") continue;
    const count = (bucket as { count?: number }).count ?? 0;
    const sample = ((bucket as { sample?: string[] }).sample ?? []).join(", ");
    lines.push(`filtered ${reason}: ${count}${sample ? ` — ${sample}` : ""}`);
  }
  lines.push(
    `schema ~${record.schema_tokens_estimate?.after ?? "?"} tok (was ~${record.schema_tokens_estimate?.before ?? "?"})`,
  );
  const skillTok = record.schema_tokens_estimate?.skill_bodies;
  if (typeof skillTok === "number" && skillTok > 0) {
    lines.push(`skill bodies ~${skillTok} tok`);
  }
  for (const [reason, bucket] of Object.entries(record.would_filter ?? {})) {
    if (!bucket || typeof bucket !== "object") continue;
    const count = (bucket as { count?: number }).count ?? 0;
    if (count > 0) {
      const sample = ((bucket as { sample?: string[] }).sample ?? []).join(", ");
      lines.push(`would filter ${reason}: ${count}${sample ? ` — ${sample}` : ""}`);
    }
  }
  for (const warning of record.warnings ?? []) {
    lines.push(`⚠ ${warning}`);
  }
  lines.push(`latency ${record.selection_latency_ms ?? 0} ms · strategy ${record.strategy}`);
  return lines;
}

/** tool_selection control envelope → store + status label + turn roll-up. */
export function onToolSelection(p: Record<string, unknown>): void {
  const record: ToolSelectionRecord = {
    version: Number(p.version ?? 1),
    session_id: String(p.session_id ?? ""),
    turn_id: String(p.turn_id ?? ""),
    step_index: Number(p.step_index ?? 0),
    available_tools: Number(p.available_tools ?? 0),
    discovered: (p.discovered as Record<string, unknown>) ?? {},
    filtered_out: (p.filtered_out as ToolSelectionRecord["filtered_out"]) ?? {},
    candidate_tools: Number(p.candidate_tools ?? 0),
    selected_tools: (p.selected_tools as string[]) ?? [],
    selected_skills: (p.selected_skills as string[]) ?? [],
    strategy: (p.strategy as ToolSelectionRecord["strategy"]) ?? "passthrough",
    ranking: p.ranking ?? null,
    selection_latency_ms: Number(p.selection_latency_ms ?? 0),
    schema_tokens_estimate:
      (p.schema_tokens_estimate as { before?: number; after?: number; skill_bodies?: number }) ?? {},
    warnings: (p.warnings as string[]) ?? [],
    would_filter: (p.would_filter as ToolSelectionRecord["would_filter"]) ?? {},
  };
  actions.applyToolSelection(record);
  acc().turnToolSelection.push(record);
  // Phase label: only while streaming (the phase is brief by design — the
  // roll-up record is the durable artifact, the label is the live hint).
  // Cleared at the round's response_start (below) — never outlives the
  // phase itself.
  if (eventScopedTab().chatStreaming) {
    actions.setChatTurnStatus("selecting tools");
  }
  // /logs stream: the raw record per step, verbatim (no transforms).
  actions.addEvent({
    id: ulid(),
    timestamp: new Date().toISOString(),
    type: "tool",
    actor: "companion",
    action: "tool_selection",
    detail: JSON.stringify(record),
  });
}

/** Close the per-turn selection roll-up into ONE keyed notice (B1 pattern). */
export function flushToolSelectionRollup(): void {
  const latest = acc().turnToolSelection[acc().turnToolSelection.length - 1];
  if (latest) {
    actions.upsertSystemNotice(TURN_SELECTION_NOTICE_KEY, toolSelectionRollupLine(latest));
    actions.setNoticeDetails(TURN_SELECTION_NOTICE_KEY, toolSelectionDetailLines(latest));
  }
  acc().turnToolSelection = [];
  // NOTE: activeTab().toolSelection intentionally NOT reset — the last known
  // record drives the cost-chip tool-schema suffix between turns (it is the
  // most recent MEASURED value; the chip label says 'tools ~N', not 'now').
}

/** Extract the tool event payload from the legacy {type,message,data} envelope. */
function toolEventData(raw: Record<string, unknown>): Record<string, unknown> {
  const data = raw.data;
  return data && typeof data === "object" ? (data as Record<string, unknown>) : {};
}

/** The agent declared its plan — surface the Todos tab (job-logs precedent). */
function focusTodosTab(): void {
  if (!workspace.activityOpen) actions.toggleActivityConsole();
  actions.setActivityTab("todos");
}

function toolEventName(raw: Record<string, unknown>): string {
  const data = toolEventData(raw);
  return String(data.name ?? raw.name ?? raw.tool ?? "tool");
}

function ensureCompanionAgent(task: string): string {
  const current = acc().companionAgentId;
  if (current) {
    const existing = agentActions.getAgentById(current);
    if (
      existing &&
      existing.state !== "completed" &&
      existing.state !== "failed" &&
      existing.state !== "cancelled"
    ) {
      agentActions.patchAgent(current, {
        currentTask: task,
        state: "running",
      });
      return current;
    }
  }
  const created = agentActions.createAgent({
    role: COMPANION_AGENT_ROLE,
    goal: "Companion research turn",
    state: "running",
    currentTask: task,
    progress: 10,
    permissions: { egress: "controlled", filesystem: "read_only", compute: "none" },
    budget: { maxTokens: 100000, maxIterations: 50 },
  });
  acc().companionAgentId = created;
  agentActions.refreshMissionControl();
  return created;
}

/** Exported so bridge-level tests can drive the event state machine directly. */
export function onBridgeEvent(raw: Record<string, unknown>): void {
  const type = String(raw.type ?? "");
  if (type === "context_compact") {
    // B6: the companion rewrote earlier turns into a summary — tell the user
    // exactly what changed under the model's memory.
    const message = String(raw.message ?? "Conversation compacted").trim();
    const data = toolEventData(raw);
    const summary = typeof data.summary === "string" ? data.summary.trim() : "";
    const kept = typeof data.kept_messages === "number" ? data.kept_messages : null;
    const keptNote = kept === null ? "" : ` (kept ${kept} msgs)`;
    actions.upsertExchangeNotice(
      COMPACTION_NOTICE_KEY,
      `⇅ context compacted${keptNote} — ${message.replace(/\.+$/, "")}`.replace(/\s+/g, " ").trim(),
    );
    if (summary) {
      actions.setNoticeDetails(COMPACTION_NOTICE_KEY, summary.split("\n").filter(Boolean));
    }
    return;
  }
  if (type === "thinking") {
    // ADR-093 D-8: the harness pre-retrieved passages before round 1
    // ("Retrieved 3 passages (sufficient)"). The bridge forwards only the
    // context_pack kind; the line goes to chat so the user sees what evidence
    // the model was given (review r4-harness-9, 2026-10-10).
    const data = toolEventData(raw);
    const message = String(raw.message ?? "").trim();
    if (data.kind === "context_pack" && message) {
      actions.sendSystemNotice(`⌕ ${message}`);
    }
    // ADR-093 D-14 (C22): once per session, the session model's scale is
    // unknown, so it reads with the medium retrieval profile; the notice says
    // how to set it (`r4 model scale set`).
    if (data.kind === "model_scale" && message) {
      actions.sendSystemNotice(`⚖ ${message}`);
    }
    return;
  }
  if (type === "response_start") {
    // Round boundary: CLOSE the tool-activity slice. The chat stream's
    // roundBreak chunk pins the accumulated lines under the text round that
    // triggered them; the slice FIFO (stage 4) pairs every flush with its
    // own pin in arrival order, even when a backgrounded tab's pump only
    // resumes after several flushes (TUI audit r2 #33).
    flushTurnSummary();
    // Phase honesty (2026-10-06 report): the round's model stream has
    // started, so the pre-call phase is over — "selecting tools" (set
    // per round in onToolSelection) and any stale tool label from the
    // previous round must drop here, or the status line pins
    // "selecting tools" for the whole generation (phase truth order:
    // tool_selection → response_start → chunks/tool_call).
    actions.setChatTurnStatus(null);
    return;
  }
  if (type === "tool_blocked" || type === "tools_disabled" || type === "error") {
    const tool = toolEventName(raw);
    const data = toolEventData(raw);
    const entry = formatToolActivityEntry({
      type,
      message: String(raw.message ?? ""),
      data: { name: tool, arguments: data.arguments },
    });
    if (entry) {
      acc().turnLines.push(entry);
    }
    if (type === "tool_blocked") {
      // Chat-visible marker (2026-10-06 report): a policy denial otherwise
      // shows ONLY in the activity console — from chat the agent looks
      // silently stopped until its next text round lands.
      const msg = String(raw.message ?? "").trim() || `Tool ${tool} blocked`;
      actions.sendSystemNotice(
        `⛔ ${msg} — the agent was told not to retry it and to explain how to proceed.`,
      );
    }
    agentActions.refreshMissionControl();
    return;
  }
  if (type === "tool_call") {
    const tool = toolEventName(raw);
    const data = toolEventData(raw);
    const args = parseToolArgs(data.arguments);
    const callId = String(data.tool_call_id ?? "");
    if (callId) acc().pendingToolCalls.set(callId, { name: tool, args });
    // Todo mirror: todowrite rewrites the whole list — mirror it verbatim.
    if (isTodoToolName(tool)) {
      const todos = parseTodoPayload(args);
      if (todos) {
        actions.setSessionTodos(todos);
        focusTodosTab();
      }
    }
    const liveLabel = humanizeToolInProgress(tool, args);
    ensureCompanionAgent(tool);
    agentActions.addTimelineEntry(
      "hypothesis_created",
      iconizeToolLabel(tool, liveLabel),
      undefined,
      acc().companionAgentId ?? undefined,
    );
    actions.addEvent({
      id: ulid(),
      timestamp: new Date().toISOString(),
      type: "tool",
      actor: "companion",
      action: "tool_call",
      detail: liveLabel.slice(0, 80),
    });
    if (eventScopedTab().chatStreaming) {
      actions.setChatTurnStatus(iconizeToolLabel(tool, liveLabel));
      // ask_user shows a dedicated overlay when ask_user_request arrives;
      // surface a clearer mid-status until then.
      if (/ask_user/i.test(tool)) {
        actions.setChatTurnStatus("Waiting for your answer (ask_user)…");
      }
    }
    agentActions.refreshMissionControl();
    return;
  }

  if (type === "tool_result") {
    onToolResult(raw);
    return;
  }

  if (type === "agent_spawn") {
    const name = String(raw.name ?? raw.agent ?? raw.message ?? "sub-agent");
    agentActions.createAgent({
      role: name.slice(0, 32) as "companion",
      goal: "Spawned by Companion",
      state: "running",
      currentTask: "running",
      progress: 5,
      permissions: { egress: "none", filesystem: "read_only", compute: "none" },
      budget: { maxIterations: 20 },
    });
    agentActions.refreshMissionControl();
  }
}

/**
 * The tool's full output. The bridge forwards TOOL_RESULT as
 * {type, message, data:{name, result, tool_call_id}}: `message` is the
 * 200-char preview, `data.result` the full text (≤2000). `raw.result` is the
 * legacy flat shape some tests still send.
 */
function toolResultText(raw: Record<string, unknown>, data: Record<string, unknown>): unknown {
  return data.result ?? raw.result;
}

function toolResultDetail(raw: Record<string, unknown>, fullResult: unknown, tool: string): string {
  if (typeof raw.message === "string") return raw.message.slice(0, 120);
  if (typeof fullResult === "string") return fullResult.slice(0, 120);
  return `Completed ${tool}`;
}

/** Todo mirror on result (fallback when the call's args were not seen). */
function mirrorTodosFromResult(
  raw: Record<string, unknown>,
  args: unknown,
  fullResult: unknown,
): void {
  const todos = parseTodoPayload(args) ?? parseTodoPayload(fullResult) ?? parseTodoPayload(raw.message);
  if (!todos) return;
  actions.setSessionTodos(todos);
  focusTodosTab();
}

/** Waiting (not running) so the Agents panel doesn't show "done: tool" @ 95% as active work. */
function markCompanionWaiting(tool: string): void {
  const current = acc().companionAgentId;
  if (!current) return;
  const agent = agentActions.getAgentById(current);
  const progress = Math.min(90, (agent?.progress ?? 20) + 15);
  agentActions.updateAgentProgress(current, progress, `finished ${tool}`);
  agentActions.patchAgent(current, { state: "waiting", currentTask: `last: ${tool}` });
}

function recordToolResult(tool: string, entry: ActivityLine | null, detail: string): void {
  agentActions.addTimelineEntry(
    "finding_registered",
    entry?.text ?? iconizeToolLabel(tool, `Result: ${tool}`),
    detail,
    acc().companionAgentId ?? undefined,
  );
  actions.addEvent({
    id: ulid(),
    timestamp: new Date().toISOString(),
    type: "tool",
    actor: "companion",
    action: "tool_result",
    detail: (entry ? entry.text : tool).slice(0, 80),
  });
  agentActions.addExplanation({
    decisionId: ulid(),
    decisionType: "tool_result",
    target: tool,
    reasoningChain: [`Companion invoked ${tool}`, detail],
    graphPaths: [],
    supportingEvidence: [],
    confidenceCalculation: { prior: 0.5, likelihood: 0.6, posterior: 0.55 },
    alternatives: [],
    rejected: [],
    timestamp: new Date().toISOString(),
    agentId: acc().companionAgentId ?? "companion",
  });
}

/** Pop the call context recorded at tool_call (args the result reuses). */
function takePendingCall(data: Record<string, unknown>): { name: string; args: ToolArgs } | undefined {
  const callId = String(data.tool_call_id ?? "");
  const pending = acc().pendingToolCalls.get(callId);
  if (callId) acc().pendingToolCalls.delete(callId);
  return pending;
}

function toolResultEntry(
  raw: Record<string, unknown>,
  tool: string,
  args: unknown,
  fullResult: unknown,
  pending: { name: string; args: ToolArgs } | undefined,
): ActivityLine | null {
  return formatToolActivityEntry(
    { type: "tool_result", message: String(raw.message ?? ""), data: { name: tool, arguments: args, result: fullResult } },
    pending ? { name: pending.name, args: pending.args } : undefined,
  );
}

function flagDeniedResult(raw: Record<string, unknown>, tool: string, detail: string): void {
  const denied = isDeniedText(detail) || isDeniedText(String(raw.message ?? ""));
  if (denied && eventScopedTab().chatStreaming) actions.setChatTurnStatus(`⛔ blocked: ${tool}`);
}

/** tool_result event → activity line, todo mirror, agent row, timeline. */
function onToolResult(raw: Record<string, unknown>): void {
  const tool = toolEventName(raw);
  const data = toolEventData(raw);
  const pending = takePendingCall(data);
  const fullResult = toolResultText(raw, data);
  const detail = toolResultDetail(raw, fullResult, tool);
  const args = pending?.args ?? data.arguments;
  if (isTodoToolName(tool)) {
    mirrorTodosFromResult(raw, args, fullResult);
    // The bridge mirrored the list into the plan graph while the tool ran;
    // reads answer mid-turn (plan H-8), so the graph follows at once.
    fetchPlanGraph();
  }
  const entry = toolResultEntry(raw, tool, args, fullResult, pending);
  if (entry) acc().turnLines.push(entry);
  flagDeniedResult(raw, tool, detail);
  markCompanionWaiting(tool);
  recordToolResult(tool, entry, detail);
  agentActions.refreshMissionControl();
}

function onTurnStopped(): void {
  const current = acc().companionAgentId;
  if (current) {
    const agent = agentActions.getAgentById(current);
    if (agent && (agent.state === "running" || agent.state === "planning" || agent.state === "waiting")) {
      agentActions.patchAgent(current, {
        state: "completed",
        progress: 100,
        currentTask: "turn complete",
      });
    }
  }
  actions.setChatTurnStatus(null);
  flushTurnSummary();
  agentActions.refreshMissionControl();
}

export function onProjectJobForAgents(raw: unknown): void {
  const ev = raw as { phase?: string; jobId?: string; operation?: string; message?: string };
  const phase = ev.phase ?? "";
  const op = ev.operation ?? "job";
  const jobId = ev.jobId ?? "";
  if (phase === "started") {
    agentActions.createAgent({
      role: "rc1",
      goal: `RC1 ${op}`,
      state: "running",
      currentTask: ev.message ?? `${op} ${jobId}`.trim(),
      progress: 15,
      permissions: { egress: "controlled", filesystem: "read_only", compute: "slurm" },
      budget: { maxIterations: 20 },
    });
    agentActions.addTimelineEntry("experiment_started", `RC1 ${op} started`, jobId);
    agentActions.refreshMissionControl();
    return;
  }
  if (["done", "error", "cancelled"].includes(phase)) {
    for (const a of agentActions.getAgentsByState("running").filter((x) => x.role === "rc1")) {
      agentActions.patchAgent(a.id, {
        state: phase === "done" ? "completed" : phase === "cancelled" ? "cancelled" : "failed",
        progress: phase === "done" ? 100 : a.progress,
        currentTask: `${op} ${phase}`,
      });
    }
    if (phase === "done") {
      agentActions.addExplanation({
        decisionId: ulid(),
        decisionType: "project_job",
        target: `RC1 ${op}`,
        reasoningChain: [
          `Project operation ${op} completed`,
          ev.message ? String(ev.message).slice(0, 120) : `job ${jobId}`,
        ],
        graphPaths: [],
        supportingEvidence: [],
        confidenceCalculation: { prior: 0.5, likelihood: 0.7, posterior: 0.65 },
        alternatives: [],
        rejected: [],
        timestamp: new Date().toISOString(),
        agentId: "rc1",
      });
    }
    agentActions.refreshMissionControl();
  }
}

let resourceTimer: ReturnType<typeof setInterval> | null = null;

/**
 * Poll Companion state every 5s: host resources plus the research panel feeds
 * (turn artifacts, science loop, knowledge base) — the RPCs tui-js used for
 * its research rail.
 *
 * Chat tabs: the feeds are chat-scoped, so the poller must follow the ACTIVE
 * tab's bridge — retargetResourcePolling re-arms the timer on every tab
 * switch (same cadence, new target).
 */
function startResourcePolling(bridge: ReturnType<typeof getCompanionBridge>): void {
  if (resourceTimer) return;
  beginResourcePollTimer(bridge);
}

/** Re-arm the 5s poller against a (possibly different) bridge — tab switches. */
export function retargetResourcePolling(bridge: ReturnType<typeof getCompanionBridge>): void {
  if (resourceTimer) {
    clearInterval(resourceTimer);
    resourceTimer = null;
  }
  beginResourcePollTimer(bridge);
}

/** Disarm the poller: the active tab has no bridge client yet. Its
 * init_state re-arms it (tabs/wiring retargetResourcePolling). */
export function stopResourcePolling(): void {
  if (resourceTimer) clearInterval(resourceTimer);
  resourceTimer = null;
}

/** The four read-only feeds of one poll round → their store setters. */
const RESOURCE_FEEDS: ReadonlyArray<readonly [string, (res: unknown) => void]> = [
  ["get_resources", (res) => actions.setResources((res as { resources?: HostResources } | null)?.resources ?? null)],
  ["get_artifacts", (res) => actions.setTurnArtifacts((res as { artifacts?: string[] } | null)?.artifacts ?? [])],
  ["get_science_loop", (res) => actions.setScienceLoop(isObject(res) ? (res as ScienceLoopState) : null)],
  ["get_knowledge", (res) => actions.setKnowledgeBase(isObject(res) ? (res as KnowledgeBaseState) : null)],
];

function isObject(value: unknown): boolean {
  return Boolean(value) && typeof value === "object";
}

/**
 * A poller never spawns a bridge. A running turn is no reason to wait: the
 * feeds are read RPCs, which the bridge answers mid-turn from its read pool
 * (plan H-8, 2026-10-18; before it every mid-turn RPC parked until the turn
 * ended — TUI audit r2 #25/#40).
 */
function pollerShouldWait(bridge: ReturnType<typeof getCompanionBridge>): boolean {
  return !bridge.alive;
}

/** One poll round; stops early when the bridge goes away mid-round. */
async function pollResourceFeeds(bridge: ReturnType<typeof getCompanionBridge>): Promise<void> {
  for (const [method, apply] of RESOURCE_FEEDS) {
    if (pollerShouldWait(bridge)) return;
    try {
      apply(await bridge.request(method, {}, 10_000));
    } catch {
      // Bridge momentarily down — keep the last snapshot; next tick retries.
    }
  }
}

function beginResourcePollTimer(bridge: ReturnType<typeof getCompanionBridge>): void {
  // One round at a time: a slow round must not stack a new chain per tick.
  let roundInFlight = false;
  const poll = async () => {
    if (roundInFlight || pollerShouldWait(bridge)) return;
    roundInFlight = true;
    try {
      await pollResourceFeeds(bridge);
    } finally {
      roundInFlight = false;
    }
  };
  void poll();
  resourceTimer = setInterval(() => {
    void poll();
  }, 5_000);
  resourceTimer.unref?.();
}

/** Refetch the structured Plan graph (the bridge's mirror of the todo list). */
function fetchPlanGraph(): void {
  void import("../lib/session-plan")
    .then((m) => m.fetchSessionPlan())
    .catch(() => undefined);
}

/** Turn-end views: /changes drawer (W1) + Plan tab graph, from the ACTIVE bridge. */
function refreshTurnEndViews(): void {
  // W1: refresh the session file-change snapshot at each turn end so the
  // /changes side-by-side drawer stays current without a manual refresh.
  void import("../lib/session-changes")
    .then((m) => m.fetchSessionChanges())
    .catch(() => undefined);
  // Keep the Plan tab graph in lockstep with the mirrored task graph.
  fetchPlanGraph();
}

/** turn_stopped channel (the bridge emits it only on cancel): close the agent row + refresh. */
export function handleTurnStopped(): void {
  onTurnStopped();
  refreshTurnEndViews();
}

/**
 * Normal turn end (send_message / confirm_plan resolved): the bridge emits
 * no turn_stopped line then (TUI audit r2 #24), so the chat adapter calls
 * this once the owning tab is active. `refresh` is false when the turn ran
 * on an injected bridge — the refresh RPCs go to the active tab's bridge.
 */
export function handleTurnComplete(opts: { refresh: boolean }): void {
  onTurnStopped();
  if (opts.refresh) refreshTurnEndViews();
}

/** init_state channel: seed inventory + chat-scoped session fields. */
export function handleInitState(snapshot: unknown): void {
  applyBridgeStatePatch(mapBridgeStateToWorkspace(snapshot, undefined));
  // Opencode-style workspace greeting: absolute root visible at boot so the
  // user knows exactly where the agent operates before the first message.
  const root =
    snapshot && typeof snapshot === "object"
      ? ((snapshot as Record<string, unknown>).workspace_root as string | undefined)
      : undefined;
  if (root) {
    actions.upsertSystemNotice(
      WORKSPACE_GREETING_KEY,
      `⌂ workspace ready: ${root}`,
    );
    actions.setNoticeDetails(WORKSPACE_GREETING_KEY, [
      "all file and shell tools resolve relative to this directory",
      "launch r4 console from another directory to change it",
    ]);
  }
  // ADR-101 D-1: the sandbox is off by default — say so, and how to turn it on.
  announceSandboxFromSnapshot(snapshot);
}

/** team_event channel: live team visibility into the teams store. */
export function handleTeamEvent(payload: unknown): void {
  void import("../stores/teams")
    .then((m) => m.teamsActions.ingestEvent(payload as import("../lib/team-live").TeamEventLine))
    .catch(() => undefined);
}

/** Attach the full channel set to ONE bridge (legacy single-bridge path; the
 *  chat-tabs wiring attaches per tab instead — tabs/wiring.ts). */
export function attachRuntimeBridgeHandlers(bridge: ReturnType<typeof getCompanionBridge>): void {
  bridge.on("event", onBridgeEvent);
  bridge.on("turn_stopped", handleTurnStopped);
  bridge.on("project_job_event", onProjectJobForAgents);
  bridge.on("control_event", onControlEvent);
  bridge.on("team_event", handleTeamEvent);
  bridge.on("init_state", (snapshot) => {
    handleInitState(snapshot);
    startResourcePolling(getCompanionBridge());
  });
  // round-3 task 7: one-line notice when the bridge's r4 source drifted
  bridge.on("source_stale", (msg: string) => {
    actions.sendSystemNotice(`[stale] ${msg}`);
  });
}

/** Wire once on live app mount — tools + project jobs. */
export function wireCompanionRuntimeBridge(): void {
  if (wired) return;
  wired = true;
  wireProjectJobEventsOnce();
  const bridge = getCompanionBridge();
  attachRuntimeBridgeHandlers(bridge);
  startResourcePolling(bridge);
  // Start the bridge at launch so init_state (all builtin/local skills, config)
  // lands immediately — previously triggered only on first chat request, which
  // made /skills look empty until the user chatted (S2 freeze chain report).
  void bridge.ensureStarted().catch((err) => {
    actions.setChatFallback(
      true,
      `companion bridge unavailable: ${err instanceof Error ? err.message.slice(0, 80) : "spawn failed"}`,
    );
  });
}

/** ADR-037: per-step selection record of the in-flight turn (roll-up). */
const TURN_SELECTION_NOTICE_KEY = "tool-selection-summary";

/** C3/B4: control envelopes ({type, payload}) → workspace slices. */
export function onControlEvent(raw: unknown): void {
  const env = raw as { type?: string; payload?: unknown };
  if (env?.payload == null || typeof env.payload !== "object") return;
  const p = env.payload as Record<string, unknown>;
  // B4: live reasoning stream (thinking_delta/thinking_done) → thinking slice.
  if (env?.type === "thinking_delta") {
    actions.appendThinkingDelta(String(p.text ?? ""));
    return;
  }
  if (env?.type === "thinking_done") {
    actions.closeThinking();
    return;
  }
  if (env?.type === "tool_selection") {
    onToolSelection(p);
    return;
  }
  if (env?.type !== "cost") return;
  actions.setCostState({
    session_id: String(p.session_id ?? ""),
    model: String(p.model ?? ""),
    turn_tokens: Number(p.turn_tokens ?? 0),
    total_tokens: Number(p.total_tokens ?? 0),
    cost_usd_turn: Number(p.cost_usd_turn ?? 0),
    cost_usd_total: Number(p.cost_usd_total ?? 0),
    price_known: Boolean(p.price_known),
    context_used_tokens: Number(p.context_used_tokens ?? 0),
    context_limit_tokens: Number(p.context_limit_tokens ?? 0),
  });
}

/** Test helper */
export function resetCompanionRuntimeBridgeForTests(): void {
  wired = false;
  accByTab.clear();
  acc().companionAgentId = null;
}
