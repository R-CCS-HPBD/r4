/**
 * Chat tabs — opencode-v2-style parallel session tabs for the chat frame.
 *
 * Pure model only: no store, no bridge, no render. Everything here is
 * pin-testable. See registry.ts for the live runtime (one bridge per tab).
 *
 * Behavior contract (ported from opencode packages/app semantics):
 * - each tab owns one companion session (its own bridge process);
 * - closing the active tab activates its right neighbor, else left, else none;
 * - closed tabs go on a reopen stack capped at CLOSED_TAB_LIMIT; reopening
 *   skips entries whose session is already open again;
 * - next/prev cycling wraps around the strip.
 */

import { ulid } from "ulid";

/** Hard cap: one python bridge process per tab — parallel has a real cost. */
export const MAX_CHAT_TABS = 8;
export const CLOSED_TAB_LIMIT = 25;

export interface ChatTabMeta {
  id: string;
  /** Companion session id ("" until the tab's bridge reports one). */
  sessionId: string;
  /** Strip label — session name when known, else a short fallback. */
  title: string;
  /** A turn is in flight on this tab's bridge. */
  busy: boolean;
  /** Approval / ask-user / error arrived while the tab was in the background. */
  needsAttention: boolean;
  /** Count of events absorbed in the background (informational badge). */
  absorbed: number;
  createdAt: string;
}

export interface ClosedChatTab {
  meta: ChatTabMeta;
  /** Chat-scoped field values captured at close (transcript et al). */
  snapshot: Record<string, unknown>;
  index: number;
}

export function makeTabMeta(index: number, now = new Date().toISOString()): ChatTabMeta {
  return {
    id: `tab-${ulid()}`,
    sessionId: "",
    title: `chat ${index}`,
    busy: false,
    needsAttention: false,
    absorbed: 0,
    createdAt: now,
  };
}

/** Right neighbor id after closing `closedId`, else left, else null. */
export function nextTabAfterClose(
  order: readonly string[],
  closedId: string,
): string | null {
  const idx = order.indexOf(closedId);
  if (idx < 0) return null;
  return order[idx + 1] ?? order[idx - 1] ?? null;
}

/** Wrap-around neighbor of `activeId` in `order` (dir +1 / -1). */
export function adjacentTabId(
  order: readonly string[],
  activeId: string,
  dir: 1 | -1,
): string | null {
  if (order.length < 2) return null;
  const idx = order.indexOf(activeId);
  if (idx < 0) return order[0] ?? null;
  return order[(idx + dir + order.length) % order.length] ?? null;
}

/** Push onto the closed stack, keeping only the newest CLOSED_TAB_LIMIT rows. */
export function pushClosedTab(
  stack: readonly ClosedChatTab[],
  entry: ClosedChatTab,
): ClosedChatTab[] {
  return [...stack, entry].slice(-CLOSED_TAB_LIMIT);
}

/**
 * Pop the topmost closed entry whose session is NOT currently open.
 * Stale entries (session re-opened meanwhile) are discarded silently.
 * Returns [entry, remainingStack]; [null, stack] when nothing reopenable.
 */
export function takeClosedTab(
  stack: readonly ClosedChatTab[],
  openSessionIds: ReadonlySet<string>,
): [ClosedChatTab | null, ClosedChatTab[]] {
  const rest = [...stack];
  while (rest.length > 0) {
    const top = rest.pop()!;
    if (top.meta.sessionId && openSessionIds.has(top.meta.sessionId)) continue;
    return [top, rest];
  }
  return [null, rest];
}

/** Tab strip fallback title: session name → short id → ordinal. */
export function tabTitle(meta: Pick<ChatTabMeta, "sessionId" | "title">, ordinal: number): string {
  if (meta.title.trim()) return meta.title.trim();
  if (meta.sessionId) return meta.sessionId.slice(0, 8);
  return `chat ${ordinal}`;
}

/**
 * Clean a server session name for the strip. The chat wire prefixes every
 * first message with a [Project context] block (buildProjectChatContext);
 * server auto-naming leaks it into session titles — cut the block (or, when
 * the name truncates inside it, everything from its start) and collapse
 * whitespace. Returns "" when nothing usable remains (caller falls back).
 */
export const PROJECT_CONTEXT_SENTINEL = "[Project context]";
export function cleanSessionTitle(raw: string): string {
  let s = raw.trim();
  const start = s.indexOf(PROJECT_CONTEXT_SENTINEL);
  if (start >= 0) {
    const endMark = "unless the user asks.";
    const end = s.indexOf(endMark, start);
    s = (end >= 0 ? s.slice(0, start) + s.slice(end + endMark.length) : s.slice(0, start)).trim();
  }
  return s.replace(/\s+/g, " ").trim();
}

/** Derive the provisional tab title from the first typed prompt (client-side,
 *  immediate, always clean — the server name refines it later). */
export function promptDerivedTitle(content: string, cap = 60): string {
  const firstLine = content.trim().split("\n")[0]?.trim() ?? "";
  return firstLine.slice(0, cap).trim();
}

/** True while the tab title is still the makeTabMeta fallback. */
export function isFallbackTitle(title: string): boolean {
  return /^chat \d+$/.test(title.trim());
}

/**
 * True for systematic placeholder session names. Mirrors the server's own
 * AUTO_SESSION_NAME_RE (`Session YYYY-MM-DD HH:MM`, session_store.py) —
 * human names like "Session notes" deliberately do NOT match (renaming
 * user intent is forbidden; only the auto-placeholder shape is fair game),
 * plus the client fallbacks: our `chat N`, "(unnamed)", "New chat".
 */
export function isGenericSessionName(name: string): boolean {
  const s = name.trim();
  if (!s) return true;
  if (isFallbackTitle(s)) return true;
  if (s === "(unnamed)") return true;
  if (/^Session \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/i.test(s)) return true;
  if (/^(new chat|new session|untitled)$/i.test(s)) return true;
  return false;
}

/**
 * The persisted A1 layout for a tab strip: only session-bound tabs, and the
 * active index counted in THAT filtered list (counting it in the full strip
 * restored the wrong tab when a session-less tab sat before the active one;
 * TUI audit r2 #34). An active tab without a session falls back to 0.
 */
export function tabsLayoutState(
  tabs: ReadonlyArray<Pick<ChatTabMeta, "id" | "title" | "sessionId">>,
  activeId: string,
): { tabs: Array<{ title: string; sessionId: string }>; activeIndex: number } {
  const bound = tabs.filter((t) => (t.sessionId ?? "") !== "");
  return {
    tabs: bound.map((t) => ({ title: t.title, sessionId: t.sessionId })),
    activeIndex: Math.max(0, bound.findIndex((t) => t.id === activeId)),
  };
}
