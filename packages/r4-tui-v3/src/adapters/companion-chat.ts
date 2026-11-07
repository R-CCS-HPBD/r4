/**
 * Chat backend via the persistent Companion JSONL bridge (`send_message`).
 *
 * Inventory/polling stays on R4CliDataSource; only chat routes through Research Companion.
 * On bridge failure, yield an honest companion-unavailable message (not "connect r4 CLI").
 */

import { EventEmitter } from "node:events";
import type { ChatHistoryItem, ChatStreamChunk } from "./types";
import type { ChatBackend } from "../stores";
import { actions, workspace, activeTab } from "../stores";
import {
  applyBridgeStatePatch,
  getCompanionBridge,
  mapBridgeStateToWorkspace,
  type CompanionBridgeClient,
} from "../bridge";
import { bridgeErrorHint } from "../bridge/jsonl-client";
import { sessionErrorOf } from "../bridge/session-recovery";
import { rpc, type RpcResult } from "../bridge/rpc";
import { TurnReplyFold, type ReplyEvent, type ReplyPiece } from "../lib/turn-reply";
import { handleTurnComplete } from "../bridge/companion-runtime-bridge";
import { isTabClosed, isTabInBackground, waitForTabTurn } from "../tabs/registry";
import { isOfflineBackend } from "../lib/console-mode";
import { companionUnavailableReply, generateLocalReply, isClientReadTimeout } from "../lib/chat-reply";

type BridgeCitation = {
  sid?: string;
  artifact_id?: string;
  title?: string;
  kind?: string;
  preview?: string;
  source_id?: string;
  project_id?: string;
};

/** The cited passages as the chat cell's citation block reads them (the
 * block was never fed: TUI audit r2 #66, 2026-10-17). */
export function citationChunk(rows: BridgeCitation[] | undefined): ChatStreamChunk | null {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return {
    citations: rows.map((row) => ({
      artifactId: String(row.artifact_id ?? row.sid ?? ""),
      kind: String(row.kind ?? "passage"),
      title: `[${row.sid ?? "?"}] ${row.title ?? ""}`.trim(),
      contentPreview: String(row.preview ?? ""),
      ...(row.sid ? { sid: String(row.sid) } : {}),
      ...(row.source_id ? { sourceId: String(row.source_id) } : {}),
      ...(row.project_id ? { projectId: String(row.project_id) } : {}),
    })),
  };
}

/**
 * A send_message / confirm_plan reply (contract: rpc-types.gen.ts). Partial
 * because jsonl-client settles an aborted in-flight send with
 * `{aborted: true, cancelled: true}` instead of a bridge reply.
 */
type SendMessageResult = Partial<RpcResult<"send_message">> & { aborted?: boolean };

/** One session.venue_sync answer (contract: rpc-types.gen.ts). */
type VenueSyncResult = RpcResult<"session.venue_sync">;

/** Poll cadence of the remote-turn watcher (tests shorten it). */
export let REMOTE_POLL_MS = 1_500;
export function setRemotePollMsForTests(ms: number): void {
  REMOTE_POLL_MS = ms;
}
const REMOTE_APPROVAL_EVERY_N_POLLS = 2;

function lastAssistantText(result: SendMessageResult): string {
  // Prefer the turn's own return value. The state-messages fallback below can
  // land on a tool-call carrier row (content null) and render a junk `""`
  // bubble — the "agent stopped silently" look (2026-10-06).
  const returned = result.response ?? "";
  if (returned.trim()) return returned;
  const msgs = result.state?.messages ?? [];
  const last = [...msgs].reverse().find((m) => m.role === "assistant" && typeof m.content === "string");
  if (!last) return returned;
  return typeof last.content === "string" ? last.content : "";
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * The reply for a failed turn RPC. A hinted error is the server's own
 * answer (an APIError: stale JWT, missing model, quota): the bridge is up,
 * so show its recovery line instead of "check companion is installed"
 * (TUI audit r2 #28).
 */
function sendFailureReply(err: unknown): string {
  const msg = errorMessage(err);
  // A read timeout is the r4 client's own wait on a silent model endpoint,
  // not a server-side timeout (chat#12): companionUnavailableReply says so.
  if (isClientReadTimeout(msg)) return companionUnavailableReply(msg);
  const hint = bridgeErrorHint(err);
  if (hint) return `[Chat unavailable] ${msg}\n→ ${hint}`;
  return companionUnavailableReply(msg);
}

/**
 * Client-side max wait for a Companion chat turn: NONE.
 * Agent turns run until the server completes or the user interrupts (Esc).
 * A wall-clock cap used to cut long-running agent work off at 10 minutes;
 * removed 2026-09-11 — the bridge request() treats a non-finite value as
 * "no timer".
 */
export const COMPANION_CHAT_TIMEOUT_MS = Number.POSITIVE_INFINITY;

const DONE: ChatStreamChunk = { done: true };

/**
 * One turn's response_* bridge events → stream chunks. The reply rules
 * (round breaks, the response_done remainder, turn ids) live in the shared
 * `lib/turn-reply.ts` (ADR-103 D-3, 2026-10-31); this sink queues them and
 * stamps time-to-first-token on the first streamed delta.
 */
class TurnEventSink {
  readonly queue: ChatStreamChunk[] = [];
  private readonly reply = new TurnReplyFold();
  private wake: (() => void) | null = null;

  constructor(private readonly startedAt: number) {}

  get sawDelta(): boolean {
    return this.reply.sawDelta;
  }

  readonly onEvent = (raw: Record<string, unknown>): void => {
    const pieces = this.reply.fold(raw as ReplyEvent);
    if (pieces.length === 0) return;
    for (const piece of pieces) this.queue.push(this.toChunk(piece));
    this.notify();
  };

  // Client-side TTFT: request dispatch → first content delta. The
  // server-side rivault_llm_first_token_seconds metric is the durable
  // anchor; this is the interactive per-turn surface for it.
  private toChunk(piece: ReplyPiece): ChatStreamChunk {
    if (piece.kind === "roundBreak") return { roundBreak: true };
    if (piece.kind === "turnOpened") return { turnId: piece.turnId };
    return { delta: piece.text, ...(piece.first ? { ttftMs: Date.now() - this.startedAt } : {}) };
  }

  notify(): void {
    this.wake?.();
    this.wake = null;
  }

  waitForWork(): Promise<void> {
    return new Promise<void>((resolve) => {
      this.wake = resolve;
    });
  }
}

/** One agent-turn RPC on a tab's bridge (send_message or confirm_plan). */
interface BridgeTurn {
  backend: CompanionChatBackend;
  bridge: CompanionBridgeClient;
  /** Owning chat tab: store writes wait until it is the active tab. */
  tabId: string;
  method: "send_message" | "confirm_plan";
  params: Record<string, unknown>;
  model: string | undefined;
  signal: AbortSignal | undefined;
  /** Turn-end refresh RPCs go to the active tab's bridge — only when that is this turn's bridge. */
  refresh: boolean;
}

type TurnSend = { settled: boolean; result: SendMessageResult | null; error: unknown };

/** Stream one agent turn: live response_* chunks, then the RPC outcome. */
async function* runBridgeTurn(turn: BridgeTurn): AsyncGenerator<ChatStreamChunk> {
  const { bridge, signal } = turn;
  // Interrupted before dispatch (Esc right after Enter, while ensureStarted /
  // set_model were awaited): never send the turn. The abort listener below
  // would never fire for an already-aborted signal, and the bridge ignores a
  // cancel_turn that precedes its turn — the reply then streamed hidden into
  // the session history (stabilization sweep 2026-10-21, chat#2).
  if (signal?.aborted) {
    yield DONE;
    return;
  }
  const sink = new TurnEventSink(Date.now());
  const onAbort = () => {
    bridge.abortInFlightSend();
    bridge.cancelTurn();
  };
  signal?.addEventListener("abort", onAbort);
  bridge.on("event", sink.onEvent);
  const send: TurnSend = { settled: false, result: null, error: null };
  void rpc(bridge, turn.method, turn.params, COMPANION_CHAT_TIMEOUT_MS)
    .then((value) => {
      send.result = value;
    })
    .catch((err) => {
      send.error = err ?? new Error("bridge request failed");
    })
    .finally(() => {
      send.settled = true;
      sink.notify();
      // A queued remote turn has only started: it settles when the watcher
      // sees it end (the tab stays busy until then; r2 #36 review).
      if (!isQueuedRemote(send.result)) emitTurnSettled(bridge, turn.method);
    });
  try {
    yield* drainUntilSettled(sink, send, signal);
    yield* turnOutcome(turn, sink, send);
  } finally {
    signal?.removeEventListener("abort", onAbort);
    bridge.off("event", sink.onEvent);
  }
}

function isQueuedRemote(result: SendMessageResult | null): boolean {
  return Boolean(result?.remote && result.queued);
}

/**
 * The turn RPC answered: tell the tab registry right away (`turn_settled`,
 * routed past the background buffer) so a background tab's busy glyph and
 * Ctrl+B task settle without waiting for a visit (TUI audit r2 #36). Only
 * a real channel emitter carries it (hand-rolled test fakes are skipped).
 */
function emitTurnSettled(bridge: CompanionBridgeClient, method: BridgeTurn["method"]): void {
  if (!(bridge instanceof EventEmitter)) return;
  try {
    bridge.emit("turn_settled", { method });
  } catch {
    // a listener's failure must not break the turn's outcome
  }
}

async function* drainUntilSettled(
  sink: TurnEventSink,
  send: TurnSend,
  signal: AbortSignal | undefined,
): AsyncGenerator<ChatStreamChunk> {
  while (true) {
    while (sink.queue.length > 0) yield sink.queue.shift()!;
    if (signal?.aborted || send.settled) return;
    await sink.waitForWork();
  }
}

/** A turn RPC that failed: a typed session error hands the store its
 * recovery (c3: conflict / corrupt row); anything else is a failure reply. */
function* failedTurn(sink: TurnEventSink, error: unknown): Generator<ChatStreamChunk> {
  // Text already streamed stays its own row; the failure opens the next
  // one (it was glued onto the partial reply, chat#7/#12).
  if (sink.sawDelta) yield { roundBreak: true };
  const sessionError = sessionErrorOf(error);
  if (sessionError) yield { sessionError };
  else yield { delta: sendFailureReply(error), model: "offline", fallback: true };
  yield DONE;
}

async function* turnOutcome(turn: BridgeTurn, sink: TurnEventSink, send: TurnSend): AsyncGenerator<ChatStreamChunk> {
  if (send.error) {
    yield* failedTurn(sink, send.error);
    return;
  }
  const result = send.result;
  if (!result || result.aborted || turn.signal?.aborted) {
    yield DONE;
    return;
  }
  if (result.remote && result.queued) {
    // Remote (frontend-venue) turn: the sandbox worker pops the queue and
    // runs it; watch the journal until the assistant row lands.
    yield* turn.backend.watchRemoteTurn(turn.bridge, result, turn.model, turn.signal, turn.tabId);
    return;
  }
  yield* completedTurnChunks(turn, sink, result);
}

async function* completedTurnChunks(
  turn: BridgeTurn,
  sink: TurnEventSink,
  result: SendMessageResult,
): AsyncGenerator<ChatStreamChunk> {
  // The result can land while the owning tab is in the background: its
  // session state, tool accumulators and agent row belong to THAT tab —
  // apply them once it is active again, never into another tab (r2 #29).
  if (!(await waitForTabTurn(turn.tabId))) {
    yield DONE;
    return;
  }
  // Tag the user row with its session turn id: rewind cuts by it (r2 #17).
  if (typeof result.turn_id === "string" && result.turn_id) yield { turnId: result.turn_id };
  const cited = citationChunk(result.citations);
  if (cited) yield cited;
  if (result.state) {
    applyBridgeStatePatch(mapBridgeStateToWorkspace(result.state, undefined));
  }
  // Turn complete (state applied): collapsed tool summary, agent row done,
  // Plan graph + /changes refresh — the bridge emits turn_stopped only on
  // cancel (r2 #24).
  handleTurnComplete({ refresh: turn.refresh });
  const modelName = result.state?.session?.model ?? turn.model;
  if (modelName) yield { model: modelName };
  yield* finalTextChunks(result, sink.sawDelta, modelName);
  yield* suggestionChunks(result, modelName);
  while (sink.queue.length > 0) yield sink.queue.shift()!;
  yield DONE;
}

function* finalTextChunks(
  result: SendMessageResult,
  sawDelta: boolean,
  modelName: string | undefined,
): Generator<ChatStreamChunk> {
  if (result.cancelled) {
    yield { delta: "Stopped.", model: modelName };
    return;
  }
  if (sawDelta) return;
  const finalText = lastAssistantText(result);
  if (finalText) {
    yield { delta: finalText, model: modelName };
    return;
  }
  yield { delta: companionUnavailableReply("empty Companion response"), model: "offline", fallback: true };
}

// T5 topic-10: server suggestion on completion — shown as a separate stream
// tail line. Capability-detected: only present when the rule engine fired
// (never fabricated on idle turns). Distinct glyph keeps it distinguishable
// from the assistant's own prose.
function* suggestionChunks(result: SendMessageResult, modelName: string | undefined): Generator<ChatStreamChunk> {
  const next = result.suggested_next;
  if (result.cancelled || typeof next !== "string" || !next.trim()) return;
  yield { delta: `\n\n⚑ suggested: ${next}`, model: modelName };
}

export class CompanionChatBackend implements ChatBackend {
  readonly name = "companion-bridge";
  private readonly bridgeOverride: CompanionBridgeClient | null;

  constructor(bridge?: CompanionBridgeClient | null) {
    this.bridgeOverride = bridge ?? null;
  }

  private bridge(): CompanionBridgeClient {
    return this.bridgeOverride ?? getCompanionBridge();
  }

  interrupt(): void {
    const bridge = this.bridge();
    bridge.abortInFlightSend();
    bridge.cancelTurn();
  }

  async chat(message: string, history?: ChatHistoryItem[], model?: string): Promise<string> {
    let text = "";
    for await (const chunk of this.chatStream(message, history, model)) {
      if (chunk.delta) text += chunk.delta;
    }
    return text;
  }

  async *chatStream(
    message: string,
    _history?: ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk> {
    if (isOfflineBackend()) {
      yield { delta: generateLocalReply(message, 0, "offline"), model: "offline", fallback: true };
      yield DONE;
      return;
    }
    // The turn belongs to the tab it was sent from — the active one now.
    const tabId = workspace.activeChatTabId;
    const bridge = this.bridge();
    try {
      await bridge.ensureStarted();
    } catch (err) {
      yield { delta: companionUnavailableReply(errorMessage(err)), model: "offline", fallback: true };
      yield DONE;
      return;
    }
    if (model) {
      try {
        await bridge.request("set_model", { model }, 15_000);
      } catch {
        // Best-effort; companion may already have a provisioned model.
      }
    }
    yield* runBridgeTurn(this.turn(bridge, tabId, "send_message", { text: message }, model, signal));
  }

  /**
   * Execute an accepted plan (`confirm_plan` execute_auto / execute_ask):
   * a whole tool-using agent turn whose reply streams exactly like a chat
   * turn (r2 #11/#62). Same chunks, same tab gating, same turn-end hook.
   */
  async *planStream(
    action: "execute_auto" | "execute_ask",
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk> {
    const tabId = workspace.activeChatTabId;
    const bridge = this.bridge();
    yield* runBridgeTurn(this.turn(bridge, tabId, "confirm_plan", { action }, undefined, signal));
  }

  private turn(
    bridge: CompanionBridgeClient,
    tabId: string,
    method: BridgeTurn["method"],
    params: Record<string, unknown>,
    model: string | undefined,
    signal: AbortSignal | undefined,
  ): BridgeTurn {
    return { backend: this, bridge, tabId, method, params, model, signal, refresh: this.bridgeOverride === null };
  }
}

// Remote-turn watcher (ADR-046 A2, G-RS-22) — a method body kept outside the
// class for readability; installed below. The per-poll decision is the pure
// `evaluateRemoteSync` in lib/venue-runtime.ts (unit-pinned there).
//
// Tab ownership (TUI audit r2 #30/#43/#58): the watcher belongs to the tab
// that sent the turn. While that tab is in the background it pauses before
// every store write (and every poll), and resumes on activation — it never
// writes into the active tab, and a tab switch never ends the watch. Only a
// session change INSIDE the owning tab does.
async function* watchRemoteTurnImpl(
  this: CompanionChatBackend,
  bridge: CompanionBridgeClient,
  first: SendMessageResult,
  model: string | undefined,
  signal: AbortSignal | undefined,
  tabId: string,
): AsyncGenerator<ChatStreamChunk> {
  const w = await openRemoteWatch(bridge, first, model, signal, tabId);
  const end = yield* pollRemoteTurn(w);
  // A closed tab's watch ends here instead of parking forever (r2 #31).
  if (!(await waitForTabTurn(tabId))) {
    yield DONE;
    return;
  }
  if (end === "aborted") {
    actions.sendSystemNotice("stopped watching — the remote turn continues on the server (/venue to check)");
  } else if (end === "left") {
    actions.sendSystemNotice("stopped watching — this tab moved to another session; the remote turn continues on the server (/venue to check)");
  }
  actions.setChatTurnStatus(null);
  yield DONE;
}

type VenueHelpers = typeof import("../lib/venue-runtime");
type StoreActions = typeof import("../stores")["actions"];
type RemoteWatchEnd = "finished" | "failed" | "left" | "aborted";

/** Mutable state of one remote-turn watch. */
interface RemoteWatch {
  bridge: CompanionBridgeClient;
  model: string | undefined;
  signal: AbortSignal | undefined;
  tabId: string;
  /** The owning tab's session when the watch began. */
  sessionId: string;
  baseTotal: number;
  venue: VenueHelpers;
  venueBridge: typeof import("../bridge/venue-bridge");
  mapSnapshotMessages: typeof import("../bridge/session-bridge")["mapSnapshotMessages"];
  shown: string;
  polls: number;
  lastPhase: string;
  lastActivity: string[];
  adopted: boolean;
  /** turn_settled was emitted from a background poll. */
  settled: boolean;
}

async function openRemoteWatch(
  bridge: CompanionBridgeClient,
  first: SendMessageResult,
  model: string | undefined,
  signal: AbortSignal | undefined,
  tabId: string,
): Promise<RemoteWatch> {
  const venue = await import("../lib/venue-runtime");
  const venueBridge = await import("../bridge/venue-bridge");
  const { mapSnapshotMessages } = await import("../bridge/session-bridge");
  // Never wait for a visit here: a tab sent to the background right after
  // the send must still poll (watchInBackground). The owner's session id is
  // read from the flat store only while the owner is the active tab, else
  // on its first visit (ownerLeft).
  const active = ownerActive(tabId);
  if (active) actions.setChatTurnStatus("queued to the remote session…");
  return {
    bridge,
    model,
    signal,
    tabId,
    sessionId: active ? activeTab().sessionId : "",
    baseTotal: sendBaseTotal(first),
    venue,
    venueBridge,
    mapSnapshotMessages,
    shown: "",
    polls: 0,
    lastPhase: "",
    lastActivity: [],
    adopted: false,
    settled: false,
  };
}

function ownerActive(tabId: string): boolean {
  return !isTabInBackground(tabId) && !isTabClosed(tabId);
}

/** Pause while the owning tab is in the background; true once the watch must end. */
async function ownerLeft(w: RemoteWatch): Promise<boolean> {
  if (!(await waitForTabTurn(w.tabId))) return true;
  w.sessionId ||= activeTab().sessionId;
  return isAborted(w.signal) || activeTab().sessionId !== w.sessionId;
}

function leftReason(w: RemoteWatch): RemoteWatchEnd {
  return isAborted(w.signal) ? "aborted" : "left";
}

/**
 * While the owning tab is in the background, keep polling its own bridge
 * (no store writes) so the end of the remote turn reaches the tab registry
 * as `turn_settled`: the busy glyph and a Ctrl+B task settle without a
 * visit, as for a local turn (TUI audit r2 #36 review). Returns once the
 * tab is active again or closed, or the turn ended.
 */
async function watchInBackground(w: RemoteWatch): Promise<void> {
  while (!w.settled && isTabInBackground(w.tabId) && !isAborted(w.signal)) {
    const sync = await quietSync(w.bridge);
    if (sync && remoteTurnEnded(w, sync)) {
      w.settled = true;
      emitTurnSettled(w.bridge, "send_message");
      return;
    }
    await new Promise<void>((resolve) => setTimeout(resolve, REMOTE_POLL_MS));
  }
}

async function quietSync(bridge: CompanionBridgeClient): Promise<VenueSyncResult | null> {
  try {
    return await rpc(bridge, "session.venue_sync", {}, 30_000);
  } catch {
    return null;
  }
}

/** The pure end-of-turn test of one venue_sync answer (no store writes). */
function remoteTurnEnded(w: RemoteWatch, sync: VenueSyncResult): boolean {
  w.adopted ||= sync.adopted === true;
  const step = w.venue.evaluateRemoteSync({ ...sync, adopted: w.adopted }, w.baseTotal, w.shown);
  return remoteTurnOver(step, sync);
}

async function* pollRemoteTurn(w: RemoteWatch): AsyncGenerator<ChatStreamChunk, RemoteWatchEnd> {
  while (!isAborted(w.signal)) {
    await new Promise<void>((resolve) => setTimeout(resolve, REMOTE_POLL_MS));
    await watchInBackground(w);
    if (await ownerLeft(w)) return leftReason(w);
    let sync: VenueSyncResult;
    try {
      sync = await rpc(w.bridge, "session.venue_sync", {}, 30_000);
    } catch (err) {
      yield remoteSyncFailure(errorMessage(err));
      return "failed";
    }
    if (await ownerLeft(w)) return leftReason(w);
    const step = await stepRemoteWatch(w, sync);
    if (step.reset) yield { roundBreak: true };
    if (step.delta) yield { delta: step.delta, model: w.model };
    if (!remoteTurnOver(step, sync)) continue;
    yield* finishRemoteTurn(w, step, sync);
    return "finished";
  }
  return "aborted";
}

/** Fold one venue_sync answer into the watch (store writes: owner tab active). */
async function stepRemoteWatch(
  w: RemoteWatch,
  sync: VenueSyncResult,
): Promise<ReturnType<VenueHelpers["evaluateRemoteSync"]>> {
  w.polls += 1;
  w.adopted ||= sync.adopted === true;
  const step = w.venue.evaluateRemoteSync({ ...sync, adopted: w.adopted }, w.baseTotal, w.shown);
  w.shown = step.shown;
  const projected = projectRemoteStep(actions, w.venue, sync, step.phase, w.lastPhase, w.lastActivity);
  w.lastPhase = projected.phase;
  w.lastActivity = projected.activity;
  if (shouldPollApprovals(step.phase, w.polls)) await surfaceOwnerApprovals(w);
  // ADR-099 D-8: the worker asked a durable question → the ask-user overlay.
  if (step.phase === "awaiting_user") await w.venueBridge.surfaceRemoteQuestions(w.bridge, { tabId: w.tabId });
  return step;
}

/**
 * Pending remote approvals of the watched session: read on the owner's
 * bridge, opened as a card only while the owner tab is active (the fetch
 * can outlast a tab switch).
 */
async function surfaceOwnerApprovals(w: RemoteWatch): Promise<void> {
  if (activeTab().toolApproval) return;
  let pending: Awaited<ReturnType<RemoteWatch["venueBridge"]["fetchRemoteApprovals"]>>;
  try {
    pending = await w.venueBridge.fetchRemoteApprovals(w.bridge);
  } catch {
    return;
  }
  if (!(await waitForTabTurn(w.tabId))) return;
  const first = pending[0];
  if (!first || activeTab().toolApproval) return;
  actions.openToolApproval(w.venueBridge.remoteApprovalCard(first));
}

function remoteTurnOver(step: { done: boolean; phase: string }, sync: VenueSyncResult): boolean {
  if (!step.done) return false;
  return !(step.phase === "idle" && sync.state?.session?.chat_queue?.length);
}

async function* finishRemoteTurn(
  w: RemoteWatch,
  step: ReturnType<VenueHelpers["evaluateRemoteSync"]>,
  sync: VenueSyncResult,
): AsyncGenerator<ChatStreamChunk> {
  if (!(await waitForTabTurn(w.tabId))) return;
  if (sync.state) applyBridgeStatePatch(mapBridgeStateToWorkspace(sync.state, undefined));
  postRemoteToolSummary(actions, w.lastActivity, sync.runtime?.turn_seq, w.model);
  yield* remoteTurnTail(step, w.shown, w.model, w.venue.partialDelta);
  if (step.phase === "idle" && sync.state?.messages) {
    yield { transcript: w.mapSnapshotMessages(sync.state.messages) };
  }
}

/** Store side effects of one poll: runtime chip + turn status on phase change.
 * With live tool_activity (2026-09-27), a running phase names the latest action.
 * An empty answer keeps the last-known lines (the worker sends [] only after
 * response_start — honouring the turn boundary), never fabricating new ones. */
function projectRemoteStep(
  actions: StoreActions,
  venue: VenueHelpers,
  sync: VenueSyncResult,
  phase: string,
  lastPhase: string,
  lastActivity: string[],
): { phase: string; activity: string[] } {
  const runtime = venue.mapVenueRuntime({ state: sync.venue_state }, syncExtras(sync));
  const activity = runtime.toolActivity.length > 0 ? runtime.toolActivity : lastActivity;
  runtime.toolActivity = activity;
  actions.setVenueRuntime(runtime);
  const lastTool = activity[activity.length - 1];
  if (phase === "running" && lastTool) {
    actions.setChatTurnStatus(`remote running… ${lastTool.slice(0, 72)}`);
    return { phase, activity };
  }
  if (phase !== lastPhase) actions.setChatTurnStatus(venue.remotePhaseStatus(phase));
  return { phase, activity };
}

function shouldPollApprovals(phase: string, polls: number): boolean {
  return phase === "awaiting_approval" || polls % REMOTE_APPROVAL_EVERY_N_POLLS === 0;
}

/** Turn-end remote tool summary (2026-09-27): one keyed notice with the
 * worker's real activity lines (carried forward — the final sync may be
 * empty); silent when the lane carried none. */
function postRemoteToolSummary(
  actions: StoreActions,
  activity: string[],
  turnSeq: number | undefined,
  model: string | undefined,
): void {
  if (activity.length === 0) return;
  const turn = turnSeq ?? 0;
  const key = `remote-tools:${turn}`;
  const plural = activity.length === 1 ? "" : "s";
  const modelTail = model ? " · " + model : "";
  actions.upsertSystemNotice(key, `remote turn ${turn} — ${activity.length} tool action${plural}${modelTail}`);
  actions.setNoticeDetails(key, activity.slice(0, 12));
}

function isAborted(signal: AbortSignal | undefined): boolean {
  return signal !== undefined && signal.aborted;
}

/** Journal size when the turn was queued (total_messages, else the window length). */
function sendBaseTotal(first: SendMessageResult): number {
  const state = first.state;
  if (!state) return 0;
  if (typeof state.total_messages === "number") return state.total_messages;
  return Array.isArray(state.messages) ? state.messages.length : 0;
}

function syncExtras(sync: VenueSyncResult): {
  runtime: unknown;
  lease_remaining_sec: number | null;
  venue_state: unknown;
} {
  const remaining = typeof sync.lease_remaining_sec === "number" ? sync.lease_remaining_sec : null;
  return { runtime: sync.runtime, lease_remaining_sec: remaining, venue_state: sync.venue_state };
}

function remoteSyncFailure(detail: string): ChatStreamChunk {
  return {
    delta: `[remote session] sync failed: ${detail} — the turn continues on the server; /venue shows its state`,
    model: "offline",
    fallback: true,
  };
}

/** Final chunks once the remote turn is over: error line, tail of the final text, or the honest empty reply. */
function* remoteTurnTail(
  step: { phase: string; finalText: string; errorDetail: string },
  shown: string,
  model: string | undefined,
  partialDelta: (prev: string, next: string) => { delta: string; reset: boolean },
): Generator<ChatStreamChunk> {
  if (step.phase === "error") {
    yield {
      delta: `[remote turn failed] ${step.errorDetail || "the sandbox reported an error"}`,
      model: "offline",
      fallback: true,
    };
    return;
  }
  if (step.finalText) {
    const tail = partialDelta(shown, step.finalText);
    if (tail.reset && shown) yield { roundBreak: true };
    if (tail.delta) yield { delta: tail.delta, model };
    return;
  }
  if (!shown) {
    yield { delta: companionUnavailableReply("empty remote response"), model: "offline", fallback: true };
  }
}

declare module "./companion-chat" {
  interface CompanionChatBackend {
    watchRemoteTurn(
      bridge: CompanionBridgeClient,
      first: SendMessageResult,
      model: string | undefined,
      signal: AbortSignal | undefined,
      tabId: string,
    ): AsyncGenerator<ChatStreamChunk>;
  }
}
CompanionChatBackend.prototype.watchRemoteTurn = watchRemoteTurnImpl;

let shared: CompanionChatBackend | null = null;

export function createCompanionChatBackend(): CompanionChatBackend {
  if (!shared) shared = new CompanionChatBackend();
  return shared;
}
