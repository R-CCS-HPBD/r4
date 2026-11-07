/**
 * Frontend-venue bridge verbs (wave 2026-09-21, ADR-046 A2).
 *
 * start / status / stop / sync ride the bridge's `session.venue_*` verbs;
 * push / unpush / list ride `source.*`. Every failure is surfaced as a
 * notice or error card — never a silent no-op.
 */

import { createEffect, on, untrack } from "solid-js";
import { closeRemoteAskUserElsewhere, returnRemoteAnswer } from "./ask-user-bridge";
import { getCompanionBridge } from "./jsonl-client";
import { rpc } from "./rpc";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import { mapSnapshotMessages } from "./session-bridge";
import { actions, setScopedField, workspace, activeTab, eventScopedTab } from "../stores";
import { applyOnTab, waitForTabTurn } from "../tabs/registry";
import {
  START_POLL_MAX_MS,
  START_POLL_MS,
  mapRemoteSourceRow,
  mapVenueRuntime,
  parseVenueError,
  questionIdFromDetail,
  startTransition,
  type RemoteSourceRow,
  type StartTransition,
  type VenueError,
  type VenueRuntime,
} from "../lib/venue-runtime";
import { webAccessLabel, type WebChoice } from "../lib/web-access";
import { remoteApprovalOptions, type ApprovalOption } from "../lib/approval-options";

const STATUS_POLL_MS = 30_000;
let statusTimer: ReturnType<typeof setInterval> | null = null;

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Every verb below writes its outcome on the tab that STARTED it (id taken
 * before the await): answers arriving after a tab switch landed in the
 * then-active tab — notices, cards, the venue runtime, even the transcript
 * (w3-tui, 2026-10-21). */
function onTab(tabId: string): (fn: () => void) => void {
  return (fn) => void applyOnTab(tabId, fn);
}

/** True when `sessionId` (if any) is the event tab's own session. */
function ownSession(sessionId?: string): boolean {
  return !sessionId || sessionId === eventScopedTab().sessionId;
}

export async function listRemoteSources(): Promise<{
  rows: RemoteSourceRow[];
  remoteAvailable: boolean;
}> {
  const result = (await getCompanionBridge().request("source.list_remote", {}, 20_000)) as {
    rows?: unknown;
    remote_available?: unknown;
  };
  const rows = Array.isArray(result.rows)
    ? (result.rows as Record<string, unknown>[]).map(mapRemoteSourceRow)
    : [];
  return { rows, remoteAvailable: result.remote_available === true };
}

export async function pushSource(name: string): Promise<{ ok: boolean; detail: string }> {
  const land = onTab(workspace.activeChatTabId);
  try {
    const result = (await getCompanionBridge().request("source.push", { name }, 60_000)) as {
      key_fingerprint?: unknown;
      allow_host?: unknown;
      replaced?: unknown;
    };
    const detail = `pushed '${name}' → ${String(result.allow_host ?? "")} (${String(result.key_fingerprint ?? "")})${result.replaced ? " · replaced" : ""}`;
    land(() => actions.sendSystemNotice(detail));
    return { ok: true, detail };
  } catch (err) {
    const detail = errText(err);
    land(() => actions.sendErrorCard({ what: `push '${name}' failed`, action: "dropped", detail }));
    return { ok: false, detail };
  }
}

export async function unpushSource(name: string): Promise<boolean> {
  const land = onTab(workspace.activeChatTabId);
  try {
    const result = (await getCompanionBridge().request("source.unpush", { name }, 60_000)) as {
      leases_revoked?: unknown;
    };
    land(() => actions.sendSystemNotice(`unpushed '${name}' (leases revoked: ${Number(result.leases_revoked ?? 0)})`));
    return true;
  } catch (err) {
    land(() => actions.sendErrorCard({ what: `unpush '${name}' failed`, action: "dropped", detail: errText(err) }));
    return false;
  }
}

/**
 * start/stop adopted the journal's newer copy (a remote turn finished while
 * nobody watched it here): re-render the transcript from the returned state —
 * the watcher would never fire again (local == journal after the adoption).
 */
export function adoptVenueTranscript(result: { adopted?: unknown; state?: unknown }): boolean {
  if (result.adopted !== true || !result.state) return false;
  setScopedField("chatMessages", mapSnapshotMessages((result.state as { messages?: unknown }).messages));
  actions.sendSystemNotice("adopted the server's newer copy of this session (a remote turn finished while unwatched)");
  return true;
}

export type StartRemoteResult =
  | { ok: true; runtime: VenueRuntime }
  | { ok: false; error: VenueError | null; detail: string };

/** Start the sandbox for the CURRENT session with the user's web-access
 * choice (ADR-099 D-2: `web_policy` + `web_allow_domains`; the server refuses
 * a start without one). Returns the parsed venue error on refusal. */
export async function startRemoteSession(opts: {
  source: string;
  webAccess: WebChoice;
}): Promise<StartRemoteResult> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(
      getCompanionBridge(),
      "session.venue_start",
      {
        source_refs: [opts.source],
        default_source: opts.source,
        web_policy: opts.webAccess.mode,
        web_allow_domains: opts.webAccess.domains,
      },
      300_000,
    );
    const runtime = mapVenueRuntime(result.venue);
    onTab(tabId)(() => applyStartResult(tabId, result, runtime, opts));
    return { ok: true, runtime };
  } catch (err) {
    const detail = errText(err);
    const parsed = parseVenueError(detail);
    if (!parsed) {
      onTab(tabId)(() => actions.sendErrorCard({ what: "remote session start failed", action: "dropped", detail }));
    }
    return { ok: false, error: parsed, detail };
  }
}

/** venue/start's answer on its own tab (runs under that tab's event target). */
function applyStartResult(
  tabId: string,
  result: { state?: unknown; adopted?: unknown },
  runtime: VenueRuntime | null,
  opts: { source: string; webAccess: WebChoice },
): void {
  if (result.state) applyBridgeStatePatch(mapBridgeStateToWorkspace(result.state, undefined));
  adoptVenueTranscript(result);
  actions.setVenueRuntime(runtime);
  startVenueStatusPolling();
  const first = startTransition(runtime);
  actions.sendSystemNotice(first.text + " · source " + opts.source + " · web " + webAccessLabel(opts.webAccess));
  if (first.kind === "pending") void awaitRemoteRunning(tabId);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * After `venue/start` answered `provisioning`: poll fast until the durable
 * session job leaves it (running → notice, failed/stopped → error card),
 * bounded by START_POLL_MAX_MS; abandoned silently if the session changes.
 */
export async function awaitRemoteRunning(tabId: string = workspace.activeChatTabId): Promise<StartTransition> {
  // The started tab's session: under applyOnTab that is the event tab.
  const sid = eventScopedTab().sessionId;
  // The wait belongs to the tab that started the session: it pauses while
  // that tab is in the background (a tab switch is not a session change)
  // and ends when the tab closes (TUI audit r2 #58).
  const deadline = Date.now() + START_POLL_MAX_MS;
  let last: StartTransition = { kind: "pending", text: "" };
  while (Date.now() < deadline) {
    await sleep(START_POLL_MS);
    if (!(await waitForTabTurn(tabId)) || activeTab().sessionId !== sid) return last;
    last = startTransition(await fetchVenueStatus());
    if (last.kind !== "pending") break;
  }
  if (!(await waitForTabTurn(tabId))) return last;
  return reportStartTransition(last);
}

function reportStartTransition(last: StartTransition): StartTransition {
  if (last.kind === "pending") {
    last = { kind: "pending", text: "remote session still provisioning — check /venue" };
  }
  if (last.kind === "running") actions.sendSystemNotice(last.text);
  else actions.sendErrorCard({ what: "remote session start", action: "dropped", detail: last.text });
  return last;
}

export async function fetchVenueStatus(sessionId?: string): Promise<VenueRuntime | null> {
  return (await fetchVenueStatusChecked(sessionId)).runtime;
}

/** fetchVenueStatus that keeps the failure: `error` is set when the call
 * failed for any reason but not_found (auth, 5xx, journal down). /venue
 * printed "none recorded" for those (TUI audit 2026-10-17). */
export async function fetchVenueStatusChecked(
  sessionId?: string,
): Promise<{ runtime: VenueRuntime | null; error?: string }> {
  const land = onTab(workspace.activeChatTabId);
  const setOwnRuntime = (runtime: VenueRuntime | null) =>
    land(() => {
      if (ownSession(sessionId)) actions.setVenueRuntime(runtime);
    });
  try {
    const params = sessionId ? { session_id: sessionId } : {};
    const result = await rpc(getCompanionBridge(), "session.venue_status", params, 20_000);
    const runtime = mapVenueRuntime(result.venue, { lease_remaining_sec: result.lease_remaining_sec });
    setOwnRuntime(runtime);
    return { runtime };
  } catch (err) {
    const parsed = parseVenueError(errText(err));
    if (parsed?.code !== "not_found") return { runtime: null, error: errText(err) };
    setOwnRuntime(null);
    return { runtime: null };
  }
}

export async function stopRemoteSession(sessionId?: string): Promise<boolean> {
  const land = onTab(workspace.activeChatTabId);
  try {
    const params = sessionId ? { session_id: sessionId } : {};
    const result = await rpc(getCompanionBridge(), "session.venue_stop", params, 150_000);
    land(() => applyStopResult(result, sessionId));
    return true;
  } catch (err) {
    land(() => actions.sendErrorCard({ what: "remote session stop failed", action: "dropped", detail: errText(err) }));
    return false;
  }
}

/** venue/stop's answer on its own tab (runs under that tab's event target). */
function applyStopResult(
  result: { state?: unknown; adopted?: unknown; venue: Parameters<typeof mapVenueRuntime>[0] },
  sessionId?: string,
): void {
  if (result.state) applyBridgeStatePatch(mapBridgeStateToWorkspace(result.state, undefined));
  if (ownSession(sessionId)) {
    adoptVenueTranscript(result);
    actions.setVenueRuntime(mapVenueRuntime(result.venue));
    stopVenueStatusPolling();
  }
  const which = sessionId ? " (" + sessionId.slice(0, 8) + ")" : "";
  actions.sendSystemNotice("remote session stopped" + which);
}

/** States whose lease / state can still change server-side. */
const LIVE_VENUE_STATES = new Set(["provisioning", "running"]);

function venueRuntimeIsLive(runtime: VenueRuntime | null): boolean {
  return runtime !== null && LIVE_VENUE_STATES.has(runtime.state);
}

/**
 * One status tick for the ACTIVE tab. venueRuntime is chat-scoped, so a
 * local tab (or a stopped runtime) skips the tick instead of stopping the
 * one global timer: a single tick on a local tab used to end polling for
 * good (TUI audit r2 #44/#61). session.venue_status is a read, answered
 * mid-turn too (plan H-8; the tick used to skip a bridge running a turn).
 */
export function venueStatusTick(): void {
  if (!venueRuntimeIsLive(activeTab().venueRuntime)) return;
  // Pin the tick's session: an answer that lands after a tab switch must
  // not paint this tab's runtime into the newly active one.
  const sid = activeTab().sessionId;
  if (!sid) return;
  void fetchVenueStatus(sid);
}

export function startVenueStatusPolling(): void {
  if (statusTimer) return;
  statusTimer = setInterval(venueStatusTick, STATUS_POLL_MS);
  statusTimer.unref?.();
}

/**
 * Keep the status poller running whenever the active tab shows a live
 * remote runtime — however it got there: /venue start, a resumed or
 * restored session's snapshot, init_state, or a tab switch. Only
 * startRemoteSession started it before, so a resumed remote session never
 * refreshed its lease chip (TUI audit r2 #44/#61). Call inside a reactive
 * owner (App mount).
 */
export function trackVenueStatusPolling(): void {
  createEffect(() => {
    if (venueRuntimeIsLive(activeTab().venueRuntime)) startVenueStatusPolling();
  });
}

/** True while the status timer is armed (tests, diagnostics). */
export function venueStatusPollingActive(): boolean {
  return statusTimer !== null;
}

export function stopVenueStatusPolling(): void {
  if (statusTimer) clearInterval(statusTimer);
  statusTimer = null;
}

export interface RemoteApproval {
  approvalId: string;
  tool: string;
  description: string;
  /** Fix 2026-10-30: the worker raised this ask from a content fence
   * (taint, credential probe, G14 ask rule, protected path). */
  fenced: boolean;
  fenceReason: string;
}

/** The tool-approval card for a pending remote approval. A fenced one is a
 * per-call decision (fix 2026-10-30): no a/p, and the card names the fence. */
export function remoteApprovalCard(row: RemoteApproval): {
  id: string;
  tool: string;
  description: string;
  options: ApprovalOption[];
  fenceReason?: string;
} {
  return {
    id: `remote:${row.approvalId}`,
    tool: row.tool,
    description: row.description || `remote tool approval: ${row.tool}`,
    // session.venue_decide takes once | always | reject (ADR-104 D-3).
    options: remoteApprovalOptions(Boolean(row.fenced)),
    ...(row.fenced ? { fenceReason: row.fenceReason || "a fence on this call's arguments" } : {}),
  };
}

type RequestRail = { request: (method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<unknown> };

export async function fetchRemoteApprovals(rail: RequestRail = getCompanionBridge()): Promise<RemoteApproval[]> {
  const result = await rpc(rail, "session.venue_approvals", {}, 15_000);
  if (!Array.isArray(result.approvals)) return [];
  return (result.approvals as Record<string, unknown>[])
    .filter((row) => row.status === "pending" || row.status === undefined)
    .map((row) => {
      const action = (row.action && typeof row.action === "object" ? row.action : {}) as Record<
        string,
        unknown
      >;
      return {
        approvalId: String(row.approval_id ?? ""),
        tool: String(action.tool ?? "tool"),
        description: String(action.description ?? ""),
        fenced: action.fenced === true,
        fenceReason: typeof action.fence_reason === "string" ? action.fence_reason : "",
      };
    })
    .filter((row) => row.approvalId);
}

export async function decideRemoteApproval(
  approvalId: string,
  decision: "once" | "always" | "reject",
): Promise<boolean> {
  const land = onTab(workspace.activeChatTabId);
  try {
    await getCompanionBridge().request(
      "session.venue_decide",
      { approval_id: approvalId, decision },
      15_000,
    );
    return true;
  } catch (err) {
    land(() => actions.sendErrorCard({ what: "remote approval failed", action: "dropped", detail: errText(err) }));
    return false;
  }
}

// ── remote ask_user questions (ADR-099 D-8) ─────────────────────────────────
//
// A remote worker's ask_user is a durable question the owner answers from any
// client. The watcher polls while the worker reports `awaiting_user` and opens
// the existing ask-user overlay with id `remote:<question_id>`; the answer goes
// to `session.venue_answer`. Esc leaves the question pending (it can still be
// answered from another client, `/venue questions` or `r4 session answer`),
// and this client does not reopen it on its own. Opening or attaching a
// remote session whose worker is `awaiting_user` surfaces the question too
// (trackRemoteQuestions), so any attached client can answer it.

export const REMOTE_ASK_PREFIX = "remote:";

export interface RemoteQuestion {
  questionId: string;
  sessionId: string;
  question: string;
  options: string[];
}

/** Questions this client set aside with Esc (never reopened by the watcher). */
const dismissedQuestions = new Set<string>();
/** Questions this client answered (in flight or done): never reopened while
 * a poll still lists them pending. A failed answer leaves the set. */
const answeredQuestions = new Set<string>();
/** question id → its session (answers name the session they belong to). */
const questionSessions = new Map<string, string>();

/** The question id behind an ask-user overlay id, or null for a local ask. */
export function remoteQuestionId(id: string | number): string | null {
  const text = String(id);
  return text.startsWith(REMOTE_ASK_PREFIX) ? text.slice(REMOTE_ASK_PREFIX.length) : null;
}

function mapRemoteQuestion(row: Record<string, unknown>): RemoteQuestion {
  const options = Array.isArray(row.options) ? row.options.map((o) => String(o)).filter((o) => o.trim()) : [];
  return {
    questionId: String(row.question_id ?? ""),
    sessionId: String(row.session_id ?? ""),
    question: String(row.question ?? ""),
    options,
  };
}

export async function fetchRemoteQuestions(
  rail: RequestRail = getCompanionBridge(),
  sessionId?: string,
): Promise<RemoteQuestion[]> {
  const params = sessionId ? { session_id: sessionId } : {};
  const result = await rpc(rail, "session.venue_questions", params, 15_000);
  if (!Array.isArray(result.questions)) return [];
  return (result.questions as Record<string, unknown>[])
    .filter((row) => row.status === "pending" || row.status === undefined)
    .map(mapRemoteQuestion)
    .filter((row) => row.questionId);
}

// The question the worker's heartbeat names: pure, in lib/venue-runtime.ts
// (the VS Code host reads it too, ADR-103 D-3).
export { questionIdFromDetail } from "../lib/venue-runtime";

/** The question to show: the one the worker's heartbeat names, else the
 * newest (rows come oldest first; an older one is a dead worker's). */
function pickRemoteQuestion(open: RemoteQuestion[], named: string | null): RemoteQuestion | undefined {
  return open.find((row) => row.questionId === named) ?? open[open.length - 1];
}

function cardOpen(): boolean {
  return Boolean(activeTab().askUser || activeTab().toolApproval);
}

export type RemoteQuestionsOutcome =
  | { kind: "surfaced"; count: number }
  | { kind: "card_open" }
  | { kind: "error"; error: string };

/** surfaceRemoteQuestions that keeps "a card is open" and "the call failed"
 * apart from "nothing pending" (for /venue questions). */
export async function surfaceRemoteQuestionsChecked(
  rail: RequestRail = getCompanionBridge(),
  opts: { sessionId?: string; tabId?: string } = {},
): Promise<RemoteQuestionsOutcome> {
  if (cardOpen()) return { kind: "card_open" };
  let pending: RemoteQuestion[];
  try {
    pending = await fetchRemoteQuestions(rail, opts.sessionId);
  } catch (err) {
    return { kind: "error", error: errText(err) };
  }
  const open = pending.filter((row) => !dismissedQuestions.has(row.questionId) && !answeredQuestions.has(row.questionId));
  if (open.length === 0) return { kind: "surfaced", count: 0 };
  if (opts.tabId && !(await waitForTabTurn(opts.tabId))) return { kind: "surfaced", count: 0 };
  if (cardOpen()) return { kind: "card_open" };
  const shown = pickRemoteQuestion(open, questionIdFromDetail(activeTab().venueRuntime?.detail))!;
  if (shown.sessionId) questionSessions.set(shown.questionId, shown.sessionId);
  actions.openAskUser({ id: REMOTE_ASK_PREFIX + shown.questionId, question: shown.question, options: shown.options });
  actions.setChatTurnStatus("the remote session is asking you a question…");
  return { kind: "surfaced", count: open.length };
}

/** Open the pending question the worker waits on (else the newest one) in
 * the ask-user overlay; set-aside and answered ones are skipped. Never over
 * another open card: the next poll tries again. */
export async function surfaceRemoteQuestions(
  rail: RequestRail = getCompanionBridge(),
  opts: { sessionId?: string; tabId?: string } = {},
): Promise<number> {
  const outcome = await surfaceRemoteQuestionsChecked(rail, opts);
  return outcome.kind === "surfaced" ? outcome.count : 0;
}

/** `/venue questions`: show the remote questions again, including the ones
 * this client set aside with Esc. */
export async function reviewRemoteQuestions(rail: RequestRail = getCompanionBridge()): Promise<RemoteQuestionsOutcome> {
  dismissedQuestions.clear();
  return surfaceRemoteQuestionsChecked(rail);
}

/** True when the active tab is a remote session whose worker waits on the
 * user (a resumed, restored or attached session included). Not while the
 * splash is up: its Esc would dismiss the question unseen. */
function awaitingRemoteAnswer(): boolean {
  const tab = activeTab();
  if (workspace.splashOpen || runtimeEnded(tab.venueRuntime?.state)) return false;
  return tab.sessionVenue === "frontend" && tab.venueRuntime?.phase === "awaiting_user";
}

/** A stopped or failed session's worker waits on nothing, whatever phase
 * its last heartbeat left behind. */
function runtimeEnded(state: string | undefined): boolean {
  return state === "stopped" || state === "failed";
}

/**
 * Any attached client can answer (ADR-099 D-8): when the active tab shows a
 * remote session in `awaiting_user` — however it got there (open, resume,
 * tab switch, the status poller) — read its pending questions once per
 * transition and open one. The remote-turn watcher covers a turn this
 * client sent. Call inside a reactive owner (App mount).
 */
export function trackRemoteQuestions(rail?: RequestRail): void {
  // First: a stale card closes before the same change surfaces the next one.
  createEffect(
    on(
      () =>
        [
          workspace.activeChatTabId,
          activeTab().venueRuntime?.state,
          activeTab().venueRuntime?.phase,
          activeTab().venueRuntime?.detail,
        ] as const,
      (now, prev) => untrack(() => closeStaleRemoteQuestion(now, prev)),
    ),
  );
  createEffect(() => {
    if (!awaitingRemoteAnswer()) return;
    // The worker naming another question is a transition too (keep-alives
    // repeat the same detail, so they never refetch).
    void activeTab().venueRuntime?.detail;
    const sessionId = activeTab().sessionId || undefined;
    // Only the transition is tracked: closing the overlay must not refetch.
    untrack(() => void surfaceOnAttach(rail ?? getCompanionBridge(), sessionId));
  });
}

type RuntimeView = readonly [
  tabId: string,
  state: string | undefined,
  phase: string | undefined,
  detail: string | undefined,
];

/** True when the worker no longer waits on `questionId`: the session stopped
 * or failed (a stop keeps the last heartbeat's phase and detail, and its
 * worker sends no final one; RS-1 round 3, N2), the phase left
 * `awaiting_user` on the same tab (answered elsewhere, expired), or the
 * worker now names another question. */
function remoteQuestionGone(questionId: string, now: RuntimeView, prev: RuntimeView | undefined): boolean {
  if (!prev || prev[0] !== now[0]) return false; // a tab switch is not a transition
  if (runtimeEnded(now[1])) return true;
  if (now[2] === "awaiting_user") {
    const named = questionIdFromDetail(now[3]);
    return named !== null && named !== questionId;
  }
  return prev[2] === "awaiting_user";
}

/** Close a remote question's card once the worker stopped waiting on it
 * (RS-1 round 2, finding 5): the card no longer outlives its question; the
 * overlay moves anything typed into the composer. */
function closeStaleRemoteQuestion(now: RuntimeView, prev: RuntimeView | undefined): void {
  const questionId = remoteQuestionId(activeTab().askUser?.id ?? "");
  if (!questionId || !remoteQuestionGone(questionId, now, prev)) return;
  closeRemoteAskUserElsewhere();
  actions.sendSystemNotice("remote question closed (answered elsewhere, expired, or the session stopped)");
}

const ATTACH_RETRY_MS = 2_000;
const ATTACH_TRIES = 5;

/** One attach-time read; a failed read (the bridge still starting, the
 * journal briefly down) is retried a few times while the same session still
 * waits on the user. */
async function surfaceOnAttach(rail: RequestRail, sessionId: string | undefined): Promise<void> {
  for (let attempt = 1; attempt <= ATTACH_TRIES; attempt++) {
    const outcome = await surfaceRemoteQuestionsChecked(rail, { sessionId });
    if (outcome.kind !== "error" || attempt === ATTACH_TRIES) return;
    await sleep(ATTACH_RETRY_MS);
    if (!awaitingRemoteAnswer() || (activeTab().sessionId || undefined) !== sessionId) return;
  }
}

/** Send the owner's answer; a closed question (answered elsewhere, expired)
 * is a notice, any other failure an error card. */
export async function answerRemoteQuestion(questionId: string, answer: string): Promise<boolean> {
  const tabId = workspace.activeChatTabId;
  const land = onTab(tabId);
  const sessionId = questionSessions.get(questionId);
  const params: Record<string, unknown> = { question_id: questionId, answer };
  if (sessionId) params.session_id = sessionId;
  answeredQuestions.add(questionId);
  try {
    await rpc(getCompanionBridge(), "session.venue_answer", params, 15_000);
    questionSessions.delete(questionId);
    return true;
  } catch (err) {
    const detail = errText(err);
    if (parseVenueError(detail)?.code !== "question_closed") answeredQuestions.delete(questionId);
    if (parseVenueError(detail)?.code === "question_closed") {
      // The model restates an unanswered question in a new turn: the typed
      // answer waits in the composer instead of being dropped.
      const stillHere = workspace.activeChatTabId === tabId;
      land(() => returnRemoteAnswer(answer, "remote question already closed (answered elsewhere or expired)", stillHere));
    } else {
      land(() => actions.sendErrorCard({ what: "remote answer failed", action: "dropped", detail }));
    }
    return false;
  }
}

/** Esc on a remote question: it stays pending server-side; this client stops
 * reopening it and says how to answer later. */
export function setAsideRemoteQuestion(questionId: string): void {
  dismissedQuestions.add(questionId);
  actions.sendSystemNotice(
    `remote question left pending — answer it later with /venue questions or \`r4 session answer <session> ${questionId} <text>\`; ` +
      "the worker stops waiting after its timeout",
  );
}

/** Test hook: forget set-aside questions. */
export function resetRemoteQuestionsForTests(): void {
  dismissedQuestions.clear();
  answeredQuestions.clear();
  questionSessions.clear();
}

/** Surface pending remote approvals through the existing tool-approval card. */
export async function surfaceRemoteApprovals(rail?: RequestRail): Promise<number> {
  const outcome = await surfaceRemoteApprovalsChecked(rail);
  return outcome.kind === "surfaced" ? outcome.count : 0;
}

export type RemoteApprovalsOutcome =
  | { kind: "surfaced"; count: number }
  | { kind: "card_open" }
  | { kind: "error"; error: string };

/** surfaceRemoteApprovals that tells "nothing pending" from "a card is
 * already open" and "the call failed" — /venue approvals read all three as
 * "no pending remote approvals" (TUI audit 2026-10-17). */
export async function surfaceRemoteApprovalsChecked(rail?: RequestRail): Promise<RemoteApprovalsOutcome> {
  if (activeTab().toolApproval) return { kind: "card_open" };
  const land = onTab(workspace.activeChatTabId);
  let pending: RemoteApproval[] = [];
  try {
    pending = await fetchRemoteApprovals(rail ?? getCompanionBridge());
  } catch (err) {
    return { kind: "error", error: errText(err) };
  }
  const first = pending[0];
  if (!first) return { kind: "surfaced", count: 0 };
  land(() => actions.openToolApproval(remoteApprovalCard(first)));
  return { kind: "surfaced", count: pending.length };
}
