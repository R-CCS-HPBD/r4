/**
 * TUI audit r2 (2026-10-17), tabs lane — App installs the live tab watchers.
 *
 * #34/#46 and #44/#61 were App wiring defects (the layout effect never
 * re-ran; nothing started the venue poller for a resumed session). A live
 * (non-offline) App mount spawns the r4 companion bridge, so the guard is
 * split: the App mount must call installLiveTabWatchers with its offline
 * flag, and installLiveTabWatchers(false) must arm both watchers.
 */

import { afterEach, beforeEach, expect, it } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { createRoot } from "solid-js";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import {
  installLiveTabWatchers,
  liveTabWatchersModeForTests,
  resetLiveTabWatchersForTests,
} from "../src/tabs/live-watchers";
import { stopVenueStatusPolling, venueStatusPollingActive } from "../src/bridge/venue-bridge";
import type { VenueRuntime } from "../src/lib/venue-runtime";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
let dir = "";
let savedHome: string | undefined;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "r4-tabs-watchers-"));
  savedHome = process.env.R4_CONFIG_HOME;
  process.env.R4_CONFIG_HOME = dir;
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  stopVenueStatusPolling();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  resetTabsRegistryForTests();
  resetLiveTabWatchersForTests();
});

afterEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  stopVenueStatusPolling();
  resetWorkspace();
  if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = savedHome;
  rmSync(dir, { recursive: true, force: true });
});

it("the App mount installs the live tab watchers with its offline flag", async () => {
  const setup = await testRender(() => <App />, { width: 120, height: 40 });
  await setup.renderOnce();
  expect(liveTabWatchersModeForTests()).toBe("offline");
  // Offline: neither watcher runs — no layout file, no poller.
  actions.setTabMeta(workspace.activeChatTabId, { sessionId: "S1", title: "alpha" });
  await wait(350);
  expect(existsSync(join(dir, "tui-session-state.json"))).toBe(false);
  setup.renderer.destroy();
});

it("installLiveTabWatchers(false) persists the strip and follows a live runtime", async () => {
  let dispose: () => void = () => undefined;
  createRoot((d) => {
    dispose = d;
    installLiveTabWatchers(false);
  });
  expect(liveTabWatchersModeForTests()).toBe("live");
  actions.setTabMeta(workspace.activeChatTabId, { sessionId: "S1", title: "alpha" });
  await wait(350);
  const saved = JSON.parse(readFileSync(join(dir, "tui-session-state.json"), "utf-8"));
  expect(saved).toEqual({ tabs: [{ title: "alpha", sessionId: "S1" }], activeIndex: 0 });

  expect(venueStatusPollingActive()).toBe(false);
  actions.setVenueRuntime({ state: "running" } as VenueRuntime);
  await wait(0);
  expect(venueStatusPollingActive()).toBe(true);
  dispose();
});
