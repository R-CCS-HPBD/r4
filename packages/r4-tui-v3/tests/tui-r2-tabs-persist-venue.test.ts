/**
 * TUI audit r2 (2026-10-17), tabs lane — long-lived watchers.
 *
 * #34/#46 the A1 layout persistence effect ran once at mount (its reads sat
 *         in a promise callback), so tabs and session bindings were never
 *         saved; the active index was counted in the unfiltered strip;
 * #44/#61 the venue status poller stopped for good on the first tick that
 *         saw a local tab, and nothing started it for a resumed session;
 * #57     `/venue start` always created a new empty session (and another
 *         one on every push-then-retry).
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { createRoot } from "solid-js";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { tabsLayoutState } from "../src/tabs/model";
import { layoutWritesAllowed, trackTabLayoutPersistence } from "../src/tabs/layout-persist";
import {
  startVenueStatusPolling,
  stopVenueStatusPolling,
  trackVenueStatusPolling,
  venueStatusPollingActive,
  venueStatusTick,
} from "../src/bridge/venue-bridge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { makeRemoteStarter } from "../src/components/remote-source-picker-overlay";
import type { RemoteSourcePickerMode } from "../src/stores";
import type { VenueRuntime } from "../src/lib/venue-runtime";

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  resetWorkspace();
  resetTabsRegistryForTests();
});

describe("A1 layout persistence follows the strip (#34/#46)", () => {
  let dir = "";
  let savedHome: string | undefined;
  let dispose: (() => void) | null = null;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "r4-tabs-layout-"));
    savedHome = process.env.R4_CONFIG_HOME;
    process.env.R4_CONFIG_HOME = dir;
  });

  afterEach(() => {
    dispose?.();
    dispose = null;
    if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
    else process.env.R4_CONFIG_HOME = savedHome;
    rmSync(dir, { recursive: true, force: true });
  });

  const saved = () =>
    JSON.parse(readFileSync(join(dir, "tui-session-state.json"), "utf-8")) as {
      tabs: Array<{ title: string; sessionId: string }>;
      activeIndex: number;
    };

  it("rewrites the layout on every bind, open and switch after mount", async () => {
    createRoot((d) => {
      dispose = d;
      trackTabLayoutPersistence();
    });
    const first = workspace.activeChatTabId;
    actions.setTabMeta(first, { sessionId: "S1", title: "alpha" });
    await wait(350);
    expect(saved()).toEqual({ tabs: [{ title: "alpha", sessionId: "S1" }], activeIndex: 0 });

    const second = actions.tabCreate()!;
    actions.setTabMeta(second, { sessionId: "S2", title: "beta" });
    await wait(350);
    expect(saved().tabs.map((t) => t.sessionId)).toEqual(["S1", "S2"]);
    expect(saved().activeIndex).toBe(1);

    actions.tabActivate(first);
    await wait(350);
    expect(saved().activeIndex).toBe(0);
  });

  it("a test that did not set R4_CONFIG_HOME never writes the user's layout", () => {
    expect(layoutWritesAllowed({ NODE_ENV: "test" })).toBe(false);
    expect(layoutWritesAllowed({ NODE_ENV: "test", R4_CONFIG_HOME: dir })).toBe(true);
    expect(layoutWritesAllowed({})).toBe(true);
    expect(layoutWritesAllowed({ NODE_ENV: "production" })).toBe(true);
  });

  it("counts the active index among session-bound tabs only", () => {
    const state = tabsLayoutState(
      [
        { id: "t1", title: "unbound", sessionId: "" },
        { id: "t2", title: "two", sessionId: "S2" },
        { id: "t3", title: "three", sessionId: "S3" },
      ],
      "t3",
    );
    expect(state.tabs.map((t) => t.sessionId)).toEqual(["S2", "S3"]);
    expect(state.activeIndex).toBe(1);
    expect(tabsLayoutState([{ id: "t1", title: "x", sessionId: "" }], "t1")).toEqual({ tabs: [], activeIndex: 0 });
  });
});

describe("venue status polling follows the active tab's runtime (#44/#61)", () => {
  const requests: string[] = [];
  const bridge = {
    alive: true,
    turnInFlight: false,
    request: async (method: string) => {
      requests.push(method);
      return { venue: { state: "running" } };
    },
  } as unknown as CompanionBridgeClient;
  const running: VenueRuntime = { state: "running" } as VenueRuntime;

  beforeEach(() => {
    requests.length = 0;
    stopVenueStatusPolling();
    setCompanionBridgeForTests(bridge);
  });

  afterEach(() => {
    stopVenueStatusPolling();
    setCompanionBridgeForTests(null);
  });

  it("a tick on a local tab skips instead of stopping the timer", () => {
    setWorkspace("sessionId", "S-remote");
    startVenueStatusPolling();
    actions.setVenueRuntime(null); // a local tab became active
    venueStatusTick();
    expect(venueStatusPollingActive()).toBe(true);
    expect(requests).toEqual([]);
    actions.setVenueRuntime(running); // back on the remote tab
    venueStatusTick();
    expect(requests).toEqual(["session.venue_status"]);
  });

  it("a tick polls a bridge that runs a turn (plan H-8: reads answer mid-turn)", () => {
    const methods: string[] = [];
    setCompanionBridgeForTests({
      alive: true,
      turnInFlight: true,
      request: async (method: string) => {
        methods.push(method);
        return { venue: { state: "running" } };
      },
    } as unknown as CompanionBridgeClient);
    setWorkspace("sessionId", "S-remote");
    actions.setVenueRuntime(running);
    venueStatusTick();
    expect(methods).toEqual(["session.venue_status"]);
  });

  it("an answer that lands after a tab switch stays out of the new tab", async () => {
    let answer: (value: unknown) => void = () => undefined;
    const params: unknown[] = [];
    setCompanionBridgeForTests({
      alive: true,
      turnInFlight: false,
      request: (_method: string, p: unknown) => {
        params.push(p);
        return new Promise((resolve) => (answer = resolve));
      },
    } as unknown as CompanionBridgeClient);
    setWorkspace("sessionId", "S-remote");
    actions.setVenueRuntime(running);
    venueStatusTick();
    expect(params).toEqual([{ session_id: "S-remote" }]);
    // Switch to a local tab while the request is in flight.
    setWorkspace("sessionId", "S-local");
    actions.setVenueRuntime(null);
    answer({ venue: { state: "running" } });
    await wait(0);
    expect(workspace.venueRuntime).toBeNull();
  });

  it("a live runtime landing from any path (resume, tab switch) starts polling", async () => {
    let dispose: () => void = () => undefined;
    createRoot((d) => {
      dispose = d;
      trackVenueStatusPolling();
    });
    expect(venueStatusPollingActive()).toBe(false);
    actions.setVenueRuntime(running);
    await wait(0);
    expect(venueStatusPollingActive()).toBe(true);
    dispose();
  });
});

describe("/venue start starts the CURRENT session (#57)", () => {
  function deps() {
    const calls: string[] = [];
    let refuse = true;
    return {
      calls,
      allow: () => (refuse = false),
      deps: {
        newSession: async () => {
          calls.push("newSession");
          return true;
        },
        startRemoteSession: async () => {
          calls.push("venue_start");
          return refuse
            ? { ok: false as const, error: { code: "source_not_pushed", message: "push first" }, detail: "" }
            : { ok: true as const, runtime: { state: "provisioning" } as VenueRuntime };
        },
      },
    };
  }

  it('"current" mode never creates a session', async () => {
    const d = deps();
    const start = makeRemoteStarter(() => "current" as RemoteSourcePickerMode, d.deps as never);
    await start({ source: "src", webAccess: { mode: "public", domains: [] } });
    d.allow();
    await start({ source: "src", webAccess: { mode: "public", domains: [] } });
    expect(d.calls).toEqual(["venue_start", "venue_start"]);
  });

  it('"start" mode creates once; a push-then-retry reuses that session', async () => {
    const d = deps();
    const start = makeRemoteStarter(() => "start" as RemoteSourcePickerMode, d.deps as never);
    const first = await start({ source: "src", webAccess: { mode: "public", domains: [] } });
    expect(first.kind).toBe("started");
    d.allow();
    await start({ source: "src", webAccess: { mode: "public", domains: [] } });
    expect(d.calls).toEqual(["newSession", "venue_start", "venue_start"]);
  });

  it("/venue start opens the picker in current-session mode", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "venue", args: "start" });
    expect(workspace.remoteSourcePickerOpen).toBe(true);
    expect(workspace.remoteSourcePickerMode).toBe("current");
    actions.closeRemoteSourcePicker();
  });
});
