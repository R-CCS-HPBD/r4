/**
 * /search-server [status|up|down] — the local SearXNG that gives the agent's
 * web_search a backend (ADR-102; guided start 2026-11-01). Bridge verbs
 * search_server.status / .up / .down.
 *
 * `up` is accepted at once (a CONTROL verb, so it is answered during a turn
 * too); the start itself — a first one downloads the image — runs on the
 * bridge's own thread and reports each step and the end as
 * `search_server_event` lines, posted as notices on the tab that asked.
 * The same start runs when the user accepts the offer that follows /web
 * Public without a search server (search-server-offer-overlay.tsx).
 */

import { actions, workspace } from "../stores";
import { applyOnTab } from "../tabs/registry";
import { isOfflineBackend } from "../lib/console-mode";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import {
  SEARCH_SERVER_BUSY,
  SEARCH_SERVER_STARTING,
  SEARCH_SERVER_STOPPED,
  SEARCH_SERVER_USAGE,
  searchServerEventText,
  searchServerStatusText,
  type SearchServerEvent,
} from "../lib/search-server-text";
import {
  noteSearchServerStarting,
  noteSearchServerStatus,
  refreshSearchServerStatus,
} from "../lib/search-server-state";

// The texts are pure, in lib/search-server-text.ts (the VS Code host prints
// them too, ADR-103 D-3).
export { SEARCH_SERVER_USAGE, searchServerEventText, searchServerStatusText, type SearchServerEvent };

export type SearchServerDispatchResult = { kind: "handled" } | { kind: "error"; message: string };

/** The tab whose start is running: its progress lands there. */
let startTab: string | null = null;

function post(tabId: string, text: string): SearchServerDispatchResult {
  applyOnTab(tabId, () => actions.sendSystemNotice(text));
  return { kind: "handled" };
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** `search_server_event` from the bridge (tabs/wiring.ts, a global channel). */
export function handleSearchServerEvent(payload: unknown): void {
  const event = (payload ?? {}) as SearchServerEvent;
  const tabId = startTab ?? workspace.activeChatTabId;
  if (event.phase !== "progress") {
    startTab = null;
    // The web chip follows the start's end (lib/web-status).
    void refreshSearchServerStatus();
  }
  post(tabId, searchServerEventText(event));
}

/** Ask the bridge to start the local search server; progress follows as events. */
export async function startSearchServer(): Promise<SearchServerDispatchResult> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(getCompanionBridge(), "search_server.up", {}, 15_000);
    if (!result.accepted) return post(tabId, SEARCH_SERVER_BUSY);
    startTab = tabId;
    noteSearchServerStarting();
    return post(tabId, SEARCH_SERVER_STARTING);
  } catch (err) {
    return { kind: "error", message: `search server not started — ${errorText(err)}` };
  }
}

async function showStatus(): Promise<SearchServerDispatchResult> {
  const tabId = workspace.activeChatTabId;
  try {
    const status = await rpc(getCompanionBridge(), "search_server.status", {}, 15_000);
    noteSearchServerStatus(status);
    return post(tabId, searchServerStatusText(status));
  } catch (err) {
    return { kind: "error", message: `search server status failed — ${errorText(err)}` };
  }
}

async function stop(): Promise<SearchServerDispatchResult> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(getCompanionBridge(), "search_server.down", {}, 60_000);
    void refreshSearchServerStatus();
    if (result.state !== "stopped") {
      return { kind: "error", message: `search server not stopped — ${result.reason ?? result.state}` };
    }
    return post(tabId, SEARCH_SERVER_STOPPED);
  } catch (err) {
    return { kind: "error", message: `search server not stopped — ${errorText(err)}` };
  }
}

const VERBS = new Map<string, () => Promise<SearchServerDispatchResult>>([
  ["", showStatus],
  ["status", showStatus],
  ["up", startSearchServer],
  ["start", startSearchServer],
  ["down", stop],
  ["stop", stop],
]);

export async function handleSearchServerCommand(args: string): Promise<SearchServerDispatchResult> {
  if (isOfflineBackend()) return { kind: "error", message: "search-server: requires a live backend" };
  const handler = VERBS.get(args.trim().toLowerCase());
  return handler ? handler() : { kind: "error", message: SEARCH_SERVER_USAGE };
}
