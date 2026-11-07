/**
 * The console's last known `search_server.status` (the web chip reads it).
 * Refreshed by the status bar on a slow cadence and by /search-server after
 * every start, stop or status (commands/search-server-command.ts). A failed
 * read keeps the last known value: never a fabricated "none".
 */

import { createSignal } from "solid-js";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import type { SearchServerStatusResult } from "../bridge/rpc-types.gen";
import { searchBackend, type SearchBackend } from "./web-status";

const [backend, setBackend] = createSignal<SearchBackend>("unknown");

/** The backend `web_search` would use right now, as last read — reactive. */
export const searchServerBackend = backend;

/** Record a status the console already holds (e.g. /search-server status). */
export function noteSearchServerStatus(status: SearchServerStatusResult): void {
  setBackend(searchBackend(status));
}

/** Mark a start as running until the next read says otherwise. */
export function noteSearchServerStarting(): void {
  setBackend("starting");
}

/** Ask the bridge; on failure keep what we had. */
export async function refreshSearchServerStatus(): Promise<void> {
  try {
    noteSearchServerStatus(await rpc(getCompanionBridge(), "search_server.status", {}, 15_000));
  } catch {
    // bridge hiccup or a bridge that predates the verb — keep the last value
  }
}
