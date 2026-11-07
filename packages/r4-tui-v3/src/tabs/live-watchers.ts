/**
 * Live-session watchers (never offline), installed by App at mount: the A1
 * tab layout is rewritten on every strip change (debounced), the venue
 * status poller follows the active tab's live remote runtime (resume,
 * restore, tab switch), and a remote worker's pending question opens in any
 * attached client. TUI audit r2 #34/#46 and #44/#61; ADR-099 D-8.
 */

import { trackRemoteQuestions, trackVenueStatusPolling } from "../bridge/venue-bridge";
import { trackTabLayoutPersistence } from "./layout-persist";

let installedMode: "live" | "offline" | null = null;

/** Install both watchers unless offline (call inside a reactive owner). */
export function installLiveTabWatchers(offline: boolean): void {
  installedMode = offline ? "offline" : "live";
  if (offline) return;
  trackTabLayoutPersistence();
  trackVenueStatusPolling();
  // ADR-099 D-8: a remote session waiting on the user's answer, however it
  // was opened or attached, opens its question here too.
  trackRemoteQuestions();
}

/** Which mode App last installed the watchers in (null: never called). */
export function liveTabWatchersModeForTests(): "live" | "offline" | null {
  return installedMode;
}

export function resetLiveTabWatchersForTests(): void {
  installedMode = null;
}
