/**
 * `/search-server` texts (ADR-102; guided start 2026-11-01): the status line
 * and the notice for each pushed `search_server_event`. Pure, so the VS Code
 * host prints them the same way (ADR-103 D-3);
 * `commands/search-server-command.ts` re-exports them.
 */

import type { SearchServerStatusResult } from "../bridge/rpc-types.gen";

export const SEARCH_SERVER_USAGE = "usage: /search-server [status|up|down]";
const PUBLIC_TAIL = "web_search works in sessions with Public web access (/web)";

/** One pushed line of a start (`search_server_event`). */
export type SearchServerEvent = {
  phase?: string;
  message?: string;
  state?: string;
  url?: string;
  reason?: string;
  hint?: string;
};

export function searchServerStatusText(status: SearchServerStatusResult): string {
  // R4_WEB_SEARCH_URL wins over the local instance (r4.agent.web_search):
  // the status used to say "none" while web_search worked (2026-11-03).
  if (status.external_url) return `search server: R4_WEB_SEARCH_URL names ${status.external_url} — ${PUBLIC_TAIL}`;
  if (status.starting) return "search server: starting…";
  if (status.state === "running") return `search server: running at ${status.url} — ${PUBLIC_TAIL}`;
  if (status.state === "down") {
    return `search server: recorded at ${status.url} but not answering — /search-server up starts it again`;
  }
  if (!status.engine) {
    return "search server: none, and no podman or docker found — install one, or set R4_WEB_SEARCH_URL to a SearXNG instance";
  }
  return `search server: none — /search-server up starts a local SearXNG with ${status.engine} (the first start downloads about 200 MB)`;
}

/** The notice for one pushed line; the end of a start says what to do next. */
export function searchServerEventText(event: SearchServerEvent): string {
  if (event.phase === "progress") return `search server: ${event.message ?? ""}`;
  if (event.phase === "done") return `✓ search server ready at ${event.url} — ${PUBLIC_TAIL}`;
  // "; <next step>": hints carry their own parentheses (podman machine init).
  const hint = event.hint ? `; ${event.hint}` : "";
  return `⚠ search server not started — ${event.reason || event.state || "unknown error"}${hint}`;
}

export const SEARCH_SERVER_STARTING = "search server: starting a local SearXNG for web_search…";
export const SEARCH_SERVER_BUSY = "search server: a start is already running";
export const SEARCH_SERVER_STOPPED = "search server: stopped (its settings are kept; /search-server up starts it again)";
