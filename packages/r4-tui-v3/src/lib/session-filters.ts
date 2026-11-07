/**
 * /sessions filter chips + venue picker gate — pure logic (plan 2026-09-09
 * §4.5 chips/keys, §8a decisions). Kept bridge-free so bun tests exercise
 * the full state machine without a live server.
 */

import type { SessionFilters, SessionStateFilter, SessionVenueFilter, VenueChoice } from "../types";

/** s-key cycle order (chip shows the active one — honesty rule). */
export const SESSION_STATE_ORDER: readonly SessionStateFilter[] = [
  "all",
  "ongoing",
  "stopped",
  "completed",
  "error",
  "archived",
];

/** v-key cycle order (round 1: no cluster venues in the filter cycle). */
export const SESSION_VENUE_ORDER: readonly SessionVenueFilter[] = ["all", "local", "frontend"];

/** First-visit defaults (plan §8a-4): archives hidden, one keystroke away. */
export const DEFAULT_SESSION_FILTERS: SessionFilters = {
  state: "all",
  venue: "all",
  includeArchived: false,
};

function cycle<T>(order: readonly T[], current: T): T {
  const idx = order.indexOf(current);
  return order[(idx + 1) % order.length]!;
}

export function cycleSessionStateFilter(current: SessionStateFilter): SessionStateFilter {
  return cycle(SESSION_STATE_ORDER, current);
}

export function cycleSessionVenueFilter(current: SessionVenueFilter): SessionVenueFilter {
  return cycle(SESSION_VENUE_ORDER, current);
}

/** Wire params for the list_sessions RPC (additive v1 fields only; empty
 *  values omitted so pre-venue servers see the old request shape). */
export function buildListSessionsParams(filters: SessionFilters): Record<string, unknown> {
  const params: Record<string, unknown> = { limit: 50 };
  if (filters.state !== "all") params.state = filters.state;
  if (filters.venue !== "all") params.venue = filters.venue;
  // The archived chip implies archive visibility — tombstones one keystroke
  // away, never zero (§8a-4). Sending state=archived without the include flag
  // would filter a bucket the server hides by default.
  if (filters.includeArchived || filters.state === "archived") params.include_archived = true;
  return params;
}

/** One venue picker row. Disabled rows ALWAYS carry their reason (console
 *  honesty: never a dead button without a reason). */
export interface VenuePickerOption {
  choice: VenueChoice;
  key: string;
  label: string;
  consequence: string;
  disabled: boolean;
  /** Present iff disabled — rendered next to the option. */
  disabledReason: string;
}

/**
 * Picker gate: Server (frontend) is disabled-with-reason while the session
 * journal is unreachable — reason comes from list_sessions.remote_reason
 * (reason-specific, never a generic "server down"; plan §6 risk 3).
 */
export function venuePickerOptions(
  remoteAvailable: boolean,
  remoteReason: string,
): VenuePickerOption[] {
  const reason = remoteReason.trim() || "session journal did not answer";
  return [
    {
      choice: "local",
      key: "l",
      label: "Local",
      consequence: "runs on this machine; dies when r4 exits; resumable here only",
      disabled: false,
      disabledReason: "",
    },
    {
      choice: "frontend",
      key: "f",
      label: "Server (frontend)",
      consequence: "runs on the platform server; survives disconnect; openable after login",
      disabled: !remoteAvailable,
      disabledReason: remoteAvailable ? "" : `unavailable — ${reason}`,
    },
    {
      choice: "cluster:pending",
      key: "c",
      label: "Cluster (cluster:<name>)",
      consequence:
        "runs inside a Slurm miniature; loop-runtime-only today — session runtime follows",
      disabled: true,
      disabledReason:
        "runtime loop-jobs-only (WP-05 r2); companion-session cluster venue lands separately",
    },
  ];
}

/** Venue glyph for list rows (full word goes in the detail line). */
export function venueGlyph(venue: string | undefined): string {
  if (venue === "local") return "L";
  if (venue === "frontend") return "F";
  if (venue && venue.startsWith("cluster:")) return "C";
  return "?";
}

/** Full venue word for the detail line — never fabricated for "" rows. */
export function venueWord(venue: string | undefined): string {
  if (venue === "local") return "local";
  if (venue === "frontend") return "frontend";
  if (venue && venue.startsWith("cluster:")) return venue;
  return "unknown";
}

/** True when the row exists only server-side (renders with its badge;
 *  local-row verbs are gated — see sessionVerbBlockedForRemoteOnly). */
export function isRemoteOnly(record: { sync?: string }): boolean {
  return record.sync === "remote-only";
}

/** Verbs still blocked on remote-only rows. RESUME is NOT blocked: the bridge
 *  pull-fallback pulls the row into the local store server-side before load. */
export const REMOTE_ONLY_BLOCKED_VERBS = ["rename", "pin", "archive", "export"] as const;

/** True when `verb` must be refused (with reason) for this row. */
export function sessionVerbBlockedForRemoteOnly(record: { sync?: string }, verb: string): boolean {
  return isRemoteOnly(record) && (REMOTE_ONLY_BLOCKED_VERBS as readonly string[]).includes(verb);
}
