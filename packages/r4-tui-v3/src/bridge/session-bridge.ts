/**
 * Wave D — session verbs over the bridge (list/resume/new/rename/pin/archive/
 * export). Resume replays the transcript from the SERVER snapshot — never
 * from client memory (plan §5: no auto-replay of mutations).
 */

import { sessionTurnLine, turnRowsOf } from "../lib/session-turns";
import { getCompanionBridge } from "./jsonl-client";
import { offerSessionRecovery, sessionErrorOf, type SessionErrorInfo } from "./session-recovery";
import { rpc } from "./rpc";
import { actions, setScopedField, workspace, activeTab, eventScopedTab } from "../stores";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import { announceSandboxFromSnapshot } from "./sandbox-bridge";
import { applyOnTab } from "../tabs/registry";
import { ulid } from "ulid";
import type { ChatMessage, SessionRecord, SessionSyncBadge, VenueChoice } from "../types";
import { buildListSessionsParams } from "../lib/session-filters";
import {
  mapWebAccess,
  webAccessLabel,
  webCapsFromLimits,
  type WebAccessChoice,
  type WebCaps,
  type WebChoice,
} from "../lib/web-access";

/** Sync badge vocabulary is fixed server-side (§8a-5) — unknown drift maps to
 *  "" (badge hidden) rather than inventing a word. */
function mapSyncBadge(raw: unknown): SessionSyncBadge {
  return raw === "local-only" || raw === "remote-only" || raw === "mirrored" || raw === "degraded"
    ? raw
    : "";
}

/** Map server snapshot.messages rows into transcript messages. */
export function mapSnapshotMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatMessage[] = [];
  for (const row of raw) {
    const message = snapshotRow(row);
    if (message) out.push(message);
  }
  return out;
}

/** One snapshot row → a transcript row (null for an empty or junk row). */
function snapshotRow(row: unknown): ChatMessage | null {
  if (!row || typeof row !== "object") return null;
  const r = row as { role?: unknown; content?: unknown; timestamp?: unknown; metadata?: unknown };
  const role = snapshotRole(r.role);
  const content = typeof r.content === "string" ? r.content : "";
  if (!content.trim()) return null;
  const turnId = role === "user" ? snapshotTurnId(r.metadata) : "";
  return {
    id: ulid(),
    role,
    content,
    timestamp: snapshotTimestamp(r.timestamp),
    ...(turnId ? { turnId } : {}),
  };
}

function snapshotRole(role: unknown): ChatMessage["role"] {
  return role === "user" || role === "assistant" ? role : "system";
}

function snapshotTimestamp(timestamp: unknown): string {
  return typeof timestamp === "string" && timestamp ? timestamp : new Date().toISOString();
}

/** A snapshot row's ADR-057 turn id (rewind cuts by it; r2 #17). */
function snapshotTurnId(metadata: unknown): string {
  if (!metadata || typeof metadata !== "object") return "";
  const id = (metadata as { turn_id?: unknown }).turn_id;
  return typeof id === "string" ? id : "";
}

function textField(row: Record<string, unknown>, key: string, fallback = ""): string {
  return String(row[key] ?? fallback);
}

function stringField(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return typeof value === "string" ? value : "";
}

function numberField(row: Record<string, unknown>, key: string): number | undefined {
  const value = row[key];
  return typeof value === "number" ? value : undefined;
}

function ordinarySessionRecord(row: Record<string, unknown>, id: string): SessionRecord {
  return {
    id,
    name: textField(row, "name", "(unnamed)"),
    model: textField(row, "model", "?"),
    created_at: textField(row, "created_at"),
    updated_at: textField(row, "updated_at"),
    pinned: row.pinned === true,
    archived_at: textField(row, "archived_at"),
    turn_count: numberField(row, "turn_count"),
    cost_usd: numberField(row, "cost_usd"),
    venue: stringField(row, "venue"),
    state: stringField(row, "state"),
    sync: mapSyncBadge(row.sync),
  };
}

function mapChildSummary(record: SessionRecord, row: Record<string, unknown>): void {
  if (row.session_kind !== "subagent") return;
  record.session_kind = "subagent";
  record.parent_session_id = textField(row, "parent_session_id");
  record.parent_agent_id = textField(row, "parent_agent_id");
  record.agent_id = textField(row, "agent_id");
  record.subagent_status = textField(row, "subagent_status");
  record.state = record.subagent_status;
}

/** Map one server record defensively (shape drift tolerated). */
export function mapSessionRecord(raw: unknown): SessionRecord | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const id = textField(row, "id");
  if (!id) return null;
  const record = ordinarySessionRecord(row, id);
  mapChildSummary(record, row);
  return record;
}

/** Record the journal reachability half of a list_sessions response. */
function applyRemoteStatus(result: { remote_available?: unknown; remote_reason?: unknown }): void {
  actions.setSessionsRemoteStatus({
    available: result.remote_available === true,
    reason: typeof result.remote_reason === "string" ? result.remote_reason : "",
  });
}

/** Read the active parent's durable local children; never merge the S5 remote journal. */
async function fetchChildInventory(
  bridge: ReturnType<typeof getCompanionBridge>,
  parentSessionId: string,
): Promise<SessionRecord[]> {
  if (!parentSessionId) return [];
  const result = (await bridge.request("list_sessions", {
    session_kind: "subagent",
    parent_session_id: parentSessionId,
    limit: 50,
  }, 30_000)) as { sessions?: unknown[] };
  return (result.sessions ?? [])
    .map(mapSessionRecord)
    .filter((row): row is SessionRecord => row?.session_kind === "subagent");
}

/** Fetch the /sessions list into the store (filters ride the wire — plan
 *  §4.4: no client-side emulation for remote rows). `quiet`: a background
 *  refresh nobody asked for posts no card when it fails. Child inventory is
 *  opt-in so title refreshes do not double their RPC traffic. */
export async function fetchSessions(opts: { quiet?: boolean; includeChildren?: boolean } = {}): Promise<number> {
  const bridge = getCompanionBridge();
  const parentSessionId = activeTab().sessionId;
  try {
    const result = (await bridge.request(
      "list_sessions",
      buildListSessionsParams(workspace.sessionFilters),
      30_000,
    )) as {
      sessions?: unknown[];
      remote_available?: unknown;
      remote_reason?: unknown;
    };
    const conversations = (result.sessions ?? []).map(mapSessionRecord).filter((r): r is SessionRecord => r !== null);
    const children = opts.includeChildren
      ? await fetchChildInventory(bridge, parentSessionId)
      : workspace.sessionsList.filter((row) => row.session_kind === "subagent" && row.parent_session_id === parentSessionId);
    const records = [...conversations, ...children];
    actions.setSessionsList(records);
    // Chat tabs: every fetch refreshes the strip titles from server names.
    actions.syncChatTabTitles();
    applyRemoteStatus(result);
    return records.length;
  } catch (err) {
    if (opts.quiet) return 0;
    actions.sendErrorCard({
      what: "session list failed",
      action: "dropped",
      detail: err instanceof Error ? err.message : String(err),
    });
    return 0;
  }
}

/** Lightweight gate probe for the venue picker: reads remote_available +
 *  remote_reason WITHOUT touching the sessions list (limit=1). */
export async function probeSessionRemote(): Promise<{ available: boolean; reason: string }> {
  const bridge = getCompanionBridge();
  try {
    const result = (await bridge.request("list_sessions", { limit: 1 }, 30_000)) as {
      remote_available?: unknown;
      remote_reason?: unknown;
    };
    applyRemoteStatus(result);
    return {
      available: result.remote_available === true,
      reason: typeof result.remote_reason === "string" ? result.remote_reason : "",
    };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    actions.setSessionsRemoteStatus({ available: false, reason });
    return { available: false, reason };
  }
}

/** Throttled refresh for title lookups (tab switches, turn ends). Quiet: it
 * ran after every turn, and a failed list posted "session list failed" over
 * the turn's own card — a session-conflict recovery card included (c3 CI,
 * 2026-10-22). */
let lastTitlesFetchAt = 0;
export function refreshSessionsForTabTitles(ms = 10_000): void {
  const now = Date.now();
  if (now - lastTitlesFetchAt < ms) return;
  lastTitlesFetchAt = now;
  void fetchSessions({ quiet: true });
}

/** Resume notice text: a snapshot `notice` (e.g. the pull-fallback's
 *  "pulled from server journal before resume" on remote-only rows) wins;
 *  otherwise the plain resumed line. */
export function resumeNoticeText(sessionId: string, state: unknown): string {
  const notice =
    state && typeof state === "object" ? (state as { notice?: unknown }).notice : undefined;
  return typeof notice === "string" && notice.trim()
    ? notice
    : `resumed session ${sessionId.slice(0, 8)}`;
}

/** Resume: server switches the live session; transcript replays from
 * snapshot. Everything lands on the tab that asked (captured before the
 * await) — a resume finishing after a tab switch wiped the OTHER tab's
 * transcript and rebound its session (stabilization 2026-10-21). */
export async function resumeSession(sessionId: string): Promise<boolean> {
  const tabId = workspace.activeChatTabId;
  const failure = await loadSessionIntoTab(sessionId, tabId);
  if (failure === null) return true;
  applyOnTab(tabId, () => postLoadFailure(failure));
  return false;
}

/** A failed load_session: its detail, and the typed session error (c3:
 * a corrupt row) when the bridge sent one. */
export interface LoadFailure {
  message: string;
  session: SessionErrorInfo | null;
}

/** The card for a failed load: the typed recovery, else "resume failed". */
export function postLoadFailure(failure: LoadFailure): void {
  if (failure.session) {
    offerSessionRecovery(failure.session);
    return;
  }
  actions.sendErrorCard({ what: "resume failed", action: "dropped", detail: failure.message });
}

/** load_session on the active tab's bridge, its snapshot applied to `tabId`.
 * Null on success, else the failure — no error card: the caller owns it (a
 * failed /sessions resume posts it after dropping its tab). */
export async function loadSessionIntoTab(sessionId: string, tabId: string): Promise<LoadFailure | null> {
  const bridge = getCompanionBridge();
  try {
    const state = await bridge.request("load_session", { session_id: sessionId }, 30_000);
    applyOnTab(tabId, () => applyResumedState(sessionId, state, tabId));
    return null;
  } catch (err) {
    return { message: err instanceof Error ? err.message : String(err), session: sessionErrorOf(err) };
  }
}

function applyResumedState(sessionId: string, state: unknown, tabId: string): void {
  applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
  const mapped = mapSnapshotMessages((state as { messages?: unknown }).messages);
  const queuePayload = persistedQueueRows(state);
  setScopedField("chatMessages", [...mapped, ...queuePayload]);
  setScopedField("pendingChatQueue", queuePayload.map((m) => m.id));
  noteReplayWindow(state);
  // Pane chrome is global: only the tab on screen closes it.
  if (tabId === workspace.activeChatTabId) actions.closePane();
  actions.sendSystemNotice(resumeNoticeText(sessionId, state));
  announceSandboxFromSnapshot(state);
  restartSessionPlan(tabId);
}

/** F9-lite: re-hydrate queued rows from persisted session.chat_queue
 * (snapshot carries exact entries — TUI shows the tombstones as queued
 * chips again so users know what survived the gap). */
function persistedQueueRows(state: unknown): ChatMessage[] {
  const sessionBlock = (state as { session?: { chat_queue?: unknown } }).session;
  const persistedQueue = Array.isArray(sessionBlock?.chat_queue)
    ? sessionBlock!.chat_queue.filter((x): x is string => typeof x === "string")
    : [];
  return persistedQueue.map((content: string) => ({
    id: ulid(),
    role: "user" as const,
    content,
    timestamp: new Date().toISOString(),
    queued: true,
  }));
}

/** F10: replay truncation marker — the bridge gives total_messages + the
 * window cap back, so the user knows "older messages exist". */
function noteReplayWindow(state: unknown): void {
  const total = (state as { total_messages?: unknown }).total_messages;
  const window = (state as { message_window?: unknown }).message_window;
  if (typeof total === "number" && typeof window === "number" && total > window) {
    actions.sendSystemNotice(`replay window: showing the last ${window} of ${total} messages`);
  }
}

/** The Plan graph belongs to the session: a new or resumed one starts from
 * its own plan, never the previous session's steps (TUI audit r2 #27). The
 * refetch rides the ACTIVE tab's bridge, so a tab already left behind keeps
 * the cleared plan until its next turn refreshes it. */
function restartSessionPlan(tabId: string): void {
  actions.setSessionPlan([]);
  if (tabId !== workspace.activeChatTabId) return;
  void import("../lib/session-plan").then(({ fetchSessionPlan }) => fetchSessionPlan()).catch(() => undefined);
}

/**
 * New session. With a venue choice the create rides the initialize RPC's
 * additive venue param — the only path that RECORDS the venue (bridge_server
 * _open_session creates when no session_id is set; the TUI spawns the bridge
 * without --session-id, so re-initialize is a safe fresh create). Without a
 * choice (legacy callers) the proven /new dispatch stays — venue defaults to
 * local server-side, same as pre-picker behavior. The result lands on the
 * tab that asked, like resumeSession. ADR-099 D-2: the web-access answer
 * asked right after the venue choice rides the same initialize call.
 */
export async function newSession(venue?: VenueChoice, webAccess?: WebChoice): Promise<boolean> {
  const tabId = workspace.activeChatTabId;
  const bridge = getCompanionBridge();
  try {
    const params: Record<string, unknown> = { venue };
    if (webAccess) params.web_access = { mode: webAccess.mode, domains: webAccess.domains };
    const state = venue
      ? ((await bridge.request("initialize", params, 30_000)) as { messages?: unknown })
      : await legacyNewSession(bridge);
    const web = webAccess ? ` · web ${webAccessLabel(webAccess)}` : "";
    const notice = venue ? `new session started (${venue} venue${web})` : "new session started";
    applyOnTab(tabId, () => applyNewSessionState(tabId, state, notice));
    return true;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    applyOnTab(tabId, () =>
      actions.sendErrorCard({ what: "new session failed", action: "dropped", detail }),
    );
    return false;
  }
}

async function legacyNewSession(
  bridge: ReturnType<typeof getCompanionBridge>,
): Promise<{ messages?: unknown }> {
  await bridge.request("dispatch_slash_command", { command: "/new" }, 30_000);
  return rpc(bridge, "get_state", {}, 30_000);
}

function applyNewSessionState(tabId: string, state: { messages?: unknown }, notice: string): void {
  // The tab's title named the OLD conversation: kept, the empty new session
  // got renamed server-side to it (firstTabPromptTitle's current-title
  // fallback) — two sessions under one name.
  actions.resetTabTitle(tabId);
  applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
  setScopedField("chatMessages", mapSnapshotMessages(state.messages));
  actions.sendSystemNotice(notice);
  // ADR-101 D-1: a new session says again that its sandbox is off.
  announceSandboxFromSnapshot(state);
  restartSessionPlan(tabId);
}

// ── ADR-099 D-2: the session's web access ──────────────────────────────────

/** `session.web_access_get`, mapped. */
export interface WebAccessState {
  current: WebAccessChoice | null;
  remembered: WebAccessChoice | null;
  preset: boolean;
}

/** The session's choice, the profile's remembered one (to preselect) and
 * whether a launch preset (`r4 --web`) exists; null when the bridge has no
 * such verb (it predates RS-1). */
export async function fetchWebAccess(): Promise<WebAccessState | null> {
  try {
    const result = await rpc(getCompanionBridge(), "session.web_access_get", {}, 15_000);
    return {
      current: mapWebAccess(result.web_access),
      remembered: mapWebAccess(result.remembered),
      preset: result.preset === true,
    };
  } catch {
    return null;
  }
}

/** Store the user's answer on the active session. A running remote session
 * answers `venue.restart_required` (squid is set up at start): the error is
 * re-told and false returned. The reply lands on the tab that asked. */
export async function setSessionWebAccess(choice: WebChoice): Promise<boolean> {
  const land = (fn: () => void) => void applyOnTab(workspace.activeChatTabId, fn);
  try {
    const result = await rpc(
      getCompanionBridge(),
      "session.web_access_set",
      { mode: choice.mode, domains: choice.domains, source: "prompt" },
      15_000,
    );
    land(() => actions.setWebAccess(mapWebAccess(result.web_access)));
    // ADR-102: e.g. public access with no search server for web_search yet.
    // Where one can start (a local session with podman or docker, 2026-11-01)
    // the console asks instead of only telling.
    const notice = result.notice;
    if (result.search_server_offer && activeTab().sessionVenue !== "frontend") actions.openSearchServerOffer();
    else if (notice) land(() => actions.sendSystemNotice(notice));
    return true;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    land(() => actions.sendErrorCard({ what: "web access not changed", action: "dropped", detail }));
    return false;
  }
}

/** The caps a remote start would get (`session.venue_policy`). No preview
 * (old server, failed call) means no caps here: the server still checks. */
export async function fetchVenuePolicy(project?: string): Promise<{ caps: WebCaps; note: string }> {
  try {
    const params = project ? { project } : {};
    const result = await rpc(getCompanionBridge(), "session.venue_policy", params, 20_000);
    const note = result.error ? `session policy preview failed (${result.error}); the server checks the choice` : "";
    return { caps: webCapsFromLimits(result.limits), note };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return { caps: webCapsFromLimits(null), note: `session policy preview failed (${detail})` };
  }
}

export async function renameSession(sessionId: string, name: string): Promise<boolean> {
  const bridge = getCompanionBridge();
  try {
    await bridge.request("session.rename", { session_id: sessionId, name }, 30_000);
    actions.sendSystemNotice(`renamed session ${sessionId.slice(0, 8)} → ${name}`);
    void fetchSessions();
    return true;
  } catch (err) {
    actions.sendErrorCard({ what: "rename failed", action: "dropped", detail: err instanceof Error ? err.message : String(err) });
    return false;
  }
}

export async function archiveSession(sessionId: string): Promise<boolean> {
  const bridge = getCompanionBridge();
  try {
    await bridge.request("session.archive", { session_id: sessionId }, 30_000);
    actions.sendSystemNotice(`archived session ${sessionId.slice(0, 8)}`);
    void fetchSessions();
    return true;
  } catch (err) {
    actions.sendErrorCard({ what: "archive failed", action: "dropped", detail: err instanceof Error ? err.message : String(err) });
    return false;
  }
}

export async function pinSession(sessionId: string, pinned: boolean): Promise<boolean> {
  const bridge = getCompanionBridge();
  try {
    await bridge.request(pinned ? "session.pin" : "session.unpin", { session_id: sessionId }, 30_000);
    actions.sendSystemNotice(`${pinned ? "pinned" : "unpinned"} session ${sessionId.slice(0, 8)}`);
    void fetchSessions();
    return true;
  } catch (err) {
    actions.sendErrorCard({ what: "pin failed", action: "dropped", detail: err instanceof Error ? err.message : String(err) });
    return false;
  }
}

/** Markdown with front-matter prepended to the server export. */
export function sessionExportFrontMatter(meta: {
  sessionId: string;
  project: string;
  model: string;
  costUsd: number | null;
  exportedAt: string;
}): string {
  return [
    "---",
    `session_id: ${meta.sessionId}`,
    `project: ${meta.project}`,
    `model: ${meta.model || "?"}`,
    `cost_usd: ${meta.costUsd === null ? "?" : meta.costUsd.toFixed(4)}`,
    `exported_at: ${meta.exportedAt}`,
    "---",
    "",
  ].join("\n");
}

interface ExportSessionReply {
  markdown?: string;
  /** Optional metadata of the EXPORTED session (bridge export_session). */
  model?: string | null;
  project?: string | null;
  cost_usd?: number | null;
}

interface ExportMeta {
  project: string;
  model: string;
  costUsd: number | null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The bridge's own answer, field by field, over the local fallback (the
 * bridge sends model and project but no cost: a loaded session has none). */
function withServerExportMeta(local: ExportMeta, reply: ExportSessionReply): ExportMeta {
  return {
    project: reply.project || local.project,
    model: reply.model || local.model,
    costUsd: numberOrNull(reply.cost_usd) ?? local.costUsd,
  };
}

/** Front-matter metadata of the EXPORTED session: the bridge's own answer
 * where it carries one; the live workspace only for the current session; the
 * session row (model, cost) for any other — never the active tab's values
 * stamped onto another session (TUI audit r2 #18). */
function exportMetaFor(sessionId: string | undefined, reply: ExportSessionReply): ExportMeta {
  const live = !sessionId || sessionId === eventScopedTab().sessionId;
  return withServerExportMeta(live ? liveExportMeta() : rowExportMeta(sessionId), reply);
}

function liveExportMeta(): ExportMeta {
  return {
    project: eventScopedTab().project?.id ?? "",
    model: eventScopedTab().currentModel ?? "",
    costUsd: eventScopedTab().cost?.cost_usd_total ?? null,
  };
}

function rowExportMeta(sessionId: string): ExportMeta {
  const row = workspace.sessionsList.find((r) => r.id === sessionId);
  return { project: "?", model: row?.model ?? "", costUsd: numberOrNull(row?.cost_usd) };
}

/** Export the current (or given) session as markdown with front-matter to cwd. */
export async function exportSession(sessionId?: string): Promise<string | null> {
  // A bare export names the live session: "current" made every bare export
  // the same r4-session-current.md (each overwrote the last) and stamped
  // `session_id: current` over a body naming the real id.
  const exportedId = sessionId || eventScopedTab().sessionId || "current";
  const bridge = getCompanionBridge();
  try {
    const result = (await bridge.request(
      "export_session",
      sessionId ? { session_id: sessionId } : {},
      30_000,
    )) as ExportSessionReply;
    const front = sessionExportFrontMatter({
      sessionId: exportedId,
      ...exportMetaFor(sessionId, result),
      exportedAt: new Date().toISOString(),
    });
    const file = `r4-session-${exportedId.slice(0, 8)}.md`;
    const { writeFileSync } = await import("node:fs");
    writeFileSync(file, front + (result.markdown ?? ""));
    actions.sendSystemNotice(`exported → ${file}`);
    return file;
  } catch (err) {
    actions.sendErrorCard({ what: "export failed", action: "dropped", detail: err instanceof Error ? err.message : String(err) });
    return null;
  }
}

// One turn-index entry (UMRS companion_sessions._turn_entries: turn_id,
// index, role, committed_at) as text: pure, in lib/session-turns.ts (the VS
// Code host prints them too, ADR-103 D-3).
export { sessionTurnLine } from "../lib/session-turns";

/** T5 topic-20 tail: fetch turn payloads for a session id (turn drillin');
 * render inline rows (server-stored shape byte-cum; no fabricated summaries). */
export async function fetchSessionTurns(sessionId: string, turnId?: string): Promise<void> {
  const bridge = getCompanionBridge();
  try {
    const params: Record<string, unknown> = { session_id: sessionId };
    if (turnId) params.turn_id = turnId;
    const result = await rpc(bridge, "session.turns", params, 30_000);
    const rows = turnRowsOf(result);
    if (rows.length === 0) {
      actions.sendSystemNotice(`session ${sessionId.slice(0, 8)}: no stored turns`);
      return;
    }
    const lines = rows.map(sessionTurnLine);
    actions.sendSystemNotice(
      `session ${sessionId.slice(0, 8)} turns (${rows.length}):\n${lines.join("\n")}`,
    );
  } catch (err) {
    actions.sendErrorCard({
      what: "turns failed",
      action: "dropped",
      detail: err instanceof Error ? err.message : String(err),
    });
  }
}
