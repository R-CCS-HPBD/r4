/**
 * The always-on web chip (maintainer 2026-11-03: "web search and web search
 * server availability should be visible always"). One chip, never dropped
 * by the status bar's fit: the session's web access (ADR-099 D-2) and, for
 * a local session, whether `web_search` has a backend (ADR-102: the local
 * SearXNG of /search-server, or `R4_WEB_SEARCH_URL`).
 *
 *   web:off · web:allow · web:public · web:?      (the choice; ? = not chosen)
 *   … srch:up · srch:down · srch:none · srch:…    (local backend; … = starting)
 *
 * A frontend (remote) session searches on the server, so it shows the choice
 * alone. Pure, bridge-free (shared with the VS Code host like sandbox.ts).
 */

import type { ChipState } from "./chip-state";
import type { SearchServerStatusResult } from "../bridge/rpc-types.gen";

export type SearchBackend = "up" | "down" | "none" | "starting" | "unknown";

/** `search_server.status` → the backend `web_search` would use. */
export function searchBackend(status: Partial<SearchServerStatusResult> | null | undefined): SearchBackend {
  if (!status) return "unknown";
  if (status.external_url) return "up";
  if (status.starting) return "starting";
  if (status.state === "running") return "up";
  if (status.state === "down") return "down";
  return "none";
}

const MODE_LABEL: Record<string, string> = { none: "off", allowlist: "allow", public: "public" };

const SEARCH_LABEL: Record<SearchBackend, string> = {
  up: "srch:up",
  down: "srch:down",
  none: "srch:none",
  starting: "srch:…",
  unknown: "srch:?",
};

function chipState(mode: string, backend: SearchBackend | null): ChipState {
  if (mode !== "public") return mode === "allowlist" ? "info" : "dim";
  // Public with no answering backend: web_search will refuse — say so.
  return backend === null || backend === "up" ? "ok" : "warn";
}

/** The chip for a session: its web choice and (local only) the search backend. */
export function webStatusChip(
  access: { mode: string } | null | undefined,
  backend: SearchBackend,
  venue: string | undefined,
): { label: string; state: ChipState } {
  const mode = access?.mode ?? "";
  const head = `web:${MODE_LABEL[mode] ?? "?"}`;
  const local = venue !== "frontend" ? backend : null;
  const label = local === null ? head : `${head} ${SEARCH_LABEL[local]}`;
  return { label, state: chipState(mode, local) };
}
