/**
 * Chat tab runtime registry — one CompanionBridgeClient per tab.
 *
 * Invariants this module protects (stage 4):
 * - chat-scoped bridge events always write to the OWNING tab's own store
 *   (withEventTarget pins the setter; per-tab turn accumulators assemble each
 *   round on its tab); no buffer and no replay anywhere (arrival order IS the
 *   round order). Global channels dispatch immediately, bypassing any tab.
 * - an in-flight turn's stream pump is suspended while its tab is backgrounded
 *   (waitWhileBackground) and resumed on activation — the pump only gates the
 *   foreground renderer; the owning tab's state advances meanwhile.
 * - closing a tab shuts down its bridge; quit shuts down all bridges.
 *
 * Store access is dependency-injected (bindTabsStoreHooks) to keep the
 * module graph acyclic: stores/index.ts binds itself at import time.
 */

import { unwrap } from "solid-js/store";
import { CompanionBridgeClient } from "../bridge/jsonl-client";
import type { ChatTabMeta, ClosedChatTab } from "./model";
import { pushClosedTab, takeClosedTab } from "./model";
import {
  applyScopedValues,
  createChatTabStore,
  type ChatTabStore,
  type ChatTabState,
} from "./chat-tab-store";

// The field lists live in ./chat-tab-store (plan H-7 stage 1: they define
// the per-tab store's shape); re-exported here for existing importers.
export { CHAT_SCOPED_FIELDS, TURN_TRANSIENT_FIELDS } from "./chat-tab-store";
export type { ChatScopedField, ChatTabState } from "./chat-tab-store";
import { CHAT_SCOPED_FIELDS, TURN_TRANSIENT_FIELDS } from "./chat-tab-store";

export interface TabRuntime {
  id: string;
  bridge: CompanionBridgeClient | null;
  /** The tab's own chat-scoped store (plan H-7 stage 1), created from the
   *  bound defaults and mirrored by the snapshot swap; stage 2's accessor
   *  routes readers to it, stage 3 routes events, stage 4 deletes the swap. */
  tabStore?: ChatTabStore | null;
  /** Chat-scoped field snapshot; non-null while the tab is NOT active. */
  snapshot: Record<string, unknown> | null;
  /** Pending activation resolution for suspended stream pumps. */
  wake: (() => void) | null;
  closed: boolean;
  /** True once this tab's bridge handlers are attached. */
  wired: boolean;
  /** A1 restore flag: bound session not yet resumed in this process —
   * tabActivate issues the lazy resume and clears it (2026-10-05). */
  restoredPending?: boolean;
  /** waitForTabTurn waiters, resolved with false when the tab closes. */
  closeWaiters?: Array<() => void>;
}


export interface TabsStoreHooks {
  isActive(tabId: string): boolean;
  readScopedFields(): Record<string, unknown>;
  applyScopedFields(values: Record<string, unknown> | null): void;
  patchTabMeta(tabId: string, patch: Partial<ChatTabMeta>): void;
  /** Tab attention bookkeeping (approval/ask_user while backgrounded). */
  noteAttention(tabId: string, channel: string): void;
  /** Bridge process died for this tab. */
  onTabBridgeExit(tabId: string, message: string): void;
  /** The tab's turn ended on the bridge (the chat adapter emits
   *  `turn_settled` when send_message answers). Tab-meta bookkeeping only:
   *  a background tab's transcript writes still wait for activation. */
  onTurnSettled?(tabId: string): void;
  /**
   * Extra env for a tab's NEW bridge process (per-tab projects,
   * 2026-10-01): the tab's R4_PROJECT_ID selection rides the spawn env so
   * the bridge process resolves its own current project from birth.
   */
  bridgeEnvForTab?(tabId: string): Record<string, string>;
  /** The session the tab is bound to ("" when none): a NEW bridge for the
   * tab initializes into it instead of creating an empty session record. */
  sessionIdForTab?(tabId: string): string;
  /** Fresh chat-scoped defaults for a new tab's own store (H-7 stage 1;
   * absent in narrow contexts, which then get no per-tab store). */
  createTabState?(): ChatTabState;
}

/** Live event dispatch table — injected by tabs/wiring.ts (stores must not
 *  import the bridge handler modules; the import graph stays acyclic). */
let chatDispatcher: ((channel: string, payload: unknown, tabId: string) => void) | null = null;
let globalDispatcher: ((channel: string, payload: unknown) => void) | null = null;
export function registerTabDispatchers(fns: {
  chat: (channel: string, payload: unknown, tabId: string) => void;
  global: (channel: string, payload: unknown) => void;
}): void {
  chatDispatcher = fns.chat;
  globalDispatcher = fns.global;
}

let hooks: TabsStoreHooks | null = null;
export function bindTabsStoreHooks(h: TabsStoreHooks): void {
  hooks = h;
}

// ── H-7 stage 3b: event-target override for background dispatch ─────────────

/** While non-null, scoped `setWorkspace` writes mirror into THIS tab's own
 *  store and skip the singleton (the target tab is in the background and the
 *  owning event must never touch the live tab's state). Read by
 *  stores/index.ts's wrapped setter. */
let eventTargetTabId: string | null = null;

export function setEventTargetTab(tabId: string | null): void {
  eventTargetTabId = tabId;
}

export function eventTargetTab(): string | null {
  return eventTargetTabId;
}

/** Run `fn` synchronously with the event target set (restored afterwards —
 * dispatch is synchronous, so no interleaving with the active path). */
export function withEventTarget<T>(tabId: string, fn: () => T): T {
  const previous = eventTargetTabId;
  eventTargetTabId = tabId;
  try {
    return fn();
  } finally {
    eventTargetTabId = previous;
  }
}

const runtimes = new Map<string, TabRuntime>();
const closedStack: ClosedChatTab[] = [];
/** Ids of tabs whose runtime was destroyed (close / quit). A stream pump
 *  that outlives its tab checks this and exits without touching the store
 *  — the flat store belongs to another tab by then (TUI audit r2 #31). */
const closedTabIds = new Set<string>();

export function isTabClosed(tabId: string): boolean {
  return closedTabIds.has(tabId);
}

/**
 * Land an async result on the tab that STARTED the request (its id captured
 * before the await): a session new/resume or a mode switch resolving after a
 * tab switch wrote the then-active tab — wiping its transcript, rebinding its
 * session, badging a tab whose bridge never changed (stabilization
 * 2026-10-21). Runs `fn` under the event target, so scoped writes reach that
 * tab's own store whether or not it is still active. False (nothing run)
 * when the tab closed meanwhile.
 */
export function applyOnTab(tabId: string, fn: () => void): boolean {
  if (closedTabIds.has(tabId)) return false;
  if (tabId) withEventTarget(tabId, fn);
  else fn();
  return true;
}

/** Open but not the active tab: its store writes must wait for a visit. */
export function isTabInBackground(tabId: string): boolean {
  return !closedTabIds.has(tabId) && hooks !== null && !hooks.isActive(tabId);
}

export function getRuntime(tabId: string): TabRuntime {
  let rt = runtimes.get(tabId);
  if (!rt || rt.closed) {
    rt = {
      id: tabId,
      bridge: null,
      tabStore: hooks?.createTabState ? createChatTabStore(hooks.createTabState()) : null,
      snapshot: null,
      wake: null,
      closed: false,
      wired: false,
    };
    runtimes.set(tabId, rt);
  }
  return rt;
}

export function runtimeOrNull(tabId: string): TabRuntime | null {
  return runtimes.get(tabId) ?? null;
}

/** One tab's chat-scoped state as a plain map (cloned): from its own store
 *  when present (canonical), else from the close/reopen seed snapshot. */
export function collectScopedState(tabId: string): Record<string, unknown> {
  const rt = runtimeOrNull(tabId);
  if (rt?.tabStore) {
    const out: Record<string, unknown> = {};
    for (const field of CHAT_SCOPED_FIELDS) {
      const raw = unwrap(rt.tabStore.state[field as keyof typeof rt.tabStore.state]);
      out[field] = raw == null ? raw : structuredClone(raw);
    }
    return out;
  }
  return rt?.snapshot ?? {};
}

/** Rehydrate the singleton mirror to this tab's store (stage 4 activation):
 *  the store is canonical and current; the legacy singleton fields follow
 *  it through the same hooks the swap used. */
export function restoreScopedFromStore(tabId: string): void {
  if (!hooks) return;
  hooks.applyScopedFields(collectScopedState(tabId));
  const rt = runtimeOrNull(tabId);
  if (rt?.tabStore) applyScopedValues(rt.tabStore, valuesFromHooks());
}

function valuesFromHooks(): Record<string, unknown> {
  return hooks ? hooks.readScopedFields() : {};
}

/**
 * Bridge for a tab — created on first use. Handler attachment is performed
 * by wiring.ts via the registry's onBridgeAttach hook (kept external so this
 * module never imports the handler table → no import cycle).
 */
let attachHandlers: ((bridge: CompanionBridgeClient, tabId: string) => void) | null = null;
export function registerBridgeAttacher(
  fn: (bridge: CompanionBridgeClient, tabId: string) => void,
): void {
  attachHandlers = fn;
}

/**
 * Fallback singleton for contexts without the tabs wiring (tests, narrow
 * tools): pre-tabs getCompanionBridge() lent ONE shared bridge per process;
 * minting a fresh process per tab id there would spawn dozens of pythons
 * across the bun suite. App runtime always installs the attacher first
 * (wireChatTabsOnce), so this branch is test-only.
 */
let fallbackShared: CompanionBridgeClient | null = null;

/** True once the live tab wiring (per-tab bridges) is installed. */
export function tabWiringInstalled(): boolean {
  return attachHandlers !== null;
}

export function bridgeForTab(tabId: string): CompanionBridgeClient {
  if (!attachHandlers) {
    if (!fallbackShared) fallbackShared = new CompanionBridgeClient();
    return fallbackShared;
  }
  const rt = getRuntime(tabId);
  if (!rt.bridge) {
    rt.bridge = new CompanionBridgeClient({
      envExtra: hooks?.bridgeEnvForTab?.(tabId),
      // Read at every (re)start: a restarted bridge rejoins the tab's session.
      initialSessionId: () => hooks?.sessionIdForTab?.(tabId) ?? "",
    });
    rt.wired = true;
    attachHandlers(rt.bridge, tabId);
  }
  return rt.bridge;
}

// ── activation gate (stream pumps) ─────────────────────────────────────

/** Await while the tab is in the background. Resolves immediately when active. */
export function waitWhileBackground(tabId: string): Promise<void> {
  if (!hooks || hooks.isActive(tabId)) return Promise.resolve();
  const rt = getRuntime(tabId);
  return new Promise<void>((resolve) => {
    const prev = rt.wake;
    rt.wake = () => {
      prev?.();
      resolve();
    };
  });
}

/**
 * Await the turn's tab: true once it is the active tab, false when it was
 * closed (now or while waiting). A turn settles into the flat store only on
 * true — a closed tab's turn settles nowhere (TUI audit r2 #31).
 * waitWhileBackground keeps its never-resolve-on-close behaviour for
 * callers that cannot tell the two apart.
 */
export function waitForTabTurn(tabId: string): Promise<boolean> {
  if (closedTabIds.has(tabId)) return Promise.resolve(false);
  if (!hooks || hooks.isActive(tabId)) return Promise.resolve(true);
  const rt = getRuntime(tabId);
  return new Promise<boolean>((resolve) => {
    const prev = rt.wake;
    rt.wake = () => {
      prev?.();
      resolve(true);
    };
    rt.closeWaiters = [...(rt.closeWaiters ?? []), () => resolve(false)];
  });
}

/** Notify a (freshly activated) tab's suspended pumps. */
export function notifyActivated(tabId: string): void {
  const rt = runtimeOrNull(tabId);
  if (!rt) return;
  const wake = rt.wake;
  rt.wake = null;
  rt.closeWaiters = [];
  wake?.();
}



/** Route one bridge event for one tab (stage 4): every chat-scoped channel
 *  dispatches directly under the event target — the handler's reads come
 *  from the event's own store (activeTab honors the target), per-tab turn
 *  accumulators assemble each round on its tab, and arrival order IS the
 *  round order (TUI audit r2 #33). No buffer, no replay; the stream pump's
 *  suspension on backgrounding (waitWhileBackground) only gates the
 *  foreground renderer. */
export function routeTabEvent(tabId: string, channel: string, payload: unknown): void {
  if (!hooks || closedTabIds.has(tabId)) return;
  if (channel === "exit") {
    hooks.onTabBridgeExit(tabId, payload instanceof Error ? payload.message : String(payload ?? ""));
    return;
  }
  if (channel === "turn_settled") {
    hooks.onTurnSettled?.(tabId);
    return;
  }
  if (GLOBAL_CHANNELS.has(channel)) {
    globalDispatcher?.(channel, payload);
    return;
  }
  if (!hooks.isActive(tabId)) hooks.noteAttention(tabId, channel);
  withEventTarget(tabId, () => chatDispatcher?.(channel, payload, tabId));
}


// ── event gating ─────────────────────────────────────────────────────────

/** Channels that must never buffer (global stores / process lifetime). */
export const GLOBAL_CHANNELS = new Set([
  "project_job_event",
  "team_event",
  "search_server_event",
  "session_title",
  "exit",
  "stderr",
  "protocol_error",
  "unmatched",
  "source_stale",
]);

// ── close / reopen ─────────────────────────────────────────────────────────

/** A close-time snapshot without the dead turn: transient turn fields drop
 *  to defaults and no message stays streaming or queued (TUI audit r2 #35). */
export function settledClosedSnapshot(snapshot: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...snapshot };
  for (const field of TURN_TRANSIENT_FIELDS) delete out[field];
  const messages = out["chatMessages"];
  if (Array.isArray(messages)) {
    out["chatMessages"] = messages.map((m: { streaming?: boolean; queued?: boolean }) =>
      m && (m.streaming || m.queued) ? { ...m, streaming: false, queued: false } : m,
    );
  }
  return out;
}

/** Close a tab's runtime: snapshot for reopen stack, kill the bridge. */
export async function closeRuntime(
  meta: ChatTabMeta,
  index: number,
  opts?: { reopenable?: boolean },
): Promise<void> {
  const rt = runtimeOrNull(meta.id);
  const live = collectScopedState(meta.id);
  const snapshot = settledClosedSnapshot(live);
  if (opts?.reopenable !== false) {
    const next = pushClosedTab(closedStack, { meta: { ...meta }, snapshot, index });
    closedStack.length = 0;
    closedStack.push(...next);
  }
  await destroyRuntime(meta.id);
}

/** Destroy without pushing to the reopen stack (quit path uses shutdownAll). */
export async function destroyRuntime(tabId: string): Promise<void> {
  closedTabIds.add(tabId);
  const rt = runtimes.get(tabId);
  if (!rt) return;
  rt.closed = true;
  rt.wake = null;
  // A turn waiting for this tab learns it closed and exits (it hung forever).
  const waiters = rt.closeWaiters ?? [];
  rt.closeWaiters = [];
  for (const waiter of waiters) waiter();
  runtimes.delete(tabId);
  if (rt.bridge) {
    try {
      await rt.bridge.shutdown();
    } catch {
      /* already gone */
    }
  }
}

/** Pop the newest reopenable closed tab (skips sessions already open). */
export function popClosedTab(openSessionIds: ReadonlySet<string>): ClosedChatTab | null {
  const [entry, rest] = takeClosedTab(closedStack, openSessionIds);
  closedStack.length = 0;
  closedStack.push(...rest);
  return entry;
}

export function closedTabCount(): number {
  return closedStack.length;
}

/** Quit path: shut down every tab's bridge. */
export async function shutdownAllTabBridges(): Promise<void> {
  const ids = [...runtimes.keys()];
  await Promise.all(ids.map((id) => destroyRuntime(id)));
}

/**
 * Test-hygiene barrier: drop the runtime wiring table (attacher +
 * dispatchers) without touching runtimes or the store hooks. Called from
 * actions.resetWorkspace — without it, one test file mounting App would
 * leave the attacher registered and LATER files would spawn REAL bridges
 * with full event handlers, leaking init_state rows into their assertions.
 * The live app never resets the workspace; App mount always re-registers.
 */
export function clearTabRuntimeWiring(): void {
  attachHandlers = null;
  chatDispatcher = null;
  globalDispatcher = null;
}

// ── test hooks ─────────────────────────────────────────────────────────────

export function resetTabsRegistryForTests(): void {
  for (const rt of runtimes.values()) {
    rt.closed = true;
      rt.wake = null;
  }
  runtimes.clear();
  closedStack.length = 0;
  closedTabIds.clear();
  // hooks stay bound: stores/index.ts binds them at import time and re-binding
  // never re-runs in a shared test process — clearing them would silently
  // disable the snapshot swap for the rest of the process.
  attachHandlers = null;
  chatDispatcher = null;
  globalDispatcher = null;
  fallbackShared = null;
}
