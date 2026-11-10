/**
 * ADR-114 D-6: the tab's session received messages from another R4 session.
 *
 * The bridge emits `{"session_inbox": {session_id, unread, from, latest}}`
 * whenever the session's inbox changes. The tab shows one live notice and,
 * when it is the active tab and idle, starts a turn so the agent reads the
 * messages with its `session_inbox` tool. The turn text is R4's own words:
 * no sender-controlled text reaches the prompt this way (sender names are
 * only shown in the notice). A background or busy tab is not woken: the
 * agent's next turn carries an "unread messages" reminder instead.
 * `R4_SESSION_BUS_WAKE=0` turns the wake off (the notice stays).
 */
import { actions, activeTab, workspace } from "../stores";
import { applyOnTab } from "../tabs/registry";

export const SESSION_INBOX_NOTICE_KEY = "session-inbox";

export const SESSION_INBOX_WAKE_TEXT =
  "New messages from other R4 sessions arrived. Read them with session_inbox and act on them as you judge fit.";

/** The last message id each tab was woken for (one wake per arrival). */
const wokenFor = new Map<string, string>();

type InboxPayload = { session_id?: unknown; unread?: unknown; from?: unknown; latest?: unknown };

function senders(from: unknown): string {
  if (!Array.isArray(from)) return "";
  const names = from.filter((name): name is string => typeof name === "string" && name.length > 0);
  return names.length ? ` from ${names.slice(0, 5).join(", ")}` : "";
}

function wakeEnabled(): boolean {
  const raw = String(process.env.R4_SESSION_BUS_WAKE ?? "").trim().toLowerCase();
  return !["0", "off", "false", "no"].includes(raw);
}

function shouldWake(tabId: string, latest: string): boolean {
  if (!latest || !wakeEnabled()) return false;
  // activeTab() is the active tab's store, the only tab woken (H-7: no
  // chat-scoped reads through the singleton).
  if (tabId !== workspace.activeChatTabId || activeTab().chatStreaming) return false;
  return wokenFor.get(tabId) !== latest;
}

function targetsBoundSession(row: InboxPayload, tabId: string): boolean {
  const target = workspace.chatTabs.find((tab) => tab.id === tabId);
  return Boolean(target?.sessionId && row.session_id === target.sessionId);
}

function deliverToNamedTab(row: InboxPayload, tabId: string): void {
  const unread = typeof row.unread === "number" ? row.unread : 0;
  if (unread <= 0) return;
  const latest = typeof row.latest === "string" ? row.latest : "";
  actions.upsertSystemNotice(
    SESSION_INBOX_NOTICE_KEY,
    `✉ ${unread} unread message${unread === 1 ? "" : "s"}${senders(row.from)} — the agent reads them with session_inbox`,
  );
  if (!shouldWake(tabId, latest)) return;
  wokenFor.set(tabId, latest);
  actions.sendChatMessage(SESSION_INBOX_WAKE_TEXT, { relay: true });
}

export function handleSessionInbox(payload: unknown, tabId: string): void {
  const row = (payload ?? {}) as InboxPayload;
  // Never fall back to the active tab: a stale bridge notice must not wake the
  // session that replaced its old owner, and background delivery is not a broadcast.
  if (!targetsBoundSession(row, tabId)) return;
  applyOnTab(tabId, () => deliverToNamedTab(row, tabId));
}

/** Tests: forget which arrivals woke a tab. */
export function resetSessionInboxWakes(): void {
  wokenFor.clear();
}
