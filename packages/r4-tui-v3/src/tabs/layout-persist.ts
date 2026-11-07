/**
 * A1 layout persistence (2026-10-05; tracked since TUI audit r2 #34/#46).
 *
 * Every change to the strip — a tab opened or closed, a session bound, a
 * title refined, the active tab switched — rewrites the layout file
 * (debounced in writeTabsState). The effect reads every input
 * synchronously: the old App effect read them inside a dynamic-import
 * callback, outside Solid's tracking, so it ran once at mount and never
 * again.
 *
 * A layout with no session-bound tab is never written: at mount the first
 * tab has no session yet, and that empty write could land over a valid
 * file before the restore read it.
 *
 * Under `bun test` (NODE_ENV=test) the file is written only when the test
 * set its own R4_CONFIG_HOME: an App mounted by a test must never rewrite
 * the user's real ~/.config/r4 layout (TUI audit r2 review, 2026-10-17).
 * The test preload (tests/preload-config-home.ts) points R4_CONFIG_HOME at
 * a scratch dir it names in R4_TEST_DEFAULT_CONFIG_HOME; that shared
 * default stays unwritten, so one test's layout never restores into the
 * next test's App.
 */

import { createEffect } from "solid-js";
import { writeTabsState } from "../lib/tui-session-state";
import { workspace } from "../stores";
import { tabsLayoutState } from "./model";

/** False while a test runs against the default (real) config home. */
export function layoutWritesAllowed(env: NodeJS.ProcessEnv = process.env): boolean {
  if (env.NODE_ENV !== "test") return true;
  return Boolean(env.R4_CONFIG_HOME) && env.R4_CONFIG_HOME !== env.R4_TEST_DEFAULT_CONFIG_HOME;
}

/** Install the persistence effect (call inside a reactive owner). */
export function trackTabLayoutPersistence(): void {
  createEffect(() => {
    const state = tabsLayoutState(
      workspace.chatTabs.map((t) => ({ id: t.id, title: t.title, sessionId: t.sessionId })),
      workspace.activeChatTabId,
    );
    if (state.tabs.length > 0 && layoutWritesAllowed()) writeTabsState(state);
  });
}
